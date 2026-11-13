"""Where things are in the game's memory (Resident Evil 0 HD Remaster, Steam).

RE0 HD runs on Capcom's MT Framework. The exe on disk is encrypted (SteamStub), so these were
found in the running game. Each MT object starts with its class's vtable, which is how the
player and other objects are recognised.
"""
import ctypes
import math
import struct
import time

from .mem import NotReady, k32

# Static pointers in the exe's data (re0hd.exe, Steam build of 2016).
GAME_WORK = 0x00DCC010        # -> the game's state (sArrange); +0x24FB0 the scene (room) number
# In it, from +0x28: every item and enemy placed in the game (as in nativePC/arc/arrange/loctbl.arc
# for the difficulty played), then free places for items you drop. 0x5C bytes each: +0 the one
# before in its room (-1 for the first), +4 the scene, +0xC the kind (0x71 an item), +0x10 the item,
# +0x14 a flag (0xA0 and on: an extra for finishing the game), +0x20 how many were put there, +0x28 where (x, y, z, in the room), +0x44 how many are there now.
ARRANGE_FIRST = 0x28
ARRANGE_SIZE = 0x5C
ARRANGE_COUNT = 1553
SCENE_NO = 0x24FB0
GAME_PAD = 0x00E2D7C8         # -> sGamePad
GAME_CHARA = 0x00DCC0D0       # -> sGameChara: +0x50 a list of nodes (16 bytes, the object at +0xC)
GAME_UNIT = 0x00E2D434        # -> sGameUnit
IN_GAME_SYSTEM = 0x00DCFFC0   # -> sInGameSystem: +0x58 -> sRoomControl: +0x50 -> the room's archive
                              # (its path at +0xC, "room\\tr\\tr02")
SCENE_INFO = 0x00DCBF40       # -> sSceneInfo
XINPUT_GETSTATE_IAT = 0x00CB13B0

INVENTORY = 0x00DCBF44         # -> the characters' items: Rebecca's 6 slots at +0x24, Billy's at +0x64,
INV_SLOTS = {"Rebecca": 0x24, "Billy": 0x64}   # each slot (item number, amount) as two 32-bit numbers
INV_EQUIPPED = {"Rebecca": 0x5C, "Billy": 0x9C}    # the slot of the weapon in hand (-1 for none)

TITLE_HOLDER = 0x00DCF688      # [[[this]+0x20]+0x14] -> the title screen object (uGUITitle) while it exists
VT_TITLE = 0x00CE2A08
TITLE_STATE = 0x24C           # in it: which screen: 3 Start Game / Quit, 8 the main menu (16 when come back
                              # to it), 6 difficulty, 11 Game Setup, 14 a message box
# screen -> (where its cursor is, the choices top to bottom)
TITLE_MENUS = {3: (0x288, ["Start Game", "Quit"]),
               8: (0x288, ["Load Game", "New Game", "Options", "Extras"]),
               16: (0x288, ["Load Game", "New Game", "Options", "Extras"]),
               6: (0x2A0, ["Easy", "Normal", "Hard"])}

TITLE_SETUP = 11              # the title screen object's state while Game Setup is up
VT_SETUP = 0x00CDF930         # uGUISetUpEng: +0x26C the row; +0x270, +0x274, +0x278 each row's choice
SETUP_ROWS = [("Display", ["Wide", "Original"]), ("Control Type", ["Alternate", "Original"]),
              ("Subtitles", ["On", "Off"])]

TITLE_LOAD = 15               # the title screen object's state while Load Game's slots are up
VT_SAVE = 0x00CE07E0          # uGUISave: +0x3D0 the slot picked (from 0), +0x3D4 its row on screen (0-5)

# Object classes (their vtables)
VT_REBECCA = 0x00CCFD10       # uPlayerRebecca
VT_BILLY = 0x00CCF5A0         # uPlayerBilly
PLAYERS = {VT_REBECCA: "Rebecca", VT_BILLY: "Billy"}
MOTION = 0x4A4               # in a character: the motion playing (low 16 bits): 0x2A0 raising the
                              # weapon, 0x2AB holding it up, 0x2AE lowering it (Rebecca, handgun)
