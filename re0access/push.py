"""Things to push into place (the Private Study Room's table, to climb on under the moose head):
where the thing is and how far it still has to go, auto walk to the side to push it from (over it
first when it is in the way), and words as it moves - "Stop" once it is there."""
import logging
import math
import struct
import time

from . import link as L
from . import room as R

VT_OBSTACLE = 0x00CC8140        # uObstacle: a thing in the room you can push (x, y, z at +0x40)
MM = 10.0

# By the room's first code (later visits to the room carry it). The thing goes in steps of about
# step cm; each stage pushes it one way to a place: axis 0 is x, 2 is z, sign the way it moves.
# (From driving it: the table starts at 446,130; pushed west along the bookshelves it stops at the
# wall at x 145, blocking the corner by the light switch; from that corner, pushed south four
# times, it is at z 304, under the moose head - climbed on there, A reaches the head.)
PUZZLES = {
    "sh0f": {
        "name": "Table",
        "start": (446.0, 130.0),
        "step": 43.5,
        "stages": [
            {"axis": 0, "sign": -1, "to": 145.0, "heading": -math.pi / 2,
             "way": "along the bookshelves to the wall at the end",
             "done": "The table is against the wall. Next, climb over it into the corner by the light "
                     "switch, then push it towards the fireplace."},
            {"axis": 2, "sign": 1, "to": 304.0, "heading": 0.0,
             "way": "towards the fireplace, to under the moose head",
             "done": "Stop. The table is under the moose head. Turn the lights on with the switch, then "
                     "climb on the table and check the moose head."},
        ],
        # (things up high that are got at from on top of it, once it is in place)
        "on_top": ("Iron Needle", "Moose head"),
        "off": "The table has gone the wrong way. Leave the room and come back to put it back.",
        "push_words": "Hold forward to push the table %s.",
    },
    # (the Small Library: up the step onto the platform, along it to the west end of the movable
    # bookcase in front of the pillar; pushed east about 77 cm it stops, and the compartment in
    # the pillar with the Book of Good is open)
    "sh10": {
        "name": "Bookcase",
        "start": (485.0, 89.5),
        "step": 26.0,
        "reach": 97.0,
        "size": (67.0, 25.0),
        "stages": [
            {"axis": 0, "sign": 1, "to": 562.5, "heading": math.pi / 2,
             "way": "to the right, along the shelves",
             "done": "Stop. The bookcase is moved. The Book of Good is in the pillar where it was."},
        ],
        "done_words": "moved, the Book of Good is behind where it was",
        "off": "The bookcase has gone the wrong way. Leave the room and come back to put it back.",
        "push_words": "Hold forward to push the bookcase %s.",
    },
    # (the chess room: the board is the floor, the pieces are big things to push - push any piece
    # but the white king (white, with the cross on top: the same size as the black king), or the
    # king the wrong way, and gas comes out. The king starts in the middle of the row of three,
    # 678,351; a push moves a piece one square, about 51 cm. South is towards the exit door (+z),
    # east to the right facing it (+x): three south, to the first square with room beside it to
    # push from, one east, two north till it stops against the piece it started beside, and the
    # board on the desk slides aside. (Pushing the piece east of it south lets out the gas.))
    "sh12": {
        "name": "White king",
        "pieces": True,
        "start": (677.8, 351.0),
        "step": 43.0,           # (one push moves a piece about 43 cm, then a pause)
        "reach": 75.0,
        "stages": [
            # (south till there is room on its left to push it from - the black pawn is beside it
            # till then: about four pushes)
            {"axis": 2, "sign": 1, "to": 515.0, "heading": 0.0, "over": 70.0,
             "way": "south, towards the door",
             "done": "Stop. Next, push it east, to the right as you face the door, till it stops "
                     "against the cabinet. Auto walk to the white king takes you round to the side "
                     "to push it from."},
            # (east till the cabinet stops it, in line with the white piece it started beside)
            {"axis": 0, "sign": 1, "to": 729.5, "heading": math.pi / 2, "reach": 70.0, "lane": 70.0,
             "until_stops": True,
             "way": "east, to the right as you face the door, till it stops",
             "done": "Stop. Last, push it north, back towards the other pieces, till it stops against "
                     "the white piece. Auto walk to the white king takes you round to the side to push "
                     "it from."},
            # (north till that piece stops it)
            {"axis": 2, "sign": -1, "to": 402.0, "heading": math.pi, "reach": 72.0, "until_stops": True,
             "way": "north, back towards the other pieces, till it stops",
             "done": "Stop. The white king is in place. The chessboard on the desk should slide aside: "
                     "the Book of Evil is in the desk."},
        ],
        "done_words": "in place, the desk is open",
        "off": "The white king has gone the wrong way. Leave the room and come back to put the board "
               "back. Do not push any other piece: that lets out poison gas.",
        "push_words": "Hold forward to push the white king %s. Only push the king.",
    },
    # (the Training Facility Entrance: the crate that comes down on the lift once the Battery powers
    # it. From driving it: it starts at 1396.5,720 in the lift with its back to the wall - climbed
    # onto from the front (A), walked to the back edge and down behind it (A), it is pushed out
    # south five times (about 47.5 cm a push) till the railing stops it; west ten times; south
    # four, beside the pillar; and west once more against it. Climbed onto from the east and facing
    # the pillar, A reaches the Discipline Tablet on top. Rebecca must wait out of its way - RB.)
    "sh09": {
        "name": "Crate",
        "pieces": True,             # (picked out from the lift's own block beside it by its way)
        "start": (1396.5, 720.0),
        "step": 47.5,
        "reach": 75.0,
        "stages": [
            {"axis": 2, "sign": 1, "to": 957.4, "heading": 0.0, "until_stops": True, "over": 60.0,
             "climb_note": True,
             "way": "out of the lift, till it stops against the railing",
             "done": "Stop. Next, push it west, to your right as you face out of the lift: ten pushes. "
                     "Auto walk to the crate takes you round to the side to push it from."},
            {"axis": 0, "sign": -1, "to": 921.3, "heading": -math.pi / 2,
             "way": "west, along the railing",
             "done": "Stop. Next, push it south, down the walkway towards the bridge: four pushes. Auto "
                     "walk to the crate takes you round to the side to push it from."},
            {"axis": 2, "sign": 1, "to": 1147.4, "heading": 0.0,
             "way": "south, down the walkway, to beside the pillar",
             "done": "Stop. Last, push it west, against the pillar. Auto walk to the crate takes you round "
                     "to the side to push it from."},
            {"axis": 0, "sign": -1, "to": 900.0, "heading": -math.pi / 2, "until_stops": True,
             "way": "west, against the pillar",
             "done": "Stop. The crate is against the pillar. Climb onto it from this side, face the pillar "
                     "and press A for the Discipline Tablet. Auto walk to the Discipline Tablet does it."},
        ],
        "front_words": "Facing the crate in the lift. Press A to climb onto it, walk forward to its far "
                       "edge, press A to get down behind it, then turn round and auto walk to the crate "
                       "again. Rebecca has to wait out of the way: RB tells her to wait.",
        # (climbed onto from the east side, the side last pushed from, facing the pillar)
        "top_from": (75.0, 0.0, -math.pi / 2),
        "on_top": ("Discipline Tablet", "Top of the pillar", "Pillar, the Discipline Tablet is on top"),
        "done_words": "against the pillar, climb on it for the Discipline Tablet",
        "off": "The crate is off the way the guide knows. Push it back in line with where it was.",
        "push_words": "Hold forward to push the crate %s.",
    },
}


