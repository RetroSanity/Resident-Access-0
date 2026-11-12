"""A beeping sound that leads you to the thing you picked.

It beeps faster as you get closer, comes from the left or right speaker
depending on which way to push the stick, and is higher when the thing is
ahead of you (push up) and lower when it is behind you (push down).
"""
import io
import math
import struct
import threading
import time
import wave

try:
    import winsound
except ImportError:          # not on Windows: stay silent
    winsound = None

RATE = 22050


def _clip(v):
    return max(-32767, min(32767, int(v * 32767)))


def _tone(freq, pan, length=0.05, volume=0.35):
    """A short stereo sine beep as WAV bytes. pan -1 is left, 1 is right."""
    frames = int(RATE * length)
    left = volume * math.sqrt((1 - pan) / 2)
    right = volume * math.sqrt((1 + pan) / 2)
    data = bytearray()
    for i in range(frames):
        # A quick fade in and out stops clicks.
        env = min(1.0, i / 200, (frames - i) / 200)
        v = math.sin(2 * math.pi * freq * i / RATE) * env
        data += struct.pack("<hh", _clip(v * left), _clip(v * right))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(data))
    return out.getvalue()


def _melody(notes, length=0.09, volume=0.3):
    """Several notes, one after another, as one WAV (no gaps between them)."""
    frames = int(RATE * length)
    data = bytearray()
    for freq in notes:
        for i in range(frames):
            env = min(1.0, i / 200, (frames - i) / 200)
            v = int(math.sin(2 * math.pi * freq * i / RATE) * env * volume * 32767)
            data += struct.pack("<hh", v, v)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(data))
    return out.getvalue()


def _aim_beep(pan, volume=1.0):
    """The aim beep: a soft, round bell-like note (C6, above the guide beeps) that rings out
    gently with a faint echo after it, so it sits in the background rather than thumping, and
    carries on any speakers."""
    length = 0.34
    frames = int(RATE * length)
    left = volume * math.sqrt((1 - pan) / 2)
    right = volume * math.sqrt((1 + pan) / 2)
    f = 1046.5
    echo = int(RATE * 0.11)
    def note(i):
        if i < 0:
            return 0.0
        t = i / RATE
        env = min(1.0, i / 220) * math.exp(-t * 16)
        return (math.sin(2 * math.pi * f * t) + 0.25 * math.sin(2 * math.pi * 2 * f * t)
                + 0.08 * math.sin(2 * math.pi * 3 * f * t)) / 1.33 * env
    data = bytearray()
    for i in range(frames):
        v = 0.55 * (note(i) + 0.35 * note(i - echo))
        data += struct.pack("<hh", _clip(v * left), _clip(v * right))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(data))
    return out.getvalue()


def _pulse(pan, length=0.16, volume=1.3):
    """A soft, deep thump: a low note with two overtones so that small
    speakers and headphones can still carry it, fading away quickly."""
    frames = int(RATE * length)
    left = volume * math.sqrt((1 - pan) / 2)
    right = volume * math.sqrt((1 + pan) / 2)
    data = bytearray()
    for i in range(frames):
        t = i / RATE
        env = min(1.0, i / 300) * math.exp(-t * 22)
        v = (math.sin(2 * math.pi * 55 * t) + 0.6 * math.sin(2 * math.pi * 110 * t)
             + 0.3 * math.sin(2 * math.pi * 165 * t)) / 1.9 * env
        data += struct.pack("<hh", _clip(v * left), _clip(v * right))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(data))
    return out.getvalue()


# The mod's sounds that have a volume setting: name -> what it is called.
SOUNDS = [("guide", "Guide beep"), ("arrive", "Arrival chime"), ("aim", "Aim beep")]