VT_SUBMENU = 0x00CE2270       # uGUISubMenu, the item screen (X): +0x2B4 the slot the cursor is on
                              # (0-5 your items), +0x294 2 while it is open, +0x2AC 0 while it is open
SUBMENU_CURSOR = 0x2B4
SUBMENU_HOLDER = 0x00DCEBD0   # -> sSubMenu: +0x60 the uGUISubMenu
VT_FLAGS = 0x00CD9184         # sFlagManager: the game's flags; the files you have from bit 979 (item 150,
                              # Player's Manual 1) on, one bit each
FILE_BIT = 979
VT_FILE = 0x00CDA970          # uGUIFile, a file read as you pick it up ([sSubMenu]+0x5C while
                              # open): +0x2E8 the page
VT_INGAME_MSG = 0x00CDE2E8     # uGUIInGameMessage, the message window: +0x284 4 while a question's answers
                              # are up, +0x2A0 the answer picked (0 Yes, 1 No, 2 Use)
VT_DIALOG = 0x00CDA380        # uGUIDialog, the Start menu's "Quit?" question: +0x268 0 Yes, 1 No
VT_PAUSE = 0x00CDFE68         # uGUIPause, the Start menu in play: +0x260 3 while open (4 with its
                              # "Quit?" question up, 0 shut), +0x268 the row (0 Back, 1 Quit Game)
SUBMENU_OPEN = 0x294
THINK = 0x67B8                # in a character: its "think" (cPlayerThink while you control it)
VT_THINK = 0x00CCBBE8         # cPlayerThink
HEALTH = 0x1030              # in a character: health (32-bit); full is 150
HEALTH_FULL = 150
CHARA_LIST = 0x20             # in sGameChara: the first node of its list of characters (16 bytes: class,
                              # next node, previous node, the character); enemies are in it too
ROOM_OFFSET = 0x50            # in sSceneInfo: where the room is (x, y, z); places in a room's files are
                              # from there
# Enemy classes (uEnemyXX, the number as in the game's location tables) by their vtable.
ENEMY_VTS = {0xCBDCD8: 0x10, 0xCC4F28: 0x11, 0xCC50A0: 0x12, 0xCC5218: 0x14, 0xCC5390: 0x15, 0xCC5508: 0x16,
             0xCC5680: 0x17, 0xCC57F8: 0x19, 0xCC5970: 0x1A, 0xCC3FE8: 0x2A, 0xCBF0D0: 0x2B, 0xCBF5F8: 0x2C,
             0xCBF9A0: 0x2F, 0xCBFBC8: 0x30, 0xCBFE58: 0x31, 0xCC01A8: 0x34, 0xCC0458: 0x35, 0xCC08C0: 0x37,
             0xCC5AE8: 0x38, 0xCC0CB0: 0x39, 0xCC13D8: 0x3A, 0xCC1618: 0x3B, 0xCC18E8: 0x3C, 0xCC5C60: 0x3E,
             0xCC1CE8: 0x40, 0xCC1E60: 0x41, 0xCC1FD8: 0x42, 0xCC25D8: 0x43, 0xCC2B38: 0x44, 0xCC2F70: 0x45,
             0xCC3248: 0x46, 0xCC3710: 0x47, 0xCC5DD8: 0x50, 0xCC5F50: 0x51, 0xCC60C8: 0x5A, 0xCC6240: 0x5B,
             0xCC63B8: 0x6A, 0xCC6530: 0x6B}
ZOMBIES = {0x10, 0x11, 0x12, 0x14, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x50, 0x51, 0x5A, 0x5B, 0x6A, 0x6B}
ENEMY_NAMES = {0x2A: "Cerberus", 0x31: "Stinger", 0x40: "Leech", 0x41: "Leech", 0x42: "Leech"}


