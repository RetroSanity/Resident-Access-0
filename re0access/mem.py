"""Reading (and, for the controller fix, writing) the memory of the running game (re0hd.exe)."""
import ctypes
import struct
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
k32.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD)]
k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


class NotReady(Exception):
    """The game is not running, or the thing asked for is not there right now."""


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


def find_pid(name=b"re0hd.exe"):
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    e = _PE()
    e.dwSize = ctypes.sizeof(_PE)
    ok = k32.Process32First(snap, ctypes.byref(e))
    pid = None
    while ok:
        if e.szExeFile.lower() == name:
            pid = e.th32ProcessID
            break
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return pid


class Memory:
    def __init__(self):
        self.h = None
        self.pid = None

    def connect(self):
        if self.h and self.alive():
            return True
        self.h = None
        pid = find_pid()
        if not pid:
            return False
        h = k32.OpenProcess(0x0438, False, pid)      # query, read, write, operation (controller fix)
        if not h:
            h = k32.OpenProcess(0x0410, False, pid)  # read only
        if not h:
            return False
        self.h, self.pid = h, pid
        return True

    def age(self):
        """Seconds since the game was started (0 if not known)."""
        import time as _t
        ft = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not k32.GetProcessTimes(self.h, *[ctypes.byref(f) for f in ft]):
                return 0.0
        except Exception:
            return 0.0
        created = (ft[0].dwHighDateTime << 32 | ft[0].dwLowDateTime) / 1e7 - 11644473600.0
        return max(0.0, _t.time() - created)

    def exe_path(self):
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(self.h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None

    def alive(self):
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(self.h, ctypes.byref(code))) and code.value == 259

    def read(self, addr, size):
        if not self.h or addr < 0x10000 or addr > 0x7FFF0000:
            raise NotReady("bad address %08X" % addr)
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)) or got.value != size:
            raise NotReady("read failed at %08X" % addr)
        return buf.raw

    def write(self, addr, data):
        got = ctypes.c_size_t()
        return bool(k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(got)))

    def u8(self, a): return self.read(a, 1)[0]
    def u16(self, a): return struct.unpack("<H", self.read(a, 2))[0]
    def s16(self, a): return struct.unpack("<h", self.read(a, 2))[0]
    def u32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
    def s32(self, a): return struct.unpack("<i", self.read(a, 4))[0]
    def f32(self, a): return struct.unpack("<f", self.read(a, 4))[0]

    def ptr(self, a):
        p = self.u32(a)
        if p < 0x10000 or p > 0x7FFF0000:
            raise NotReady("null pointer at %08X" % a)
        return p
