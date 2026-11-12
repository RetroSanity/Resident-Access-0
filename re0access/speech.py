"""Speaking through the screen reader.

NVDA is used directly through its controller DLL. Without NVDA, Prism (lib\\prism.dll, MPL 2.0:
lib\\prism-LICENSE.txt) speaks instead: through JAWS, ZoomText, System Access or another screen
reader that is running, and with none of them, through Windows' own voices (OneCore, as Narrator
uses, or the older SAPI ones). Failing all of that the text is only logged.
"""
import ctypes
import logging
import os
import threading
import time

log = logging.getLogger("speech")

HERE = os.path.dirname(os.path.abspath(__file__))
# (Releases from 0.65 name it nvda_controller64.dll: a copy of the mod that is running holds its
# DLL open, so an update can't write over it. A new name lets older copies update cleanly.)
_LIB = os.path.join(os.path.dirname(HERE), "lib")
DLL = next((os.path.join(_LIB, n) for n in ("nvda_controller64.dll", "nvdaControllerClient64.dll")
            if os.path.exists(os.path.join(_LIB, n))), os.path.join(_LIB, "nvda_controller64.dll"))
PRISM_DLL = os.path.join(_LIB, "prism.dll")


class _PrismConfig(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint8),
                ("registry", ctypes.c_void_p),
                ("availability_callback", ctypes.c_void_p),
                ("availability_userdata", ctypes.c_void_p),
                ("availability_poll_interval_ms", ctypes.c_uint32),
                ("availability_debounce_samples", ctypes.c_uint32),
                ("availability_backoff_max_ms", ctypes.c_uint32),
                ("availability_auto_power_manage", ctypes.c_bool),
                ("availability_baseline_callback", ctypes.c_void_p)]


class Prism:
    """Speech through Prism: the screen reader that is running, or Windows' voices."""
    # Prism's names for them (backend ids)
    READERS = [("NVDA", 0x89CC19C5C4AC1A56), ("JAWS", 0xAC3D60E9BD84B53E),
               ("ZoomText", 0xAE439D62DC7B1479), ("System Access", 0x8380F2A37B2C3EB6),
               ("PC-Talker", 0x344B951962E3B835), ("ZDSR", 0x3D93C56C9E7F2A2E),
               ("BoyPC Reader", 0x285ABA1C16F3300F), ("Sense Reader", 0xED4760890B55C2F2),
               ("Window-Eyes", 0x9120D89908785C13)]
    VOICES = [("Windows OneCore voice", 0x6797D32F0D994CB4), ("Windows SAPI voice", 0x1D6DF72422CEEE66)]
    OK, ALREADY_INITIALIZED = 0, 15
    RECHECK = 10.0          # how often to look for a screen reader started since (seconds)

    def __init__(self):
        p = ctypes.CDLL(PRISM_DLL)
        vp, u64 = ctypes.c_void_p, ctypes.c_uint64
        p.prism_config_init.restype = _PrismConfig
        p.prism_init.restype = vp
        p.prism_init.argtypes = [ctypes.POINTER(_PrismConfig)]
        p.prism_registry_create.restype = vp
        p.prism_registry_create.argtypes = [vp, u64]
        p.prism_backend_initialize.argtypes = [vp]
        p.prism_backend_free.argtypes = [vp]
        p.prism_backend_free.restype = None
        for name in ("prism_backend_speak", "prism_backend_output"):
            getattr(p, name).argtypes = [vp, ctypes.c_char_p, ctypes.c_bool]
        p.prism_backend_stop.argtypes = [vp]
        p.prism_version_string.restype = ctypes.c_char_p
        self.p = p
        cfg = p.prism_config_init()
        self.ctx = p.prism_init(ctypes.byref(cfg))
        if not self.ctx:
            raise OSError("Prism could not start")
        self.backend = None
        self.name = None
        self.reader = False
        self.checked = 0.0
        log.info("Prism %s loaded", (p.prism_version_string() or b"?").decode("utf-8", "replace"))

    def _make(self, bid):
        b = self.p.prism_registry_create(self.ctx, bid)
        if not b:
            return None
        if self.p.prism_backend_initialize(b) not in (self.OK, self.ALREADY_INITIALIZED):
            self.p.prism_backend_free(b)
            return None
        return b

    def _use(self, name, b, reader):
        if self.backend and self.backend != b:
            self.p.prism_backend_free(self.backend)
        if name != self.name:
            log.info("speech now through %s", name)
        self.backend, self.name, self.reader = b, name, reader

    def pick(self, force=False):
        """The screen reader that is running if there is one, otherwise a Windows voice."""
        self.checked = time.monotonic()
        for name, bid in self.READERS:
            b = self._make(bid)
            if b:
                if name == self.name and not force:
                    self.p.prism_backend_free(b)        # (still the one in use: keep it)
                else:
                    self._use(name, b, True)
                return True
        if not force and self.backend and not self.reader:
            return True                                 # (a Windows voice already: keep it)
        for name, bid in self.VOICES:
            if force and name == self.name:
                continue
            b = self._make(bid)
            if b:
                self._use(name, b, False)
                return True
        if self.backend:
            self.p.prism_backend_free(self.backend)
        self.backend, self.name, self.reader = None, None, False
        return False

    def say(self, text, interrupt):
        if self.backend is None or time.monotonic() - self.checked > self.RECHECK:
            self.pick()
        for attempt in range(2):
            if self.backend is None:
                return
            fn = self.p.prism_backend_output if self.reader else self.p.prism_backend_speak
            r = fn(self.backend, text.encode("utf-8", "replace"), interrupt)
            if r == self.OK:
                return
            log.info("%s could not speak (error %d)", self.name, r)
            self.pick(force=True)

    def stop(self):
        if self.backend:
            self.p.prism_backend_stop(self.backend)


class Speech:
    _prism = None           # one for every Speech in the mod (shared: it holds the voice)
    _prism_failed = False
    _lock = threading.Lock()    # (shared too: two Speech objects must not speak through Prism at once)

    def __init__(self):
        self.nvda = None
        self.last = ""
        try:
            self.nvda = ctypes.WinDLL(DLL)
            self.nvda.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
        except OSError as exc:
            log.warning("NVDA controller not loaded: %s", exc)

    def nvda_running(self):
        try:
            return bool(self.nvda) and self.nvda.nvdaController_testIfRunning() == 0
        except OSError:
            return False

    def prism(self):
        """Prism, started the first time it is needed (never, while NVDA is running)."""
        cls = Speech
        if cls._prism is None and not cls._prism_failed:
            try:
                cls._prism = Prism()
            except Exception as exc:
                cls._prism_failed = True
                log.warning("Prism not loaded: %s", exc)
        return cls._prism

    def say(self, text, interrupt=False, remember=True):
        text = (text or "").strip()
        if not text:
            return
        if remember:
            self.last = text
        log.info("SAY%s %s", "!" if interrupt else "", text)
        # (not printed to the mod's window: a screen reader focused on it would read it a second time)
        with self._lock:
            try:
                if self.nvda_running():
                    if interrupt:
                        self.nvda.nvdaController_cancelSpeech()
                    self.nvda.nvdaController_speakText(text)
                elif self.prism():
                    self.prism().say(text, interrupt)
            except Exception:
                log.exception("speech")

    def stop(self):
        """Cut off whatever is being said."""
        with self._lock:
            try:
                if self.nvda_running():
                    self.nvda.nvdaController_cancelSpeech()
                elif Speech._prism is not None:
                    Speech._prism.stop()
            except Exception:
                log.exception("stop speech")
