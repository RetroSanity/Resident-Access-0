"""Reading the game's message files (MT Framework GMD, in nativePC/arc/message/msg_eng.arc)."""
import os
import struct

from . import sbc


def parse(g):
    """{label: text} and the list of texts, in order."""
    if g[:4] != b"GMD\0":
        return {}, []
    nlabels, nsections, label_size, section_size, name_size = struct.unpack_from("<5I", g, 0x14)
    texts = g[len(g) - section_size:].split(b"\0")[:nsections]
    texts = [t.decode("utf-8", "replace") for t in texts]
    labels_raw = g[len(g) - section_size - label_size:len(g) - section_size].split(b"\0")[:nlabels]
    labels = [x.decode("ascii", "replace") for x in labels_raw]
    # labels go with the first texts in order
    return dict(zip(labels, texts)), texts


_cache = {}


def archive(game_dir, lang="eng"):
    key = (game_dir, lang)
    if key not in _cache:
        path = os.path.join(game_dir, "nativePC", "arc", "message", "msg_%s.arc" % lang)
        d, ents = sbc.read_arc(path)
        out = {}
        for name, entry in ents.items():
            if entry[0] == 0x242BB29A:
                out[name.split("\\")[-1]] = parse(sbc.unpack(d, entry))
        _cache[key] = out
    return _cache[key]


def texts(game_dir, name, lang="eng"):
    return archive(game_dir, lang).get("%s_%s" % (name, lang), ({}, []))[1]


_pages = {}


def file_pages(game_dir, item, lang="eng"):
    key = (game_dir, item, lang)
    if key not in _pages:
        _pages[key] = _file_pages(game_dir, item, lang)
    return _pages[key]


def _file_pages(game_dir, item, lang="eng"):
    """The pages of a file (item numbers 150 and on: "fa01" is item 150), as plain text, from
    message_file_text. A file's last page is kept under the next file's number, page 1."""
    import re
    labels = archive(game_dir, lang).get("message_file_text_%s" % lang, ({}, []))[0]
    no = item - 149

    def pages_of(n):
        out = []
        for key, text in labels.items():
            m = re.match(r"fa([0-9a-f]{2})_(\d+)(_pc)?$", key)
            if m and int(m.group(1), 16) == n:
                out.append((int(m.group(2)), bool(m.group(3)), text))
        if any(pc for _, pc, _ in out):
            pcs = {p for p, pc, _ in out if pc}
            out = [o for o in out if o[1] or o[0] not in pcs]
        return sorted(out)
    pages = [t for p, pc, t in pages_of(no) if p >= 2]
    nxt = [t for p, pc, t in pages_of(no + 1) if p == 1]
    pages += nxt
    clean = []
    for t in pages:
        t = re.sub(r"<PAGE>", " ", t)
        t = re.sub(r"<[^>]*>", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t and t not in ("Invalid Message", "ダミー"):
            clean.append(t)
    return clean
