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
import struct

from . import sbc, gmd, names

# kinds of script entry (the first word of each 16-byte entry at the start of the script)
DOOR_KINDS = (0x1F, 0x04)
CHECK_KINDS = (0x02, 0x08, 0x0E)

OP_TYPEWRITER = 0x51      # 00 51 01: the typewriter (save) question
OP_MESSAGE = 0x31          # 00 31 NN 00 00: show message NN of the room's message file
OP_DOORS = (0x38, 0x39)    # 00 38 SS RR ..: go through to stage SS, room RR (39: the same, stairs and others)
OP_JUMPS = (0x04, 0x06, 0x0D)   # 00 04 HH LL and others: jumps to HHLL

ITEM_RECORD = 0x71         # an item lying in the room (loctbl)
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
            if code[i] == 0 and code[i + 1] in OP_DOORS and code[i + 2] < 12 and code[i + 3] < 0x60:
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


class RoomData:
    """Everything known about one room from the files. code is like "tr07"."""

    def __init__(self, game_dir, code, scene_no, scene_of, room_words, table="arrange\\loctbl0100"):
        self.code = code
        self.things = []
        arc = os.path.join(game_dir, "nativePC", "arc", "scene", code + ".arc")
        try:
            d, ents = sbc.read_arc(arc)
        except OSError:
            return
        zones = {}
        key = "scene\\event\\%sb" % code
        if key in ents:
            zones = _zones(sbc.unpack(d, ents[key]))
        script = None
        scode = code
        for k in ents:
            if k.startswith("event\\"):
                scode = k.split("\\")[-1]
                try:
                    script = Script(sbc.unpack(d, ents[k]))
                except Exception:
                    script = None
                break
        texts = gmd.texts(game_dir, "scene_" + scode) or gmd.texts(game_dir, "scene_" + code)
        used = set()
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
                    if to:
                        st, rm = to[0]
                        target = scene_of(st, rm)
                        name = "Door to " + room_words(target) if target is not None else "Door"
                        self.things.append(Thing("door", name, centre, (lo, hi), target=target, zone=index))
                    else:
                        msgs = [names.clean(texts[m]) for m in script.messages(body) if m < len(texts)]
                        self.things.append(Thing("door", "Door" + (", locked" if any("lock" in m.lower() for m in msgs) else ""),
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
                    if not words:
                        continue        # (no message: a story event starts there, not a thing to check)
                    name = names.best_name(words) or "Something to check"
                    # a box as wide as the room (a whole-room message) is not a place to walk to
                    if len(parts) > 1 or (hi[0] - lo[0]) * (hi[2] - lo[2]) > 600 * 600:
                        continue
                    self.things.append(Thing("check", name, centre, (lo, hi), zone=index, says=words,
                                             outline=_footprint(zones[index])))
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
        if rec[0] != ITEM_RECORD or 0xA0 <= rec[2] <= 0xAF and rec[3] == 0:
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