class Beacon:
    def __init__(self, volumes=None):
        self.volumes = {name: 1.0 for name, _ in SOUNDS}
        for name, v in (volumes or {}).items():
            if name in self.volumes:
                self.volumes[name] = max(0.0, min(2.0, float(v)))
        self._sound = None
        self._interval = 1.0
        self._cache = {}
        self._lock = threading.Lock()
        self._chime = None
        self._pending_chime = False
        self._tunes = {}
        self._pending_tunes = []
        self._threat = None
        self._threat_interval = 1.0
        self._threat_cache = {}
        self.enabled = True             # False: the mod makes no sounds at all (mod settings)
        if winsound:
            threading.Thread(target=self._run, daemon=True).start()

    def sound_for(self, ahead, right):
        """ahead and right are -1 to 1: where the thing is, stick-wise."""
        pitch = int(round((ahead + 1) * 2))          # 0 (behind) to 4 (ahead)
        pan = int(round(max(-1.0, min(1.0, right)) * 4))  # -4 to 4
        key = (pitch, pan)
        if key not in self._cache:
            self._cache[key] = _tone(440 + pitch * 110, pan / 4 * 0.9, volume=0.35 * self.volumes["guide"])
        return self._cache[key]

    def point(self, distance, ahead, right):
        """Beep towards something `distance` world units away."""
        with self._lock:
            self._sound = self.sound_for(ahead, right)
            self._interval = max(0.09, min(1.4, 0.06 + distance * 0.006))

    def quiet(self):
        with self._lock:
            self._sound = None

    def threat(self, distance, ahead, right, locked=False, hit=False):
        """A low thud towards an enemy `distance` metres away: faster as it
        gets nearer, and a sharp high tick instead when you are facing it.
        With `hit` (weapon raised and a shot would land) a soft ringing beep."""
        pan = int(round(max(-1.0, min(1.0, right)) * 4))
        if hit:
            key = ("hit", pan)
            if key not in self._threat_cache:
                self._threat_cache[key] = _aim_beep(pan / 4 * 0.6, volume=self.volumes["aim"])
            with self._lock:
                self._threat = self._threat_cache[key]
                self._threat_interval = 0.45
            return
        key = ("lock", pan) if locked else (int(round((ahead + 1) * 1.5)), pan)
        if key not in self._threat_cache:
            if locked:
                self._threat_cache[key] = _tone(1320, pan / 4 * 0.9, 0.035, 0.3)
            else:
                self._threat_cache[key] = _tone(110 + key[0] * 35, pan / 4 * 0.9, 0.09, 0.6)
        with self._lock:
            self._threat = self._threat_cache[key]
            self._threat_interval = max(0.16, min(1.5, 0.1 + distance * 0.16))

    def calm(self):
        with self._lock:
            self._threat = None

    def chime(self):
        """Two quick high notes: you have arrived."""
        if self._chime is None:
            v = 0.35 * self.volumes["arrive"]
            self._chime = (_tone(1175, 0, 0.07, v), _tone(1568, 0, 0.1, v))
        with self._lock:
            self._sound = None
            self._pending_chime = True

    def set_volume(self, name, value):
        """value 0 (silent) to 2 (twice as loud as normal)."""
        self.volumes[name] = max(0.0, min(2.0, value))
        with self._lock:
            if name == "guide":
                self._cache = {}
            elif name == "aim":
                self._threat_cache = {k: v for k, v in self._threat_cache.items() if k[0] != "hit"}
            elif name == "arrive":
                self._chime = None

    def preview(self, name):
        """Play one of the sounds once, at its volume."""
        if name == "guide":
            parts = [self.sound_for(1.0, 0.0)] * 2
        elif name == "aim":
            parts = [_aim_beep(0.0, volume=self.volumes["aim"])] * 2
        else:
            v = 0.35 * self.volumes["arrive"]
            parts = [_tone(1175, 0, 0.07, v), _tone(1568, 0, 0.1, v)]
        if not self.volumes[name]:
            return
        with self._lock:
            self._pending_tunes.append(parts)

    def cue(self, name):
        """A short tune: "scene start" rises, "scene end" falls."""
        tunes = {"scene start": (523, 659, 784, 1047), "scene end": (1047, 784, 659, 523)}
        if name not in self._tunes:
            notes = tunes[name]
            self._tunes[name] = [_melody(notes)]
        with self._lock:
            self._pending_tunes.append(self._tunes[name])

    def _run(self):
        last = 0.0
        last_threat = 0.0
        while True:
            with self._lock:
                sound, interval = self._sound, self._interval
                threat, threat_interval = self._threat, self._threat_interval
                chime, self._pending_chime = self._pending_chime, False
                tunes, self._pending_tunes = self._pending_tunes, []
            now = time.monotonic()
            if not self.enabled:
                time.sleep(0.05)
                continue
            try:
                for tune in tunes:
                    for part in tune:
                        winsound.PlaySound(part, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                if chime and self._chime:
                    for part in self._chime:
                        winsound.PlaySound(part, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                elif sound and now - last >= interval:
                    last = now
                    winsound.PlaySound(sound, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                if threat and time.monotonic() - last_threat >= threat_interval:
                    last_threat = time.monotonic()
                    winsound.PlaySound(threat, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except Exception:
                pass
            time.sleep(0.02)
