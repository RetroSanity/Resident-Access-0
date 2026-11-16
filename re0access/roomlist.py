"""The room list (the e list), auto walk and the 3D beacon, as in Resident Access HD.

Hold the left bumper (not aiming) and:
- D-pad right / left: the next / previous list (Doors, Items, Things to check, Puzzles, Enemies).
- D-pad down / up: the next / previous thing in it, nearest first.
- Left stick in: auto walk there. Right stick in: the 3D beacon to it (again to stop).
- B: stop walking. Y: the way to it again. X: health. A: the room and what is in it.
- Right bumper: the enemies in the room.
"""
import logging
import math
import os
import time

from . import link as L
from . import room as R
from .game import STAGE_CODES, STAGE_FIRST, enemy_name, scene_code, scene_words
from .mem import NotReady
from .nav import NavMesh
from . import sbc
from .walker import Walker, angle_diff

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


class RoomList:
    """Mixed into the App: needs self.game, self.link, self.say, self.scene."""

    def init_list(self):
        self.group = 0
        self.picked = {}            # group -> the name and place last picked in it
        self.lb_mode = False
        self.pad_held = 0
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
                self.mesh = self.mesh_main = NavMesh(self._tris_e)
                key_a = "scene\\scr\\%sa" % name
                if key_a in ents:
                    self._tris_a = [tuple(tuple(c * MM for c in p) for p in t)
                                    for t in sbc.triangles(sbc.unpack(d, ents[key_a]))]
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
        if name and was and name != was and len(chars) == 2:
            if self.game.item_view() is not None:
                return      # (Y on the item screen: "Rebecca's items" is said with the item)
            self.say("Now %s." % name, interrupt=True)

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
                if getattr(self, "mesh_main", None) and not self.on_floor(pos, 100.0):
                    continue        # (in the next car or room, not this one)
                out.append(R.Thing("enemy", enemy_name(ek), pos, obj=obj, hp=hp, top=self.enemy_top(obj, hp)))
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
        if kind == "item":
            # (an item got by checking the thing it is on - the hookshot fixed to the wall - is
            # listed here, at that thing)
            out = [self.item_in_check(t) or t for t in out]
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
            for k, item, amount, put, pos in recs:
                if amount == 0 or pos == (0.0, 0.0, 0.0):
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
        for obj, ek, pos, hp in es:
            now_hp[obj] = hp
            if hp > 0:
                top = self.enemy_top(obj, hp)
            old = was.get(obj)
            if old is not None and old > 0 and hp < old and getattr(self, "_hp_scene", None) == self.scene:
                # hit: how much it has left (or that it is down)
                if hp <= 0:
                    self.say("%s down." % enemy_name(ek), interrupt=True)
                else:
                    self.say("%d percent" % max(1, round(100.0 * hp / top)), interrupt=True)
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

    def shot_would_hit(self, es):
        obj = self.me()
        if not obj or not self.aiming():
            return False
        p = self.game.local_pos(obj)
        h = self.game.heading(obj)
        if not p or h is None:
            return False
        x, y, z = p[0] * MM, p[1] * MM, p[2] * MM
        for eobj, ek, pos, hp in es:
            if hp <= 0:
                continue
            ex, ey, ez = pos[0] * MM, pos[1] * MM, pos[2] * MM
            if abs(ey - y) > 1500:
                continue
            dist = math.hypot(ex - x, ez - z)
            if dist > self.GUN_REACH:
                continue
            d = angle_diff(math.atan2(ex - x, ez - z), h)
            if abs(d) > math.pi / 2:
                continue
            if dist * math.sin(abs(d)) > self.HIT_WIDTH and abs(d) > self.CONE:
                continue
            if self.mesh is not None and not self.mesh.clear_shot(x, y, z, ex, ez):
                continue
            return True
        return False

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
        if t.kind == "partner" and getattr(t, "hp", None) is not None:
            name = "%s, health %d percent" % (name, t.hp)
        if not p:
            return name
        if getattr(t, "away", False):
            return name + ", in another room"
        d = math.hypot(t.pos[0] - p[0], t.pos[2] - p[2])
        words = "%s, %s, %s" % (name, metres(d), self.way_words(t.pos[0], t.pos[2], p, h))
        if t.pos[1] - p[1] > 150:
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
                if t.name == cur[0] and math.hypot(t.pos[0] - cur[1], t.pos[2] - cur[2]) < 5:
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

    def poll_pass_number(self, v):
        """How many to pass over (pictures on screen): "0 of 15", then each change."""
        _k, n, own_slot, partner_slot = v
        was = getattr(self, "number_was", None)
        self.number_was = n
        if was == n:
            return
        top = None
        c = self.game.controlled()
        if c:
            mine = self.game.inventory(c[0])
            others = [w for w, _ in self.game.characters() if w != c[0]]
            theirs = self.game.inventory(others[0]) if others else []
            if own_slot < len(mine) and mine[own_slot][0] > 0:
                top = mine[own_slot][1]
            elif partner_slot < len(theirs) and theirs[partner_slot][0] > 0:
                top = theirs[partner_slot][1]
        # (counted towards you: right takes your partner's, left passes yours to them)
        words = "%d of %d" % (abs(n), top) if top else "%d" % abs(n)
        if was is None:
            self.say(words + ". Left and right change it, then A.", interrupt=False)
        else:
            self.say(words, interrupt=True)

    def poll_files(self):
        """The Files page of the item screen is pictures: say the file the cursor is on, and read
        it out when it is opened."""
        fs = self.game.files_screen()
        was = getattr(self, "files_was", None)
        self.files_was = fs
        if fs is None and not getattr(self, "_files_primed", False) and self.scene is not None:
            self._files_primed = self.game.files_owned() is not None   # (found ahead of time)
        if fs is None or fs == was:
            return
        mode, cur = fs
        owned = self.game.files_owned() or []
        item = owned[cur] if 0 <= cur < len(owned) else None
        gd = self.game_dir()
        from . import gmd
        name = None
        if item is not None and gd:
            labels = gmd.archive(gd).get("message_item_name_eng", ({}, []))[0]
            name = R.item_name(labels, item)
        if mode == 6 and (was is None or was[0] != 6):
            pages = gmd.file_pages(gd, item) if item is not None and gd else []
            self.say(" ".join(pages) if pages else "This file can't be read out.", interrupt=True)
            self.file_said = (None, 0.0)
        elif mode != 6 and (was is None or was[1] != cur):
            words = "%s, %d of %d" % (name, cur + 1, len(owned)) if name else "File %d" % (cur + 1)
            last = getattr(self, "file_said", (None, 0.0))
            if words != last[0] or time.monotonic() - last[1] > 2.0:
                self.say(words, interrupt=True)
            self.file_said = (words, time.monotonic())

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
    def poll_controller(self):
        real = self.link.real()
        if not real.get("connected"):
            return
        buttons = real["buttons"]
        pressed = buttons & ~self.pad_held
        self.pad_held = buttons
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
        m = self._alt_meshes.get("floors")
        if m is None:
            try:
                m = NavMesh(self._tris_e + (getattr(self, "_tris_a", None) or []), up_only=True)
            except Exception:
                logging.exception("floors")
                m = False
            self._alt_meshes["floors"] = m
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
            m = self._alt_meshes.get(clearance)
            if m is None:
                try:
                    m = NavMesh(self._walls_e() + self._tris_a, clearance=clearance, up_only=True)
                except Exception:
                    logging.exception("second walk map")
                    return
                self._alt_meshes[clearance] = m
            if m.free_at(x, y, z):
                self.mesh = m
                logging.info("walk map: second one, clearance %d", clearance)
                return

    def _walls_e(self):
        """The main walk map's walls only (its flat tops of furniture would make steps up to
        the second map's high places)."""
        out = []
        for a, b, c in self._tris_e:
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
        hs = self.mesh.heights.get(c, ())
        best = None
        for h in hs:
            if h > gy * MM + 400 or h < gy * MM - 1500:
                continue
            if best is None or abs(h - gy * MM) < abs(best - gy * MM):
                best = h
        return best        # (None: no ground near it on this map: no way there)

    def plan(self, p, goal, gy=None):
        if not self.mesh:
            return []
        y = p[1] * MM
        gyy = self.floor_at(goal[0], goal[1], gy, y)
        if gyy is None:
            if gy is not None and abs(gy * MM - y) > 1500:
                return None
            gyy = y
        way = self.mesh.route((p[0] * MM, y, p[2] * MM), (goal[0], gyy, goal[1]))
        return way

    def start_walk(self, guide=False):
        t = self.current_target()
        if t is None:
            self.say("Pick something in the list first.", interrupt=True)
            return
        self.load_room()
        p, h = self.where_me()
        if not p or h is None:
            self.say("Not in the game.", interrupt=True)
            return
        if getattr(t, "away", False):
            self.say("%s is in another room." % t.name, interrupt=True)
            return
        self.pick_mesh(p)
        goal, face = self.goal_for(t)
        gy = (t.box[0][1] + t.box[1][1]) / 2 if getattr(t, "box", None) else t.pos[1]
        way = self.plan(p, goal, gy)
        self.walk_gy = gy
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
            t.outline = [(x - r, z - r), (x + r, z - r), (x + r, z + r), (x - r, z + r)]
            box = t.box = ((x - r, y, z - r), (x + r, y, z + r))
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
                self.walker.start(t.name, cx, cz, via=via, near=150.0, face=(fx, fz),
                                  reached=None if guide else reached, origin=(p[0] * MM, p[2] * MM))
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
            self.walk_box = (x0 * MM - 600, x1 * MM + 600, z0 * MM - 600, z1 * MM + 600)
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

        def roomy(x, z):
            # (free there and a hand's width round it: the walk mesh misses the smaller things)
            return all(self.mesh.free_at(x + dx, y, z + dz)
                       for dx, dz in ((0, 0), (150, 0), (-150, 0), (0, 150), (0, -150)))
        free = [c for c in cands if roomy(c[0][0], c[0][1])]
        if not free:
            free = [c for c in cands if self.mesh.free_at(c[0][0], y, c[0][1])]
        if not free:
            return None
        me = (p[0] * MM, p[2] * MM)
        free.sort(key=lambda c: math.hypot(c[0][0] - me[0], c[0][1] - me[1]) + c[2] * 3)
        for c in free[:6]:
            way = self.plan(p, c[0])
            if way is not None:
                return c[0], c[1]
        return None

    def reach_fits(self, t, p):
        """True if some way of facing from p (cm) has the game's A reach the thing."""
        x, z = p[0] * MM, p[2] * MM
        return any(self.can_reach(t, x, z, k * math.pi / 24) for k in range(48))

    def reach_facing(self, t, p):
        """The way to face from p (cm) so the game's A reaches the thing: the nearest way to
        straight at its middle that does."""
        x, z = p[0] * MM, p[2] * MM
        base = math.atan2(t.pos[0] * MM - x, t.pos[2] * MM - z)
        for k in range(0, 25):
            for sign in ((1,) if k == 0 else (1, -1)):
                a = base + sign * k * math.pi / 24
                if self.can_reach(t, x, z, a):
                    return a
        return base

    def can_reach(self, t, x, z, rot):
        """True if the point the game's A looks at (from x, z mm facing rot) is in the thing's outline."""
        fx, fz = x + math.sin(rot) * self.REACH, z + math.cos(rot) * self.REACH
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
        t = getattr(self, "walk_target", None)
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
            if getattr(self, "walk_spot", None) and t is not None:
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
        elif res in ("replan", "bump", "blocked", "gave up") and getattr(self, "walk_spot", None) \
                and t is not None and self.reach_fits(t, p):
            # (walked into something right by the thing: turning on the spot reaches it)
            self.walker.stop()
            self.turning = (self.reach_facing(t, p), time.monotonic() + 3.0, t)
            return
        elif res in ("replan", "bump"):
            goal = (self.walker.goal[1], self.walker.goal[2])
            way = self.plan(p, goal, getattr(self, "walk_gy", None))
            if way:
                self.walker.reroute(way[:-1], way[-1][0], way[-1][1], (p[0] * MM, p[2] * MM))
        elif res in ("blocked", "gave up"):
            self.stop_walk("Something is in the way. " + (self.thing_words(t) if t else ""))
