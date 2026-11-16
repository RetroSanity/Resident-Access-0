"""Short names for things to check, from what the game says about them
(the same rules as Resident Access HD), and the game's text tags taken out."""
import re


def clean(text):
    """The game's message text as plain words: tags, colours and line breaks out."""
    t = re.sub(r"<PAGE>", " ", text or "")
    t = re.sub(r"<[^>]*>", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# Leading words that say nothing about what the thing is (longest first).
ARTICLES = ["an assortment of", "a collection of", "a selection of", "a huge collection of", "a couple of",
            "a number of", "a bunch of", "a pile of", "some kind of", "just some", "various", "some", "an", "a", "the"]
# Words at which the name of the thing has ended.
VERBS = {"pertaining", "belonging", "concerning", "regarding", "is", "are", "was", "were", "has", "have", "had", "blocks", "block", "stands", "stand", "shines",
         "lie", "lies", "look", "looks", "decorates", "smells", "points", "gives", "doesn't", "don't",
         "appears", "appear", "seem", "seems", "shows", "crushing", "crush", "drips", "drip", "leaking",
         "made", "carved", "lined", "line", "stretches", "glow", "containing", "featuring", "depicting",
         "hung", "fallen", "tossed", "scattered", "stacked", "piled", "packed", "labeled", "full",
         "covered", "cluttered", "crawling", "sticking", "swimming", "surrounds", "surround", "smiles",
         "contain", "contains", "illuminated", "collapsed", "mounted", "suspended", "towering", "headed",
         "soaked", "burned", "tangled", "lying", "with", "that", "that's", "which", "you've", "you",
         "it's", "it", "here", "there", "but", "must", "can", "can't", "will", "won't", "would", "should"}
PREPOSITIONS = {"of", "on", "in", "for", "from", "against", "over", "at", "to", "by", "through", "under"}
# Messages that need a hand-picked name: (pattern in the lower-case first sentence, name).
FIXED = [
    (r"release lever", "Hookshot release"),
    (r"hand-operated brake", "Brake, magnetic card reader"),
    (r"hookshot is attached to the wall", "Hookshot on the wall"),
    (r"^there's nothing else here|no need to use it anymore", "Nothing else"),
    (r"go outside|outside the train", "Door to outside"),
    (r"against the window|out the window|through the window", "Window"),
    (r"poor (guy|man|woman|soul)|this (guy|man|woman) |dead body|corpse", "Dead body"),
    (r"^better |you can't go back|you don't need to go back|got to protect", "Not yet"),
    (r"darkness and fog", "Window"),
    (r"^delete", "Nothing much"),
    (r"^it has no effect", "Nothing happens here"),
    (r"nothing (catches|particularly|special|useful|here|in|on|but|of)|doesn't seem to be anything|^they're empty|no time to relax",
     "Nothing much"),
    (r"(?<![bn])locked|won't open|won't budge|blocked from the other side|held tightly shut", "Locked door"),
    (r"^caution", "Warning sign"),
    (r"this one's been dead", "Dead body"),
    (r"water still works", "Tap"),
    (r"been serviced", "Old machine"),
    (r"candle's flame", "Candle"),
    (r"could this be (a |an )?(.+)\?", "Cap:2"),
    (r"you can feel the heat", "Hot light"),
    (r"your view is blocked", "Thick trees"),
    (r"too dark to see", "Dark corner"),
    (r"from here you've got a good view", "Window"),
    (r"inside the cardboard box", "Cardboard box"),
    (r"lines carved into the relief", "Relief carving"),
    (r"these footprints", "Footprints"),
    (r"picture stands", "Picture stands and statues"),
    (r"lined with portraits", "Portraits on the wall"),
    (r"glass has been broken", "Broken glass case"),
    (r"power to the elevator", "Elevator, no power"),
    (r"power has been restored", "Power switch"),
    (r"someone took something", "Empty spot"),
    (r"drips from the sink", "Sink"),
    (r"leaking from the tank", "Leaking tank"),
    (r"water is all gone", "Drained passage"),
    (r'labeled "dynamite"', "Dynamite"),
    (r'labeled "chemicals"', "Chemical boxes"),
    (r"materials are packed", "Packed materials"),
    (r"materials have been piled", "Piled materials"),
    (r"rack full of wine", "Wine rack"),
    (r"kinds of dolls", "Old dolls"),
    (r"contents are still intact", "Cupboard"),
    (r"the title is written", "Book title"),
    (r"something written on the wall", "Writing on the wall"),
    (r"something written in the corner of the picture", "Writing on the picture"),
    (r"an? inscription on the shield", "Inscription on the shield"),
    (r"^chris lies", "Chris"), (r"^jill lies", "Jill"), (r"^enrico is dead", "Enrico"),
    (r"corpse of your comrade, kenneth", "Kenneth's body"),
]


def quoted_name(t):
    """A message that starts with words in quotes is something written: a sign, a menu, a
    picture's title ("Picture, Join the Party!")."""
    end = t.find('"', 1)
    title = t[1:end] if end > 1 else t[1:]
    rest = t[end + 1:] if end > 1 else ""
    title = re.split(r"(?<=[.!?])\s", title.strip())[0]
    words = title.strip(' ".').split()
    if len(words) > 6:
        words = words[:2] if len(words) > 6 else words
    title = " ".join(words).rstrip(",;:")
    low = t.lower()
    if "picture" in low or "painting" in low or "photo" in low:
        kind = "Picture"
    elif "special" in low or "menu" in low:
        kind = "Menu"
    elif "poster" in low:
        kind = "Poster"
    else:
        kind = "Sign"
    return "%s, %s" % (kind, title) if title else kind


def short_name(text):
    """A short name for a thing, from the message the game shows when you
    check it, or None when the message is not about a thing at all."""
    t = re.sub(r"\[[0-9A-F]+\]|\s*/\s*", " ", text).strip()
    if t.startswith('"') and not t.lower().startswith('"caution'):
        return quoted_name(t)
    t = re.split(r"(?<=[.!?])\s", t)[0].strip()
    t = re.sub(r"^here's", "There's", t)
    if not t:
        return None
    if t.startswith('"') and not t.lower().startswith('"caution'):
        words = t.strip('"').split()
        return "Note: " + " ".join(words[:5]) + ("..." if len(words) > 5 else "")
    t = t.strip('"')
    low = t.lower()
    for pat, name in FIXED:
        m = re.search(pat, low)
        if not m:
            continue
        if name.startswith("Cap:"):
            name = m.group(int(name[4:]))
            return name[0].upper() + name[1:]
        return name
    m = re.match(r"^(these|this|it|they) (appears?|seems?) to be (a |an |the |some )?", low)
    if m:
        t = t[m.end():]
        low = t.lower()
    m = re.match(r"^(it|this) looks? like (a |an |the )?", low)
    if m:
        t = t[m.end():]
    m = re.match(r"^(it's|it is|that's|they're) ", t, flags=re.I)
    if m:
        t = t[m.end():]
    m = re.match(r"^(there's|there is|there are) ", t, flags=re.I)
    if m:
        t = t[m.end():]
    elif re.match(r"^this ", t, flags=re.I):
        t = t[5:]
    elif re.match(r"^these ", t, flags=re.I):
        t = t[6:]
    low = t.lower()
    for a in ARTICLES:
        if low.startswith(a + " "):
            t = t[len(a) + 1:]
            break
    words = []
    for w in t.replace(",", " ").split():
        if w.lower() == "but":
            continue
        if w.strip('",.!?').lower() in VERBS:
            break
        words.append(w.strip('".!?'))
    if len(words) > 7:
        cut = max([i for i in range(2, 7) if words[i].lower() in PREPOSITIONS] or [6])
        words = words[:cut]
    while words and words[-1].lower() in PREPOSITIONS | {"and", "inside"}:
        words.pop()
    if not words:
        return None
    name = " ".join(words)
    return name[0].upper() + name[1:]


WEAK = {"Not yet", "Nothing much", "Train", "Nothing else", "No need to use"}


def best_name(texts):
    """The best short name from several messages a thing can show."""
    if any("go outside" in t.lower() for t in texts if t):
        return "Door to outside"
    if any("train is out of control" in t.lower() for t in texts if t):
        return "Rear exit"
    names_ = [short_name(t) for t in texts if t]
    names_ = [n for n in names_ if n and n.isascii()]
    for n in names_:
        if n not in WEAK:
            return n
    return names_[0] if names_ else None