class PushMixin:
    # -- where it is ------------------------------------------------------------------------
    def push_puzzle(self):
        rd = self.room_data
        return PUZZLES.get(rd.base_code) if rd else None

    def push_objs(self):
        """[(address, (x, y, z) cm in the room)] of the things to push here."""
        out = []
        try:
            off = self.game.room_offset() or (0.0, 0.0, 0.0)
            for vt, obj in self.game.units():
                if vt == VT_OBSTACLE:
                    x, y, z = struct.unpack("<3f", self.mem.read(obj + 0x40, 12))
                    out.append((obj, (x - off[0], y - off[1], z - off[2])))
        except Exception:
            return []
        return out

    def push_obj_pos(self):
        """(x, y, z) cm in the room of the thing to push, or None."""
        objs = self.push_objs()
        if not objs:
            return None
        pz = self.push_puzzle()
        if not pz or not pz.get("pieces"):
            return objs[0][1]
        return self.push_piece(pz, objs)

    def _push_path(self, pz):
        pts = [tuple(pz["start"])]
        for s in pz["stages"]:
            x, z = pts[-1]
            pts.append((s["to"], z) if s["axis"] == 0 else (x, s["to"]))
        return pts

    def push_piece(self, pz, objs):
        """Of the pieces on the board, the one to push (the white king): the one seen as it before
        while it is still there, else the one on its way from where it starts to where it goes."""
        ground = [(a, p) for a, p in objs if abs(p[1]) < 40.0]
        if not ground:
            return None
        path = self._push_path(pz)

        def gap(p):
            best = 1e9
            for (ax, az), (bx, bz) in zip(path, path[1:]):
                dx, dz = bx - ax, bz - az
                ln = dx * dx + dz * dz
                k = 0.0 if ln == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[2] - az) * dz) / ln))
                best = min(best, math.hypot(p[0] - ax - k * dx, p[2] - az - k * dz))
            return best
        # (on its way there is only ever the king - every other piece is a square off it; off the
        # way, the one seen as it before, while that is still in the room)
        a, p = min(ground, key=lambda ap: gap(ap[1]))
        if gap(p) <= 25.0:
            self.push_piece_addr = (self.scene, a)
            return p
        mine = getattr(self, "push_piece_addr", None)
        if mine and mine[0] == self.scene:
            for a2, p2 in ground:
                if a2 == mine[1]:
                    return p2
        return p

    def push_thing(self):
        pz = self.push_puzzle()
        if not pz:
            return None
        pos = self.push_obj_pos()
        if not pos:
            return None
        return R.Thing("check", pz["name"], pos, None, puzzle=True, push=True)

    def push_stage(self, pos=None):
        """(index of the stage it is in, pushes left in it), (len(stages), 0) once in place, or
        (None, 0) when it has gone off the way."""
        pz = self.push_puzzle()
        if not pz:
            return None, 0
        pos = pos or self.push_obj_pos()
        if not pos:
            return None, 0
        st = pz["stages"]
        cur = [pz["start"][0], pz["start"][1]]          # (x, z) where each stage starts
        v = (pos[0], pos[2])
        found = None
        for i, s in enumerate(st):
            # (on this stage's stretch of the way: in line with it, not past either end; where two
            # stretches meet, the later one - the king goes back up the way it came down)
            ax = 0 if s["axis"] == 0 else 1
            other = 1 - ax
            left = (s["to"] - v[ax]) * s["sign"]
            span = (s["to"] - cur[ax]) * s["sign"]
            last = i == len(st) - 1
            lane = s.get("lane", 12.0)
            over = s.get("over", 50.0 if last or s.get("until_stops") else 12.0)
            if i:
                # (only once the stage before is done: past its place, or as good as)
                ps_ = st[i - 1]
                pv = v[0 if ps_["axis"] == 0 else 1]
                if (ps_["to"] - pv) * ps_["sign"] > (15.0 if ps_.get("until_stops") else 12.0):
                    cur[ax] = s["to"]
                    continue
            if abs(v[other] - cur[other]) <= lane and -over <= left <= span + 12.0:
                found = (i, left)
            cur[ax] = s["to"]
        self._push_found = found
        if found is None:
            return None, 0                               # (off the way, or pushed back past it)
        i, left = found
        if left > 12.0:
            return i, max(1, int(round(left / pz["step"])))
        return (len(st), 0) if i == len(st) - 1 else (i + 1, max(1, int(round(abs(st[i + 1]["to"] - (v[0] if st[i + 1]["axis"] == 0 else v[1])) / pz["step"]))))

    def push_words(self, t):
        """What is said after the table's name in the list."""
        pz = self.push_puzzle()
        i, left = self.push_stage(t.pos)
        if pz is None:
            return ""
        if i is None:
            return pz["off"]
        if i >= len(pz["stages"]):
            return pz.get("done_words", "in place, under the moose head")
        s = pz["stages"][i]
        return "push it %s, %d more %s" % (s["way"], left, "push" if left == 1 else "pushes")

    # -- auto walk to push it -----------------------------------------------------------------
    def push_spot(self, pos, i, p):
        """((x, z) cm to stand at, heading to face, climb over first?) for stage i."""
        pz = self.push_puzzle()
        s = pz["stages"][i]
        x, z = pos[0], pos[2]
        self.push_spot_words = None
        if pz.get("pieces"):
            # (straight behind it, square on: a piece moves the way it is pushed)
            r = s.get("reach", pz.get("reach", 85.0))
            if s.get("climb_note") and s["axis"] == 2 and (p is None or (p[2] - z) * s["sign"] > -40.0):
                # (not behind it yet - the crate in the lift has its back to the wall: to its
                # front, facing it, to climb over)
                self.push_spot_words = pz.get("front_words")
                return (x, z + s["sign"] * r), s["heading"] + math.pi, False
            if s["axis"] == 0:
                return (x - s["sign"] * r, z), s["heading"], False
            return (x, z - s["sign"] * r), s["heading"], False
        if s["axis"] == 0:
            return (x - s["sign"] * pz.get("reach", 85.0), z), s["heading"], False
        # (behind it along z: from the corner it blocks, climbed over to while it is still in the way)
        behind = z - s["sign"] * 80.0
        on_far_side = p is not None and (p[2] - z) * s["sign"] < -40.0
        if abs(z - pz["start"][1]) < 25.0 and not on_far_side:
            prev = pz["stages"][i - 1] if i else None
            if prev is not None and prev["axis"] == 0:
                return (x - prev["sign"] * 85.0, z), prev["heading"], True
        return (x - 20.0, behind), s["heading"], False

    def start_push_walk(self, t, guide=False):
        pz = self.push_puzzle()
        p, _h = self.where_me()
        i, left = self.push_stage(t.pos)
        if i is None:
            self.say(pz["off"], interrupt=True)
            return
        if i >= len(pz["stages"]):
            self.say("The %s is %s." % (pz["name"].lower(), pz.get("done_words", "in place, under the moose head")),
                     interrupt=True)
            return
        (sx, sz), heading, climb = self.push_spot(t.pos, i, p)
        r = 10.0
        spot = R.Thing("check", t.name, (sx, t.pos[1], sz), ((sx - r, t.pos[1] - 10, sz - r), (sx + r, t.pos[1] + 10, sz + r)),
                       stand=True, outline=[(sx - r, sz - r), (sx + r, sz - r), (sx + r, sz + r), (sx - r, sz + r)])
        spot.push_of = t
        self.push_plan = {"heading": heading, "climb": climb, "stage": i, "guide": guide, "t": t,
                          "words": getattr(self, "push_spot_words", None)}
        logging.info("push walk: stage %s spot %.0f,%.0f climb %s", i, sx, sz, climb)
        if p and math.hypot(p[0] - sx, p[2] - sz) < 30.0:
            self.walk_target = spot
            self.turning = (heading, time.monotonic() + 3.0, spot)
            return
        self.start_walk(guide=guide, target=spot)

    def start_top_walk(self, t, guide=False):
        """Auto walk to something got at from on top of the table (the Iron Needle on the moose
        head): onto the table once it is in place, facing it. False if this is not one."""
        pz = self.push_puzzle()
        if not pz or t.name not in pz.get("on_top", ()):
            return False
        pos = self.push_obj_pos()
        i, _left = self.push_stage(pos) if pos else (None, 0)
        if i is None or i < len(pz["stages"]):
            return False                    # (the table is not under it yet: the usual walk)
        p, h = self.where_me()
        if not p:
            return False
        first = pz["stages"][0]
        if pz.get("top_from"):
            first = dict(first, heading=pz["top_from"][2], axis=0, sign=-1 if pz["top_from"][0] > 0 else 1)
        if p[1] - pos[1] > 70.0:
            # (on it already: only turn to face it)
            self.push_plan = {"heading": first["heading"], "climb": True, "stage": 1, "top": t, "guide": guide,
                              "first": first}
            now = time.monotonic()
            self.push_climb = {"step": 5, "y0": p[1], "next": now, "release": 0.0, "since": now,
                               "presses": 0, "until": now + 15.0}
            return True
        # (climbed from the side it was first pushed from: square on to it, then up and facing the wall)
        sx, sz = pos[0] - first["sign"] * 85.0, pos[2]
        if pz.get("top_from"):
            dx, dz, hd = pz["top_from"]
            sx, sz = pos[0] + dx, pos[2] + dz
            first = dict(first, heading=hd, axis=0, sign=-1 if dx > 0 else 1)
        r = 10.0
        spot = R.Thing("check", t.name, (sx, pos[1], sz), ((sx - r, pos[1] - 10, sz - r), (sx + r, pos[1] + 10, sz + r)),
                       stand=True, outline=[(sx - r, sz - r), (sx + r, sz - r), (sx + r, sz + r), (sx - r, sz + r)])
        spot.push_of = pz
        self.push_plan = {"heading": first["heading"], "climb": True, "stage": 1, "top": t, "guide": guide,
                          "first": first}
        logging.info("top walk: to %s from %.0f,%.0f", t.name, sx, sz)
        if math.hypot(p[0] - sx, p[2] - sz) < 30.0:
            self.walk_target = spot
            self.turning = (first["heading"], time.monotonic() + 3.0, spot)
            return True
        self.start_walk(guide=guide, target=spot)
        return True

    def start_off_table(self, t, guide=False):
        """Standing on the table and walking to something else: off it first (A at an open edge),
        then the walk. False if not on it."""
        pz = self.push_puzzle()
        if not pz:
            return False
        pos = self.push_obj_pos()
        p, _h = self.where_me()
        if not pos or not p or p[1] - pos[1] < 70.0 or math.hypot(p[0] - pos[0], p[2] - pos[2]) > 110.0:
            return False
        # (the edges to try, nearest the way to the thing first; never towards the wall the
        # table was first pushed to - the fireplace)
        to = math.atan2(t.pos[0] - p[0], t.pos[2] - p[2])
        wall = pz["stages"][0]["heading"]
        ways = [w for w in (0.0, math.pi / 2, math.pi, -math.pi / 2)
                if abs((w - wall + math.pi) % (2 * math.pi) - math.pi) > 0.5]
        ways.sort(key=lambda w: abs((w - to + math.pi) % (2 * math.pi) - math.pi))
        now = time.monotonic()
        self.push_down = {"ways": ways, "k": 0, "phase": "turn", "y0": p[1], "next": now, "release": 0.0,
                          "until": now + 30.0, "guide": guide, "name": t.name}
        self.stop_walk("Getting off the table.")
        return True

    def poll_push_down(self):
        pd = getattr(self, "push_down", None)
        if not pd:
            return
        now = time.monotonic()
        if pd["release"] and now >= pd["release"]:
            pd["release"] = 0.0
            self.walker.hold(set())
        if pd["release"] or now < pd["next"]:
            return
        if now > pd["until"] or pd["k"] >= len(pd["ways"]):
            self.push_down = None
            self.walker.hold(set())
            self.say("Could not get off the table.", interrupt=True)
            return
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        h = self.game.heading(obj) if obj else None
        if not p or h is None:
            return
        if pd["y0"] - p[1] > 60.0:
            # (down: once landed, on with the walk)
            key = round(p[1] / 5.0)
            if pd.get("stable") != key:
                pd["stable"] = key
                pd["next"] = now + 0.8
                return
            self.push_down = None
            self.walker.hold(set())
            cur = self.current_target()
            if cur is not None and cur.name == pd["name"]:
                self.start_walk(guide=pd["guide"])
            return
        fa = pd["ways"][pd["k"]]
        if pd["phase"] == "turn":
            d = (fa - h + math.pi) % (2 * math.pi) - math.pi
            last = pd.get("last_h", h)
            spin = abs((h - last + math.pi) % (2 * math.pi) - math.pi)
            pd["last_h"] = h
            if abs(d) < 0.08:
                self.walker.hold(set())
                if spin < 0.01:
                    pd["phase"] = "step"
                return
            self.walker.hold({"left" if d > 0 else "right"})
            if abs(d) < 0.4:
                pd["release"] = now + 0.04
            return
        if pd["phase"] == "step":
            pd["phase"] = "a"
            pd["release"] = now + 0.4          # (to the edge)
            pd["next"] = pd["release"] + 0.2
            self.walker.hold({"up"})
            return
        if pd["phase"] == "a":
            pd["phase"] = "wait"
            pd["release"] = now + 0.25
            pd["next"] = now + 2.8
            self.walker.hold({"a"})
            return
        # (still up there after A: the next edge)
        pd["k"] += 1
        pd["phase"] = "turn"

    def top_box(self, top):
        """The box of the thing on the wall got at from the table (the moose head's, for the
        needle on it too)."""
        if top is None:
            return None
        if getattr(top, "box", None) and top.kind == "check":
            return top.box
        rd = self.room_data
        best = None
        for th in (rd.things if rd else []):
            if th.kind == "check" and getattr(th, "box", None):
                d = self.box_gap(th.box, top.pos)
                if d < 60.0 and (best is None or d < best[0]):
                    best = (d, th.box)
        return best[1] if best else None

    def push_extra(self):
        """The thing as a circle (x, y, z mm, radius) for ways to keep round."""
        if not self.push_puzzle():
            return ()
        pz = self.push_puzzle()
        if pz.get("pieces"):
            # (every piece on the board, to keep well clear of: walking into one pushes it)
            # (the king a little less, so the side to push it from is not walled off)
            king = self.push_obj_pos()
            return tuple((p[0] * MM, p[1] * MM, p[2] * MM, 470.0)
                         for _a, p in self.push_objs() if abs(p[1]) < 40.0)
        pos = self.push_obj_pos()
        if not pos:
            return ()
        # (its size, filled with small circles - one big one would wall off the gap between it
        # and the shelves: the table is about a metre square)
        hx, hz = (v * MM for v in self.push_puzzle().get("size", (50.0, 50.0)))
        x, y, z = pos[0] * MM, pos[1] * MM, pos[2] * MM
        nx, nz = max(1, int(math.ceil(hx / 250.0))), max(1, int(math.ceil(hz / 250.0)))
        return tuple((x + hx * i / nx, y, z + hz * k / nz, 300.0)
                     for i in range(-nx, nx + 1) for k in range(-nz, nz + 1))

    def push_arrived(self):
        """Turned to face the way to push (called from the end of the turn after auto walk):
        climb over first if need be, else say to push. True if it took over."""
        pp = getattr(self, "push_plan", None)
        if not pp:
            return False
        t = getattr(self, "walk_target", None)
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        if t is not None and p and math.hypot(t.pos[0] - p[0], t.pos[2] - p[2]) > 30.0:
            # (the walk stopped short - against the table itself, coming from the wrong side)
            self.push_plan = None
            pz = self.push_puzzle()
            self.stop_walk("Could not get round to the side to push the %s from. Try again from "
                           "nearer it." % (pz["name"].lower() if pz else "thing"))
            return True
        if pp["climb"]:
            now = time.monotonic()
            obj = self.me()
            p = self.game.local_pos(obj) if obj else None
            self.push_climb = {"step": 0, "y0": p[1] if p else 0.0, "next": now + 0.2, "release": 0.0,
                               "since": now, "presses": 0, "until": now + 35.0}
            self.stop_walk("Climbing onto the table." if pp.get("top") else "Climbing over the table.")
            return True
        self.push_plan = None
        pz = self.push_puzzle()
        s = pz["stages"][pp["stage"]]
        if pp.get("words"):
            self.stop_walk("Here. " + pp["words"])
            return True
        self.stop_walk("Here. " + pz["push_words"] % s["way"])
        return True

    def poll_push_climb(self):
        """Up onto the table (A), across it to above the corner, turn to the corner, a step to the
        edge and down into it (A), then turn back to the table."""
        pc = getattr(self, "push_climb", None)
        if not pc:
            return
        now = time.monotonic()
        if pc["release"] and now >= pc["release"]:
            pc["release"] = 0.0
            self.walker.hold(set())
        if now > pc["until"]:
            self.push_climb = None
            self.walker.hold(set())
            self.say("Could not climb over the table here.", interrupt=True)
            return
        if pc["release"]:
            return
        obj = self.me()
        if not obj:
            return
        p = self.game.local_pos(obj)
        h = self.game.heading(obj)
        if not p or h is None:
            return
        pz = self.push_puzzle()
        pp = self.push_plan or {}
        stage = pz["stages"][pp.get("stage", 1)]
        step = pc["step"]

        def turn(fa, tol=0.12):
            # (she keeps turning a little after the stick is let go: short taps near the end, and
            # done only once she has stopped turning)
            d = (fa - h + math.pi) % (2 * math.pi) - math.pi
            last = pc.get("last_h", h)
            spin = abs((h - last + math.pi) % (2 * math.pi) - math.pi)
            pc["last_h"] = h
            if abs(d) < tol:
                self.walker.hold(set())
                return spin < 0.01
            self.walker.hold({"left" if d > 0 else "right"})
            if abs(d) < 0.4:
                pc["release"] = now + 0.04
            return False
        if step in (0, 3):
            # (press A, then wait for the climb to finish: up by a metre, or back down)
            want_up = step == 0
            moved = (p[1] - pc["y0"] > 60.0) if want_up else (pc["y0"] - p[1] > 60.0)
            if moved:
                key = round(p[1] / 5.0)
                if pc.get("stable") != key:
                    pc["stable"] = key
                    pc["stable_at"] = now
                    return
                if now - pc["stable_at"] < 0.8:
                    return
                pc["step"] += 1
                if step == 0 and pp.get("top") is not None:
                    pc["step"] = 5          # (up on it, to get at the thing: face it)
                pc["y0"] = p[1]
                pc["presses"] = 0
                pc["stable"] = None
                return
            if now < pc["next"]:
                return
            if pc["presses"] >= 3:
                self.push_climb = None
                self.say("Could not climb %s here." % ("onto the table" if want_up else "down"), interrupt=True)
                return
            if want_up:
                # (in line with its middle and square on to it: A only climbs it from there - a
                # step into it first would start a push instead)
                prev = pp.get("first") or pz["stages"][pp.get("stage", 1) - 1]
                t = self.push_obj_pos()
                if t and pc.get("aligns", 0) < 4:
                    lat = 2 if prev["axis"] == 0 else 0
                    off = t[lat] - p[lat]
                    if abs(off) > 6.0:
                        fa = (0.0 if off > 0 else math.pi) if lat == 2 else (math.pi / 2 if off > 0 else -math.pi / 2)
                        if turn(fa, 0.08):
                            pc["aligns"] = pc.get("aligns", 0) + 1
                            pc["release"] = now + min(0.45, 0.08 + abs(off) / 120.0)
                            self.walker.hold({"up"})
                        return
                if not turn(prev["heading"], 0.04):
                    return
                pc["nudged"] = True
            if not pc.get("nudged"):
                pc["nudged"] = True             # (a step into it, or to the edge, first)
                pc["release"] = now + 0.3
                pc["next"] = pc["release"] + 0.2
                self.walker.hold({"up"})
                return
            pc["nudged"] = False
            pc["presses"] += 1
            pc["next"] = now + 2.5
            pc["release"] = now + 0.25
            self.walker.hold({"a"})
            return
        if step == 1:
            # (across the top to above the corner: the corner is narrower than the table)
            t = self.push_obj_pos()
            if not t:
                return
            prev = pz["stages"][pp.get("stage", 1) - 1]
            gx, gz = t[0] + prev["sign"] * 25.0, t[2]
            dist = math.hypot(gx - p[0], gz - p[2])
            if dist < 12.0 or pc.get("shuffles", 0) >= 6:
                pc["step"] = 2
                return
            if turn(math.atan2(gx - p[0], gz - p[2])):
                pc["shuffles"] = pc.get("shuffles", 0) + 1
                pc["release"] = now + min(0.6, 0.1 + dist / 120.0)
                self.walker.hold({"up"})
            return
        if step == 5:
            top = pp.get("top")
            fa = math.atan2(top.pos[0] - p[0], top.pos[2] - p[2]) if top is not None else stage["heading"]
            if turn(fa, 0.06):
                # (then a step or two along the top until near enough to reach it: the zone A
                # works in is the one on the wall it is part of)
                box = self.top_box(top)
                if box is not None and self.box_gap(box, p) > 45.0 and pc.get("taps", 0) < 5:
                    pc["taps"] = pc.get("taps", 0) + 1
                    pc["release"] = now + 0.15
                    self.walker.hold({"up"})
                    return
                self.push_climb = None
                self.push_plan = None
                self.say("On the table, facing the %s. Press A." % (top.name if top is not None else "wall"),
                         interrupt=True)
            return
        if step == 2:
            if turn(stage["heading"] + math.pi):
                pc["step"] = 3
                pc["next"] = now + 0.3
            return
        if turn(stage["heading"]):
            self.push_climb = None
            self.push_plan = None
            self.say("In the corner. " + pz["push_words"] % stage["way"], interrupt=True)

    # -- words as it moves ----------------------------------------------------------------------
    def poll_push(self):
        self.poll_push_climb()
        self.poll_push_down()
        pz = self.push_puzzle()
        if not pz:
            self.push_seen = None
            return
        now = time.monotonic()
        ps = getattr(self, "push_seen", None)
        if getattr(self, "push_hold", None):
            # (stopped on its square: the stick held back till the player lets go of it, so the
            # push does not go on past it)
            try:
                real = self.link.real()
                still = abs(real["lx"]) < 9000 and abs(real["ly"]) < 9000 and not (real["buttons"] & 0x000F)
            except Exception:
                still = True
            if (still and now >= self.push_hold) or now >= self.push_hold + 8.0:
                self.push_hold = None
                self.link.block_stick = False
                self.link.block &= ~(L.UP | L.DOWN | L.LEFT | L.RIGHT)
        moving = bool(ps and ps.get("moving"))
        if now < getattr(self, "push_next_look", 0.0):
            return
        self.push_next_look = now + (0.02 if moving else 0.15)
        pos = self.push_obj_pos()
        if not pos:
            return
        key = (round(pos[0], 1), round(pos[2], 1))
        st = pz["stages"]
        i, _left = self.push_stage(pos)
        if not ps or ps["scene"] != self.scene:
            self.push_seen = {"scene": self.scene, "key": key, "at": now, "moving": False,
                              "stage": i, "n": None, "pos": pos, "t": now}
            return
        if key == ps["key"]:
            k0 = ps.get("stage")
            # (a push pauses between pushes for most of a second: stopped against something only
            # once it has stayed still longer than that)
            wait = 1.2 if k0 is not None and k0 < len(st) and st[k0].get("until_stops") else 0.35
            if ps["moving"] and now - ps["at"] > wait:
                ps["moving"] = False
                if i is None:
                    if ps.get("off_said") != key:
                        ps["off_said"] = key
                        self.say(pz["off"], interrupt=True)
                else:
                    k = ps.get("stage")
                    if k is not None and k < len(st) and st[k].get("until_stops"):
                        s = st[k]
                        v = pos[0] if s["axis"] == 0 else pos[2]
                        if abs((s["to"] - v) * s["sign"]) <= 15.0:
                            # (stopped against what is in the way: there)
                            ps["stage"] = k + 1
                            self.say(s["done"], interrupt=True)
                            return
                    ps["stage"] = i         # (in step with where it is, after it stops)
            return
        # (moving)
        dt = max(0.01, now - ps["t"])
        speed = math.hypot(pos[0] - ps["pos"][0], pos[2] - ps["pos"][2]) / dt
        starting = not ps["moving"] or now - ps["at"] > 0.3      # (a push starting, after a pause)
        ps.update(key=key, at=now, moving=True, pos=pos, t=now)
        k = ps.get("stage")
        if k is None or k >= len(st):
            return
        s = st[k]
        v = pos[0] if s["axis"] == 0 else pos[2]
        left_cm = (s["to"] - v) * s["sign"]
        if s.get("until_stops"):
            if left_cm <= 15.0:
                return              # (it stops against what is there by itself: Stop once it has)
        elif left_cm <= max(1.0, speed * 0.03):
            # (there - or will be before the next look: hold the stick back now, so it stops on
            # the square, and Stop)
            self.link.block_stick = True
            self.link.block |= L.UP | L.DOWN | L.LEFT | L.RIGHT
            self.push_hold = now + 0.6
            ps["stage"] = k + 1
            ps["n"] = None
            self.say(s["done"], interrupt=True)
            return
        # (as a push starts, what will be left once it is done)
        n = max(0, int(math.ceil((left_cm - pz["step"] - 3.0) / pz["step"])))
        if starting and n != ps.get("n"):
            ps["n"] = n
            self.say("Last push." if n == 0 else "%d more." % n, interrupt=True)

    _push_last_stage = None
