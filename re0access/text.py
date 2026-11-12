"""Turning the game's text (with its little <TAGS>) into something to speak."""
import re
import time

HINT_ICON_CALLER = 0x004D8C60     # the game lays out a button picture on its own after a hint

PAD_ICONS = {
    "PAD_A": "A", "PAD_B": "B", "PAD_X": "X", "PAD_Y": "Y", "PAD_START": "Start", "PAD_BACK": "Back",
    "PAD_SELECT": "Back", "PAD_LB": "LB", "PAD_RB": "RB", "PAD_LT": "LT", "PAD_RT": "RT",
    "PAD_L_R": "Left and right", "PAD_U_D": "Up and down", "PAD_LS": "Left stick", "PAD_RS": "Right stick",
    "PAD_L3": "Left stick press", "PAD_R3": "Right stick press", "PAD_DPAD": "D-pad",
    "PAD_AL": "Left stick", "PAD_AR": "Right stick", "PAD_CROSS": "D-pad", "PAD_UP": "Up", "PAD_DOWN": "Down",
    "PAD_LEFT": "Left", "PAD_RIGHT": "Right",
}

_TAG = re.compile(r"<[^<>]*>")


def icon_name(tag):
    """'<ICON PAD_A>' -> 'A', keyboard pictures -> '' (the mod is played on a controller)."""
    m = re.match(r"<ICO?N2? ([A-Z0-9_]+)>", tag)
    if not m:
        return None
    name = m.group(1)
    if name in PAD_ICONS:
        return PAD_ICONS[name]
    if name.startswith("PAD_"):
        return name[4:].replace("_", " ").title()
    return ""


def clean(raw):
    s = raw.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("　", " ").replace("・", ", ").replace("★", "").replace("…", "...")
    s = s.replace("▲", "").replace("▼", "").replace("—", " - ")

    def tag(m):
        t = m.group(0)
        n = icon_name(t)
        if n:
            return " " + n + " "
        return " " if t.upper().startswith("<PAGE") else ""
    s = _TAG.sub(tag, s)
    s = re.sub(r"#\s*(\d)", r"number \1", s)          # ("open valve #1": not "hash")
    s = " ".join(s.split())
    for a, b in ((" .", "."), (" ,", ","), (".,", "."), ("?,", "?"), ("!,", "!"), (" ?", "?"), (" !", "!"), (", ,", ","), ("( ", "("), (" )", ")")):
        s = s.replace(a, b)
    return s.strip(" ,")


def is_hint(raw):
    """The bar of button hints at the bottom of menus: '<SIZE 22><ICN2 DECIDE></SIZE> OK'."""
    return raw.startswith("<SIZE 22><IC")


class Collector:
    """Gathers the strings of one moment into what should be said.

    Button hints come as a word ('OK') with the picture of the button laid out
    just after it; they are joined as 'A: OK'. Pictures of keyboard keys are
    dropped. Subtitles that are typed out bit by bit only say the new part."""

    def __init__(self):
        self.msg = None                 # the message window's text with pages: {raw, left, time}
        self.pending_hint = None
        self.last_said = ""
        self.last_name = ""         # the last short line with no tags: often an item's name

    def page_turned(self):
        """The message window turned a page (it lays its text, or its arrows, out again)."""
        m, now = self.msg, time.monotonic()
        if m and m["left"] > 0 and now - m["time"] > 0.5:
            m["left"] -= 1
            m["time"] = now

    GM = re.compile(r"<GM[^<>]*>")

    def feed(self, items):
        """items: [(caller, holder, raw)] -> (spoken lines, hint lines)"""
        lines, hints = [], []
        arrows = turned = paged = False
        filled = None                   # the name put into a "You got the ..." line in this batch
        now0 = time.monotonic()
        batch_name = None               # a name laid out earlier in this batch
        names_after = []                # short plain lines in this batch, with their places
        for i, (caller, holder, raw) in enumerate(items):
            if caller != HINT_ICON_CALLER and not is_hint(raw) and "<" not in raw:
                t = clean(raw)
                if t and len(t) <= 40:
                    names_after.append((i, t))
        for i, (caller, holder, raw) in enumerate(items):
            if caller == HINT_ICON_CALLER:
                key = clean(raw)
                if self.pending_hint and key:
                    hints.append("%s: %s" % (key, self.pending_hint))
                    self.pending_hint = None
                continue
            if is_hint(raw):
                self.pending_hint = clean(raw)
                continue
            if "<GM" in raw:
                # "You got the <GM ...>." / "Will you take the <GM ...>?": the game fills in the
                # item's name, which it lays out on its own just before.
                # (laid out before it in this batch, or just before; else the one laid out after it:
                # the map of the mansion's name, "MANSION 1F", comes after, and the name from the
                # status screen left over before it was put in instead)
                name = batch_name or (self.last_name if now0 - getattr(self, "last_name_at", 0) < 1.0 else "")
                if not name:
                    name = next((t for j, t in names_after if j > i), "") or self.last_name
                if name.isupper():
                    name = name.title().replace("1F", "1F").replace("2F", "2F")
                    name = re.sub(r"\b(\d)f\b", lambda m: m.group(1) + "F", name)
                t = clean(self.GM.sub(name.replace("\\", ""), raw))
                if name and name in lines:
                    lines.remove(name)
                filled = name
                if t:
                    lines.append(t)
                continue
            stripped = raw.strip()
            if stripped and all(c in "▼▲ \r\n" for c in stripped):
                arrows = True           # the arrows of the message window, laid out again when it turns a page
                continue
            if "<PAGE" in raw.upper():
                # A message of several pages is said whole when it opens. Turning a page lays it
                # out again: that is not said again.
                m, now = self.msg, time.monotonic()
                if m and raw == m["raw"] and (m["left"] > 0 or now - m["time"] < 0.5) and now - m["time"] < 90:
                    turned = True
                    continue
                parts = [clean(p) for p in re.split(r"<PAGE[^<>]*>", raw, flags=re.I)]
                parts = [p for p in parts if p]
                if parts:
                    self.msg = {"raw": raw, "left": len(parts) - 1, "time": now, "parts": parts,
                                "joined": " ".join(parts)}
                    paged = True
                    lines.append(" ".join(parts))
                continue
            t = clean(raw)
            if not t:
                continue
            if "<" not in raw and len(t) <= 40:
                self.last_name = t
                self.last_name_at = now0
                batch_name = t
            if filled and t.lower() == filled.lower():
                continue                # the name again, already said in the line above
            lines.append(t)
        if turned or (arrows and not paged and not lines):
            self.page_turned()
        # drop repeats within this batch, keep order; and a short line that is only part of a longer
        # one ("MANSION 1F" after "You got the map of the Mansion 1F.")
        seen, out = set(), []
        for t in lines:
            # (whole words only, and never a box's YES or NO: "NO" is in "do not save")
            if len(t) <= 40 and t.upper() not in ("YES", "NO") and any(
                    t != o and re.search(r"(?<!\w)%s(?!\w)" % re.escape(t.lower()), o.lower()) for o in lines):
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
        hs, hout = set(), []
        for h in hints:
            if h not in hs:
                hs.add(h)
                hout.append(h)
        return out, hout
