"""Sound all round you: the guide beacon in 3D, through OpenAL Soft with HRTF.

HRTF filters each sound the way your own ears and head would, so on headphones a sound in
front is heard in front, one behind is heard behind, and one above is heard higher. The
beacon is a short, bright knock (a mix of tones and a touch of noise: a pure tone is hard
to place) played where the next turn of the way is, every so often: faster the nearer it is.

OpenAL Soft is a small library (lib\\openal_soft.dll, LGPL: lib\\OpenAL-COPYING.txt) that
comes with the mod. If it cannot be loaded, `available` is False and the mod uses its old
stereo beeps instead.
"""
import ctypes
import logging
import math
import os
import random
import struct
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(os.path.dirname(HERE), "lib", "openal_soft.dll")
RATE = 44100

# OpenAL and its HRTF extension
ALC_HRTF_SOFT = 0x1992
ALC_HRTF_STATUS_SOFT = 0x1993
AL_FORMAT_MONO16 = 0x1101
AL_BUFFER = 0x1009
AL_POSITION = 0x1004
AL_GAIN = 0x100A
AL_SOURCE_RELATIVE = 0x202
AL_SOURCE_STATE = 0x1010
AL_PLAYING = 0x1012
AL_REFERENCE_DISTANCE = 0x1020
AL_ROLLOFF_FACTOR = 0x1021
AL_PITCH = 0x1003


def _knock(length=0.09, base=520.0, bright=1.0, volume=0.8):
    """The beacon's sound, mono 16-bit: a few tones and a little noise, struck and dying away."""
    n = int(RATE * length)
    rnd = random.Random(7)
    out = bytearray()
    noise = 0.0
    for i in range(n):
        t = i / RATE
        env = min(1.0, i / 60.0) * math.exp(-t * 38)
        v = (math.sin(2 * math.pi * base * t) + 0.6 * math.sin(2 * math.pi * base * 2.01 * t)
             + 0.35 * bright * math.sin(2 * math.pi * base * 3.03 * t)
             + 0.2 * bright * math.sin(2 * math.pi * base * 5.1 * t))
        noise = 0.6 * noise + 0.4 * (rnd.random() * 2 - 1)      # (softened noise: the click of it)
        v = (v / 2.15 + 0.35 * bright * noise * math.exp(-t * 90)) * env
        out += struct.pack("<h", max(-32767, min(32767, int(v * volume * 32767))))
    return bytes(out)


def _chime(volume=0.6):
    """Two quick notes: a turn of the way reached."""
    out = bytearray()
    for f, ln in ((1175.0, 0.07), (1568.0, 0.11)):
        n = int(RATE * ln)
        for i in range(n):
            t = i / RATE
            env = min(1.0, i / 80.0) * min(1.0, (n - i) / 300.0)
            v = (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(2 * math.pi * 2 * f * t)) / 1.3 * env
            out += struct.pack("<h", int(v * volume * 32767))
    return bytes(out)


