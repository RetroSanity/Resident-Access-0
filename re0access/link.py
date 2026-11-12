"""The shared memory block that the controller add-on (xinput1_3.dll in the
game folder) and this companion use to talk to each other."""
import mmap
import struct
import ctypes

NAME = "Local\\ResidentAccess0Pad"
SIZE = 0x10000
MAGIC = 0x41444852
PADFMT = "<IIIIHBBhhhhIHHIhhIIBBHI"
RING_OFF, RING_N, TEXT_MAX = 0x100, 48, 1000
RECSZ = 16 + TEXT_MAX + 12

_k32 = ctypes.WinDLL("kernel32")
_k32.GetTickCount.restype = ctypes.c_uint32

# XInput button bits
UP, DOWN, LEFT, RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
START, BACK, L3, R3 = 0x0010, 0x0020, 0x0040, 0x0080
LB, RB = 0x0100, 0x0200
A, B, X, Y = 0x1000, 0x2000, 0x4000, 0x8000


class Link:
    def __init__(self):
        self.m = mmap.mmap(-1, SIZE, tagname=NAME)
        if struct.unpack_from("<I", self.m, 0)[0] != MAGIC:
            struct.pack_into("<II", self.m, 0, MAGIC, 1)
        self.last_text = struct.unpack_from("<I", self.m, RING_OFF + 4)[0]
        self.block = 0
        self.add = 0
        self.stick = None
        self.block_stick = False
        self.block_all = False
        self.rt = 0

    # -- controller
    def real(self):
        v = struct.unpack_from(PADFMT, self.m, 0)
        return {"polls": v[3], "buttons": v[4], "lt": v[5], "rt": v[6], "lx": v[7], "ly": v[8],
                "rx": v[9], "ry": v[10], "connected": v[11], "tick": v[22]}

    def sent_buttons(self):
        """Buttons being added for the game through the add-on right now (by the mod or a helper)."""
        return struct.unpack_from("<H", self.m, 34)[0]

    def game_alive(self):
        """True if the game has read the controller in the last two seconds."""
        tick = struct.unpack_from("<I", self.m, 56)[0]
        return tick and (_k32.GetTickCount() - tick) & 0xFFFFFFFF < 2000

    def active(self):
        return bool(self.block or self.add or self.stick or self.block_stick or self.block_all or self.rt)

    def push(self):
        """Send what the game should see. Call this every loop. While the mod is
        not changing anything it leaves the block alone (it then times out and
        the game sees the controller as it is)."""
        st = self.stick
        on = self.active()
        if not on and not getattr(self, "_was_on", False):
            return
        self._was_on = on
        struct.pack_into("<I", self.m, 8, _k32.GetTickCount())
        struct.pack_into("<HHIhhIIBB", self.m, 32, self.block & 0xFFFF, self.add & 0xFFFF,
                         1 if st else 0, int(st[0]) if st else 0, int(st[1]) if st else 0,
                         1 if self.block_stick else 0, 1 if self.block_all else 0, self.rt, 0)

    def release(self):
        self.block = self.add = 0
        self.stick = None
        self.block_stick = self.block_all = False
        self.rt = 0
        self.push()

    # -- text
    def hooked(self):
        return struct.unpack_from("<I", self.m, RING_OFF + 8)[0]

    def addon_build(self):
        """The mod version the game's controller add-on came with (54 for 0.54), 0 if older."""
        return struct.unpack_from("<I", self.m, RING_OFF + 12)[0]

    def texts(self):
        """New strings the game has laid out: [(caller, holder, text)]."""
        write = struct.unpack_from("<I", self.m, RING_OFF + 4)[0]
        if write < self.last_text:          # the game restarted
            self.last_text = 0
        out = []
        for n in range(max(self.last_text + 1, write - RING_N + 1, 1), write + 1):
            off = RING_OFF + 16 + (n % RING_N) * RECSZ
            seq, caller, tick, ln = struct.unpack_from("<IIII", self.m, off)
            if seq != n:
                continue
            raw = bytes(self.m[off + 16: off + 16 + min(ln, TEXT_MAX - 1)])
            holder = struct.unpack_from("<I", self.m, off + 16 + TEXT_MAX)[0]
            out.append((caller, holder, raw.decode("utf-8", "replace")))
        self.last_text = write
        return out
