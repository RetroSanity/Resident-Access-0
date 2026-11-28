"""The room list (the e list), auto walk and the 3D beacon, as in Resident Access HD.

Hold the left bumper (not aiming) and:
- D-pad right / left: the next / previous list (Doors, Items, Things to check, Puzzles, Enemies).
- D-pad down / up: the next / previous thing in it, nearest first.
- Left stick in: auto walk there. Right stick in: the 3D beacon to it (again to stop).
- B: stop walking. Y: the way to it again. X: health. A: the room and what is in it.
- Right bumper: the enemies in the room.
"""
import logging
import copy
import math
import os
import threading
import time

from . import link as L
from . import room as R
from .game import STAGE_CODES, STAGE_FIRST, enemy_name, scene_code, scene_words
from .mem import NotReady
from .nav import NavMesh
from . import nav as _nav
from . import sbc
from .walker import Walker, angle_diff
from .push import PushMixin

MM = 10.0           # the walker and the way finder count millimetres; the game centimetres

GROUPS = [("Doors", "door"), ("Items", "item"), ("Things to check", "check"), ("Puzzles", "puzzle"),
          ("Enemies", "enemy"),
          ("Partner", "partner")]
GUN_ITEMS = {3, 4, 5, 6, 7, 8, 9, 10, 11, 17, 19, 22, 23}
LIST_BUTTONS = L.LB | L.UP | L.DOWN | L.LEFT | L.RIGHT | L.A | L.B | L.X | L.Y | L.L3 | L.R3 | L.RB


def metres(cm):
    m = cm / 100.0
    if m < 1:
        return "less than a metre"
    if m < 1.5:
        return "1 metre"
    return "%d metres" % round(m)


# Rooms where the ground under a staircase is no way through (the walk went in under the steps and
# got stuck): the Cavernous Area, to the Training Room door; the Training Room, whose stairs up to
# the control room are flat steps (a way started on them went down under them first).
# The Training Facility Entrance: a slope under the floor by the railing (the way went down it, in
# under the railing, and Billy walked into the railing) - and the lift was reached from behind.
# Rooms whose second collision file has walls the first lacks, that stop you: the Outside Area's
# fence across the path north of the gate (auto walk walked into it).
A_WALL_ROOMS = {"ch00", "ch10"}
LOW_HEAD_ROOMS = {"dm09": "sloped", "dm0a": "any", "sh09": "any"}