class Space3D:
    def __init__(self):
        self.available = False
        self.hrtf = False
        self.enabled = True
        self.volume = 1.0
        self._lock = threading.Lock()
        self._target = None         # (x, y, z) in metres, listener's own frame: x right, y up, -z ahead
        self._interval = 1.0
        self._centred = False
        self._pending = []          # one-off sounds: ("chime",) or ("knock", x, y, z)
        try:
            self.al = ctypes.CDLL(DLL)
        except OSError as e:
            logging.info("3D sound: no OpenAL (%s): the stereo beeps instead", e)
            return
        threading.Thread(target=self._run, daemon=True).start()

    # ------------------------------------------------------------------ from the mod
    def point(self, rel, dist, up=0.0):
        """The beacon at `dist` metres, `rel` radians off straight ahead (more than 0: to the left),
        `up` metres above you."""
        d = max(0.6, min(dist, 12.0))       # (held within hearing: further than this sounds the same)
        x, z = -math.sin(rel) * d, -math.cos(rel) * d
        with self._lock:
            self._target = (x, max(-3.0, min(3.0, up)), z)
            self._interval = max(0.18, min(1.1, 0.12 + dist * 0.09))
            self._centred = abs(rel) < 0.16
        return self.available

    def quiet(self):
        with self._lock:
            self._target = None

    def chime(self):
        with self._lock:
            self._target = None
            self._pending.append(("chime",))

    def test(self, say):
        """Round your head once, saying where each knock is."""
        def run():
            for words, rel in (("In front.", 0.0), ("To the right.", -math.pi / 2), ("Behind.", math.pi),
                               ("To the left.", math.pi / 2), ("In front again.", 0.0)):
                say(words)
                time.sleep(0.9)
                for _ in range(3):
                    with self._lock:
                        self._pending.append(("knock", -math.sin(rel) * 2.0, 0.0, -math.cos(rel) * 2.0))
                    time.sleep(0.45)
            say("Above you." if self.hrtf else "Done.")
            if self.hrtf:
                time.sleep(0.9)
                for _ in range(3):
                    with self._lock:
                        self._pending.append(("knock", 0.0, 2.0, -0.6))
                    time.sleep(0.45)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ the sound thread
    def _setup(self):
        al = self.al
        al.alcOpenDevice.restype = ctypes.c_void_p
        al.alcOpenDevice.argtypes = [ctypes.c_char_p]
        al.alcCreateContext.restype = ctypes.c_void_p
        al.alcCreateContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        al.alcMakeContextCurrent.argtypes = [ctypes.c_void_p]
        al.alcGetIntegerv.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        al.alBufferData.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        al.alSource3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]
        al.alSourcef.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
        al.alSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
        al.alSourcePlay.argtypes = [ctypes.c_uint]
        al.alSourceStop.argtypes = [ctypes.c_uint]
        al.alGetSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        dev = al.alcOpenDevice(None)
        if not dev:
            raise OSError("no sound device")
        attrs = (ctypes.c_int * 3)(ALC_HRTF_SOFT, 1, 0)
        ctx = al.alcCreateContext(dev, attrs)
        if not ctx or not al.alcMakeContextCurrent(ctx):
            raise OSError("no sound context")
        st = ctypes.c_int(0)
        al.alcGetIntegerv(dev, ALC_HRTF_STATUS_SOFT, 1, ctypes.byref(st))
        self.hrtf = st.value in (1, 3, 4)       # (enabled, required, headphones detected)
        self.dev, self.ctx = dev, ctx

        def buffer(data):
            b = ctypes.c_uint(0)
            al.alGenBuffers(1, ctypes.byref(b))
            al.alBufferData(b.value, AL_FORMAT_MONO16, data, len(data), RATE)
            return b.value
        self.buf_knock = buffer(_knock())
        self.buf_lock = buffer(_knock(0.07, 880.0, 1.4, 0.6))
        self.buf_chime = buffer(_chime())
        self.sources = []
        for _ in range(6):
            s = ctypes.c_uint(0)
            al.alGenSources(1, ctypes.byref(s))
            al.alSourcei(s.value, AL_SOURCE_RELATIVE, 1)       # (placed round the listener, who never moves)
            al.alSourcef(s.value, AL_REFERENCE_DISTANCE, 1.5)
            al.alSourcef(s.value, AL_ROLLOFF_FACTOR, 0.6)
            self.sources.append(s.value)
        self.next_src = 0
        logging.info("3D sound: OpenAL ready, HRTF %s (status %d)", "on" if self.hrtf else "off", st.value)

    def _play(self, buf, pos, gain=1.0):
        al = self.al
        s = self.sources[self.next_src]
        self.next_src = (self.next_src + 1) % len(self.sources)
        al.alSourceStop(s)
        al.alSourcei(s, AL_BUFFER, buf)
        al.alSourcef(s, AL_GAIN, gain * self.volume)
        al.alSource3f(s, AL_POSITION, pos[0], pos[1], pos[2])
        al.alSourcePlay(s)

    def _run(self):
        try:
            self._setup()
        except Exception as e:
            logging.info("3D sound: could not start OpenAL (%s): the stereo beeps instead", e)
            return
        self.available = True
        last = 0.0
        while True:
            with self._lock:
                target, interval, centred = self._target, self._interval, self._centred
                pending, self._pending = self._pending, []
            try:
                if self.enabled:
                    for p in pending:
                        if p[0] == "chime":
                            self._play(self.buf_chime, (0.0, 0.0, -0.5), 0.8)
                        else:
                            self._play(self.buf_knock, p[1:])
                    now = time.monotonic()
                    if target and now - last >= interval:
                        last = now
                        self._play(self.buf_knock, target)
                        if centred:
                            # (straight ahead: a second, higher knock with it, to walk on)
                            time.sleep(0.07)
                            self._play(self.buf_lock, target, 0.7)
            except Exception:
                logging.exception("3D sound")
            time.sleep(0.02)
