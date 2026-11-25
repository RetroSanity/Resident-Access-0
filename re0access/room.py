"""What is in a room, from the game's own files:
- nativePC/arc/scene/<room>.arc: scene\\event\\<room>b holds the boxes you
  stand in to use something (doors, things to check), event\\<room> the room's
  script, which says what each box does (a door and where it goes, the
  message a thing shows).
- nativePC/arc/arrange/loctbl.arc: the items lying in each room and where (loctbl0000 Easy,
  0100 Normal, 0200 Hard).
Places are room-local, in centimetres (x, y, z), like the player's position
less the room's offset."""
import os
import re
import math
import struct

from . import sbc, gmd, names

# kinds of script entry (the first word of each 16-byte entry at the start of the script)
DOOR_KINDS = (0x1F, 0x04)
CHECK_KINDS = (0x02, 0x08, 0x0E)
# (Mixing Set, the Red, Blue and Green Chemicals, Stripping Agent, Sulfuric Acid, Battery Fluid)
CHEMICALS = {0x7B, 0x86, 0x87, 0x88, 0x89, 0x8B, 0x8C}
# (names from anywhere in what a thing says, where its first sentence names nothing)
CHECK_NAMES = [
    ("spikes are encrusted", "Spiked torture device"),
    ("bottom of the well", "Torture well"),
    ("could collapse at any time", "Crumbling floor"),
    ("you can't reach it by yourself", "Something too high to reach alone"),
    ("flames flicker mysteriously", "Flickering flames"),
]
# (room: (typewriter x, z, stand x, z, facing) cm)
# Names for things whose own words do not say what they are: (room, zone) -> name.
ZONE_NAMES = {
    ("dm0a", 1): "Locked locker, needs the Locker Key",     # (the Training Room's, upstairs: "It's locked.")
    ("dm0a", 3): "Red button, starts a fight with Hunters",  # ("Press the button?": Battle sequence initiated)
    # (the Statue Room's six statues, each named after the riddle on it)
    ("dm04", 7): "Eagle statue",        # (I dance freely through the air, capturing a legless prey)
    ("dm04", 8): "Deer statue",         # (I stand tall on the earth with horns proudly displayed)
    ("dm04", 9): "Snake statue",        # (I creep up on my victims in legless silence...)
    ("dm04", 10): "Wolf statue",        # (My sharp wits allow me to bring down even the greatest horned beast)
    ("dm04", 11): "Horse statue",       # (No amount of cunning can match the speed of my supple limbs)
    ("dm04", 12): "Panther statue",     # (I am the king of all I survey)
}
TYPEWRITER_SPOTS = {"sh00": (1075.0, 827.0, 1142.0, 845.0, -math.pi / 2)}

OP_TYPEWRITER = 0x51      # 00 51 01: the typewriter (save) question
OP_MESSAGE = 0x31          # 00 31 NN 00 00: show message NN of the room's message file
OP_DOORS = (0x38, 0x39)    # 00 38 SS RR ..: go through to stage SS, room RR (39: the same, stairs and others)
OP_JUMPS = (0x04, 0x06, 0x0D)   # 00 04 HH LL and others: jumps to HHLL

ITEM_RECORD = 0x71         # an item lying in the room (loctbl)
KEY_ITEM_RECORD = 0x78     # a key item lying there (Train Key, Briefcase, Hookshot...)
FIXED_ITEM = 0x70          # an item that is always there (locfix)


def _zones(x):
    """{index: [triangles]} from an rEventCollision file (XFS: one object of
    48-byte triangles per box, numbered from 2)."""
    out = {}
    for m in re.finditer(rb"\x05\x00(.)\x00", x, re.S):
        i = m.start()
        if i + 12 > len(x):
            continue
        sz, n = struct.unpack_from("<II", x, i + 4)
        if 0 < n < 5000 and sz == 8 + 48 * n and i + 4 + sz <= len(x):
            tris = [struct.unpack_from("<12f", x, i + 12 + 48 * k) for k in range(n)]
            out[m.group(1)[0] - 2] = tris
    return out