class RoomList(PushMixin):
    """Mixed into the App: needs self.game, self.link, self.say, self.scene."""

    def init_list(self):
        self.group = 0
        self.picked = {}            # group -> the name and place last picked in it
        self.lb_mode = False
        self.pad_held = 0
        self.start_latch()
        self.room_data = None
        self.room_for = None
        self.mesh = None
        self.mesh_main = None
        self.walker = Walker(self.link)
        self.target = None
        self.taken = set()          # (scene, item, x, z) of items picked up
        self.inv_was = None
        self.space = None
        self.guide_on = False
        self.last_guide_say = 0.0
        self._items_logged = None

    # -- the room's data ------------------------------------------------------------
    def game_dir(self):
        if getattr(self, "_game_dir", None) is None:
            try:
                exe = self.mem.exe_path()
                self._game_dir = os.path.dirname(exe) if exe else None
            except Exception:
                self._game_dir = None
        return self._game_dir

    def scene_fits(self, no):
        """True if who you play is inside room `no` (its walk map, a little either side), False if
        well outside it: the game loads another room for a moment during some events (pulling
        the hookshot release loads Cabin 201) without you going there. None if unknown."""
        cache = getattr(self, "_extents", None)
        if cache is None:
            cache = self._extents = {}
        if no not in cache:
            cache[no] = None
            gd = self.game_dir()
            try:
                code, n = scene_code(no)
                name = "%s%02x" % (code, n)
                d, ents = sbc.read_arc(os.path.join(gd, "nativePC", "arc", "scene", name + ".arc"))
                trans = R.room_trans(d, ents, name)
                key = "scene\\scr\\%se" % name
                if trans and key in ents:
                    tris = sbc.triangles(sbc.unpack(d, ents[key]))
                    xs = [v[0] for t in tris for v in t]
                    zs = [v[2] for t in tris for v in t]
                    if xs:
                        cache[no] = (trans, min(xs), max(xs), min(zs), max(zs))
            except Exception:
                logging.exception("room extent %s", no)
        ext = cache[no]
        if not ext:
            return None
        obj = self.me()
        raw = self.game.pos(obj) if obj else None
        if not raw:
            return None
        (tx, _ty, tz), x0, x1, z0, z1 = ext
        x, z = raw[0] - tx, raw[2] - tz
        return x0 - 200 <= x <= x1 + 200 and z0 - 200 <= z <= z1 + 200

    def load_room(self):
        if self.scene is None or self.room_for == self.scene:
            return self.room_data
        self.room_for = self.scene
        self.room_data = None
        self.mesh = None
        self.mesh_main = None
        gd = self.game_dir()
        if not gd:
            return None
        code, n = scene_code(self.scene)
        name = "%s%02x" % (code, n)
        _nav.HEADROOM_ON = LOW_HEAD_ROOMS.get(name, False)

        def scene_of(stage, room):
            if stage >= len(STAGE_FIRST):
                return None
            return STAGE_FIRST[stage] + room
        try:
            self.room_data = R.RoomData(gd, name, self.scene, scene_of, scene_words)
            logging.info("room %s: %s", name, self.room_data.things)
        except Exception:
            logging.exception("room data %s", name)
        try:
            d, ents = sbc.read_arc(os.path.join(gd, "nativePC", "arc", "scene", name + ".arc"))
            key = "scene\\scr\\%se" % name
            self._tris_a = []
            self._alt_meshes = {}
            if key in ents:
                tris = sbc.triangles(sbc.unpack(d, ents[key]))
                self._tris_e = [tuple(tuple(c * MM for c in p) for p in t) for t in tris]
                key_a = "scene\\scr\\%sa" % name
                if key_a in ents:
                    self._tris_a = [tuple(tuple(c * MM for c in p) for p in t)
                                    for t in sbc.triangles(sbc.unpack(d, ents[key_a]))]
                walls_a = self._walls_of(self._tris_a) if name in A_WALL_ROOMS else []
                self.mesh = self.mesh_main = NavMesh(self._tris_e + walls_a, ceilings=True)
                self.apply_spots(self.mesh_main)
                self.fix_door_heights()
                if self.fix_step_heights():
                    self.apply_spots(self.mesh_main)
                self.prepare_meshes()
        except Exception:
            logging.exception("walk map %s", name)
        return self.room_data

    # -- who you are and where -------------------------------------------------------
    def me(self):
        """The character you control."""
        c = self.game.controlled()
        return c[1] if c else None

    def poll_partner(self):
        """Say who you are playing as when you switch between Rebecca and Billy."""
        chars = self.game.characters()
        c = self.game.controlled() if chars else None
        name = c[0] if c else None
        was = getattr(self, "playing_as", None)
        self.playing_as = name
        if len(chars) == 2 and getattr(self, "pair_logged", None) != self.scene:
            self.pair_logged = self.scene
            try:
                logging.info("pair: %s", [(n, "%08X" % self.mem.u32(self.mem.u32(o + 0x67B8))) for n, o in chars])
            except Exception:
                pass
        self.note_rooms(chars, name)
        if name and was and name != was and len(chars) == 2:
            if self.game.item_view() is not None:
                return      # (Y on the item screen: "Rebecca's items" is said with the item)
            self.say("Now %s." % name, interrupt=True)

    def note_rooms(self, chars, name):
        """Which room each of them was last in: whoever you play as is in this one, and so is
        your partner while they are with you; one left behind stays where you last saw them."""
        now = time.monotonic()
        if self.scene is None or now < getattr(self, "rooms_next", 0.0):
            return
        self.rooms_next = now + 1.0
        seen = getattr(self, "char_scene", None)
        if seen is None:
            seen = self.char_scene = {}
        # (where the story moves someone you are not playing as: after the power panel, Rebecca
        # falls through the Torture Room's floor into the chasm under the basement's Storage Room)
        moved = getattr(self, "story_moves", None)
        if moved and name and moved[0] != name:
            seen[moved[0]] = moved[1]
            self.story_moves = None
        if self.scene == 0x62 and name == "Billy" and seen.get("Rebecca") in (None, 0x58, 0x62):
            seen["Rebecca"] = 0x5B      # (the Torture Room with its floor gone: she is below)
        for n, obj in chars:
            if n == name:
                seen[n] = self.scene
                continue
            pos = self.game.local_pos(obj)
            if pos and getattr(self, "mesh_main", None) and self.on_floor(pos, 100.0):
                seen[n] = self.scene

    def partner_way(self, name):
        """(the room your partner is in, the door here to take towards it) - either can be None."""
        sc = getattr(self, "char_scene", {}).get(name)
        if sc is None or sc == self.scene:
            return None, None
        from .rooms import place
        from .game import STAGE_NAMES
        there = scene_words(sc)
        if scene_code(sc)[0] != scene_code(self.scene)[0]:
            there += ", %s" % STAGE_NAMES.get(scene_code(sc)[0], "")      # (the Storage Room in the basement)
        g = getattr(self, "room_graph", None)
        if g is None:
            gd = self.game_dir()
            if not gd:
                return there, None
            from .rooms import RoomGraph
            g = self.room_graph = RoomGraph(gd)
        path = g.way(place(self.scene), place(sc))
        if not path or len(path) < 2:
            return there, None
        rd = self.load_room()
        for t in (rd.things if rd else []):
            if t.kind == "door" and getattr(t, "target", None) is not None and \
                    place(t.target) == path[1] and not t.name.startswith(("Air vent", "Hole")):
                return there, t
        return there, None

    def where_me(self):
        obj = self.me()
        if not obj:
            return None, None
        return self.game.local_pos(obj), self.game.heading(obj)

    def way_words(self, x, z, p=None, h=None):
        if p is None:
            p, h = self.where_me()
        if not p or h is None:
            return ""
        deg = math.degrees(angle_diff(math.atan2(x - p[0], z - p[2]), h))
        if abs(deg) < 25:
            return "ahead"
        if abs(deg) > 155:
            return "behind"
        if deg < 0:
            return "to the right" if deg < -65 else "ahead and to the right"
        return "to the left" if deg > 65 else "ahead and to the left"

    # -- the lists ----------------------------------------------------------------------
    def listing(self, kind):
        p, _h = self.where_me()
        out = []
        if kind == "enemy":
            for obj, ek, pos, hp in self.game.enemies():
                if hp <= 0:
                    continue
                high = 0.0
                if getattr(self, "mesh_main", None) and not self.on_floor(pos, 100.0):
                    # (in the next car or room, not this one - unless it is over a floor of this
                    # one: a spider up on the wall or the ceiling)
                    under = [h / MM for h in self.floor_heights(pos[0] * MM, pos[2] * MM) if h / MM < pos[1]]
                    if not under or pos[1] - max(under) > 450.0:
                        continue
                    high = pos[1] - max(under)
                out.append(R.Thing("enemy", enemy_name(ek), pos, obj=obj, hp=hp, top=self.enemy_top(obj, hp),
                                   high=high))
        elif kind == "partner":
            mine = self.me()
            for name, obj in self.game.characters():
                if obj == mine:
                    continue
                pos = self.game.local_pos(obj)
                h = self.game.health(obj)
                if pos:
                    away = bool(getattr(self, "mesh_main", None)) and not self.on_floor(pos, 100.0)
                    out.append(R.Thing("partner", name, pos, obj=obj, away=away,
                                       hp=round(100.0 * h[0] / h[1]) if h else None))
        elif kind == "item" and self.live_items() is not None:
            out = list(self.live_items())
        else:
            rd = self.load_room()
            if rd:
                for t in rd.things:
                    tk = "puzzle" if t.kind == "check" and getattr(t, "puzzle", False) else t.kind
                    if tk != kind:
                        continue
                    if kind == "item" and self.is_taken(t):
                        continue
                    out.append(t)
                if kind in ("puzzle", "check"):
                    # (the place a key item was got from, named after it, while someone has it:
                    # where it goes back - the Piano Bar's Battery, behind the bar)
                    got = self.carried_key_names()
                    for k, t in enumerate(out):
                        if t.name in got:
                            t2 = copy.copy(t)
                            t2.name = "Where the %s goes" % t.name
                            out[k] = t2
                if kind == "puzzle":
                    pt = self.push_thing()
                    if pt is not None:
                        out.append(pt)
        if kind == "item":
            # (an item got by checking the thing it is on - the hookshot fixed to the wall - is
            # listed here, at that thing)
            out = [self.item_in_check(t) or t for t in out]
            # (the same item twice at one place - the Living Room's Crank Handle has a record for
            # each difficulty - is one item)
            seen, uniq = set(), []
            for t in out:
                key = (t.name, round(t.pos[0] / 25.0), round(t.pos[2] / 25.0))
                if key not in seen:
                    seen.add(key)
                    uniq.append(t)
            out = uniq
        elif kind == "check":
            items = self.live_items()
            if items is None:
                rd0 = self.load_room()
                items = [t for t in (rd0.things if rd0 else []) if t.kind == "item" and not self.is_taken(t)]
            owners = {id(c) for c in (self.check_of(i) for i in items) if c is not None}
            out = [t for t in out if id(t) not in owners]
        if p:
            out.sort(key=lambda t: (abs(t.pos[1] - p[1]) > 150, math.hypot(t.pos[0] - p[0], t.pos[2] - p[2])))
        return out

    def carried_key_names(self):
        names = set()
        try:
            gd = self.game_dir()
            from . import gmd
            labels = gmd.archive(gd).get("message_item_name_eng", ({}, []))[0] if gd else {}
            for who, _ in self.game.characters():
                for i, _n in self.game.inventory(who):
                    if i > 0 and i in self.game.key_items:
                        nm = R.item_name(labels, i)
                        if nm:
                            names.add(nm)
        except Exception:
            logging.exception("carried key items")
        return names

    def check_of(self, item):
        """The thing to check next to the item that names it (the hookshot fixed to the wall: it
        is got by checking the wall), or None."""
        rd = self.load_room()
        if not rd:
            return None
        name = item.name.lower()
        for t in rd.things:
            if t.kind != "check":
                continue
            if math.hypot(t.pos[0] - item.pos[0], t.pos[2] - item.pos[2]) > 150:
                continue
            if any(name in w.lower() for w in (getattr(t, "says", None) or [])):
                return t
        return None

    def item_in_check(self, item):
        """The item as got from that thing (at its place, walked to like it), or None."""
        c = self.check_of(item)
        if c is None:
            return None
        extra = {k: v for k, v in item.__dict__.items() if k not in ("kind", "name", "pos", "box")}
        extra.update(zone=getattr(c, "zone", None), says=getattr(c, "says", None),
                     outline=getattr(c, "outline", None), via_check=True)
        return R.Thing("item", item.name, c.pos, c.box, **extra)

    def base_scene(self):
        """The scene number of the room this one is a later version of (its script's), or None."""
        rd = self.load_room()
        code = getattr(rd, "base_code", None) if rd else None
        if not code or len(code) != 4 or code[:2] not in STAGE_CODES:
            return None
        try:
            st = STAGE_CODES.index(code[:2])
            return STAGE_FIRST[st] + int(code[2:], 16)
        except (ValueError, IndexError):
            return None

    def live_items(self):
        """The items lying in the room, from the game's own record of them (they leave it as you
        pick them up; ones you drop are added). Read again at most every half second."""
        now = time.monotonic()
        cache = getattr(self, "_items_cache", None)
        if cache and cache[0] == self.scene and now - cache[1] < 0.5:
            return cache[2]
        recs = None
        if self.scene is not None:
            recs = self.game.placed_items(self.scene)
            base = self.base_scene()
            if recs is not None and base is not None and base != self.scene:
                # (the same room later in the story: the game keeps its items under the first)
                recs = recs + (self.game.placed_items(base) or [])
        out = None
        if recs is not None:
            gd = self.game_dir()
            from . import gmd
            labels = gmd.archive(gd).get("message_item_name_eng", ({}, []))[0] if gd else {}
            out = []
            # (an item with a record for each difficulty - the Piano Bar's Battery - has two at
            # one place; picked up, one goes and the other stays: once fewer are left than there
            # were, the rest are not really there)
            groups = {}
            for k, item, amount, put, pos in recs:
                if amount and pos != (0.0, 0.0, 0.0):
                    key = (self.scene, item, round(pos[0] / 25.0), round(pos[2] / 25.0))
                    groups[key] = groups.get(key, 0) + 1
            # (a key item has one record for it as a key item and another for it as an item: a
            # key item someone carries is not lying here too - the Piano Bar's Battery, got by
            # Rebecca, kept both records)
            carried = set()
            try:
                for who, _ in self.game.characters():
                    carried.update(i for i, _n in self.game.inventory(who) if i > 0)
            except Exception:
                logging.exception("carried")
            twins = self.__dict__.setdefault("_twins", {})
            for key, n in groups.items():
                if n > 1:
                    twins[key] = max(n, twins.get(key, 0))
            for k, item, amount, put, pos in recs:
                if amount == 0 or pos == (0.0, 0.0, 0.0) or pos[1] < -10000.0:
                    continue        # (put away far below the floor: the Turntable Yard's spare Dial)
                key = (self.scene, item, round(pos[0] / 25.0), round(pos[2] / 25.0))
                if key in twins and groups.get(key, 0) < twins[key]:
                    continue
                if item in carried and item in self.game.key_items:
                    continue
                name = R.item_name(labels, item) or "Item %d" % item
                out.append(R.Thing("item", name, pos, item=item, amount=amount, index=k))
            if cache is None or cache[0] != self.scene or self._items_logged != (self.scene, len(recs)):
                self._items_logged = (self.scene, len(recs))
                logging.info("items here: %s", [(k, item, amount, put) for k, item, amount, put, _ in recs])
        self._items_cache = (self.scene, now, out)
        return out

    # -- enemies' health, and the aim beep ---------------------------------------------------
    GUN_REACH = 14000.0         # millimetres
    HIT_WIDTH = 300.0           # how far off an enemy's middle a shot may pass and still hit
    CONE = math.radians(7.0)

    def poll_enemies(self):
        now = time.monotonic()
        if now - getattr(self, "_enemy_t", 0.0) < 0.15:
            return
        self._enemy_t = now
        es = self.game.enemies()
        was = getattr(self, "enemy_hp", {})
        now_hp = {}
        died, hits = [], []
        for obj, ek, pos, hp in es:
            now_hp[obj] = hp
            if hp > 0:
                top = self.enemy_top(obj, hp)
            old = was.get(obj)
            if old is not None and old > 0 and hp < old and getattr(self, "_hp_scene", None) == self.scene:
                # hit: how much it has left (or that it is dead)
                if hp <= 0:
                    died.append(enemy_name(ek))
                else:
                    hits.append(max(1, round(100.0 * hp / top)))
        # (one line for all that died at once: the bat's little bats die with it)
        if len(died) == 1:
            self.say("%s dead." % died[0], interrupt=True)
        elif died:
            many = {"Zombie": "zombies", "Leech": "leeches", "Spider": "spiders", "Cerberus": "Cerberuses"}
            what = many.get(died[0], "enemies") if len(set(died)) == 1 else "enemies"
            self.say("%d %s dead." % (len(died), what), interrupt=True)
        elif hits:
            self.say("%d percent" % min(hits), interrupt=True)
        self.enemy_hp = now_hp
        self._hp_scene = self.scene
        beeps = getattr(self, "beeps", None)
        if not beeps:
            return
        if getattr(self, "sounds_on", True) and self.shot_would_hit(es):
            beeps.threat(0.0, 1.0, 0.0, True, True)
        else:
            beeps.calm()

    def aiming(self):
        """The weapon is up: the aim button (right bumper) held in play."""
        real = self.link.real()
        return bool(real.get("buttons", 0) & L.RB) and not self.lb_mode

    FLYING = {0x35, 0x3C}       # (the bats: up in the air, shot by aiming up)

    def shot_would_hit(self, es):
        obj = self.me()
        aim = self.aiming()
        if aim and not getattr(self, "_aim_was", False):
            self._aim_log = True        # (each time the weapon goes up: why it would or would not hit, in the log)
        self._aim_was = aim
        if not obj or not aim:
            return False
        p = self.game.local_pos(obj)
        h = self.game.heading(obj)
        log = getattr(self, "_aim_log", False)
        self._aim_log = False
        if not p or h is None:
            if log:
                logging.info("aim: no place or heading (%s, %s)", p, h)
            return False
        x, y, z = p[0] * MM, p[1] * MM, p[2] * MM
        why = []
        hit = False
        for eobj, ek, pos, hp in es:
            if hp <= 0:
                continue
            ex, ey, ez = pos[0] * MM, pos[1] * MM, pos[2] * MM
            flying = ek in self.FLYING
            dist = math.hypot(ex - x, ez - z)
            d = angle_diff(math.atan2(ex - x, ez - z), h)
            clear = self.mesh is None or self.mesh.clear_shot(x, y, z, ex, ez)
            if log:
                why.append("%s hp %d dist %.0f up %.0f angle %.0f clear %s" % (
                    enemy_name(ek), hp, dist, ey - y, math.degrees(d), clear))
            if abs(ey - y) > (6000 if flying else 1500):
                continue
            if dist > self.GUN_REACH:
                continue
            if flying and dist < 1500 and ey - y > 800:
                hit = True              # (right overhead: aiming up gets it)
                break
            if abs(d) > math.pi / 2:
                continue
            width = self.HIT_WIDTH * (1.8 if flying else 1.0)
            if dist * math.sin(abs(d)) > width and abs(d) > self.CONE:
                continue
            if not clear:
                continue
            hit = True
            break
        if log:
            logging.info("aim: hit=%s; %s", hit, "; ".join(why) or "no living enemies")
        return hit

    def enemy_top(self, obj, hp):
        """An enemy's full health: the most it has been seen with (they come into a room unhurt)."""
        tops = getattr(self, "_tops", None)
        if tops is None or getattr(self, "_tops_scene", None) != self.scene:
            tops = self._tops = {}
            self._tops_scene = self.scene
        tops[obj] = max(tops.get(obj, 0), hp, 1)
        return tops[obj]

    def is_taken(self, t):
        return (self.scene, t.item, round(t.pos[0]), round(t.pos[2])) in self.taken

    def thing_words(self, t, p=None, h=None):
        if p is None:
            p, h = self.where_me()
        name = t.name
        amount = getattr(t, "amount", 0) if t.kind == "item" else 0
        if amount and getattr(t, "item", None) in GUN_ITEMS:
            name = "%s, %d %s" % (name, amount, "round" if amount == 1 else "rounds")
        elif amount < 0 or amount == 0xFFFF:
            name = "%s, unlimited" % name
        elif amount > 1:
            name = "%s, %d" % (name, amount)
        if t.kind == "enemy" and getattr(t, "hp", None) is not None:
            top = self.enemy_top(t.obj, t.hp)
            name = "%s, %d percent" % (name, max(1, round(100.0 * t.hp / top)))
            if getattr(t, "high", 0.0) > 120.0:
                name += ", up high"     # (on the wall or the ceiling)
        if t.kind == "partner" and getattr(t, "hp", None) is not None:
            name = "%s, health %d percent" % (name, t.hp)
        if getattr(t, "push", False):
            name = "%s, %s" % (name, self.push_words(t))
        if not p:
            return name
        if getattr(t, "away", False):
            if t.kind == "partner":
                there, door = self.partner_way(t.name)
                if there:
                    return "%s, in %s%s" % (name, there, (", through %s" % door.name) if door else "")
            return name + ", in another room"
        d = math.hypot(t.pos[0] - p[0], t.pos[2] - p[2])
        words = "%s, %s, %s" % (name, metres(d), self.way_words(t.pos[0], t.pos[2], p, h))
        if getattr(t, "high", 0.0) > 120.0:
            pass                # (up on the wall or the ceiling, said already: not another floor)
        elif t.pos[1] - p[1] > 150:
            words += ", upstairs"
        elif p[1] - t.pos[1] > 150:
            words += ", downstairs"
        return words

    def group_words(self):
        title, kind = GROUPS[self.group]
        n = len(self.listing(kind))
        return "%s, %d" % (title, n)

    def step_thing(self, delta):
        title, kind = GROUPS[self.group]
        things = self.listing(kind)
        if not things:
            self.target = None
            return "No %s." % title.lower()
        cur = self.picked.get(kind)
        idx = -1
        if cur is not None:
            for i, t in enumerate(things):
                if t.name == cur[0] and (math.hypot(t.pos[0] - cur[1], t.pos[2] - cur[2]) < 5 or
                                         getattr(t, "push", False)):
                    idx = i
                    break
        idx = (idx + delta) % len(things) if idx >= 0 else (0 if delta > 0 else len(things) - 1)
        t = things[idx]
        self.picked[kind] = (t.name, t.pos[0], t.pos[2])
        self.target = t
        return self.thing_words(t)

    def current_target(self):
        """The thing picked, looked up again (enemies move)."""
        t = self.target
        if t is None:
            return None
        if t.kind == "enemy":
            for obj, ek, pos, hp in self.game.enemies():
                if obj == t.obj:
                    t.pos, t.hp = pos, hp
                    return t if hp > 0 else None
            return None
        if t.kind == "partner":
            pos = self.game.local_pos(t.obj)
            if not pos:
                return None
            t.pos = pos
        if getattr(t, "push", False):
            pos = self.push_obj_pos()
            if not pos:
                return None
            t.pos = pos
        return t

    def room_summary(self):
        if self.scene is None:
            return "Not in the game."
        parts = [scene_words(self.scene) + "."]
        for title, kind in GROUPS:
            if kind == "partner":
                continue
            n = len(self.listing(kind))
            if n:
                one = {"Doors": "door", "Items": "item", "Things to check": "thing to check", "Puzzles": "puzzle",
                       "Enemies": "enemy"}.get(title, title.lower())
                parts.append("%d %s." % (n, title.lower() if n != 1 else one))
        doors = self.listing("door")
        if doors:
            parts.append(" ".join(d.name + "." for d in doors))
        for t in self.listing("partner"):
            parts.append(self.thing_words(t) + ".")
        return " ".join(parts)

    def enemies_words(self):
        es = self.listing("enemy")
        if not es:
            return "No enemies."
        return ". ".join(self.thing_words(e) for e in es) + "."

    # -- the item screen ---------------------------------------------------------------
    def poll_item_screen(self):
        """The game says nothing on an empty slot: say "Empty" (on your partner's slots too, when
        changing an item with them), and say the number when choosing how many to pass over."""
        v = self.game.item_view()
        if v and v[0] == "number":
            self.poll_pass_number(v)
            self.slot_was = None
            return
        self.number_was = None
        shown = self.items_shown() if v and hasattr(self, "items_shown") else None
        key = shown
        was = getattr(self, "slot_was", None)
        self.slot_was = key
        if not shown or key == was:
            return
        who, cur = shown
        inv = self.game.inventory(who)
        names = self.item_names() if hasattr(self, "item_names") else []
        item = inv[cur][0] if cur < len(inv) else 0
        name = names[item] if 0 < item < len(names) else None
        name_was = getattr(self, "slot_name_was", None)
        self.slot_name_was = name
        if cur < len(inv) and item <= 0:
            self.say(self.whose_items("Empty") if hasattr(self, "whose_items") else "Empty", interrupt=True)
        elif name and name == name_was and was is not None:
            # (the same item as the slot before: the game's text does not change, so it is not
            # laid out again; say it here)
            self.say(self.whose_items(self.with_equip(self.with_amount(name), name)), interrupt=True)

    # (the sides of an examined item, by its own axis towards you: -z is the side shown first.
    # For a book - from turning the Book of Good round - -z is the front cover, +x the spine,
    # -x the page edges, +y the top)
    SIDES = {"-z": "front", "+z": "back", "+x": "left side", "-x": "right side", "+y": "top", "-y": "bottom"}
    BOOK_SIDES = {"-z": "front cover", "+z": "back cover", "+x": "spine", "-x": "page edges",
                  "+y": "top edge", "-y": "bottom edge"}

    def poll_examine(self):
        """Turning an item round when examining it: say which side faces you as it changes."""
        ax = self.game.examined_axes()
        if ax is None:
            self.exam_side = None
            return
        i = max(range(3), key=lambda k: abs(ax[k]))
        key = ("+" if ax[i] > 0 else "-") + "xyz"[i]
        st = getattr(self, "exam_side", None)
        now = time.monotonic()
        if st is None:
            self.exam_side = {"said": key, "key": key, "since": now, "ax": ax}
            return
        if key != st["key"]:
            st["key"] = key
            st["since"] = now
        if st["key"] != st["said"] and now - st["since"] > 0.3:
            st["said"] = st["key"]
            sides = self.SIDES
            v = self.game.item_view()
            try:
                c = self.game.controlled()
                inv = self.game.inventory(c[0]) if v and c else []
                names = self.item_names() if hasattr(self, "item_names") else []
                item = inv[v[1]][0] if v and v[1] < len(inv) else 0
                name = names[item] if 0 < item < len(names) else ""
            except Exception:
                name = ""
            if "book" in name.lower():
                sides = self.BOOK_SIDES
            # (how square on it is: "partly" when another side shows nearly as much)
            rest = sorted((abs(a) for k2, a in enumerate(ax) if k2 != i), reverse=True)
            words = sides[st["key"]].capitalize()
            if rest and rest[0] > 0.6:
                words += ", at an angle"
            self.say(words, interrupt=True)

    def poll_pass_number(self, v):
        """How many to pass over (pictures on screen): "0 of 15", then each change. Left passes
        yours to your partner, right takes theirs (15 rounds a press for Handgun Ammo); a press
        that changes nothing - right with none of theirs to take, left with all yours already -
        says so, as the game does not."""
        _k, n, own_slot, partner_slot = v
        was = getattr(self, "number_was", None)
        self.number_was = n
        now = time.monotonic()
        c = self.game.controlled()
        mine_n = theirs_n = 0
        item = 0
        partner = "your partner"
        if c:
            mine = self.game.inventory(c[0])
            others = [w for w, _ in self.game.characters() if w != c[0]]
            theirs = self.game.inventory(others[0]) if others else []
            if others:
                partner = others[0]
            if own_slot < len(mine) and mine[own_slot][0] > 0:
                mine_n, item = mine[own_slot][1], mine[own_slot][0]
            if partner_slot < len(theirs) and theirs[partner_slot][0] > 0:
                theirs_n = theirs[partner_slot][1]
                item = item or theirs[partner_slot][0]
        top = mine_n or theirs_n
        # (a press: the stick or the pad, left or right)
        d = 0
        try:
            real = self.link.real()
            if real["buttons"] & 0x4 or real["lx"] < -16000:
                d = -1
            elif real["buttons"] & 0x8 or real["lx"] > 16000:
                d = 1
        except Exception:
            pass
        d_was = getattr(self, "number_dir_was", 0)
        self.number_dir_was = d
        if d and not d_was:
            self.number_press = (d, n, now)
        if was is None:
            names = self.item_names() if hasattr(self, "item_names") else []
            what = names[item] if 0 < item < len(names) else "it"
            how = []
            if mine_n:
                how.append("left gives your %s to %s" % (what, partner))
            if theirs_n:
                how.append("right takes %s's" % partner)
            words = "%d of %d" % (abs(n), top) if top else "%d" % abs(n)
            how = ", ".join(how) or "left and right change it"
            self.say("%s. %s, then A." % (words, how[0].upper() + how[1:]), interrupt=False)
            return
        if was != n:
            self.number_press = None
            self.say("%d of %d" % (abs(n), top) if top else "%d" % abs(n), interrupt=True)
            return
        pr = getattr(self, "number_press", None)
        if pr and now - pr[2] > 0.35:
            self.number_press = None
            if pr[1] == n:
                if pr[0] > 0 and n >= 0:
                    self.say("None of %s's to take. Left gives yours." % partner if mine_n else
                             "None to take.", interrupt=True)
                elif pr[0] < 0 and n <= 0 and not mine_n:
                    self.say("None of yours to give. Right takes %s's." % partner, interrupt=True)
                else:
                    self.say("That's all. %d of %d" % (abs(n), top) if top else "That's all.", interrupt=True)

    # The Files page: three tabs (left and right), each a list of files in two columns (the
    # cursor counts down the first column, then the second), then a file read a page at a time
    FILE_TABS = [
        [150, 151, 152, 179, 153, 154, 155, 156, 157],          # the train (the Hookshot manual 4th)
        [158, 159, 160, 161, 162, 163, 164, 166, 167, 168],     # the Training Facility
        [169, 170, 171, 172, 173, 174, 175, 176, 177, 178],     # the Laboratory and on
    ]

    def tab_files(self, tab):
        owned = self.game.files_owned()
        files = self.FILE_TABS[tab] if 0 <= tab < len(self.FILE_TABS) else []
        return [f for f in files if owned is None or f in owned]

    def file_name(self, item):
        gd = self.game_dir()
        from . import gmd
        if not gd:
            return "File"
        labels = gmd.archive(gd).get("message_item_name_eng", ({}, []))[0]
        return R.item_name(labels, item) or "File"

    def poll_files(self):
        """The Files page of the item screen is pictures: say the tab, the file the cursor is on,
        and each page of a file as it is turned."""
        fs = self.game.files_screen()
        if fs is None and not getattr(self, "_files_primed", False) and self.scene is not None:
            self._files_primed = self.game.files_owned() is not None   # (found ahead of time)
        state = None
        if fs is not None:
            mode = fs[0]
            tab = fs[1]
            idx = self.game.files_index()
            page = self.game.file_reader() if mode == 10 else None
            if page is not None and page > 1000:
                page = None             # (-1 while it opens)
            state = (mode, tab, idx, page)
        was = getattr(self, "files_was", None)
        self.files_was = state
        if state is None or state == was:
            return
        mode, tab, idx, page = state
        files = self.tab_files(tab)
        if mode == 3 and (was is None or was[0] != 3 or was[1] != tab):
            n = len(files)
            self.say("Files %d, %s." % (tab + 1, "%d file%s" % (n, "" if n == 1 else "s") if n else "empty"),
                     interrupt=True)
        elif mode == 6 and (was is None or was[0] != 6 or was[2] != idx):
            if 0 <= (idx or 0) < len(files):
                self.say("%s, %d of %d" % (self.file_name(files[idx]), idx + 1, len(files)), interrupt=True)
            else:
                self.say("File %d" % ((idx or 0) + 1), interrupt=True)
        elif mode == 10 and page is not None and page >= 0 and (was is None or was[0] != 10 or was[3] != page):
            item = files[idx] if idx is not None and 0 <= idx < len(files) else None
            gd = self.game_dir()
            from . import gmd
            pages = gmd.file_pages(gd, item) if item is not None and gd else []
            if not pages:
                self.say("This file can't be read out.", interrupt=True)
            elif page == 0:
                self.say("%s. %d page%s. Right for the next page." % (pages[0].rstrip(". "), len(pages) - 1,
                                                                    "" if len(pages) == 2 else "s"),
                         interrupt=True)
            elif page < len(pages):
                self.say("%s%s" % (pages[page], " End of file." if page == len(pages) - 1 else ""), interrupt=True)

    # -- picking up items ----------------------------------------------------------------
    def poll_taken(self):
        """An item you carry more of than a moment ago, with one of that kind lying close by: that
        one has been picked up, and leaves the list."""
        inv = {}
        for who, _ in self.game.characters():
            for item, amount in self.game.inventory(who):
                if item > 0:
                    inv[item] = inv.get(item, 0) + max(amount, 1)
        was, self.inv_was = self.inv_was, (self.scene, inv)
        if not was or was[0] != self.scene:
            return
        gained = [i for i, n in inv.items() if n > was[1].get(i, 0)]
        if not gained:
            return
        p, _h = self.where_me()
        rd = self.room_data
        if not p or not rd:
            return
        for t in rd.things:
            if t.kind == "item" and t.item in gained and not self.is_taken(t) and \
                    math.hypot(t.pos[0] - p[0], t.pos[2] - p[2]) < 250:
                self.taken.add((self.scene, t.item, round(t.pos[0]), round(t.pos[2])))
                logging.info("picked up %s", t.name)

    # -- the controller -------------------------------------------------------------------
    def start_latch(self):
        """Watch the buttons on their own, a hundred times a second, and keep every press till the
        loop gets to it: a quick tap while the loop is busy (finding a way round something) still
        stops auto walk or moves in the list."""
        if getattr(self, "_latch_thread", None):
            return
        self._latched = 0
        self._latch_lock = threading.Lock()

        def watch():
            held = 0
            while True:
                try:
                    r = self.link.real()
                    b = r["buttons"] if r.get("connected") else 0
                except Exception:
                    b = 0
                new = b & ~held
                if new:
                    with self._latch_lock:
                        self._latched |= new
                held = b
                time.sleep(0.01)
        self._latch_thread = threading.Thread(target=watch, daemon=True)
        self._latch_thread.start()

    def take_latched(self):
        lock = getattr(self, "_latch_lock", None)
        if lock is None:
            return 0
        with lock:
            b, self._latched = self._latched, 0
        return b

    def poll_controller(self):
        real = self.link.real()
        if not real.get("connected"):
            return
        buttons = real["buttons"]
        # (a press the watcher caught that the last loop already saw - the button was down then -
        # is not a second press: auto walk would start and stop at once)
        pressed = (buttons & ~self.pad_held) | (self.take_latched() & ~self.pad_held)
        self.pad_held = buttons
        gp = getattr(self, "gate_pass", None)
        if gp and gp.get("phase") == "open" and ((pressed & ~L.LB) or abs(real["lx"]) > 12000 or abs(real["ly"]) > 12000):
            self.gate_pass = None       # (you took over at the gate)
            self.walker.hold(set())
        if self.walker.walking and not self.walker.guide:
            # touching the controller stops auto walk (the run button only makes it run)
            moved = abs(real["lx"]) > 12000 or abs(real["ly"]) > 12000
            if (pressed & ~(L.B | L.LB)) or moved:
                if not (buttons & L.LB):
                    self.stop_walk("Stopped.")
            self.walker.run_held = bool(buttons & L.B) and not (buttons & L.LB)
        lb = bool(buttons & L.LB) and real["lt"] < 60
        if lb != self.lb_mode:
            self.lb_mode = lb
            self.link.block = (self.link.block | LIST_BUTTONS) if lb else (self.link.block & ~LIST_BUTTONS)
            self.link.push()
            if lb:
                self.say(self.group_words(), interrupt=True)
        if not lb:
            if pressed & L.R3:
                self.say(self.health_words(), interrupt=True)
            return
        if pressed & L.RIGHT:
            self.group = (self.group + 1) % len(GROUPS)
            self.target = None
            self.say(self.group_words(), interrupt=True)
        elif pressed & L.LEFT:
            self.group = (self.group - 1) % len(GROUPS)
            self.target = None
            self.say(self.group_words(), interrupt=True)
        elif pressed & L.DOWN:
            self.say(self.step_thing(1), interrupt=True)
        elif pressed & L.UP:
            self.say(self.step_thing(-1), interrupt=True)
        elif pressed & L.L3:
            if self.walker.walking and not self.walker.guide:
                self.stop_walk("Stopped.")
            else:
                self.start_walk()
        elif pressed & L.R3:
            if self.guide_on:
                self.stop_walk("Beacon off.")
            else:
                self.start_walk(guide=True)
        elif pressed & L.B:
            if self.walker.walking:
                self.stop_walk("Stopped.")
        elif pressed & L.Y:
            t = self.current_target()
            self.say(self.thing_words(t) if t else "Nothing picked.", interrupt=True)
        elif pressed & L.X:
            self.say(self.health_words(), interrupt=True)
        elif pressed & L.A:
            self.say(self.room_summary(), interrupt=True)
        elif pressed & L.RB:
            self.say(self.enemies_words(), interrupt=True)

    # -- auto walk and the beacon --------------------------------------------------------
    def goal_for(self, t):
        """(where to walk to, what to face) in millimetres."""
        gx, gz = t.pos[0] * MM, t.pos[2] * MM
        return (gx, gz), (gx, gz)

    def floor_heights(self, x, z):
        """Heights (mm) of the ground at (x, z) mm in this room: what faces up in its walk maps
        (the floors, stairs, the tops of things; not ceilings or the undersides of racks)."""
        if not hasattr(self, "_alt_meshes"):
            self._alt_meshes = {}
        if not getattr(self, "mesh_main", None):
            return []
        m = self.cached_mesh("floors")
        if not m:
            return []
        return list(m.heights.get(m.cell(x, z), ()))

    def on_floor(self, pos, tol=40.0):
        """True if a place (room cm) is on the ground somewhere in this room."""
        hs = self.floor_heights(pos[0] * MM, pos[2] * MM)
        return any(abs(h - pos[1] * MM) <= tol * MM for h in hs)

    def poll_offset(self):
        """The game's room offset belongs to the room whoever went in first is in: with Rebecca
        and Billy in different rooms it can be the other room's. Check it against the floor under
        you; when it is wrong, use one seen right for this room before, or fix its height."""
        now = time.monotonic()
        if now < getattr(self, "offset_next", 0.0) and getattr(self, "offset_for", None) == self.scene:
            return
        self.offset_next = now + 0.5
        self.offset_for = self.scene
        g = self.game
        rd = self.load_room() if self.scene is not None else None
        if rd is not None and getattr(rd, "trans", None):
            g.offset_fix = rd.trans     # (from the room's own scene file: always right)
            return
        if self.scene is None or not getattr(self, "mesh_main", None):
            g.offset_fix = None
            return
        obj = self.me()
        raw = g.pos(obj) if obj else None
        off = g.room_offset_raw()
        if not raw or not off:
            return
        local = (raw[0] - off[0], raw[1] - off[1], raw[2] - off[2])
        good = getattr(self, "good_offsets", None)
        if good is None:
            good = self.good_offsets = {}
        if self.on_floor(local):
            good[self.scene] = off
            g.offset_fix = None
            return
        if self.scene in good:
            fix = good[self.scene]
        else:
            hs = []
            for d in (0, 150, 300, 450, 600):
                for dx, dz in ((0, 0), (d, 0), (-d, 0), (0, d), (0, -d)):
                    hs += self.floor_heights(local[0] * MM + dx, local[2] * MM + dz)
                if hs:
                    break
            if not hs:
                return          # (not even over this room: leave it)
            h = min(hs, key=abs) / MM
            fix = (off[0], raw[1] - h, off[2])
        was = g.offset_fix
        if was is None or max(abs(a - b) for a, b in zip(was, fix)) > 5.0:
            logging.info("room offset %s is not this room's: using %s", off, fix)
            g.offset_fix = fix

    def pick_mesh(self, p):
        """The walk map to use from where you are. The room's main one has the floors; some
        places (the roof of the Service Room) are only in its second file, and narrow walkways
        need a closer squeeze past the walls: then that one."""
        main = getattr(self, "mesh_main", None)
        if not main:
            return
        x, y, z = p[0] * MM, p[1] * MM, p[2] * MM
        self.mesh = main
        if main.free_at(x, y, z) or not getattr(self, "_tris_a", None):
            return
        for clearance in (200.0, 120.0):
            m = self.cached_mesh(clearance)
            if not m:
                return
            if m.free_at(x, y, z):
                self.mesh = m
                logging.info("walk map: second one, clearance %d", clearance)
                return

    @staticmethod
    def _walls_of(tris):
        out = []
        for a, b, c in tris:
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln < 1e-6 or abs(ny) / ln < 0.35:
                out.append((a, b, c))
        return out

    def fix_door_heights(self):
        """A door's zone can sit at height 0 whatever floor the door is on (the game looks only at
        where you stand from above): the Large Hall's doors on its high walkway. A door with no
        floor at its own height beside it - but a pocket shut in by walls - goes on the floor
        beside it that is part of the room you walk round."""
        rd = self.room_data
        m = self.mesh_main
        if not rd or not m:
            return
        up = None
        for th in rd.things:
            if th.kind != "door" or not getattr(th, "box", None):
                continue
            x, z = th.pos[0] * MM, th.pos[2] * MM
            y = th.box[0][1] * MM
            try:
                if self.own_floor(m, x, y, z):
                    continue
                if up is None:
                    up = NavMesh(self._tris_e, up_only=True)
                found = up.main_floor_near(x, z)
            except Exception:
                logging.exception("door height")
                continue
            if not found or found[0] - y <= 600.0:
                continue        # (only ever up: a door on the floor of a pit stays there)
            dy = (found[0] - y) / MM
            (x0, y0, z0), (x1, y1, z1) = th.box
            th.box = ((x0, y0 + dy, z0), (x1, y1 + dy, z1))
            th.pos = (th.pos[0], th.pos[1] + dy, th.pos[2])
            logging.info("door %s: on the floor at %.0f, not %.0f", th.name, th.pos[1], th.pos[1] - dy)

    @staticmethod
    def own_floor(m, x, y, z, radius=1200.0):
        """True if there is floor about at height y near (x, z) that is a real part of the room."""
        c0, r0 = m.cell(x, z)
        n = int(radius // 150.0) + 1
        for dc in range(-n, n + 1):
            for dr in range(-n, n + 1):
                for i, h in enumerate(m.heights.get((c0 + dc, r0 + dr), ())):
                    if abs(h - y) > 300.0 or (c0 + dc, r0 + dr, i) in m.blocked:
                        continue
                    cx, cz = m.centre(c0 + dc, r0 + dr)
                    label = m.region(cx, h, cz)
                    if label is not None and m.region_size(label) >= 400:
                        return True
        return False

    def fix_step_heights(self):
        """Steps up onto a platform (op 99): their zones are at the level they take you to; put
        each at the level you stand on to use it - the other floor there. True if any."""
        rd = self.room_data
        steps = [th for th in (rd.things if rd else []) if getattr(th, "step", False)]
        if not steps:
            return False
        try:
            m = NavMesh(self._tris_e + (getattr(self, "_tris_a", None) or []), up_only=True)
        except Exception:
            logging.exception("step heights")
            return False
        for th in steps:
            (x0, y0, z0), (x1, y1, z1) = th.box
            zy = y0 * MM
            hs = [h for h in m.heights.get(m.cell(th.pos[0] * MM, th.pos[2] * MM), ()) if 500.0 < abs(h - zy) <= 1300.0]
            if not hs:
                continue
            h = max(hs, key=lambda v: abs(v - zy)) / MM
            th.box = ((x0, h - 10.0, z0), (x1, h + 10.0, z1))
            th.pos = (th.pos[0], h - 10.0, th.pos[2])
            logging.info("step %s at %.0f,%.0f: from height %.0f", th.name, th.pos[0], th.pos[2], h)
        for th in steps:
            # (which way to face to use it: towards the other end of the same step)
            other = [o for o in steps if o.way != th.way]
            if other:
                o = min(other, key=lambda o: math.hypot(o.pos[0] - th.pos[0], o.pos[2] - th.pos[2]))
                th.face = math.atan2(o.pos[0] - th.pos[0], o.pos[2] - th.pos[2])
        return True

    def apply_spots(self, mesh):
        """Give a walk map the room's places to jump down and climb up."""
        rd = self.room_data
        spots = [(th.way, (th.pos[0] * MM, th.box[0][1] * MM, th.pos[2] * MM))
                 for th in (rd.things if rd else []) if th.kind == "ledge"]
        if spots and mesh:
            try:
                mesh.set_spots(spots)
            except Exception:
                logging.exception("jump spots")

    def alt_mesh(self, clearance):
        """The second walk map (the main one's walls with the second collision file's floors
        and stairs), made the first time it is wanted; None if there is none."""
        if not getattr(self, "_tris_a", None) or not getattr(self, "mesh_main", None):
            return None
        return self.cached_mesh(clearance) or None

    def cached_mesh(self, key, src=None):
        """One of the room's extra walk maps ("floors", "fine", or the second map at a clearance),
        made once. They are made ahead in the background as you come into a room (prepare_meshes),
        so the list and auto walk do not wait for them; asked for while one is being made, this waits
        for it rather than making it again."""
        if src is None:
            if not hasattr(self, "_alt_meshes"):
                self._alt_meshes = {}
            src = (self._alt_meshes, getattr(self, "_tris_e", None) or [], getattr(self, "_tris_a", None) or [])
        store, tris_e, tris_a = src
        locks = self.__dict__.setdefault("_mesh_locks", {})
        lock = locks.setdefault((id(store), key), threading.Lock())
        with lock:
            m = store.get(key)
            if m is None:
                t0 = time.monotonic()
                try:
                    if key == "floors":
                        m = NavMesh(tris_e + tris_a, up_only=True)
                    elif key == "fine":
                        # (with the walls of the second collision file too: the counter round the Main
                        # Hall's typewriter is only there)
                        m = NavMesh(tris_e + self._walls_of(tris_a), clearance=170.0)
                    elif tris_a:
                        m = NavMesh(self._walls_e(tris_e) + tris_a, clearance=key, up_only=True)
                        if store is self._alt_meshes:
                            self.apply_spots(m)
                    else:
                        m = False
                except Exception:
                    logging.exception("walk map %s", key)
                    m = False
                store[key] = m
                if time.monotonic() - t0 > 0.3:
                    logging.info("walk map %s made in %.1f s", key, time.monotonic() - t0)
        return m

    def prepare_meshes(self):
        """Make the room's extra walk maps in the background, the ones wanted first first."""
        src = (self._alt_meshes, list(getattr(self, "_tris_e", None) or []), list(getattr(self, "_tris_a", None) or []))

        def work():
            for key in ("floors", 200.0, 120.0, "fine"):
                if self._alt_meshes is not src[0]:
                    return          # (gone into another room meanwhile: these would be the old room's)
                self.cached_mesh(key, src)
        threading.Thread(target=work, daemon=True).start()

    def _walls_e(self, tris=None):
        """The main walk map's walls only (its flat tops of furniture would make steps up to
        the second map's high places)."""
        out = []
        for a, b, c in (self._tris_e if tris is None else tris):
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln < 1e-6 or abs(ny) / ln < 0.35:
                out.append((a, b, c))
        return out

    def floor_at(self, gx, gz, gy, fallback):
        """The height (mm) of the ground at (gx, gz) mm for a thing at height gy (cm): the one
        nearest it, not above it by much (a thing on a shelf stands over the floor)."""
        if gy is None or not self.mesh:
            return fallback
        c = self.mesh.cell(gx, gz)

        def nearest(hs, best):
            for h in hs:
                if h > gy * MM + 400 or h < gy * MM - 1500:
                    continue
                if best is None or abs(h - gy * MM) < abs(best - gy * MM):
                    best = h
            return best
        best = nearest(self.mesh.heights.get(c, ()), None)
        if best is None or abs(best - gy * MM) > 300.0:
            # (a map can have no floor at its own height right under it - the lecture room's
            # lectern stands on the stage, but the squares under it have only the floor below
            # the stage: then the floor at its height just beside it)
            for d in (1, 2, 3):
                ring = [(c[0] + dc, c[1] + dr) for dc in range(-d, d + 1) for dr in range(-d, d + 1)
                        if max(abs(dc), abs(dr)) == d]
                near = nearest([h for rc in ring for h in self.mesh.heights.get(rc, ())
                                if self._big_floor(rc, h)], None)
                if near is not None and (best is None or abs(best - gy * MM) - abs(near - gy * MM) > 300.0):
                    best = near
                    break
        return best        # (None: no ground near it on this map: no way there)

    def _big_floor(self, rc, h):
        """True if the ground at height h in square rc is part of a floor you walk round (the
        stage), not the top of a shelf or a table."""
        m = self.mesh
        try:
            cx, cz = m.centre(*rc)
            label = m.region(cx, h, cz)
            return label is not None and m.region_size(label) >= 150
        except Exception:
            logging.exception("big floor")
            return False

    def plan(self, p, goal, gy=None):
        """The way to goal; when there is none on foot, one with a jump down (or a climb up) at
        an edge: then the way only as far as that edge, and plan_ledge says which."""
        self.plan_ledge = None
        way = self.plan_on(p, goal, gy)
        joined = self.plan_joined
        end = way[-1] if way else (p[0] * MM, p[2] * MM)
        short = way is None or (getattr(self, "plan_kind", None) == "door" and
                                (not joined or math.hypot(end[0] - goal[0], end[1] - goal[1]) > 900.0))
        rd = self.room_data
        spots = bool(rd) and any(th.kind == "ledge" for th in rd.things)
        if short and self.mesh and spots:
            # (no way, or only to somewhere short of it: over an edge, if that gets there - only
            # in a room with the game's own places to jump down and climb up, as the sewer)
            alt = self.plan_on(p, goal, gy, ledges=True)
            ledge = getattr(self.mesh, "last_ledge", None)
            if alt is not None and ledge:
                self.plan_ledge = ledge
                return alt
        self.plan_joined = joined
        return way

    def plan_on(self, p, goal, gy=None, ledges=False):
        if not self.mesh:
            return []
        y = p[1] * MM
        self.plan_joined = True
        gyy = self.floor_at(goal[0], goal[1], gy, y)
        if gyy is None:
            gyy = gy * MM if gy is not None else y      # (no ground right there: the nearest at its height)
        extra = tuple(getattr(self, "walk_blocks", {}).get(self.scene, ())) + tuple(self.push_extra())
        mate = tuple(self.partner_extra(goal))
        way = self.mesh.route((p[0] * MM, y, p[2] * MM), (goal[0], gyy, goal[1]), extra=extra + mate, ledges=ledges)
        if way is None and mate:
            # (no way round your partner: the way through where they stand - they may move)
            way = self.mesh.route((p[0] * MM, y, p[2] * MM), (goal[0], gyy, goal[1]), extra=extra, ledges=ledges)
        self.plan_floor_ok = self.route_floors_ok(ledges)
        if way is None and gy is not None and abs(gy * MM - gyy) > 300:
            # (the ground right under it is lower, as water under a ladder's foot: the floor
            # beside it at its own height)
            gyy = gy * MM
            way = self.mesh.route((p[0] * MM, y, p[2] * MM), (goal[0], gyy, goal[1]), extra=extra, ledges=ledges)
            self.plan_floor_ok = self.route_floors_ok(ledges)
        # (a way can end only near it - as near as this map gets - on another floor: the stairs
        # to it missing from this map; joined says it really gets there)
        try:
            self.plan_joined = self.mesh.joined_near((p[0] * MM, y, p[2] * MM), (goal[0], gyy, goal[1]))
        except Exception:
            logging.exception("joined")
            self.plan_joined = True
        if not self.plan_floor_ok:
            # (this map has you, or the end of the way, on another floor - standing on the Main
            # Hall's balcony right over the Dining Room door, a map without the balcony said
            # "Here" at once: the way does not really get there)
            self.plan_joined = False
        return way

    def route_floors_ok(self, ledges=False):
        """After a route on self.mesh: True unless it started on another floor than yours, or
        (on foot) ended on another floor than the one asked for."""
        m = self.mesh
        sdy = getattr(m, "last_start_dy", None)
        edy = getattr(m, "last_end_dy", None)
        if sdy is not None and sdy > 600.0:
            return False
        if edy is not None and edy > 900.0 and not (ledges and getattr(m, "last_ledge", None)):
            return False
        return True

    def partner_near(self, p):
        """(name, (x, y, z) cm) of your partner, if in this room and on its floor, else None."""
        try:
            me = self.me()
            for name, obj in self.game.characters():
                if obj == me:
                    continue
                q = self.game.local_pos(obj)
                if q and abs(q[1] - p[1]) < 150.0 and (not getattr(self, "mesh_main", None) or self.on_floor(q, 100.0)):
                    return name, q
        except NotReady:
            pass
        return None

    def partner_extra(self, goal):
        """Your partner as a circle for ways to keep round (they stand still while you walk past:
        Rebecca at the foot of the step by the pressure pad). Not when they are what you walk to."""
        p, _h = self.where_me()
        pn = self.partner_near(p) if p else None
        if not pn:
            return ()
        q = pn[1]
        if math.hypot(q[0] * MM - goal[0], q[2] * MM - goal[1]) < 1200.0 or \
                math.hypot(q[0] - p[0], q[2] - p[2]) < 60.0:
            return ()
        return ((q[0] * MM, q[1] * MM, q[2] * MM, 300.0),)

    def partner_in_way(self, p, h):
        """Your partner's name if they stand just ahead of you (within a metre and a half)."""
        pn = self.partner_near(p)
        if not pn:
            return None
        q = pn[1]
        d = math.hypot(q[0] - p[0], q[2] - p[2])
        if d > 150.0:
            return None
        ahead = abs(angle_diff(math.atan2(q[0] - p[0], q[2] - p[2]), h)) < math.radians(70)
        return pn[0] if ahead or d < 80.0 else None

    def learn_block(self, p, h):
        """Walked into something the walk map does not have (a statue, a way the game keeps shut
        till later) twice at the same place: remember it for this room, so the way goes round."""
        x, y, z = p[0] * MM, p[1] * MM, p[2] * MM
        last = getattr(self, "last_bump", None)
        self.last_bump = (x, z, self.scene)
        if not last or last[2] != self.scene or math.hypot(last[0] - x, last[1] - z) > 1200.0:
            return False
        blocks = getattr(self, "walk_blocks", None)
        if blocks is None:
            blocks = self.walk_blocks = {}
        bx, bz = x + math.sin(h) * 450.0, z + math.cos(h) * 450.0
        mine = blocks.setdefault(self.scene, [])
        mine.append((bx, y, bz, 450.0))
        del mine[:-24]
        self.last_bump = None
        logging.info("walk: learnt something in the way at %.0f,%.0f", bx / MM, bz / MM)
        return True

    def gate_side(self, g, p):
        """The gate as seen from where you are: its zone on your side, faced across to the other.
        (The Outside Area's gate has a zone each side; only the one you stand in opens it.)"""
        sides = getattr(g, "sides", None)
        if not sides or len(sides) < 2:
            return g
        def mid(b):
            return ((b[0][0] + b[1][0]) / 2, b[0][1], (b[0][2] + b[1][2]) / 2)
        sides = sorted(sides, key=lambda b: math.hypot(mid(b)[0] - p[0], mid(b)[2] - p[2]))
        near, far = sides[0], sides[1]
        g2 = copy.copy(g)
        (x0, y0, z0), (x1, y1, z1) = near
        nm, fm = mid(near), mid(far)
        # (stand inside your side's zone, near the gate, and face through it)
        dx, dz = fm[0] - nm[0], fm[2] - nm[2]
        ln = math.hypot(dx, dz) or 1.0
        g2.pos = (nm[0] + dx / ln * 10.0, nm[1], nm[2] + dz / ln * 10.0)
        g2.box = near
        g2.face = math.atan2(dx, dz)
        g2.stand = True
        g2.gate_of = g
        return g2

    def gate_ahead(self, p):
        """The gate you are up against (within a metre of it), or None (not twice in 20 seconds).
        From the south it opens with A each time; from the north it is walked through."""
        if time.monotonic() - getattr(self, "gate_tried", -99.0) < 20.0:
            return None
        rd = self.room_data
        if not rd:
            return None
        for g in rd.things:
            if g.kind == "door" and g.name.startswith("Gate") and getattr(g, "box", None):
                for b in getattr(g, "sides", None) or [g.box]:
                    if self.box_gap(b, p) < 100.0:
                        return g
        return None

    def poll_gate(self):
        """Going through the gate for you on the way somewhere: at it, A, and once on the other
        side, on to where you were going."""
        gp = getattr(self, "gate_pass", None)
        if not gp or gp.get("phase") != "open":
            return
        now = time.monotonic()
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        if not p:
            return
        if gp.get("unlock"):
            if gp["presses"] == 0:
                gp["presses"] = 1
                gp["release"] = now + 0.25
                gp["next"] = now + 2.0
                self.walker.hold({"a"})
            elif now > gp["release"] and now < gp["next"]:
                self.walker.hold(set())
            elif now >= gp["next"]:
                then = gp["then"]
                self.gate_pass = None
                logging.info("gate opened: on to %s", then.name)
                self.target = then
                self.start_walk(target=then)
            return
        if math.hypot(p[0] - gp["from"][0], p[2] - gp["from"][2]) > 120.0:
            # (through: the gate puts you on its other side)
            then = gp["then"]
            self.gate_pass = None
            self.walker.hold(set())
            logging.info("through the gate: on to %s", then.name)
            self.target = then
            self.start_walk(target=then)
            return
        if now < gp["next"]:
            if gp.get("a_at") and now >= gp["a_at"]:
                gp["a_at"] = None
                gp["release"] = now + 0.25
                self.walker.hold({"a"})
            elif now > gp["release"]:
                self.walker.hold(set())
            return
        if gp["presses"] >= 3:
            self.gate_pass = None
            self.walker.hold(set())
            self.stop_walk("At the gate. Press A, then auto walk again.")
            return
        gp["presses"] += 1
        g = getattr(self, "walk_target", None)
        inside = g is not None and getattr(g, "box", None) is not None and self.in_box(g.box, p, -5.0)
        if inside:
            gp["a_at"] = now
            gp["release"] = now + 0.25
        else:
            # (the walk stops short of the gate - the walk map keeps off its bars: a step on, into
            # the place where A opens it, then A)
            gp["release"] = now + 0.35
            gp["a_at"] = now + 0.45
            self.walker.hold({"up"})
        gp["next"] = now + 3.0

    def gate_on_way(self, t, p, way):
        """A gate (a door into another part of the same room) the way goes through, or None."""
        rd = self.room_data
        if not rd or t.kind == "door" and t.name.startswith("Gate") or getattr(t, "gate_of", None) is not None:
            return None
        pts = [(p[0] * MM, p[2] * MM)] + list(way or [])
        for g in rd.things:
            if g.kind != "door" or not g.name.startswith("Gate") or not getattr(g, "box", None):
                continue
            boxes = getattr(g, "sides", None) or [g.box]
            x0 = min(b[0][0] for b in boxes); x1 = max(b[1][0] for b in boxes)
            z0 = min(b[0][2] for b in boxes); z1 = max(b[1][2] for b in boxes)
            pad = 30.0
            x0, x1, z0, z1 = (x0 - pad) * MM, (x1 + pad) * MM, (z0 - pad) * MM, (z1 + pad) * MM
            for a, b in zip(pts, pts[1:]):
                n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 100.0))
                for k in range(n + 1):
                    x = a[0] + (b[0] - a[0]) * k / n
                    z = a[1] + (b[1] - a[1]) * k / n
                    if x0 <= x <= x1 and z0 <= z <= z1 and \
                            math.hypot(x - pts[0][0], z - pts[0][1]) > 1000.0:
                        return g    # (not where you stand: just come through it, from its far side)
        return None

    def start_walk(self, guide=False, target=None):
        t0 = time.monotonic()
        try:
            self._start_walk(guide, target)
        finally:
            took = time.monotonic() - t0
            if took > 0.25:
                logging.info("start walk took %.2f s", took)

    def _start_walk(self, guide=False, target=None):
        t = target or self.current_target()
        if t is not None and getattr(t, "sides", None) and getattr(t, "gate_of", None) is None:
            p0, _h0 = self.where_me()
            if p0:
                t = self.gate_side(t, p0)
        if t is None:
            self.say("Pick something in the list first.", interrupt=True)
            return
        if getattr(t, "push", False):
            self.start_push_walk(t, guide)      # (to the side to push it from)
            return
        if target is None and self.start_top_walk(t, guide):
            return                              # (up onto the table to get at it)
        if target is None and self.start_off_table(t, guide):
            return                              # (down off the table first)
        if target is None:
            self.push_plan = None
        self.load_room()
        p, h = self.where_me()
        if not p or h is None:
            self.say("Not in the game.", interrupt=True)
            return
        if getattr(t, "away", False):
            there, door = self.partner_way(t.name) if t.kind == "partner" else (None, None)
            if door is None:
                self.say("%s is in %s." % (t.name, there or "another room"), interrupt=True)
                return
            # (to the door that leads that way: from room to room, a door at a time)
            self.target = door
            self.start_walk(guide=guide, target=door)
            self.say("%s is in %s, this way." % (t.name, there), interrupt=False)
            return
        self.pick_mesh(p)
        goal, face = self.goal_for(t)
        gy = (t.box[0][1] + t.box[1][1]) / 2 if getattr(t, "box", None) else t.pos[1]
        # (an edge to jump down or climb up only on the way to a door or ladder, to a side to push
        # something from, or to a thing up on another level in a room with steps - the Small
        # Library's platform - or when there is no other way at all)
        doorish = t.kind == "door" or getattr(t, "push_of", None) is not None
        rd0 = self.room_data
        if not doorish and rd0 and (t.kind == "check" or any(th.kind == "ledge" for th in rd0.things)):
            # (a check's zone is on the floor you stand on to use it: the lecture room's lectern is
            # up on the stage, reached by the steps at its sides)
            gyy = self.floor_at(goal[0], goal[1], gy, p[1] * MM)
            doorish = gyy is not None and abs(gyy - p[1] * MM) > 600.0
        self.plan_kind = "door" if doorish else t.kind
        way = self.plan(p, goal, gy)

        def far(w):
            end = w[-1] if w else (p[0] * MM, p[2] * MM)
            b = getattr(t, "box", None)
            if b and t.kind == "door":
                # (from the door's box, not its middle - a door's middle is in the wall, and a map
                # that has the far side of it, reached the long way round, gets nearer that)
                dx = max(b[0][0] * MM - end[0], 0.0, end[0] - b[1][0] * MM)
                dz = max(b[0][2] * MM - end[1], 0.0, end[1] - b[1][2] * MM)
                return math.hypot(dx, dz) > 900.0
            return math.hypot(end[0] - goal[0], end[1] - goal[1]) > 900.0
        def length(w):
            pts = [(p[0] * MM, p[2] * MM)] + list(w or [])
            return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        joined = self.plan_joined
        # (a room whose main map has the second file's walls too - the Outside Area - has the
        # whole of it there: the other maps lack things that stop you, so they are only for when
        # the main one has no way)
        whole = bool(rd0) and rd0.code in A_WALL_ROOMS and self.mesh is getattr(self, "mesh_main", None) \
            and way is not None and joined and not far(way)
        if not whole and (way is None or self.plan_ledge or not joined or far(way) or doorish) and \
                (way is None or doorish or abs(gy - p[1]) > 100.0):
            # (the map picked may not reach it: standing in a doorway the main map walls off, or
            # stairs only the second collision file has whole - the Main Hall's; try the others:
            # a way on foot that gets there first, then one over an edge, then what there was)
            picked = self.mesh
            tries = [(picked, way, self.plan_ledge, joined)]
            for m in [getattr(self, "mesh_main", None), self.alt_mesh(200.0), self.alt_mesh(120.0)]:
                if m and m is not picked:
                    self.mesh = m
                    w = self.plan(p, goal, gy)
                    tries.append((m, w, self.plan_ledge, self.plan_joined))
            # (of the ways on foot that get there, the shortest: in the Main Hall the main map's
            # long ways round go through the passage the scales keep shut)
            good = sorted((x for x in tries if x[1] is not None and not x[2] and x[3] and not far(x[1])),
                          key=lambda x: length(x[1]) * (1.2 if x[0] is self._alt_meshes.get(120.0) else 1.0))
            pick = (good[0] if good else None) or \
                next((x for x in tries if x[1] is not None and x[2]), None) or \
                next((x for x in tries if x[1] is not None and not x[2] and x[3]), None) or tries[0]
            self.mesh, way, self.plan_ledge, joined = pick
            if pick[0] is not picked:
                logging.info("walk map: another one reaches %s", t.name)
        if way is not None and not joined and not self.plan_ledge and doorish and abs(gy - p[1]) > 150.0:
            way = None              # (only to under it, on this floor: say it is upstairs)
        self.walk_gy = gy
        self.walk_ledge = None
        self.ledge_wait = None
        if way is not None and getattr(self, "plan_ledge", None):
            self.walk_to_ledge(t, p, way, guide)
            return
        if way is None:
            far = (", it's downstairs" if p[1] - gy > 150 else ", it's upstairs" if gy - p[1] > 150 else "")
            self.say("No way there from here%s." % far, interrupt=True)
            return
        via = way[:-1] if way else []
        end = way[-1] if way else goal
        near = {"enemy": 1500.0, "partner": 900.0}.get(t.kind, 450.0)
        reached = None
        self.turning = None
        self.settling = None
        self.face_angle = None
        self.walk_box = None
        box = getattr(t, "box", None)
        if t.kind == "item" and not getattr(t, "via_check", False):
            # (an item is picked up like a thing is checked: from where the point the game's A
            # looks at is on it)
            x, y, z = t.pos
            r = self.ITEM_REACH
            fl = None
            try:
                # (the floor you stand on at the end of the way: free ground there, not over the item)
                ex, ez = way[-1] if way else (p[0] * MM, p[2] * MM)
                m = self.mesh
                c, r_ = m.cell(ex, ez)
                hs = [h for i, h in enumerate(m.heights.get((c, r_), ())) if (c, r_, i) not in m.blocked
                      and h <= y * MM + 100.0]
                fl = max(hs) if hs else (p[1] * MM if not way else None)
            except Exception:
                logging.exception("item floor")
            if fl is not None and y * MM - fl > 400.0:
                r = self.TABLE_REACH    # (up on a table: the near edge is as near as you get - the
                                        # Training Room's ammo on the gun bench, at the back of it)
                t.raised = True         # (and you must be near it and facing it: can_reach)
            t.outline = [(x - r, z - r), (x + r, z - r), (x + r, z + r), (x - r, z + r)]
            box = t.box = ((x - r, y, z - r), (x + r, y, z + r))
        if getattr(t, "stand", False) and way is not None:
            # (a lift to stand on: walk onto the middle of it)
            outline = self.outline_of(t)

            def on_it(x, z, rot, outline=outline):
                return R.in_outline(outline, x / MM, z / MM)
            self.walk_spot = None
            self.walk_box = None
            self.face_angle = None
            self.walker.guide = guide
            self.walker.start(t.name, t.pos[0] * MM, t.pos[2] * MM, via=way[:-1] if way else [], near=150.0,
                              reached=None if guide else on_it, origin=(p[0] * MM, p[2] * MM))
            self.walk_target = t
            self.walk_scene = self.scene
            self.guide_on = guide
            if guide:
                self.make_space()
                self.say("Beacon on. " + self.thing_words(t), interrupt=True)
            else:
                self.say("Walking to " + t.name, interrupt=True)
            return
        if box and t.kind in ("check", "item"):
            # A thing to check: towards the middle of it until the point the game's A looks at
            # (a little way in front of you) is on it, then stop there (standing in its box is
            # not enough: you have to be facing it from near enough)
            cx, cz = t.pos[0] * MM, t.pos[2] * MM
            fx, fz = cx, cz
            if way is not None:
                via = way[:-1] if way else []

                def reached(x, z, rot, t=t):
                    return self.can_reach(t, x, z, rot)
                self.face_angle = None
                self.walk_box = None
                self.walk_spot = True
                self.walker.guide = guide
                gx, gz = cx, cz
                if way and math.hypot(way[-1][0] - cx, way[-1][1] - cz) > 300.0:
                    # (its middle cannot be stood on - a typewriter on a table: walk to a spot
                    # beside it from which A reaches it, else to where the way ends)
                    gx, gz = way[-1]
                    spot = getattr(t, "spot", None) or self.check_spot(t, p)
                    if spot:
                        sway = self.plan(p, spot[0], gy)
                        if sway is not None and (not sway or math.hypot(sway[-1][0] - spot[0][0], sway[-1][1] - spot[0][1]) < 450.0):
                            via = sway[:-1] if sway else []
                            gx, gz = spot[0]
                            way = sway
                self.walker.start(t.name, gx, gz, via=via, near=150.0, face=(fx, fz),
                                  reached=None if guide else reached, origin=(p[0] * MM, p[2] * MM))
                logging.info("walk to %s: way %s, goal %.0f,%.0f", t.name, [tuple(round(v) for v in w) for w in way or []], gx, gz)
                self.walk_target = t
                self.walk_scene = self.scene
                self.guide_on = guide
                if guide:
                    self.make_space()
                    self.say("Beacon on. " + self.thing_words(t), interrupt=True)
                else:
                    self.say("Walking to " + t.name, interrupt=True)
                return
        self.walk_spot = None
        if box and t.kind in ("door", "check"):
            # Into the box you press A in, then facing the door (or the thing): the way into the
            # box across its thin side, from where the way ends.
            fa = self.box_facing(box, end if way else (p[0] * MM, p[2] * MM))
            self.face_angle = fa
            (x0, _y0, z0), (x1, _y1, z1) = box
            cx, cz = (x0 + x1) / 2 * MM, (z0 + z1) / 2 * MM
            face = (cx + math.sin(fa) * 400.0, cz + math.cos(fa) * 400.0)
            pad = 20.0

            def reached(x, z, rot, x0=x0 * MM - pad, x1=x1 * MM + pad, z0=z0 * MM - pad, z1=z1 * MM + pad, fa=fa):
                return x0 <= x <= x1 and z0 <= z <= z1 and abs(angle_diff(fa, rot)) < 0.3
            # (the game's A reaches a little way ahead of you: within about 35 cm of the box will do,
            # as against a wall in front of a door)
            self.walk_box = (x0 * MM - 900, x1 * MM + 900, z0 * MM - 900, z1 * MM + 900)
        self.walker.guide = guide
        self.walker.start(t.name, end[0], end[1], via=via, near=near, face=face,
                          reached=None if guide else reached, origin=(p[0] * MM, p[2] * MM))
        self.walk_target = t
        self.walk_scene = self.scene        # (a walk ends if the room changes)
        self.guide_on = guide
        if guide:
            self.make_space()
            self.say("Beacon on. " + self.thing_words(t), interrupt=True)
        else:
            self.say("Walking to " + t.name, interrupt=True)

    LEDGE_WORDS = {"down": "Edge. Press A to jump down.", "up": "Edge. Press A to climb up."}

    def ledge_spot(self, kind, frm, p):
        """The room's own place to jump down (or climb up) nearest the edge planned, at the height
        you are at, or None."""
        rd = self.load_room()
        best = None
        for th in (rd.things if rd else []):
            if th.kind != "ledge" or getattr(th, "way", None) != kind:
                continue
            if abs(th.box[0][1] - p[1]) > 60.0:
                continue                            # (the spot for the other level)
            d = math.hypot(th.pos[0] * MM - frm[0], th.pos[2] * MM - frm[1])
            if best is None or d < best[0]:
                best = (d, th)
        return best[1] if best else None

    def walk_to_ledge(self, t, p, way, guide):
        """The way to t goes over an edge: walk to it and face across, then the player presses
        A; once they are down (or up) the walk to t goes on by itself."""
        kind, frm, to = self.plan_ledge
        self.walk_ledge = (kind, frm, to)
        self.ledge_goal = t
        spot = self.ledge_spot(kind, frm, p)
        if spot is not None:
            # (the game only lets you down or up at its own spots: walk to the one there, like
            # to a thing to check, until A reaches it)
            self.plan_kind = "ledge"
            sway = self.plan(p, (spot.pos[0] * MM, spot.pos[2] * MM), spot.pos[1])
            self.plan_ledge = None
            if sway is not None:
                cx, cz = spot.pos[0] * MM, spot.pos[2] * MM
                self.walk_spot = True
                self.walk_box = None
                self.face_angle = None
                self.settling = None
                self.turning = None
                self.walker.guide = guide
                self.walk_target = spot
                self.walk_scene = self.scene
                self.guide_on = guide

                def reached(x, z, rot, s=spot):
                    return self.can_reach(s, x, z, rot)
                if getattr(spot, "step", False) and getattr(spot, "face", None) is not None:
                    # (a step: stood on its zone - the game only lets you up or down from there -
                    # then facing across it)
                    outline = self.outline_of(spot)
                    self.walk_spot = None
                    self.face_angle = spot.face

                    def reached(x, z, rot, outline=outline):
                        return R.in_outline(outline, x / MM, z / MM)
                self.walker.start(spot.name, cx, cz, via=sway[:-1] if sway else [], near=150.0, face=(cx, cz),
                                  reached=None if guide else reached, origin=(p[0] * MM, p[2] * MM))
                if guide:
                    self.make_space()
                    self.say("Beacon on. %s, over an edge." % t.name, interrupt=True)
                else:
                    self.say("Walking to %s, %s an edge first." % (t.name, kind), interrupt=True)
                return
        self.walk_spot = None
        self.walk_box = None
        self.settling = None
        self.face_angle = math.atan2(to[0] - frm[0], to[1] - frm[1])
        fa = self.face_angle
        face = (frm[0] + math.sin(fa) * 400.0, frm[1] + math.cos(fa) * 400.0)
        self.walk_target = t
        self.walk_scene = self.scene
        self.guide_on = guide
        self.walker.guide = guide
        if not way:
            # (at the edge already: only turn to it)
            self.turning = (fa, time.monotonic() + 3.0, t)
            self.say("Walking to %s: an edge first." % t.name, interrupt=True)
            return
        self.turning = None
        self.walker.start(t.name, frm[0], frm[1], via=way[:-1], near=150.0, face=face,
                          reached=None, origin=(p[0] * MM, p[2] * MM))
        if guide:
            self.make_space()
            self.say("Beacon on. %s, over an edge." % t.name, interrupt=True)
        else:
            self.say("Walking to %s, %s an edge first." % (t.name, "down" if kind == "down" else "up"),
                     interrupt=True)

    def at_ledge(self):
        """Arrived at the edge (and turned to it). Auto walk: press A to jump down (or climb up),
        then on to the thing once landed. The beacon: say so, and the player presses A."""
        kind = self.walk_ledge[0]
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        guide = self.walker.guide
        now = time.monotonic()
        self.ledge_wait = {"t": getattr(self, "ledge_goal", None) or getattr(self, "walk_target", None),
                           "y0": p[1] if p else None,
                           "since": now, "guide": guide, "presses": 0, "next": now + 0.2,
                           "release": 0.0, "kind": kind}
        self.walk_ledge = None
        if guide:
            self.stop_walk(self.LEDGE_WORDS[kind])
        else:
            self.stop_walk("Jumping down." if kind == "down" else "Climbing up.")

    def poll_ledge(self):
        """After an edge: press A (auto walk), and once down (or up), on to the thing."""
        lw = getattr(self, "ledge_wait", None)
        if not lw:
            return
        t, y0, guide = lw["t"], lw["y0"], lw["guide"]
        now = time.monotonic()
        if lw["release"] and now >= lw["release"]:
            lw["release"] = 0.0
            self.walker.hold(set())
        if now - lw["since"] > 90.0 or t is None or y0 is None:
            self.ledge_wait = None
            return
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        if not p:
            return
        if abs(p[1] - y0) < 40.0:
            self.ledge_stable = None
            if not guide and not lw["release"] and now >= lw["next"]:
                if lw["presses"] >= 4:
                    self.ledge_wait = None
                    self.say("Could not %s here." % ("jump down" if lw["kind"] == "down" else "climb up"),
                             interrupt=True)
                    return
                if not lw.get("nudged"):
                    # (a step on into the edge first: the spot is a little beyond where the walk
                    # keeps off the wall)
                    lw["nudged"] = True
                    lw["release"] = now + (0.5 if lw["presses"] == 0 else 0.3)
                    lw["next"] = lw["release"] + 0.15
                    self.walker.hold({"up"})
                    return
                lw["nudged"] = False
                lw["presses"] += 1
                lw["next"] = now + 2.5
                lw["release"] = now + 0.25
                self.walker.hold({"a"})
            return
        key = round(p[1] / 5.0)                     # (down or up: wait for the landing to finish)
        st = getattr(self, "ledge_stable", None)
        if not st or st[0] != key:
            self.ledge_stable = (key, now)
            return
        if now - st[1] < 0.8:
            return
        self.ledge_wait = None
        self.ledge_stable = None
        if lw["release"]:
            self.walker.hold(set())
        cur = self.current_target()
        if cur is not None and cur.name == t.name:
            self.start_walk(guide=guide)

    @staticmethod
    def box_facing(box, frm):
        """The way to face to use a box (a door's: across its thin side), coming from frm (mm)."""
        (x0, _y0, z0), (x1, _y1, z1) = box
        cx, cz = (x0 + x1) / 2 * MM, (z0 + z1) / 2 * MM
        vx, vz = cx - frm[0], cz - frm[1]
        wx, wz = (x1 - x0), (z1 - z0)
        if wx * 1.5 <= wz:                  # thin across x: face along x
            return math.atan2(1.0 if vx >= 0 else -1.0, 0.0)
        if wz * 1.5 <= wx:                  # thin across z: face along z
            return math.atan2(0.0, 1.0 if vz >= 0 else -1.0)
        if abs(vx) < 1 and abs(vz) < 1:
            return 0.0
        return math.atan2(vx, vz)

    ITEM_REACH = 12.0   # (cm: how near an item the point A looks at has to be)
    TABLE_REACH = 45.0  # (cm: the same for an item up on a table or a shelf - the Operating Room's
                        # spray beside the overturned table is taken with A pointing 41 cm from it)
    FLOOR_REACH = 400.0  # (mm: where A looks for an item on the floor, in front of you)
    RAISED_NEAR = 780.0  # (mm: how near an item up on something you have to stand)
    REACH = 520.0       # (mm: the game's A looks for a thing to check about this far in front of you)

    def outline_of(self, t):
        o = getattr(t, "outline", None)
        if o and len(o) >= 3:
            return o
        (x0, _y0, z0), (x1, _y1, z1) = t.box
        return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]

    def check_spot(self, t, p):
        """((x, z) mm, facing) of a free spot beside the thing's outline, from which the point the
        game's A looks at (REACH in front) is just inside it; the nearest with a way to it."""
        if not self.mesh:
            return None
        poly = self.outline_of(t)
        y = p[1] * MM
        cands = []
        for i in range(len(poly)):
            (ax, az), (bx, bz) = poly[i], poly[(i + 1) % len(poly)]
            ax, az, bx, bz = ax * MM, az * MM, bx * MM, bz * MM
            ln = math.hypot(bx - ax, bz - az)
            if ln < 1.0:
                continue
            ux, uz = (bx - ax) / ln, (bz - az) / ln
            nx, nz = -uz, ux                    # (inwards)
            if ln <= 200:
                alongs = [ln / 2]
            else:
                n = max(1, int((ln - 200) // 150))
                alongs = [100 + (ln - 200) * k / n for k in range(n + 1)]
            for s_ in alongs:
                ex, ez = ax + ux * s_, az + uz * s_
                for inset in (150.0, 80.0, 30.0):
                    tx, tz = ex + nx * inset, ez + nz * inset
                    if R.in_outline(poly, tx / MM, tz / MM):
                        break
                else:
                    continue
                for back in (0.0, 100.0, 200.0):
                    sx, sz = tx - nx * (self.REACH + back), tz - nz * (self.REACH + back)
                    cands.append(((sx, sz), math.atan2(nx, nz), back))

        # (where the player can stand: closer to a table than the walk map keeps)
        fine = self.cached_mesh("fine")
        stand = fine or self.mesh
        mine = stand.region(p[0] * MM, y, p[2] * MM)
        # (free, and joined to where you are: not inside a counter whose walls close it round)
        free = [c for c in cands if stand.free_at(c[0][0], y, c[0][1], reach=150.0) and
                (mine is None or stand.region(c[0][0], y, c[0][1]) == mine)]
        if not free:
            return None
        me = (p[0] * MM, p[2] * MM)
        # (one from which A reaches it as it is first, nearest you; stepping in only if none)
        free.sort(key=lambda c: (c[2], math.hypot(c[0][0] - me[0], c[0][1] - me[1])))
        for c in free[:10]:
            way = self.plan(p, c[0])
            if way is not None and (not way or math.hypot(way[-1][0] - c[0][0], way[-1][1] - c[0][1]) < 450.0):
                return c[0], c[1]
        return None

    def reach_fits(self, t, p):
        """True if some way of facing from p (cm) has the game's A reach the thing."""
        x, z = p[0] * MM, p[2] * MM
        return any(self.can_reach(t, x, z, k * math.pi / 24) for k in range(48))

    def reach_facing(self, t, p):
        """The way to face from p (cm) so the game's A reaches the thing: the nearest way to
        straight at its middle that does."""
        if getattr(t, "face", None) is not None:
            return t.face               # (a step up or down: across it, to the other level)
        x, z = p[0] * MM, p[2] * MM
        base = math.atan2(t.pos[0] * MM - x, t.pos[2] * MM - z)
        if getattr(t, "raised", False):
            return base                 # (up on something: straight at it)
        for k in range(0, 25):
            for sign in ((1,) if k == 0 else (1, -1)):
                a = base + sign * k * math.pi / 24
                if self.can_reach(t, x, z, a):
                    return a
        return base

    def can_reach(self, t, x, z, rot):
        """True if the point the game's A looks at (from x, z mm facing rot) is in the thing's outline."""
        reach = self.REACH
        if t.kind == "item" and not getattr(t, "raised", False) and not getattr(t, "via_check", False):
            reach = self.FLOOR_REACH    # (on the floor: the game looks nearer your feet - the
                                        # Output Reg. Coil the monkey pulled out, missed from a step back)
        fx, fz = x + math.sin(rot) * reach, z + math.cos(rot) * reach
        if getattr(t, "raised", False):
            # (up on something: the game gives it to you only from within about 80 cm of it and
            # facing it - the Breeding Room Key on the gas injector, where A from farther off
            # looks at the injector instead)
            dx, dz = t.pos[0] * MM - x, t.pos[2] * MM - z
            if math.hypot(dx, dz) > self.RAISED_NEAR or abs(angle_diff(math.atan2(dx, dz), rot)) > 0.45:
                return False
        return R.in_outline(self.outline_of(t), fx / MM, fz / MM)

    @staticmethod
    def in_box(box, p, pad):
        (x0, _y0, z0), (x1, _y1, z1) = box
        return x0 - pad <= p[0] <= x1 + pad and z0 - pad <= p[2] <= z1 + pad

    def blocked_way(self, p, default):
        """Of the eight ways round you, the one nearest something you cannot walk into."""
        if not self.mesh:
            return default
        dists = []
        for k in range(8):
            a = k * math.pi / 4
            hit = None
            for step in range(1, 8):
                d = step * 150.0
                x, z = p[0] * MM + math.sin(a) * d, p[2] * MM + math.cos(a) * d
                if not self.mesh.free_at(x, p[1] * MM, z):
                    hit = d
                    break
            dists.append(hit)
        found = [d for d in dists if d is not None]
        if not found:
            return default
        near = min(found)
        ways = [k * math.pi / 4 for k, d in enumerate(dists) if d == near]
        # (several equally near: the middle of them)
        sx = sum(math.sin(a) for a in ways)
        sz = sum(math.cos(a) for a in ways)
        if abs(sx) < 1e-6 and abs(sz) < 1e-6:
            return ways[0]
        return math.atan2(sx, sz)

    @staticmethod
    def box_gap(box, p):
        """How far (cm) p is from the box seen from above: 0 inside it."""
        (x0, _y0, z0), (x1, _y1, z1) = box
        dx = max(x0 - p[0], 0.0, p[0] - x1)
        dz = max(z0 - p[2], 0.0, p[2] - z1)
        return math.hypot(dx, dz)

    def in_walk_box(self, p):
        b = getattr(self, "walk_box", None)
        return b is None or (b[0] <= p[0] * MM <= b[1] and b[2] <= p[2] * MM <= b[3])

    def poll_turn(self):
        """After auto walk: turning on the spot to face the door or thing."""
        tr = getattr(self, "turning", None)
        if not tr:
            return
        fa, until, t = tr
        obj = self.me()
        h = self.game.heading(obj) if obj else None
        if h is None:
            return
        d = angle_diff(fa, h)
        if abs(d) < 0.15 or time.monotonic() > until:
            self.turning = None
            self.walker.hold(set())
            if getattr(self, "walk_ledge", None):
                self.at_ledge()
                return
            if getattr(t, "push_of", None) is not None and self.push_arrived():
                return
            p = self.game.local_pos(obj)
            spot = getattr(self, "walk_spot", None)
            box = getattr(t, "box", None) if t is not None else None
            if spot and box and p:
                ok = self.can_reach(t, p[0] * MM, p[2] * MM, h)
            else:
                ok = p and self.in_walk_box(p) and abs(d) < 0.3
            if ok:
                self.stop_walk("Here. Press A.")
            else:
                self.stop_walk("Here.")
            return
        self.walker.hold({"left" if d > 0 else "right"})

    def make_space(self):
        if self.space is None:
            try:
                from .sound3d import Space3D
                self.space = Space3D()
                self.space.volume = getattr(self, "volumes", {}).get("guide", 1.0)
                self.space.enabled = getattr(self, "sounds_on", True)
            except Exception:
                logging.exception("3D sound")
        return self.space

    def stop_walk(self, words=None):
        t = getattr(self, "walk_target", None)
        gp = getattr(self, "gate_pass", None)
        if gp and gp.get("phase") == "walk":
            if words and words.startswith(("Here", "On the")) and t is not None and getattr(t, "gate_of", None):
                # (at the gate, facing through it: A for you - poll_gate)
                self.settling = None
                if getattr(self, "beeps", None):
                    self.beeps.quiet()
                self.walker.stop()
                self.link.stick = None
                p, _h = self.where_me()
                gp.update(phase="open", presses=0, next=0.0, release=0.0, **{"from": p or (0, 0, 0)})
                self.say("Opening the gate.", interrupt=True)
                return
            self.gate_pass = None       # (stopped, or could not get there: no going through)
        if words and words.startswith(("Here", "On the")) and t is not None and getattr(t, "pad_words", None):
            words = t.pad_words     # (a pressure pad: stood on, nothing to press; the Hookshot's place)
        self.settling = None
        if getattr(self, "beeps", None):
            self.beeps.quiet()
        self.walker.stop()
        self.walker.guide = False
        self.guide_on = False
        if self.space:
            self.space.quiet()
        self.link.stick = None
        self.link.add = 0
        self.link.push()
        if words:
            self.say(words, interrupt=True)

    def poll_settle(self):
        """After walking up to a thing to check: still in reach once stopped? If not, a short
        step on (a few times), then turn to it."""
        st = self.settling
        t, until, tries, step_until = st
        now = time.monotonic()
        if step_until:
            if now < step_until:
                return
            self.walker.hold(set())
            st[3] = 0.0
            st[1] = now + 0.45
            return
        if now < until:
            return
        obj = self.me()
        p = self.game.local_pos(obj) if obj else None
        h = self.game.heading(obj) if obj else None
        if not p or h is None:
            self.settling = None
            self.stop_walk("Here.")
            return
        if self.can_reach(t, p[0] * MM, p[2] * MM, h):
            self.settling = None
            if getattr(self, "walk_ledge", None):
                if getattr(t, "face", None) is not None and abs(angle_diff(t.face, h)) > 0.2:
                    self.turning = (t.face, time.monotonic() + 3.0, t)     # (across the step first)
                    return
                self.at_ledge()
                return
            self.stop_walk("Here. Press A.")
            return
        if tries < 3:
            st[2] = tries + 1
            want = self.reach_facing(t, p)
            keys, _d = self.walker.turn_keys(want, h, tight=0.15)
            self.walker.hold(keys if keys else {"up"})
            st[3] = now + (0.12 if keys else 0.18)
            return
        self.settling = None
        self.turning = (self.reach_facing(t, p), time.monotonic() + 3.0, t)

    def poll_walk(self):
        if getattr(self, "gate_pass", None) and self.gate_pass.get("phase") == "open":
            self.poll_gate()
            return
        if (getattr(self, "settling", None) or getattr(self, "turning", None)) and \
                self.scene != getattr(self, "walk_scene", self.scene):
            # (gone through: stairs you walk onto take you to the next room while auto walk is
            # still settling there - nothing more to say, the room's name is said)
            self.turning = None
            self.stop_walk()
            return
        if getattr(self, "settling", None):
            self.poll_settle()
            return
        if getattr(self, "turning", None):
            self.poll_turn()
            return
        if not self.walker.walking:
            return
        obj = self.me()
        if not obj:
            self.stop_walk()
            return
        p = self.game.local_pos(obj)
        h = self.game.heading(obj)
        if not p or h is None:
            return
        if self.scene != getattr(self, "walk_scene", self.scene):
            self.stop_walk("Stopped: new room.")
            return
        self.walk_scene = self.scene
        res = self.walker.step(p[0] * MM, p[2] * MM, h)
        if not self.walker.guide and self.walker.goal:
            # (auto walk: the guide beep of Resident Access HD towards the thing, faster as you get
            # near, higher ahead of you, lower behind, from the side it is on)
            t0 = getattr(self, "walk_target", None)
            beeps = getattr(self, "beeps", None)
            if beeps and t0 is not None and getattr(self, "sounds_on", True):
                d = angle_diff(math.atan2(t0.pos[0] - p[0], t0.pos[2] - p[2]), h)
                dist = math.hypot(t0.pos[0] - p[0], t0.pos[2] - p[2]) * MM
                beeps.point((dist + 300.0) / 80.0, math.cos(d), -math.sin(d))
        if self.walker.guide and self.space:
            aim = self.walker.aim or (self.walker.goal[1], self.walker.goal[2]) if self.walker.goal else None
            if aim:
                rel = angle_diff(math.atan2(aim[0] - p[0] * MM, aim[1] - p[2] * MM), h)
                dist = math.hypot(aim[0] - p[0] * MM, aim[1] - p[2] * MM) / 1000.0
                self.space.point(rel, dist)
            if self.walker.corner_turn > 0.5:
                self.walker.corner_turn = 0.0
                self.space.chime()
        if res is None:
            return
        logging.info("walk: %s at %.0f,%.0f", res, p[0], p[2])
        t = getattr(self, "walk_target", None)
        if res in ("arrived", "near") and getattr(self, "walk_ledge", None) and not getattr(self, "walk_spot", None):
            if self.space:
                self.space.quiet()
            fa = self.face_angle
            if abs(angle_diff(fa, h)) < 0.2:
                self.at_ledge()
            else:
                self.walker.stop()
                self.turning = (fa, time.monotonic() + 3.0, t)
            return
        if res in ("arrived", "near") and t is not None and getattr(t, "push_of", None) is not None \
                and getattr(self, "push_plan", None):
            # (at the side to push the table from: turn the way it is to go)
            if self.space:
                self.space.quiet()
            self.walker.stop()
            self.turning = (self.push_plan["heading"], time.monotonic() + 3.0, t)
            return
        if res in ("arrived", "near") and t is not None and getattr(t, "stand", False):
            if self.space:
                self.space.quiet()
            if getattr(t, "face", None) is not None and getattr(t, "push_of", None) is None:
                # (a seat: stood behind its desk, then facing it)
                self.walker.stop()
                self.turning = (t.face, time.monotonic() + 3.0, t)
                return
            self.stop_walk("On the %s." % t.name.lower() if R.in_outline(self.outline_of(t), p[0], p[2]) else "Here.")
            return
        if res in ("arrived", "near"):
            guide_was = self.walker.guide
            if self.space:
                self.space.quiet()
            self.guide_on = False
            self.walker.guide = False
            words = "Here."
            if getattr(self, "beeps", None) and getattr(self, "sounds_on", True):
                self.beeps.chime()
            fa = getattr(self, "face_angle", None)
            box = getattr(t, "box", None) if t is not None else None
            if res == "arrived" and getattr(self, "walk_spot", None):
                # (wait for her to settle: walking into something she can bounce back a little)
                self.walker.stop()
                self.settling = [t, time.monotonic() + 0.45, 0, 0.0]
                return
            if t is not None and getattr(t, "face", None) is not None:
                fa = t.face                     # (a step up or down: across it, to the other level)
            elif getattr(self, "walk_spot", None) and t is not None:
                fa = self.reach_facing(t, p)
            elif fa is not None and box:
                # (from where you have ended up: the way planned can end on the far side)
                fa = self.box_facing(box, (p[0] * MM, p[2] * MM))
                inside = self.in_box(box, p, 0.0)
                (x0, _y0, z0), (x1, _y1, z1) = box
                if inside and t.kind == "check" and not ((x1 - x0) * 1.5 <= (z1 - z0) or (z1 - z0) * 1.5 <= (x1 - x0)):
                    # standing in the spot: the thing is the way you cannot walk (a body on the
                    # floor against the wall, a shelf)
                    fa = self.blocked_way(p, fa)
                self.face_angle = fa
            if fa is None and t is not None and t.kind in ("item", "check", "door"):
                fa = math.atan2(t.pos[0] - p[0], t.pos[2] - p[2])     # (face the thing itself)
            if fa is None:
                self.stop_walk(words)
                return
            if res == "arrived" and abs(angle_diff(fa, h)) < 0.2:
                self.stop_walk("Here. Press A.")
                return
            # Turn on the spot to face it (after the beacon too), then "Here. Press A."
            self.walker.stop()
            if getattr(self, "beeps", None):
                self.beeps.quiet()
            self.turning = (fa, time.monotonic() + 3.0, t)
            return
        elif res in ("bump", "blocked", "gave up") and getattr(self, "walk_ledge", None) and t is not None and \
                math.hypot(t.pos[0] - p[0], t.pos[2] - p[2]) < 250.0:
            # (up against the edge by the spot to jump down or climb up: turn to it, step in, A)
            self.walker.stop()
            self.turning = (math.atan2(t.pos[0] - p[0], t.pos[2] - p[2]), time.monotonic() + 3.0, t)
            return
        elif res in ("bump", "blocked", "gave up") and t is not None and t.kind == "door" and \
                getattr(t, "box", None) and self.box_gap(t.box, p) < 100.0:
            # (a ladder hatch or a door behind a rail: as near as the walk gets, within reach of it -
            # turn to it there)
            (x0, _y0, z0), (x1, _y1, z1) = t.box
            fa = math.atan2((x0 + x1) / 2 - p[0], (z0 + z1) / 2 - p[2])
            self.walker.stop()
            self.walk_box = None
            self.turning = (fa, time.monotonic() + 3.0, t)
            return
        elif res in ("replan", "bump", "blocked", "gave up") and getattr(self, "walk_spot", None) \
                and t is not None and self.reach_fits(t, p):
            # (walked into something right by the thing: turning on the spot reaches it)
            self.walker.stop()
            self.turning = (self.reach_facing(t, p), time.monotonic() + 3.0, t)
            return
        elif res in ("bump", "blocked", "gave up") and t is not None and t.kind == "item" and \
                math.hypot(t.pos[0] - p[0], t.pos[2] - p[2]) < 110.0 and \
                t.pos[1] - p[1] > 40.0:
            # (an item up on furniture you cannot get round: stopped against it near enough - turn
            # straight at it there; A takes it from further than the walk map lets you stand)
            self.walker.stop()
            self.turning = (math.atan2(t.pos[0] - p[0], t.pos[2] - p[2]), time.monotonic() + 3.0, t)
            return
        elif res in ("bump", "blocked", "gave up") and t is not None and self.gate_ahead(p) is not None:
            # (walked into the Outside Area's gate while it is still locked: A at it unlocks it -
            # "The door is now unlocked" - then on; once open it is walked through)
            g = self.gate_ahead(p)
            self.gate_tried = time.monotonic()  # (once per walk: a second bump there is something else)
            self.walker.stop()
            self.gate_pass = {"then": t, "phase": "open", "unlock": True, "presses": 0, "next": 0.0,
                              "release": 0.0, "from": p, "gate": g}
            logging.info("walked into %s: A to open it", g.name)
            self.say("Opening the gate.", interrupt=True)
            return
        elif res in ("replan", "bump"):
            if res == "bump" and not self.partner_in_way(p, h):
                self.learn_block(p, h)      # (your partner moves: not learnt as something in the way)
            goal = (self.walker.goal[1], self.walker.goal[2])
            way = self.plan(p, goal, getattr(self, "walk_gy", None))
            if way:
                self.walker.reroute(way[:-1], way[-1][0], way[-1][1], (p[0] * MM, p[2] * MM))
        elif res in ("blocked", "gave up") and getattr(self, "walk_ledge", None) and t is not None and \
                math.hypot(t.pos[0] - p[0], t.pos[2] - p[2]) < 250.0:
            # (up against the wall under the spot to climb: turn to it, step in and press A)
            self.walker.stop()
            self.turning = (math.atan2(t.pos[0] - p[0], t.pos[2] - p[2]), time.monotonic() + 3.0, t)
            return
        elif res in ("blocked", "gave up"):
            who = self.partner_in_way(p, h)
            if who:
                self.stop_walk("%s is in the way. The right bumper tells %s to wait or follow. %s" % (
                    who, "her" if who == "Rebecca" else "him", self.thing_words(t) if t else ""))
            else:
                self.stop_walk("Something is in the way. " + (self.thing_words(t) if t else ""))