def enemy_name(kind):
    if kind in ZOMBIES:
        return "Zombie"
    return ENEMY_NAMES.get(kind, "Enemy")


POS = 0x40                    # in any unit: x, y, z (floats, centimetres)
MATRIX = 0x70                 # in any unit: its world matrix, 4 rows of 4 floats (the last row is the position)

# Scenes are numbered from 0 for the whole game; each stage (area) has a range of them.
# (From the exe: the stage table "tr", "sh", "dm", "ch", "bt", "pl", "dt", "ds", "dd", "dc" and
# the first scene number of each.)
STAGE_FIRST = [0, 0x24, 0x56, 0x63, 0x84, 0x8D, 0xAF, 0xCD, 0xE7, 0xEF, 0x112, 0x163]
STAGE_CODES = ["tr", "sh", "dm", "ch", "bt", "pl", "dt", "ds", "dd", "dc", "", ""]
STAGE_NAMES = {"tr": "Train", "sh": "Training Facility", "dm": "Facility Basement", "ch": "Laboratory",
               "bt": "Factory", "pl": "Treatment Plant"}

# Room names, by their code ("tr07"): filled in as each room is checked in the game.
ROOM_NAMES = {
    # The train (names from the game's map, matched by which rooms each room's doors lead to;
    # tr10 and up are the same cars later in the story)
    "tr00": "Engine Cab", "tr01": "Engine Carriage", "tr02": "Officer's Car", "tr03": "Conductor's Office",
    "tr04": "Cabin 201", "tr05": "Cabin 202", "tr06": "Passenger Car 2", "tr07": "Passenger Car 1",
    "tr08": "Dining Car", "tr09": "Freight Car", "tr0a": "Rear Carriage", "tr0b": "Bar Lounge",
    "tr0c": "Upstairs Passenger Car", "tr0d": "Cabin 101", "tr0e": "Cabin 102", "tr0f": "Restaurant Car",
    "tr11": "Service Room", "tr10": "Officer's Car", "tr15": "Dining Car", "tr16": "Bar Lounge",
    "tr18": "Upstairs Passenger Car", "tr1a": "Rear Carriage", "tr1b": "Officer's Car", "tr1c": "Cabin 201",
    "tr1d": "Cabin 202", "tr1e": "Passenger Car 2", "tr1f": "Passenger Car 1", "tr20": "Upstairs Passenger Car",
    "tr21": "Restaurant Car", "tr22": "Dining Car",
    # The Training Facility (the rooms its things and doors make certain; sh0a and up are the
    # same rooms later in the story)
    "sh00": "Main Hall", "sh01": "Dining Room", "sh02": "Kitchen", "sh03": "Projection Room",
    "sh04": "West Wing Corridor", "sh05": "Bathroom", "sh06": "Boiler Room", "sh08": "Storage Room",
    "sh0b": "Art Room", "sh0c": "Art Corridor", "sh0e": "Main Library", "sh0f": "Assistant Director's Office",
    "sh1f": "Sewer Passage", "sh0a": "Main Hall", "sh29": "Main Hall", "sh22": "Art Room", "sh23": "Art Room",
    "sh24": "Art Room", "sh25": "Assistant Director's Office", "sh2e": "Main Library", "sh30": "Main Library",
    "sh31": "Main Library",
}


def scene_code(no):
    """(stage code, room number in the stage) for a scene number, e.g. 7 -> ("tr", 7)."""
    stage = 0
    while stage + 1 < len(STAGE_FIRST) and STAGE_FIRST[stage + 1] and no >= STAGE_FIRST[stage + 1]:
        stage += 1
    return STAGE_CODES[stage], no - STAGE_FIRST[stage]


def scene_words(no):
    code, n = scene_code(no)
    key = "%s%02x" % (code, n)
    if key in ROOM_NAMES:
        return ROOM_NAMES[key]
    return "%s, room %d" % (STAGE_NAMES.get(code, "Area " + " ".join(code.upper())), n)


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.c_ulong), ("PartitionId", ctypes.c_ushort),
                ("RegionSize", ctypes.c_size_t), ("State", ctypes.c_ulong),
                ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong)]


