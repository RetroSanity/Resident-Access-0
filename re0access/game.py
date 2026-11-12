"""Where things are in the game's memory (Resident Evil 0 HD Remaster, Steam).

RE0 HD runs on Capcom's MT Framework. The exe on disk is encrypted (SteamStub), so these were
found in the running game. Each MT object starts with its class's vtable, which is how the
player and other objects are recognised.
"""
import ctypes
import struct

from .mem import NotReady, k32

# Static pointers in the exe's data (re0hd.exe, Steam build of 2016).
GAME_WORK = 0x00DCC010        # -> the game's state; +0x24FB0 the scene (room) number
SCENE_NO = 0x24FB0
GAME_PAD = 0x00E2D7C8         # -> sGamePad
GAME_CHARA = 0x00DCC0D0       # -> sGameChara: +0x50 a list of nodes (16 bytes, the object at +0xC)
GAME_UNIT = 0x00E2D434        # -> sGameUnit
SCENE_INFO = 0x00DCBF40       # -> sSceneInfo
XINPUT_GETSTATE_IAT = 0x00CB13B0

INVENTORY = 0x00DCBF44         # -> the characters' items: Rebecca's 6 slots at +0x24, Billy's at +0x64,
INV_SLOTS = {"Rebecca": 0x24, "Billy": 0x64}   # each slot (item number, amount) as two 32-bit numbers

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

# Object classes (their vtables)
VT_REBECCA = 0x00CCFD10       # uPlayerRebecca
VT_BILLY = 0x00CCF5A0         # uPlayerBilly
PLAYERS = {VT_REBECCA: "Rebecca", VT_BILLY: "Billy"}
POS = 0x40                    # in any unit: x, y, z (floats, centimetres)
MATRIX = 0x70                 # in any unit: its world matrix, 4 rows of 4 floats (the last row is the position)

# Scenes are numbered from 0 for the whole game; each stage (area) has a range of them.
# (From the exe: the stage table "tr", "sh", "dm", "ch", "bt", "pl", "dt", "ds", "dd", "dc" and
# the first scene number of each.)
STAGE_FIRST = [0, 0x24, 0x56, 0x63, 0x84, 0x8D, 0xAF, 0xCD, 0xE7, 0xEF, 0x112, 0x163]
STAGE_CODES = ["tr", "sh", "dm", "ch", "bt", "pl", "dt", "ds", "dd", "dc", "", ""]
STAGE_NAMES = {"tr": "Train", "sh": "Training Facility", "dm": "Area dm", "ch": "Area ch",
               "bt": "Area bt", "pl": "Area pl"}

# Room names, by their code ("tr07"): filled in as each room is checked in the game.
ROOM_NAMES = {}


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
        """The scene (room) number, or None when there is no game going."""
        try:
            gw = self.mem.ptr(GAME_WORK)
            no = self.mem.u32(gw + SCENE_NO)
        except NotReady:
            return None
        return no if 0 <= no < 0x200 else None

    def characters(self):
        """[(name, object)] for Rebecca and Billy as far as they are in the game."""
        out = []
        try:
            sc = self.mem.ptr(GAME_CHARA)
            data = self.mem.read(sc + 0x50, 16 * 16)
        except NotReady:
            return out
        for k in range(16):
            obj = struct.unpack_from("<I", data, k * 16 + 12)[0]
            if not obj:
                continue
            try:
                vt = self.mem.u32(obj)
            except NotReady:
                continue
            if vt in PLAYERS:
                out.append((PLAYERS[vt], obj))
        return out

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

    def inventory(self, who="Rebecca"):
        """[(item number, amount)] for the six slots (0 where empty)."""
        try:
            base = self.mem.ptr(INVENTORY)
            data = self.mem.read(base + INV_SLOTS[who], 48)
        except NotReady:
            return []
        return [struct.unpack_from("<ii", data, k * 8) for k in range(6)]

    def pos(self, obj):
        try:
            return struct.unpack("<3f", self.mem.read(obj + POS, 12))
        except NotReady:
            return None
