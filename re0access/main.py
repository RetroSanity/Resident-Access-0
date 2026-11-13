"""Resident Access 0: a screen reader companion for Resident Evil 0 HD Remaster (Steam).

It runs next to the game, reads its memory and the text the controller add-on passes on, and
speaks through your screen reader (or Windows' own voice). Early builds: it finds the game,
makes the controller add-on work with Steam Input, reads the game's messages and menus, and
says where you are as you move between rooms, and has the room list (doors, items, things to
check, enemies), auto walk and the 3D beacon from Resident Access HD (see roomlist.py).
"""
import ctypes
import logging
import re
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
from .roomlist import RoomList
from .modmenu import ModMenu

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, "re0access.log")
SETTINGS = os.path.join(ROOT, "settings.json")
QUIT_EVENT = "Local\\ResidentAccess0Quit"


class App(RoomList, ModMenu):
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
        self.long_said = {}
        self.init_list()
        self.init_menu(SETTINGS)

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
        self.target = None
        if self.walker.walking:
            self.stop_walk()
        try:
            self.load_room()
        except Exception:
            logging.exception("room")

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

    SLOT_LABEL = re.compile(r"^\s*(\d+)\.$")

    def poll_text(self):
        now = time.monotonic()
        fresh = []
        batch = self.link.texts()
        for _, _, raw in batch:
            # A question's answers are named in its text: <SEL Yes_No_Use>
            m = re.search(r"<SEL ([A-Za-z_]+)>", raw)
            if m:
                self.choice_words = m.group(1).split("_")
                self.question_at = now
        if self.slot_list_up(batch):
            self.read_slots(batch, now)
            return
        reading = self.game.file_reader()
        if reading is not None:
            self.read_file_page(batch, reading)
            return
        self.reader_file = None
        for caller, holder, raw in batch:
            self.note_text(T.clean(raw), now)
            last = self.text_seen.get(raw)
            self.text_seen[raw] = now
            if last is not None and now - last < self.GONE:
                continue
            if now < self.text_quiet_until:
                continue            # (what was already on screen when the mod started)
            if raw.startswith(("<B_DE>", "<B_CA>", "<B_")) or raw.startswith("<ICON"):
                continue            # the button hints at the bottom of a screen
            words = T.clean(raw)
            if self.note_text(words, now):
                continue            # (the mod is speaking its own row of the Option menu)
            if len(words) < 2 or words in fresh:
                continue
            if len(words) > 60:
                # (a file's pages are laid out again as you turn them, the last one with the
                # next: a long text is said once in a while)
                said = self.long_said.get(words)
                if said is not None and now - said < 60:
                    continue
                self.long_said[words] = now
            fresh.append(self.with_equip(self.with_amount(words), words))
        if len(self.text_seen) > 3000:
            self.text_seen = {k: v for k, v in self.text_seen.items() if now - v < 30}
        fresh = self.fill_item_name(fresh)
        if not any("?" in w for w in fresh):
            fresh = [w for w in fresh if w not in ("Yes", "No")]
        if fresh:
            # (a dialog the game keeps laid out out of sight shows just "Yes" and "No": a real
            # question comes with its words)
            # (a menu's choice just said comes first: its description follows it)
            self.say(" ".join(fresh), interrupt=time.monotonic() - getattr(self, "menu_said", 0) > 0.8)

    def read_file_page(self, batch, page):
        """A file picked up is read a page at a time (the game lays out the pages either side as
        well, for turning): say the page the game is on, from the file's own text."""
        gd = self.game_dir()
        if not gd:
            return
        from .gmd import file_pages
        if getattr(self, "reader_file", None) is None:
            said = [T.clean(raw) for caller, holder, raw in batch]
            for exact in (True, False):
                for item in range(150, 181):
                    pages = file_pages(gd, item)
                    if not pages:
                        continue
                    if exact and pages[0] in said or \
                            not exact and any(len(w) > 25 and any(w in pg for pg in pages) for w in said):
                        self.reader_file = item
                        self.reader_page = None
                        break
                if getattr(self, "reader_file", None):
                    break
        item = getattr(self, "reader_file", None)
        if item is None or page == getattr(self, "reader_page", None):
            return
        self.reader_page = page
        pages = file_pages(gd, item)
        if 0 <= page < len(pages):
            self.say("%s%s" % (pages[page], " (last page)" if page == len(pages) - 1 else ""), interrupt=True)

    def poll_choice(self):
        """Yes and No (and Use) under a question in the message window are pictures: say the one
        the cursor is on as it moves."""
        cur = self.game.message_choice()
        # (the window waits the same way at the end of any message: only a question just asked
        # has answers to say)
        if cur is not None and time.monotonic() - getattr(self, "question_at", -99.0) > 60.0:
            cur = None
        if cur is None and getattr(self, "choice_was", None) is not None:
            self.question_at = -99.0        # (that question is over)
        if cur is not None:
            self.game.save_row()        # (finds the save list ahead of time, for the typewriter)
        was = getattr(self, "choice_was", None)
        self.choice_was = cur
        if cur is None or cur == was:
            return
        words = getattr(self, "choice_words", None) or ["Yes", "No"]
        if cur < len(words):
            # (the first one comes after the question itself)
            self.say(words[cur], interrupt=was is not None)

    GAP = re.compile(r"\b(the|a|an)\s*([?.!])")

    def fill_item_name(self, fresh):
        """"Take the ?" and "You got the ." have the item's name laid out on its own: put it in
        its place ("Take the Handgun Ammo?")."""
        names = set(self.item_names() or [])
        for i, w in enumerate(fresh):
            m = self.GAP.search(w)
            if not m:
                continue
            for j, other in enumerate(fresh):
                base = other.split(",")[0]
                if j != i and base in names:
                    # (the name alone: the amount added for your own items is not this one's)
                    fresh[i] = w[:m.start()] + m.group(1) + " " + base + m.group(2) + w[m.end():]
                    del fresh[j]
                    return fresh
        return fresh

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

    # -- the save / load slots ------------------------------------------------------------
    def slot_list_up(self, batch=()):
        from .game import TITLE_LOAD
        tm = self.game.title_menu()
        if tm and tm[0] == TITLE_LOAD:
            return True
        # In play, the typewriter's save list: its slots are laid out as "1.", "2." ...
        if tm is None:
            now = time.monotonic()
            if any(self.SLOT_LABEL.match(T.clean(raw)) for _, _, raw in batch):
                self.save_list_until = now + 2.0
            return now < getattr(self, "save_list_until", 0.0)
        return False

    def read_slots(self, batch, now):
        """The slot list is laid out again as it scrolls: keep what each slot says, and say just
        the slot the cursor is on."""
        slots = getattr(self, "slots", None)
        if slots is None:
            slots = self.slots = {}
        cur = None
        for caller, holder, raw in batch:
            self.text_seen[raw] = now
            words = T.clean(raw)
            mm = self.SLOT_LABEL.match(words)
            if mm:
                cur = int(mm.group(1))
                slots[cur] = []
            elif cur is not None and words and not raw.startswith(("<B_", "<ICON")):
                slots[cur].append(words)
        idx = self.game.save_row()
        if idx is None or idx + 1 not in slots:
            if idx is not None and idx != getattr(self, "slot_missing", None):
                self.slot_missing = idx
                logging.info("slot %s not laid out yet (have %s)", idx + 1, sorted(slots))
            return
        n = idx + 1
        key = (n, tuple(slots[n]))
        if key == getattr(self, "slot_said", None):
            return
        self.slot_said = key
        self.say("Slot %d: %s" % (n, ", ".join(slots[n]) or "No Data"), interrupt=True)

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

    def with_equip(self, said, words):
        """On the item screen: ", equipped" after the weapon in hand, and the game's "Equip weapon"
        (shown for both) said as "Un-equip" when the weapon picked is the one in hand."""
        cur = self.game.item_cursor()
        c = self.game.controlled()
        if cur is None or not c:
            return said
        eq = self.game.equipped(c[0])
        if eq is None or eq < 0:
            return said
        if words == "Equip weapon":
            return "Un-equip weapon" if cur == eq else said
        inv = self.game.inventory(c[0])
        names = self.item_names()
        if eq < len(inv) and 0 < inv[eq][0] < len(names) and names[inv[eq][0]] == words and cur == eq:
            return said + ", equipped"
        return said

    def poll_equip(self):
        """Say when the weapon in hand changes."""
        c = self.game.controlled()
        key = None
        if c:
            eq = self.game.equipped(c[0])
            if eq is not None:
                key = (c[0], eq)
        was = getattr(self, "equip_was", None)
        self.equip_was = key
        if not key or not was or was[0] != key[0] or was[1] == key[1]:
            return
        if key[1] < 0:
            self.say("Weapon unequipped.", interrupt=False)
            return
        inv = self.game.inventory(key[0])
        names = self.item_names()
        item = inv[key[1]][0] if key[1] < len(inv) else 0
        name = names[item] if 0 < item < len(names) else "Weapon"
        self.say("%s equipped." % name, interrupt=False)

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
    def health_words(self):
        """'Rebecca: Fine. Health 100 percent.' for each character in the game."""
        out = []
        for name, obj in self.game.characters():
            h = self.game.health(obj)
            if not h:
                continue
            hp, top = h
            ratio = min(1.0, hp / float(top))
            if hp == 0:
                word = "Dead"
            elif ratio > 0.66:
                word = "Fine"
            elif ratio > 0.33:
                word = "Caution"
            else:
                word = "Danger"
            out.append("%s: %s. Health %d percent." % (name, word, round(ratio * 100)))
        if not out:
            return "Not in the game."
        if len(out) == 1:
            return out[0].split(": ", 1)[1]
        return " ".join(out)

    def poll_death(self):
        """Say when a character dies (the game over screen is a picture)."""
        dead = set()
        for name, obj in self.game.characters():
            h = self.game.health(obj)
            if h and h[0] == 0:
                dead.add(name)
        for name in dead - getattr(self, "dead_seen", set()):
            self.say("%s has died." % name, interrupt=True)
        self.dead_seen = dead

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
                self.poll_death()
                self.poll_controller()
                self.poll_walk()
                self.poll_taken()
                self.poll_partner()
                self.poll_modmenu()
                self.poll_enemies()
                self.poll_item_screen()
                self.poll_equip()
                self.poll_files()
                self.poll_choice()
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
