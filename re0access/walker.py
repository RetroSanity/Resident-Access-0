"""Walking the player to a chosen spot.

Resident Evil steers like a tank (with the original controls): left and right
turn you on the spot and up walks the way you are facing. The controller
add-on in the game folder lets the mod move the stick and press buttons for
the game, so the walker holds "keys" that are turned into stick positions.
All distances here are in millimetres, as in the GameCube version (the HD
game's units are centimetres; the caller multiplies by ten).
"""
import ctypes
import math
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32") if hasattr(ctypes, "WinDLL") else None

# DirectInput scan codes: (code, extended)
SCANS = {"up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True),
         "run": (0x2C, False), "a": (0x2D, False),
         # F13: held by the mod's own menu so Dolphin keeps the controller from the game.
         "gate": (0x64, False)}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send_key(name, up):
    code, ext = SCANS[name]
    flags = 0x0008 | (0x0001 if ext else 0) | (0x0002 if up else 0)
    inp = INPUT(type=1)
    inp.ki = KEYBDINPUT(0, code, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def angle_diff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Walker:
    CORNER = 300.0      # how close to get to a turning point on the way
    hurry = False       # run all the way (past the sharks in the Aqua Ring)
    careful = False     # never run (carrying the fuel capsule: running can make it explode)
    slow = False        # creep: forward in short steps, never run (the trophy room's eagle follows you)
    run_held = False    # the player holding the run button (X) while auto walk walks them: then it runs
    force_run = False   # run whether or not they hold it (the trophy room's dash to the step, as asked)
    guide = False       # the 3D beacon: the player walks, following the sound; the walker only keeps
                        # track of the way (aim: where the next turn of it is) and never takes the stick
    GUIDE_CORNER = 450.0
    aim = None
    corner_turn = 0.0   # (how sharply the way turned at the last corner passed, radians)
    AHEAD = 650.0       # how far along the planned line to aim
    OFF = 650.0         # further from the planned line than this: plan again
    STUCK = 2.5         # seconds without getting nearer before trying something else
    LIMIT = 120.0

    def __init__(self, link=None):
        self.held = set()
        self.goal = None
        self.link = link

    @property
    def walking(self):
        return self.goal is not None

    def start(self, name, x, z, via=(), near=600.0, face=None, run=True, reached=None, origin=None):
        """Walk through `via` to (x, z), then on towards `face` until
        `reached(x, z, rot)` says we are there (or we can get no nearer)."""
        self.goal = (name, x, z)
        self.face = face
        self.near = near
        self.via = list(via)
        self.prev = origin
        self.run = run
        self.reached = reached
        self.pushing = False
        self.push_best = None
        self.push_since = 0.0
        self.started = time.monotonic()
        self.still_since = self.started
        self.best = None
        self.backing_until = 0.0
        self.retries = 0
        self.probe = None
        self.replans = 0

    def reroute(self, via, x, z, origin):
        self.goal = (self.goal[0], x, z)
        self.via = list(via)
        self.prev = origin
        self.best = None
        self.probe = None
        self.pushing = False

    def stop(self):
        self.goal = None
        self.hold(set())

    def hold(self, wanted):
        self.held = set(wanted)
        if self.link is None:
            return
        if self.guide:
            from .link import X
            self.held = set()
            self.link.stick = None
            self.link.add = 0
            self.link.block = self.link.block & ~X
            self.link.push()
            return
        lx = (-32000 if "left" in wanted else 0) + (32000 if "right" in wanted else 0)
        ly = (32000 if "up" in wanted else 0) + (-32000 if "down" in wanted else 0)
        self.link.stick = (lx, ly) if wanted & {"left", "right", "up", "down"} else None
        from .link import X, A
        self.link.add = (X if "run" in wanted else 0) | (A if "a" in wanted else 0)
        # (the player's own run button held while walking: the walker decides when it runs, so the
        # last steps and the sharp bends are still walked)
        self.link.block = (self.link.block | X) if self.goal is not None else (self.link.block & ~X)
        self.link.push()

    def bumped(self, x, z, now):
        """True when we have been walking forward and not moving."""
        if "up" not in self.held:
            self.probe = None
            return False
        if self.probe is None:
            self.probe = (now, x, z)
            return False
        since, px, pz = self.probe
        if now - since < 0.55:
            return False
        self.probe = (now, x, z)
        return math.hypot(x - px, z - pz) < 70.0

    def back_off(self, now, word):
        self.retries += 1
        if self.retries > 7:
            self.stop()
            return "blocked"
        self.hold({"down"})
        self.backing_until = now + 0.45
        self.still_since = now + 0.45
        self.best = None
        self.probe = None
        return word

    def step(self, x, z, rot):
        """Steer one tick. Returns None while walking, else 'arrived',
        'near' (as close as it could get), 'bump' or 'replan' (the caller
        should plan the way again), 'blocked' or 'gave up'."""
        name, gx, gz = self.goal
        now = time.monotonic()
        if self.guide:
            return self.guide_step(x, z, rot, gx, gz, now)
        if now - self.started > self.LIMIT:
            self.stop()
            return "gave up"
        if now < self.backing_until:
            return None
        if self.reached and self.reached(x, z, rot):
            self.stop()
            return "arrived"
        if self.pushing:
            # The last stretch: straight at the thing until the game would
            # let you use it, or until we stop getting nearer.
            fx, fz = self.face
            dist = math.hypot(fx - x, fz - z)
            if self.push_best is None or dist < self.push_best - 30.0:
                self.push_best = dist
                self.push_since = now
            if now - self.push_since > 0.8 or dist < 150:
                self.stop()
                return "near"
            d = angle_diff(math.atan2(fx - x, fz - z), rot)
            keys = set()
            if abs(d) > 0.1:
                keys.add("left" if d > 0 else "right")
            if abs(d) < 0.35:
                keys.add("up")
            self.hold(keys)
            return None
        final = not self.via
        tx, tz = (gx, gz) if final else self.via[0]
        dist = math.hypot(tx - x, tz - z)
        if dist < (self.near if final else self.CORNER):
            if not final:
                self.prev = self.via.pop(0)
                self.best = None
                self.still_since = now
                return None
            if self.face and self.reached:
                self.pushing = True
                self.push_since = now
                self.hold(set())
                return None
            if self.face:
                keys, d = self.turn_keys(math.atan2(self.face[0] - x, self.face[1] - z), rot)
                if keys:
                    self.hold(keys)
                    return None
            self.stop()
            return "arrived"
        if self.bumped(x, z, now):
            return self.back_off(now, "bump")
        if self.best is None or dist < self.best - 60.0:
            self.best = dist
            self.still_since = now
        if now - self.still_since > self.STUCK:
            return self.back_off(now, "replan")
        # Keep to the planned line: aim at a point a little way along it,
        # not straight at its far end, so drifting off does not cut a corner.
        ax, az = tx, tz
        if self.prev is not None:
            px, pz = self.prev
            sx, sz = tx - px, tz - pz
            length = math.hypot(sx, sz)
            if length > 1.0:
                along = ((x - px) * sx + (z - pz) * sz) / length
                off = abs((x - px) * sz - (z - pz) * sx) / length
                if off > self.OFF and self.replans < 20:
                    self.replans += 1
                    return "replan"
                aim = min(length, max(0.0, along) + self.AHEAD)
                ax, az = px + sx * aim / length, pz + sz * aim / length
        d = angle_diff(math.atan2(ax - x, az - z), rot)
        keys = set()
        if abs(d) > 0.08:
            keys.add("left" if d > 0 else "right")
        if abs(d) < 0.3:
            keys.add("up")
            if self.slow:
                if now % 0.6 > 0.25:
                    keys.discard("up")      # (a short step, then a pause)
                self.hold(keys)
                return None
            # Run while the player holds the run button (or where it has to), but for the last 70 cm to
            # where it stops (and not round the sharper bends, unless in a hurry: then round them too).
            if self.run and not self.careful and (self.run_held or self.force_run) \
                    and (not final or dist > 700) and (self.hurry or abs(d) < 0.2):
                keys.add("run")
        self.hold(keys)
        return None

    def guide_step(self, x, z, rot, gx, gz, now):
        """The 3D beacon's walk: keep track of the way as the player walks it."""
        if self.reached and self.reached(x, z, rot):
            self.stop()
            return "arrived"
        # Past the turns already reached (and any so near they are no use to aim at)
        while self.via and math.hypot(self.via[0][0] - x, self.via[0][1] - z) < self.GUIDE_CORNER:
            before = self.prev
            self.prev = self.via.pop(0)
            nxt = self.via[0] if self.via else (gx, gz)
            if before is not None:
                a1 = math.atan2(self.prev[0] - before[0], self.prev[1] - before[1])
                a2 = math.atan2(nxt[0] - self.prev[0], nxt[1] - self.prev[1])
                self.corner_turn = max(self.corner_turn, abs(angle_diff(a2, a1)))
        final = not self.via
        tx, tz = (gx, gz) if final else self.via[0]
        dist = math.hypot(tx - x, tz - z)
        if final and dist < self.near:
            self.stop()
            return "arrived"
        if not final:
            # (aim at the furthest of the next turns that is still nearly the same way: on a
            # long line of little steps the beacon stays at its far end, not a step ahead)
            a0 = math.atan2(tx - x, tz - z)
            for px, pz in self.via[1:4] + [(gx, gz)] * (len(self.via) <= 3):
                if abs(angle_diff(math.atan2(px - x, pz - z), a0)) < 0.12:
                    tx, tz = px, pz
                else:
                    break
        self.aim = (tx, tz)
        if self.prev is not None and self.replans < 40:
            px, pz = self.prev
            sx, sz = tx - px, tz - pz
            length = math.hypot(sx, sz)
            if length > 1.0 and abs((x - px) * sz - (z - pz) * sx) / length > self.OFF * 1.5:
                self.replans += 1
                return "replan"          # (well off the way: find it again from here)
        return None

    def turn_keys(self, want, rot, tight=0.12):
        d = angle_diff(want, rot)
        if abs(d) < tight:
            return set(), d
        return {"left" if d > 0 else "right"}, d
