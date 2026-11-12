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