def _box(tris):
    pts = [(v[j], v[j + 1], v[j + 2]) for v in tris for j in (0, 4, 8)]
    lo = tuple(min(p[a] for p in pts) for a in range(3))
    hi = tuple(max(p[a] for p in pts) for a in range(3))
    return lo, hi


def _parts(tris):
    """The separate boxes in one zone (a zone can be several boxes of 12 triangles)."""
    if len(tris) % 12 == 0 and len(tris) > 12:
        return [_box(tris[k:k + 12]) for k in range(0, len(tris), 12)]
    return [_box(tris)]


def _footprint(tris):
    """The outline of a zone seen from above: [(x, z)] round it anticlockwise (convex hull)."""
    pts = sorted(set((round(v[j], 1), round(v[j + 2], 1)) for v in tris for j in (0, 4, 8)))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def in_outline(poly, x, z):
    """True if (x, z) is inside the outline (anticlockwise, convex)."""
    if len(poly) < 3:
        return False
    for i in range(len(poly)):
        (ax, az), (bx, bz) = poly[i], poly[(i + 1) % len(poly)]
        if (bx - ax) * (z - az) - (bz - az) * (x - ax) < 0:
            return False
    return True


class Thing:
    def __init__(self, kind, name, pos, box=None, **extra):
        self.kind = kind            # "door", "item", "check"
        self.name = name
        self.pos = pos              # (x, y, z) room-local centimetres
        self.box = box              # ((x0, y0, z0), (x1, y1, z1)) or None
        self.__dict__.update(extra)

    def __repr__(self):
        return "<%s %s at %.0f,%.0f>" % (self.kind, self.name, self.pos[0], self.pos[2])


class Script:
    def __init__(self, data):
        self.d = data
        count = struct.unpack_from(">I", data, 0)[0]
        self.entries = []
        for k in range(1, count + 1):
            if 16 * k + 16 > len(data):
                break
            kind, off, index, _ = struct.unpack_from(">IIII", data, 16 * k)
            self.entries.append((kind, off, index))
        offs = sorted(set(o for _, o, _ in self.entries) | {len(data)})
        self.ends = {o: offs[offs.index(o) + 1] for o in offs[:-1]}

    def body(self, off):
        return self.d[off:self.ends.get(off, len(self.d))]

    @staticmethod
    def messages(body):
        return [body[i + 2] for i in range(len(body) - 4)
                if body[i] == 0 and body[i + 1] == OP_MESSAGE and body[i + 3] == 0 and body[i + 4] == 0]

    @staticmethod
    def _door_ops(code):
        out = []
        for i in range(len(code) - 4):
            if code[i] != 0 or code[i + 1] not in OP_DOORS:
                continue
            # (00 38 00 NN: to scene NN of the whole game - the Laboratory and on are 0x63 and up,
            # and the Observation Room's hole Rebecca is lifted through goes to 0x61; a first byte
            # that is not 0 is read as before, a stage and a room in it)
            if code[i + 2] == 0 and 0 < code[i + 3] < 0xB0 or 0 < code[i + 2] < 12 and code[i + 3] < 0x60:
                out.append((code[i + 2], code[i + 3]))
        return out

    def doors(self, off):
        """[(stage, room)] the entry at off can take you to (following its jumps out of it once)."""
        body = self.body(off)
        out = self._door_ops(body)
        if out:
            return out
        end = self.ends.get(off, len(self.d))
        for i in range(len(body) - 3):
            if body[i] == 0 and body[i + 1] in OP_JUMPS:
                to = (body[i + 2] << 8) | body[i + 3]
                if not (off <= to < end) and 0 < to < len(self.d):
                    out += self._door_ops(self.d[to:to + 0x80])
        return out


_TRANS_PAT = re.compile(rb"\x01\x00\x00\x00....\x01\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00", re.S)


def room_trans(d, ents, code):
    """Where the room sits in the game's world (x, y, z cm): mTrans, the first thing in its
    scene file. Positions in the room's files are counted from there."""
    k = "scene\\" + code
    if k not in ents:
        return None
    try:
        b = sbc.unpack(d, ents[k])
        m = _TRANS_PAT.search(b, max(0, b.rfind(b"data\x00")))
        if not m:
            return None
        v = struct.unpack_from("<3f", b, m.end())
    except Exception:
        return None
    if not all(abs(c) < 100000 for c in v):
        return None
    return v


