"""Resident Access 0: a screen reader companion for Resident Evil 0 HD Remaster (Steam).

It runs next to the game, reads its memory and the text the controller add-on passes on, and
speaks through your screen reader (or Windows' own voice). Early builds: it finds the game,
makes the controller add-on work with Steam Input, reads the game's messages and menus, and
says where you are as you move between rooms. The room lists, auto walk and the 3D beacon
from Resident Access HD come next.
"""
import ctypes
import logging
import logging.handlers
import os
import time

from .game import Game, scene_words
from . import text as T
from . import gmd
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
        self.text_seen = {}         # string -> when last laid out
        self.text_quiet_until = 0.0

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

    # -- the game's text ----------------------------------------------------------------
    # Strings the game lays out are passed on by the controller add-on (it catches the game's
    # text layout). Most are drawn again every frame while they are on screen, so a string is
    # said when it appears, and again only after it has been gone for a moment.
    GONE = 0.6

    def poll_text(self):
        now = time.monotonic()
        fresh = []
        for caller, holder, raw in self.link.texts():
            last = self.text_seen.get(raw)
            self.text_seen[raw] = now
            if last is not None and now - last < self.GONE:
                continue
            if now < self.text_quiet_until:
                continue            # (what was already on screen when the mod started)
            if raw.startswith(("<B_DE>", "<B_CA>", "<B_")) or raw.startswith("<ICON"):
                continue            # the button hints at the bottom of a screen
            words = T.clean(raw)
            if len(words) < 2 or words in fresh:
                continue
            fresh.append(self.with_amount(words))
        if len(self.text_seen) > 3000:
            self.text_seen = {k: v for k, v in self.text_seen.items() if now - v < 30}
        if fresh and not all(w in ("Yes", "No") for w in fresh):
            # (a dialog the game keeps laid out out of sight shows just "Yes" and "No": a real
            # question comes with its words)
            # (a menu's choice just said comes first: its description follows it)
            self.say(" ".join(fresh), interrupt=time.monotonic() - getattr(self, "menu_said", 0) > 0.8)

    # -- the title screen's menus (pictures, not text) ----------------------------------
    def poll_title(self):
        tm = self.game.title_menu()
        key = tm[:2] if tm else None
        if key != getattr(self, "title_seen", None):
            self.title_seen = key
            if tm and tm[2]:
                self.say_menu(tm[2])
        self.poll_setup(tm)

    def say_menu(self, words):
        self.menu_said = time.monotonic()
        self.say(words, interrupt=True)

    def poll_setup(self, tm):
        from .game import SETUP_ROWS, TITLE_SETUP
        st = self.game.setup() if tm and tm[0] == TITLE_SETUP else None
        was = getattr(self, "setup_seen", None)
        self.setup_seen = st
        if not st:
            return
        row, vals = st
        name, choices = SETUP_ROWS[row]
        if was is None or was[0] != row:
            self.say_menu("%s: %s" % (name, choices[vals[row]]))
        elif was[1][row] != vals[row]:
            self.say_menu(choices[vals[row]])

    # -- items --------------------------------------------------------------------------
    GUNS = {3, 4, 5, 6, 7, 8, 9, 10, 11, 17, 19, 22, 23}

    def item_names(self):
        if getattr(self, "_items", None) is None:
            self._items = []
            try:
                exe = self.mem.exe_path()
                if exe:
                    self._items = gmd.texts(os.path.dirname(exe), "message_item_name")
            except Exception:
                logging.exception("item names")
        return self._items

    def with_amount(self, words):
        """'Handgun' -> 'Handgun, 15 rounds' when it is one of the items you carry."""
        names = self.item_names()
        if not names:
            return words
        for who, _ in self.game.characters() or [("Rebecca", None)]:
            for item, amount in self.game.inventory(who):
                if 0 < item < len(names) and names[item] == words:
                    if item in self.GUNS:
                        return "%s, %d %s" % (words, amount, "round" if amount == 1 else "rounds")
                    if amount > 1 or "Ammo" in words or "Grenade" in words:
                        return "%s, %d" % (words, amount)
                    return words
        return words

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
                self.text_quiet_until = time.monotonic() + 2.0
                self.say("Resident Access 0 ready.")
            try:
                self.poll_pad_fix()
                self.poll_room()
                self.poll_title()
                self.poll_text()
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
