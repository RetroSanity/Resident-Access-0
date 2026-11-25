"""Which rooms lead to which, from every room's doors: to say which door to take towards a room
you are not in (your partner's, when you are split up)."""
import glob
import logging
import os
import threading
from collections import deque

from . import room as R
from .game import STAGE_CODES, STAGE_FIRST, scene_code, scene_words

# (ways through only your partner can take - Billy lifts Rebecca up into the vent)
NOT_WALKED = ("Air vent", "Hole")


def place(no):
    """A room as a place: its name in its area (the basement's Storage Room is not the Training
    Facility's; the states of one room - the Torture Room before and after its floor gives way -
    are one place)."""
    return scene_code(no)[0], scene_words(no)


def scene_of(stage, room):
    return STAGE_FIRST[stage] + room if stage < len(STAGE_FIRST) else None


class RoomGraph:
    def __init__(self, gd):
        self.gd = gd
        self.links = None           # place -> {next place}
        self.busy = False

    def build(self):
        links = {}
        for arc in sorted(glob.glob(os.path.join(self.gd, "nativePC", "arc", "scene", "*.arc"))):
            code = os.path.basename(arc)[:-4]
            if len(code) != 4 or code[:2] not in STAGE_CODES:
                continue
            try:
                no = STAGE_FIRST[STAGE_CODES.index(code[:2])] + int(code[2:], 16)
                rd = R.RoomData(self.gd, code, no, scene_of, scene_words)
            except Exception:
                continue
            here = place(no)
            for t in rd.things:
                if t.kind != "door" or getattr(t, "target", None) is None or t.name.startswith(NOT_WALKED):
                    continue
                there = place(t.target)
                if there != here:
                    links.setdefault(here, set()).add(there)
        self.links = links
        logging.info("room links: %d rooms", len(links))

    def ready(self):
        """True once built (building it the first time, in the background)."""
        if self.links is not None:
            return True
        if not self.busy:
            self.busy = True

            def run():
                try:
                    self.build()
                except Exception:
                    logging.exception("room links")
                    self.links = {}
            threading.Thread(target=run, daemon=True).start()
        return False

    def way(self, here, there):
        """[places] from here to there (here first), or None."""
        if not self.ready() or here == there:
            return None
        prev = {here: None}
        q = deque([here])
        while q:
            a = q.popleft()
            if a == there:
                path = [a]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            for b in sorted(self.links.get(a, ())):
                if b not in prev:
                    prev[b] = a
                    q.append(b)
        return None