class RoomData:
    """Everything known about one room from the files. code is like "tr07"."""

    def __init__(self, game_dir, code, scene_no, scene_of, room_words, table="arrange\\loctbl0100"):
        self.code = code
        self.things = []
        self.trans = None
        self.base_code = code
        arc = os.path.join(game_dir, "nativePC", "arc", "scene", code + ".arc")
        try:
            d, ents = sbc.read_arc(arc)
        except OSError:
            return
        self.trans = room_trans(d, ents, code)
        zones = {}
        key = "scene\\event\\%sb" % code
        if key in ents:
            zones = _zones(sbc.unpack(d, ents[key]))
        script = None
        scode = code
        self.base_code = code
        for k in ents:
            if k.startswith("event\\"):
                scode = k.split("\\")[-1]
                self.base_code = scode      # (a room later in the story carries its first one's)
                try:
                    script = Script(sbc.unpack(d, ents[k]))
                except Exception:
                    script = None
                break
        texts = gmd.texts(game_dir, "scene_" + scode) or gmd.texts(game_dir, "scene_" + code)
        used = set()
        # (zones where an item can be used - the hookshot at the window: a kind 0x08 entry)
        use_zones = {index for kind, off, index in script.entries if kind == 0x08} if script else set()
        # (the floor: most zones lie on it; a door well below or above it is a stairway)
        floors = sorted(_box(z)[0][1] for z in zones.values()) if zones else [0.0]
        floor = floors[len(floors) // 2]
        if script:
            for kind, off, index in script.entries:
                if index not in zones or index in used:
                    continue
                body = script.body(off)
                parts = _parts(zones[index])
                lo, hi = _box(zones[index])
                centre = ((lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2)
                if kind in DOOR_KINDS:
                    to = script.doors(off)
                    msgs = [names.clean(texts[m]) for m in script.messages(body) if m < len(texts)]
                    low = " ".join(msgs).lower()
                    if to:
                        st, rm = to[0]
                        if "air vent" in low and len(to) > 1:
                            st, rm = to[-1]     # (the Observation Room's cell's vent: the first is a stray, the Main Hall)
                        target = scene_of(st, rm)
                        where = (" to " + room_words(target)) if target is not None else ""
                        if "ladder" in low:
                            way = " up" if ("climb up" in low or "lead up" in low or "leads up" in low) else \
                                  " down" if ("climb down" in low or "lead down" in low or "leads down" in low) else ""
                            name = "Ladder" + way + where
                        elif hi[1] - lo[1] > 60:
                            # (a sloping zone: a stairway; flat ones on another floor are doors there)
                            mid = (lo[1] + hi[1]) / 2
                            name = ("Stairs down" if mid < floor - 30 else
                                    "Stairs up" if mid > floor + 30 else "Stairs") + where
                        else:
                            name = "Door" + where
                        if "air vent" in low and "boost" in low:
                            name = "Air vent Billy lifts Rebecca up through," + where
                        self.things.append(Thing("door", name, centre, (lo, hi), target=target, zone=index, says=msgs))
                    else:
                        if not msgs:
                            continue        # (leads nowhere and says nothing: a story event, not a door)
                        self.things.append(Thing("door", "Door" + (", locked" if "lock" in low else ""),
                                                 centre, (lo, hi), target=None, zone=index, says=msgs))
                    used.add(index)
                elif kind in CHECK_KINDS:
                    msgs = [m for m in script.messages(body) if m < len(texts)]
                    words = [names.clean(texts[m]) for m in msgs]
                    words = [w for w in words if w and w != "Invalid Message" and w.isascii()]
                    if not words and any(body[i] == 0 and body[i + 1] == OP_TYPEWRITER for i in range(len(body) - 2)):
                        self.things.append(Thing("check", "Typewriter", centre, (lo, hi), zone=index, says=[],
                                                 outline=_footprint(zones[index])))
                        used.add(index)
                        continue
                    if not words and kind == 0x02 and script.doors(off):
                        # (no message but it opens a close-up: a panel to work - the engine's brake)
                        name = "Brake panel" if self.base_code == "tr00" else "Control panel"
                        self.things.append(Thing("check", name, centre, (lo, hi), zone=index, says=[],
                                                 outline=_footprint(zones[index]), puzzle=True))
                        used.add(index)
                        continue
                    j = -1
                    if not words and kind == 0x02:
                        j = next((i for i in range(len(body) - 10)
                                  if body[i:i + 4] in (b"\x00\x9a\x00\x02", b"\x00\x99\x00\x02")), -1)
                    if j >= 0:
                        # (a spot to jump down from or climb up onto a raised walkway: op 9a, then
                        # 1 for up, 2 for down - the sewer's water channel. Op 99 is a step up onto
                        # a platform - the Small Library's - 2 for up, 1 for down, and its zone's
                        # height is the level it takes you to: fix_step_heights puts it right)
                        v = struct.unpack_from(">H", body, j + 8)[0]
                        if body[j + 1] == 0x9a:
                            way = "up" if v == 1 else "down"
                            name = "Climb up" if way == "up" else "Jump down"
                        else:
                            way = "up" if v == 2 else "down"
                            name = "Step up" if way == "up" else "Step down"
                        self.things.append(Thing("ledge", name, centre, (lo, hi), zone=index, says=[], way=way,
                                                 outline=_footprint(zones[index]), step=body[j + 1] == 0x99))
                        used.add(index)
                        continue
                    if not words and kind == 0x02:
                        # (no message of its own, but it looks for chemicals you carry - op 3d with
                        # an item: the Medicine Room's canister, where the Mixing Set and the
                        # chemicals are used)
                        ids = {struct.unpack_from(">H", body, i + 2)[0] for i in range(len(body) - 3)
                               if body[i] == 0 and body[i + 1] == 0x3D}
                        if ids & CHEMICALS:
                            name = "Chemical canister" if self.base_code == "sh0d" else "Place to use a chemical"
                            self.things.append(Thing("check", name, centre, (lo, hi), zone=index,
                                                     says=[], outline=_footprint(zones[index]), puzzle=True))
                            used.add(index)
                            continue
                    if not words:
                        continue        # (no message: a story event starts there, not a thing to check)
                    name = names.best_name(words) or "Something to check"
                    low = " ".join(words).lower()
                    if "service lift" in low or "dumbwaiter" in low:
                        name = "Service lift"
                    elif "mounted stuffed animal" in low:
                        name = "Moose head"     # (high on the wall: "too dark to see" till the lights are on)
                    # (one that asks you something - reconnect the cable? press the button? - is
                    # a puzzle or a machine to work, not just something to look at)
                    puzzle = any("<SEL" in texts[m] for m in msgs) or index in use_zones or \
                        "if you had" in low     # ("If you had some microfilm, you could view it": an item goes here)
                    # a box as wide as the room (a whole-room message) is not a place to walk to
                    area = (hi[0] - lo[0]) * (hi[2] - lo[2])
                    if area > 600 * 600 or (len(parts) > 1 and area > 250 * 250):
                        continue        # (a small zone in two parts - the lecture room's lectern - is one thing)
                    if "control device" in low and "broken" in low:
                        name = "Broken control device"
                    for pat, nm in CHECK_NAMES:
                        if pat in low and (name in ("Something to check", "Nothing much") or pat.startswith("flames")):
                            name = nm
                            break
                    if "system disk" in low:
                        name = "Lectern"    # (the lecture room's: "Evac. Drill Path: Locked")
                    if "lift" in low and "not getting any power" in low:
                        name = "Lift, runs on the Battery"  # (the Training Facility Entrance's: "Use the lift?")
                    if "the piano" in low and "piano" not in name.lower():
                        name = "Piano"      # (the Piano Bar's: "No need to play" once it is played)
                    name = ZONE_NAMES.get((self.base_code, index), name)
                    self.things.append(Thing("check", name, centre, (lo, hi), zone=index, says=words,
                                             outline=_footprint(zones[index]), puzzle=puzzle))
                    used.add(index)
        # (where to stand to use a typewriter whose own side is blocked: the Main Hall's has a
        # counter in front of it - A reaches it from its right-hand end, facing west)
        for th in self.things:
            st = TYPEWRITER_SPOTS.get(self.base_code)
            if th.name == "Typewriter" and st and math.hypot(th.pos[0] - st[0], th.pos[2] - st[1]) < 150:
                th.spot = ((st[2] * 10.0, st[3] * 10.0), st[4])
        # Places the game has no zone of their own for (all in one big zone, the script working out
        # which from where you stand): the lecture room's sixteen seats with switches on their
        # desks, numbered 0 to F as on the Microfilm Image (front row, nearest the screen: 0 1 2 3)
        if self.base_code == "sh0b":
            rows = ("front row", "second row", "third row", "back row")
            cols = ("far left", "left of the aisle", "right of the aisle", "far right")
            for r, z in enumerate((530.0, 680.0, 830.0, 980.0)):
                for c, x in enumerate((490.0, 630.0, 970.0, 1110.0)):
                    n = "0123456789ABCDEF"[r * 4 + c]
                    self.things.append(Thing("check", "Seat %s, %s, %s" % (n, rows[r], cols[c]), (x, -100.0, z),
                                             ((x - 12, -110.0, z - 12), (x + 12, -90.0, z + 12)),
                                             stand=True, face=math.pi, puzzle=True, seat=n,
                                             outline=[(x - 12, z - 12), (x + 12, z - 12), (x + 12, z + 12), (x - 12, z + 12)]))
        # A lift you stand a character on (the Main Library's): a zone with only an enter and a
        # leave event (kinds 4 and 5, no door), in a room whose words talk of a lift
        if script:
            kinds = {}
            for kind, off, index in script.entries:
                kinds.setdefault(index, set()).add(kind)
            lifty = any("lift" in " ".join(getattr(th, "says", None) or []).lower() and
                        "service lift" not in " ".join(getattr(th, "says", None) or []).lower()
                        for th in self.things if th.kind == "check")
            for index, ks in kinds.items():
                if not lifty or index in used or index not in zones or not {0x04, 0x05} <= ks:
                    continue
                if any(kind == 0x04 and script.doors(off) for kind, off, i in script.entries if i == index):
                    continue
                lo, hi = _box(zones[index])
                centre = ((lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2)
                self.things.append(Thing("check", "Lift", centre, (lo, hi), zone=index, says=[], puzzle=True,
                                         stand=True, outline=_footprint(zones[index])))
                used.add(index)
        self.items = load_items(game_dir, scene_no, table)
        for it in self.items:
            self.things.append(it)


_loc = {}


def load_items(game_dir, scene_no, table="arrange\\loctbl0100"):
    """[Thing] items placed in the room (scene number) by the game's location tables."""
    if (game_dir, table) not in _loc:
        try:
            d, ents = sbc.read_arc(os.path.join(game_dir, "nativePC", "arc", "arrange", "loctbl.arc"))
            groups = {}
            for name in (table,):
                if name not in ents:
                    continue
                x = sbc.unpack(d, ents[name])
                n = struct.unpack_from("<I", x, 8)[0]
                o = 12
                for _ in range(n):
                    room, cnt = struct.unpack_from("<HH", x, o)
                    o += 4
                    for _ in range(cnt):
                        groups.setdefault(room, []).append(x[o:o + 32])
                        o += 32
            _loc[(game_dir, table)] = groups
        except (OSError, struct.error):
            _loc[(game_dir, table)] = {}
    labels, _texts = gmd.archive(game_dir).get("message_item_name_eng", ({}, []))
    out = []
    for rec in _loc[(game_dir, table)].get(scene_no, []):
        if rec[0] not in (ITEM_RECORD, KEY_ITEM_RECORD) or 0xA0 <= rec[2] <= 0xAF and rec[3] == 0:
            continue
        item = rec[1]
        amount = struct.unpack_from("<h", rec, 6)[0]
        x, y, z = struct.unpack_from("<3f", rec, 8)
        name = item_name(labels, item)
        if name is None or (x, y, z) == (0, 0, 0):
            continue
        out.append(Thing("item", name, (x, y, z), item=item, amount=amount, flag=rec[2]))
    return out


MIXED = {}


def item_name(labels, item):
    name = names.clean(labels.get("item_name_%d" % item, ""))
    if not name or name == "Invalid Message":
        return None
    return name