k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def regions(mem, max_size=0x10000000):
    """The game's writable memory: [(start, size)]."""
    addr, out, m = 0x10000, [], _MBI()
    while addr < 0x7FFF0000:
        if not k32.VirtualQueryEx(mem.h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(m)):
            break
        base = m.BaseAddress or 0
        if m.State == 0x1000 and m.Protect in (0x04, 0x08) and m.RegionSize <= max_size:
            out.append((base, m.RegionSize))
        addr = base + m.RegionSize
    return out


class Game:
    def __init__(self, mem):
        self.mem = mem
        self.pad_fixed_for = None

    # -- the controller ----------------------------------------------------------------------
    def fix_pad(self):
        """Steam's overlay hooks the game's XInput, and with Steam Input on it answers for the
        controller itself, so the game never reaches our add-on. Point the game's import of
        XInputGetState at a copy of the add-on's own start instead (it then reads the
        controller as it is and adds what the mod sends). Returns a word on what happened."""
        m = self.mem
        if self.pad_fixed_for == m.pid:
            return "done"
        try:
            cur = m.u32(XINPUT_GETSTATE_IAT)
            head = m.read(cur, 7)
        except NotReady:
            return "not ready"
        if head == b"\x55\x57\x56\x53\x83\xEC\x3C":
            try:
                if m.read(cur + 7, 1) == b"\xE9":
                    self.pad_fixed_for = m.pid          # our copy is already in place
                    return "done"
            except NotReady:
                pass
            # not hooked (yet): Steam's overlay hooks a few seconds after the game starts
            return "not hooked"
        if head[:1] != b"\xE9" or head[5:7] != b"\xEC\x3C":
            # someone else's code, or already pointed at our copy
            try:
                if m.read(cur, 7) == b"\x55\x57\x56\x53\x83\xEC\x3C":
                    self.pad_fixed_for = m.pid
                    return "done"
            except NotReady:
                pass
            self.pad_fixed_for = m.pid
            return "unknown %s" % head.hex()
        cave = k32.VirtualAllocEx(m.h, None, 0x1000, 0x3000, 0x40)
        if not cave:
            return "no memory"
        code = b"\x55\x57\x56\x53\x83\xEC\x3C" + b"\xE9" + struct.pack("<i", (cur + 7) - (cave + 12))
        if not m.write(cave, code):
            return "write failed"
        old = ctypes.c_ulong()
        k32.VirtualProtectEx(m.h, XINPUT_GETSTATE_IAT, 4, 0x04, ctypes.byref(old))
        ok = m.write(XINPUT_GETSTATE_IAT, struct.pack("<I", cave))
        k32.VirtualProtectEx(m.h, XINPUT_GETSTATE_IAT, 4, old.value, ctypes.byref(old))
        if ok:
            self.pad_fixed_for = m.pid
            return "fixed"
        return "write failed"

    # -- where you are -----------------------------------------------------------------------
    def scene(self):
        """The scene (room) number, or None when there is no game going. From the room archive
        the game has loaded ("room\\tr\\tr02" is the train's room 2, scene 2)."""
        try:
            rc = self.mem.u32(self.mem.ptr(IN_GAME_SYSTEM) + 0x58)
            arc = self.mem.u32(rc + 0x50)
            if arc:
                path = self.mem.read(arc + 0xC, 40).split(b"\0")[0].decode("ascii", "replace")
                code = path.split("\\")[-1]
                if len(code) == 4 and code[:2] in STAGE_CODES:
                    st = STAGE_CODES.index(code[:2])
                    return STAGE_FIRST[st] + int(code[2:], 16)
        except (NotReady, ValueError):
            pass
        try:
            gw = self.mem.ptr(GAME_WORK)
            no = self.mem.u32(gw + SCENE_NO)
        except NotReady:
            return None
        return no if 0 <= no < 0x200 else None

    def units(self):
        """[(vtable, object)] of the characters in the room: Rebecca, Billy, enemies, weapons."""
        out = []
        seen = set()
        try:
            node = self.mem.ptr(GAME_CHARA) + CHARA_LIST
            for _ in range(96):
                if node in seen:
                    break
                seen.add(node)
                nxt, _prev, obj = struct.unpack("<III", self.mem.read(node + 4, 12))
                if obj and obj not in seen:
                    seen.add(obj)
                    try:
                        out.append((self.mem.u32(obj), obj))
                    except NotReady:
                        pass
                if not nxt or nxt == node:
                    break
                node = nxt
        except NotReady:
            pass
        return out

    def characters(self):
        """[(name, object)] for Rebecca and Billy as far as they are in the game."""
        return [(PLAYERS[vt], obj) for vt, obj in self.units() if vt in PLAYERS]

    def controlled(self):
        """(name, object) of the character you control, or None."""
        chars = self.characters()
        if len(chars) <= 1:
            return chars[0] if chars else None
        for name, obj in chars:
            try:
                if self.mem.u32(self.mem.u32(obj + THINK)) == VT_THINK:
                    return name, obj
            except NotReady:
                pass
        return chars[0]

    def motion(self, obj):
        try:
            return self.mem.u32(obj + MOTION) & 0xFFFF
        except NotReady:
            return None

    _pause = None

    def pause_menu(self):
        """(state, row) of the Start menu, or None. State 3 is open on its rows."""
        try:
            pa = self._pause
            if not pa or self.mem.u32(pa) != VT_PAUSE:
                pa = self._pause = self.find_cached(VT_PAUSE, "_pause_tried")
                if not pa:
                    return None
            state = self.mem.u32(pa + 0x260)
            if state == 3 and not self._dialog:
                self._dialog = self.find_cached(VT_DIALOG, "_dialog_tried")  # (found ahead of the question)
            return state, self.mem.u32(pa + 0x268)
        except NotReady:
            return None

    _dialog = None
    _flags = None

    def files_owned(self):
        """[item numbers] of the files you have (150 and on), in order, or None if not known."""
        try:
            f = self._flags
            if not f or self.mem.u32(f) != VT_FLAGS:
                f = self._flags = self.find_cached(VT_FLAGS, "_flags_tried")
                if not f:
                    return None
            data = self.mem.read(f + FILE_BIT // 8, 8)
        except NotReady:
            return None
        bits = int.from_bytes(data, "little") >> (FILE_BIT % 8)
        return [150 + k for k in range(31) if bits >> k & 1]

    def file_reader(self):
        """The page (from 0, the title) of the file being read as you pick it up, or None."""
        try:
            f = self.mem.u32(self.mem.ptr(SUBMENU_HOLDER) + 0x5C)
            if not f or self.mem.u32(f) != VT_FILE:
                return None
            return self.mem.u32(f + 0x2E8)
        except NotReady:
            return None

    _msg = None

    def message_choice(self):
        """The cursor (0, 1, 2: Yes, No, Use) on a question in the message window, or None when no
        question is up."""
        try:
            m = self._msg
            if not m or self.mem.u32(m) != VT_INGAME_MSG:
                m = self._msg = self.find_cached(VT_INGAME_MSG, "_msg_tried")
                if not m:
                    return None
            if self.mem.u32(m + 0x284) != 4:
                return None
            cur = self.mem.u32(m + 0x2A0)
        except NotReady:
            return None
        return cur if 0 <= cur < 4 else None

    def files_screen(self):
        """(mode, cursor) on the item screen's Files page (mode 3 the list, 6 reading one), or None."""
        try:
            sub = self.mem.u32(self.mem.ptr(SUBMENU_HOLDER) + 0x60)
            if not sub or self.mem.u32(sub) != VT_SUBMENU:
                return None
            if self.mem.u32(sub + SUBMENU_OPEN) not in (2, 3) or self.mem.u32(sub + 0x290) != 4:
                return None
            return self.mem.u32(sub + 0x2A0), self.mem.u32(sub + 0x39C)
        except NotReady:
            return None

    def quit_answer(self):
        """On the Start menu's "Quit?" question: 0 with the cursor on Yes, 1 on No (None unknown)."""
        try:
            d = self._dialog
            if not d or self.mem.u32(d) != VT_DIALOG:
                d = self._dialog = self.find_cached(VT_DIALOG, "_dialog_tried")
                if not d:
                    return None
            v = self.mem.u32(d + 0x268)
        except NotReady:
            return None
        return v if v in (0, 1) else None

    def find_cached(self, vt, stamp):
        """find_object in the background (it reads all the game's memory, which takes a few
        seconds): None until it has been found; tried again at most every 30 seconds."""
        found = getattr(self, stamp + "_found", None)
        if found:
            setattr(self, stamp + "_found", None)
            return found
        now = time.monotonic()
        if getattr(self, stamp + "_busy", False) or now - getattr(self, stamp, -999.0) < 30.0:
            return None
        setattr(self, stamp, now)
        setattr(self, stamp + "_busy", True)

        def run():
            try:
                setattr(self, stamp + "_found", self.find_object(vt))
            except Exception:
                pass
            setattr(self, stamp + "_busy", False)
        import threading
        threading.Thread(target=run, daemon=True).start()
        return None


    def item_cursor(self):
        """The slot the cursor is on in the item screen (0-5), or None when it is not open."""
        try:
            sub = self.mem.u32(self.mem.ptr(SUBMENU_HOLDER) + 0x60)
            if not sub or self.mem.u32(sub) != VT_SUBMENU:
                return None
            if self.mem.u32(sub + SUBMENU_OPEN) != 2 or self.mem.u32(sub + 0x2AC) != 0:
                return None
            cur = self.mem.u32(sub + SUBMENU_CURSOR)
        except NotReady:
            return None
        return cur if 0 <= cur < 6 else None

    def enemies(self):
        """[(object, kind, (x, y, z) in the room, health)] for the enemies in the room."""
        out = []
        off = self.room_offset()
        for vt, obj in self.units():
            kind = ENEMY_VTS.get(vt)
            if kind is None:
                continue
            try:
                x, y, z = struct.unpack("<3f", self.mem.read(obj + POS, 12))
                hp = self.mem.s32(obj + HEALTH)
            except NotReady:
                continue
            if off:
                x, y, z = x - off[0], y - off[1], z - off[2]
            out.append((obj, kind, (x, y, z), hp))
        return out

    def placed_items(self, scene):
        """[(index, item, amount, (x, y, z))] of the items lying in a room now (dropped ones too)."""
        try:
            gw = self.mem.ptr(GAME_WORK)
            data = self.mem.read(gw + ARRANGE_FIRST, ARRANGE_SIZE * ARRANGE_COUNT)
        except NotReady:
            return None
        out = []
        for k in range(ARRANGE_COUNT):
            o = k * ARRANGE_SIZE
            room, _r2, kind, item, flag = struct.unpack_from("<iiIII", data, o + 4)
            if kind != 0x71 or room != scene or 0xA0 <= flag <= 0xAF:
                continue        # (0xA0 and on: the extras unlocked by finishing the game)
            put = struct.unpack_from("<i", data, o + 0x20)[0]
            now = struct.unpack_from("<i", data, o + 0x44)[0]
            x, y, z = struct.unpack_from("<3f", data, o + 0x28)
            out.append((k, item, now, put, (x, y, z)))
        return out

    def room_offset(self):
        try:
            return struct.unpack("<3f", self.mem.read(self.mem.ptr(SCENE_INFO) + ROOM_OFFSET, 12))
        except NotReady:
            return None

    def local_pos(self, obj):
        """Where a character is in the room (x, y, z, centimetres), as the room's files count."""
        p = self.pos(obj)
        off = self.room_offset()
        if not p or not off:
            return None
        return p[0] - off[0], p[1] - off[1], p[2] - off[2]

    def heading(self, obj):
        """The way a character faces, in radians (atan2 of x and z; more is further left)."""
        try:
            m = struct.unpack("<12f", self.mem.read(obj + MATRIX, 48))
        except NotReady:
            return None
        return math.atan2(m[8], m[10])

    def title(self):
        """The title screen object, or None (in the game)."""
        m = self.mem
        try:
            t = m.ptr(m.ptr(m.ptr(TITLE_HOLDER) + 0x20) + 0x14)
            if m.u32(t) == VT_TITLE:
                return t
        except NotReady:
            pass
        return None

    def title_menu(self):
        """(screen, cursor, the choice's words) on a title menu, or None."""
        t = self.title()
        if not t:
            return None
        try:
            state = self.mem.u32(t + TITLE_STATE)
            if state not in TITLE_MENUS:
                return (state, None, None)
            off, items = TITLE_MENUS[state]
            cur = self.mem.u32(t + off)
        except NotReady:
            return None
        if not 0 <= cur < len(items):
            return (state, cur, None)
        return (state, cur, items[cur])

    def find_object(self, vt, max_region=0x10000000):
        """The first object of a class (by its vtable) found in the game's memory, or None. Slow
        (it reads all of the game's data): callers keep what it finds."""
        pat = struct.pack("<I", vt)
        for base, size in regions(self.mem, max_region):  # noqa
            try:
                data = self.mem.read(base, size)
            except NotReady:
                continue
            i = data.find(pat)
            while i >= 0:
                if i % 4 == 0:
                    return base + i
                i = data.find(pat, i + 1)
        return None

    _setup = None

    def setup(self):
        """(row, [choice of each row]) on the Game Setup screen, or None."""
        t = self.title()
        if not t:
            return None
        try:
            if self.mem.u32(t + TITLE_STATE) != TITLE_SETUP:
                return None
            su = self._setup
            if not su or self.mem.u32(su) != VT_SETUP:
                su = self._setup = self.find_object(VT_SETUP)
            if not su:
                return None
            row = self.mem.u32(su + 0x26C)
            vals = [self.mem.u32(su + 0x270 + 4 * k) for k in range(3)]
        except NotReady:
            return None
        if not 0 <= row < 3 or any(not 0 <= v < 2 for v in vals):
            return None
        return row, vals

    _save = None

    def save_row(self):
        """The slot picked on the save / load list (from 0), or None."""
        try:
            sv = self._save
            if not sv or self.mem.u32(sv) != VT_SAVE:
                sv = self._save = self.find_cached(VT_SAVE, "_save_tried") if self.title() is None \
                    else self.find_object(VT_SAVE)
            if not sv:
                return None
            row = self.mem.u32(sv + 0x3D0)
        except NotReady:
            return None
        return row if 0 <= row < 100 else None

    def inventory(self, who="Rebecca"):
        """[(item number, amount)] for the six slots (0 where empty)."""
        try:
            base = self.mem.ptr(INVENTORY)
            data = self.mem.read(base + INV_SLOTS[who], 48)
        except NotReady:
            return []
        return [struct.unpack_from("<ii", data, k * 8) for k in range(6)]

    def equipped(self, who="Rebecca"):
        """The slot (0-5) of the weapon the character has equipped, or -1 for none (None unknown)."""
        try:
            v = self.mem.s32(self.mem.ptr(INVENTORY) + INV_EQUIPPED[who])
        except NotReady:
            return None
        return v if -1 <= v < 6 else None

    def health(self, obj):
        """(health, full) of a character, or None."""
        try:
            hp = self.mem.u32(obj + HEALTH)
        except NotReady:
            return None
        if hp > 0x7FFFFFFF:
            hp = 0
        return hp, HEALTH_FULL

    def pos(self, obj):
        try:
            return struct.unpack("<3f", self.mem.read(obj + POS, 12))
        except NotReady:
            return None
