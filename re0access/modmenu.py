"""Mod settings, as a row under the game's own Start menu (like Resident Access HD's under its
Option menu), and the mod's sounds that the settings control (the aim beep, the arrival chime, the
3D beacon's knock).

The Start menu in play has two rows, Back and Quit Game. On Quit Game a step down goes to "Mod
settings" instead of round to the top: A opens it, up and down choose a setting there, left and
right change it, B goes back."""
import json
import logging
import os
import time

from . import link as L

MOD_ROWS = ["guide", "arrive", "aim", "sounds", "back"]
ROW_NAMES = {"guide": "3D beacon volume", "arrive": "Arrival chime volume", "aim": "Aim beep volume"}
NAV = L.UP | L.DOWN | L.LEFT | L.RIGHT | L.A | L.B | L.START


class ModMenu:
    """Mixed into the App: needs self.link, self.say, SETTINGS."""

    def init_menu(self, path):
        self.settings_path = path
        self.settings = {}
        try:
            with open(path, encoding="utf-8") as fh:
                self.settings = json.load(fh)
        except (OSError, ValueError):
            pass
        vols = self.settings.get("volumes", {})
        self.volumes = {k: max(0.0, min(2.0, float(vols.get(k, 1.0)))) for k in ("guide", "arrive", "aim")}
        self.sounds_on = bool(self.settings.get("sounds", True))
        self.beeps = None
        try:
            from .beacon import Beacon
            self.beeps = Beacon(self.volumes)
            self.beeps.enabled = self.sounds_on
        except Exception:
            logging.exception("sounds")
        self.opt_mode = None        # None, "row" (on Mod settings) or "menu" (in it)
        self.opt_prev = 0
        self.mod_row = 0
        self.opt_blocking = False
        self.opt_inject = None
        self.opt_anchor = 1
        self.quit_said = None

    def save_settings(self):
        self.settings["volumes"] = dict(self.volumes)
        self.settings["sounds"] = self.sounds_on
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=1)
        except OSError:
            logging.exception("settings")

    def apply_sound_settings(self):
        if self.beeps:
            for k, v in self.volumes.items():
                self.beeps.set_volume(k, v)
            self.beeps.enabled = self.sounds_on
            if not self.sounds_on:
                self.beeps.quiet()
                self.beeps.calm()
        space = getattr(self, "space", None)
        if space:
            space.volume = self.volumes["guide"]
            space.enabled = self.sounds_on

    def note_text(self, words, now):
        """Returns True when a line the game lays out should not be said (the mod is speaking its
        own rows of the Start menu)."""
        return self.opt_mode is not None and words in ("Back", "Quit Game", "Return to the game.",
                                                       "Quit the game and return to the title screen.")

    def row_words(self, row):
        if row in ROW_NAMES:
            return "%s, %d percent." % (ROW_NAMES[row], round(self.volumes[row] * 100))
        if row == "sounds":
            return "Mod sounds, %s." % ("on" if self.sounds_on else "off")
        return "Back to the Start menu."

    def poll_modmenu(self):
        now = time.monotonic()
        real = self.link.real()
        b = real.get("buttons", 0)
        # (the left stick moves the menu's cursor too)
        if real.get("ly", 0) > 20000:
            b |= L.UP
        elif real.get("ly", 0) < -20000:
            b |= L.DOWN
        if real.get("lx", 0) < -20000:
            b |= L.LEFT
        elif real.get("lx", 0) > 20000:
            b |= L.RIGHT
        pressed = b & ~self.opt_prev
        self.opt_prev = b
        if self.opt_inject:
            if now < self.opt_inject[1]:
                self.link.add |= self.opt_inject[0]
            else:
                self.link.add &= ~self.opt_inject[0]
                self.opt_inject = None
            self.link.push()
        pm = self.game.pause_menu()
        if pm is not None and pm[0] == 4:
            self.poll_quit_question(now)
        else:
            self.quit_said = None
        if pm is None or pm[0] != 3:
            if self.opt_mode is not None or self.opt_blocking:
                self.opt_mode = None        # (the menu has closed)
                self.set_block(0)
            return
        mode = self.opt_mode
        stick_up = real.get("ly", 0) > 20000
        stick_down = real.get("ly", 0) < -20000
        if mode is None:
            # The rows go round: Back, Quit Game, Mod settings, and back to Back. Up and down are
            # held back from the game here and the mod moves its cursor itself (passing a press on),
            # so the two never get out of step.
            self.set_block(L.UP | L.DOWN)
            if self.opt_inject:
                pass
            elif pressed & L.DOWN:
                if pm[1] == 1:
                    self.opt_mode = "row"
                    self.say("Mod settings. Press A to open.", interrupt=True)
                else:
                    self.opt_inject = (L.DOWN, now + 0.12)
            elif pressed & L.UP:
                if pm[1] == 0:
                    self.opt_mode = "row"
                    self.say("Mod settings. Press A to open.", interrupt=True)
                else:
                    self.opt_inject = (L.UP, now + 0.12)
        elif mode == "row":
            self.set_block(NAV)
            if self.opt_inject:
                pass
            elif pressed & L.UP:
                self.opt_mode = None
                if pm[1] == 1:
                    self.say("Quit Game. Quit the game and return to the title screen.", interrupt=True)
                else:
                    self.opt_inject = (L.UP, now + 0.12)       # (the game's own line is said)
            elif pressed & L.DOWN:
                self.opt_mode = None
                if pm[1] == 0:
                    self.say("Back. Return to the game.", interrupt=True)
                else:
                    self.opt_inject = (L.DOWN, now + 0.12)
            elif pressed & L.A:
                self.opt_mode = "menu"
                self.mod_row = 0
                self.say("Mod settings. Up and down to choose, left and right to change, B to go back. "
                         + self.row_words(MOD_ROWS[0]), interrupt=True)
            elif pressed & (L.B | L.START):
                self.opt_mode = None
                self.set_block(0)
                self.say("Back. Return to the game." if pm[1] == 0 else "Quit Game.", interrupt=True)
        elif mode == "menu":
            self.set_block(NAV)
            row = MOD_ROWS[self.mod_row]
            if pressed & (L.UP | L.DOWN):
                self.mod_row = (self.mod_row + (1 if pressed & L.DOWN else -1)) % len(MOD_ROWS)
                self.say(self.row_words(MOD_ROWS[self.mod_row]), interrupt=True)
            elif pressed & (L.LEFT | L.RIGHT) and row in self.volumes:
                v = round(self.volumes[row] * 10 + (1 if pressed & L.RIGHT else -1)) / 10.0
                self.volumes[row] = max(0.0, min(2.0, v))
                self.apply_sound_settings()
                self.save_settings()
                self.say("%d percent" % round(self.volumes[row] * 100), interrupt=True)
                self.preview(row)
            elif row == "sounds" and pressed & (L.LEFT | L.RIGHT | L.A):
                self.sounds_on = not self.sounds_on
                self.apply_sound_settings()
                self.save_settings()
                self.say("Mod sounds %s." % ("on" if self.sounds_on else "off"), interrupt=True)
            elif (row == "back" and pressed & L.A) or pressed & (L.B | L.START):
                self.opt_mode = "row"
                self.say("Mod settings.", interrupt=True)

    def poll_quit_question(self, now):
        """The "Quit?" question under Quit Game: the game draws its Yes and No all the time, so
        say the question, and which answer the cursor is on as it moves."""
        cur = self.game.quit_answer()
        if cur is None:
            return
        words = "Yes" if cur == 0 else "No"
        if self.quit_said is None:
            self.say("Quit the game and return to the title screen? %s" % words, interrupt=True)
        elif self.quit_said != cur:
            self.say(words, interrupt=True)
        self.quit_said = cur

    def preview(self, row):
        if row == "guide":
            space = getattr(self, "space", None) or self.make_space()
            if space:
                with space._lock:
                    space._pending.append(("knock", 0.0, 0.0, -2.0))
        elif self.beeps:
            self.beeps.preview(row)

    def set_block(self, mask):
        """Hold back these buttons (and the left stick, with any) from the game for the menu."""
        mine = getattr(self, "menu_block", 0)
        self.link.block = (self.link.block & ~mine) | mask
        self.menu_block = mask
        self.link.block_stick = bool(mask)
        self.opt_blocking = bool(mask)
        self.link.push()
