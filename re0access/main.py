"""Resident Access 0: a screen reader companion for Resident Evil 0 HD Remaster (Steam).

It runs next to the game, reads its memory, and speaks through your screen reader (or
Windows' own voice). This is the first, early build: it finds the game, makes the controller
add-on work with Steam Input, and says where you are as you move between rooms. The room
lists, auto walk and the 3D beacon from Resident Access HD come next.
"""
import ctypes
import logging
import logging.handlers
import os
import time

from .game import Game, scene_words
from .link import Link
from . import link as L
from .mem import Memory, NotReady
from .speech import Speech
from .version import VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, "re0access.log")
SETTINGS = os.path.join(ROOT, "settings.json")
QUIT_EVENT = "Local\\ResidentAccess0Quit"


class App:
    quit_event = None

    def __init__(self):
        self.speech = Speech()
        self.mem = Memory()
        self.game = Game(self.mem)
        self.link = Link()
        self.connected = False
        self.scene = None
        self.pad_state = None
        self.pad_tries = 0
        self.prev_buttons = 0

    def say(self, text, interrupt=False):
        if not text:
            return
        logging.info("say: %s", text)
        try:
            self.speech.say(text, interrupt=interrupt)
        except Exception:
            logging.exception("speech")

    # -- the controller fix ------------------------------------------------------------------
    def poll_pad_fix(self):
        if self.pad_state in ("fixed", "done") or self.pad_tries > 600:
            return
        self.pad_tries += 1
        st = self.game.fix_pad()
        if st != self.pad_state:
            logging.info("controller fix: %s", st)
            self.pad_state = st

    # -- rooms -------------------------------------------------------------------------------
    def poll_room(self):
        no = self.game.scene()
        if no is None or no == self.scene:
            return
        first = self.scene is None
        self.scene = no
        if not self.game.characters():
            return                      # (title screen, or a scene still loading)
        self.say(scene_words(no), interrupt=not first)

    def where_words(self):
        chars = self.game.characters()
        if self.scene is None or not chars:
            return "Not in the game."
        names = " and ".join(n for n, _ in chars)
        return "%s. %s here." % (scene_words(self.scene), names)

    # -- controller keys ---------------------------------------------------------------------
    def poll_keys(self):
        b = self.link.real().get("buttons", 0)
        new = b & ~self.prev_buttons
        self.prev_buttons = b
        if b & L.LB and not b & 0x0400:          # left bumper held (not aiming with the trigger)
            if new & L.A:
                self.say(self.where_words(), interrupt=True)

    def run(self):
        self.say("Resident Access 0, version %s. Waiting for the game." % VERSION)
        while True:
            if told_to_quit(self.quit_event):
                logging.info("a newer copy has started: closing")
                self.link.release()
                os._exit(4)
            self.link.push()
            if not self.mem.connect():
                if self.connected:
                    self.say("The game has closed.")
                    self.connected = False
                    self.scene = None
                    self.pad_state = None
                    self.pad_tries = 0
                time.sleep(1.0)
                continue
            if not self.connected:
                self.connected = True
                self.say("Resident Access 0 ready.")
            try:
                self.poll_pad_fix()
                self.poll_room()
                self.poll_keys()
            except NotReady:
                pass
            except Exception:
                logging.exception("poll")
            time.sleep(0.05)


def other_copies():
    """Windows and processes of other copies of the mod: [(pid, name)]."""
    import subprocess
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(cmd|py|pyw|python|pythonw)\\.exe$' } | "
          "ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, $_.Name, $_.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=20, creationflags=0x08000000).stdout
    except Exception:
        logging.exception("listing processes")
        return []
    procs = {}
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), parts[2].lower(), parts[3])
    mine, p = set(), os.getpid()
    while p in procs and p not in mine:
        mine.add(p)
        p = procs[p][0]
    mine.add(p)
    found = []
    for pid, (ppid, name, cmd) in procs.items():
        if pid in mine:
            continue
        low = cmd.lower()
        if (name == "cmd.exe" and "start resident access 0.bat" in low) or \
                (name != "cmd.exe" and "re0access" in low and "dev_runner" not in low):
            found.append((pid, name))
    return found


def take_over():
    """Close every other copy of the mod, and their windows, so only this one speaks."""
    import subprocess
    k32 = ctypes.WinDLL("kernel32")
    k32.CreateEventW.restype = ctypes.c_void_p
    ev = k32.CreateEventW(None, True, False, QUIT_EVENT)
    k32.SetEvent(ctypes.c_void_p(ev))
    time.sleep(1.5)
    k32.ResetEvent(ctypes.c_void_p(ev))
    for pid, name in other_copies():
        logging.info("closing old copy: %s %d", name, pid)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                       creationflags=0x08000000)
    return ev


def told_to_quit(ev):
    if not ev:
        return False
    k32 = ctypes.WinDLL("kernel32")
    return k32.WaitForSingleObject(ctypes.c_void_p(ev), 0) == 0


def start_log():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if os.path.exists(LOG):
        try:
            os.replace(LOG, os.path.join(ROOT, "re0access.old.log"))
        except OSError:
            pass
    fh = logging.handlers.RotatingFileHandler(LOG, maxBytes=20 * 1024 * 1024, backupCount=0,
                                              encoding="utf-8", delay=True)
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    root.addHandler(fh)


def main():
    App.quit_event = take_over()
    start_log()
    logging.info("start %s", VERSION)
    try:
        from . import update
        if update.check(Speech().say):
            logging.info("updated: starting the new version")
            os._exit(update.RESTART)
    except Exception:
        logging.exception("update check")
    try:
        with open(os.path.join(ROOT, "re0access.pid"), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
    try:
        App().run()
    except KeyboardInterrupt:
        pass
