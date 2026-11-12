"""Updating Resident Access 0 from its GitHub releases.

When the mod starts it asks GitHub for the newest release. If that is newer
than this copy, the release's zip is downloaded and unpacked over this folder
(keeping settings.json, install.json, the log and the bundled Python), the
controller add-on in the game folder is brought up to date too, and the mod
restarts. "Update Resident Access 0.bat" does the same check by hand.

To publish an update: raise VERSION in version.py, zip the release folder,
and make a GitHub release whose tag is the new version (v1.0.1) with the zip
attached.
"""
import io
import json
import logging
import os
import shutil
import sys
import urllib.request
import zipfile

from .version import VERSION, REPO

log = logging.getLogger("update")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KEEP = {"settings.json", "install.json", "re0access.log", "re0access.old.log", "re0access.pid", "learned_spots.json", "diagnostics.txt",
        "repair.json"}
SKIP_DIRS = {"python", "dev"}      # Python is in use while we run; dev is never shipped
RESTART = 3                        # exit code that asks the start file to run the mod again


def parse(version):
    parts = []
    for p in version.strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (3 - len(parts)))


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "Resident-Access-0/" + VERSION,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest(timeout=6):
    """(version, zip url) of the newest release, or None."""
    data = json.loads(_get("https://api.github.com/repos/%s/releases/latest" % REPO, timeout))
    tag = data.get("tag_name") or data.get("name") or ""
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".zip"):
            return tag, asset["browser_download_url"]
    return None


def is_release_copy():
    """Only a copy installed from a release updates itself (not a developer's)."""
    return os.path.isdir(os.path.join(ROOT, "python"))


def updates_on():
    try:
        with open(os.path.join(ROOT, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh).get("updates", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def _order(parts):
    """The order files are put in place: everything else first, then the mod's code, and its
    version last of all, so an update that stops half way is tried again next time."""
    rel = "/".join(parts)
    if rel == "re0access/version.py":
        return 2
    return 1 if parts[0] == "re0access" else 0


def _replace(tmp, dest):
    try:
        os.replace(tmp, dest)
        return
    except PermissionError:
        if not os.path.exists(dest):
            raise
    # A file that is in use (a DLL the running mod has loaded) can't be written over, but Windows
    # lets it be renamed: move it aside and put the new one in its place. The old one is cleared
    # up next time.
    old = dest + ".old"
    try:
        os.remove(old)
    except OSError:
        pass
    os.replace(dest, old)
    os.replace(tmp, dest)


def clear_old():
    """Remove files moved aside by an earlier update (once nothing holds them any more)."""
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != "__pycache__"]
        for f in files:
            if f.endswith(".old") or f.endswith(".new"):
                try:
                    os.remove(os.path.join(base, f))
                except OSError:
                    pass


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not any(n.replace("\\", "/").split("/")[-1] == "__main__.py" for n in names):
            raise ValueError("that zip is not Resident Access 0")
        # Releases hold everything inside one folder: strip it.
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        todo = []
        for name in names:
            rel = name.split("/", 1)[1] if strip else name
            parts = [p for p in rel.replace("\\", "/").split("/") if p]
            if not parts or parts[0] in SKIP_DIRS or "/".join(parts) in KEEP or ".." in parts \
                    or ":" in parts[0] or "__pycache__" in parts:
                continue
            dest = os.path.join(ROOT, *parts)
            body = z.read(name)
            try:
                with open(dest, "rb") as fh:
                    if fh.read() == body:
                        continue            # (the same as the one here: nothing to do)
            except OSError:
                pass
            todo.append((_order(parts), dest, body))
    # Every new file is written beside its old one first; only once all of them are there are
    # they swapped in.
    todo.sort(key=lambda t: t[0])
    for _, dest, body in todo:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".new", "wb") as out:
            out.write(body)
    for _, dest, _ in todo:
        _replace(dest + ".new", dest)
    log.info("update: %d files replaced", len(todo))


def repair(exc_text):
    """The mod could not start (its files are broken, an update stopped half way): log why, and
    put the newest release back over this folder. Returns True if it did, and the mod should be
    started again. Tried at most once an hour."""
    logging.basicConfig(filename=os.path.join(ROOT, "re0access.log"), level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s", encoding="utf-8")
    log.error("the mod could not start:\n%s", exc_text)
    if not is_release_copy():
        return False
    import time
    mark = os.path.join(ROOT, "repair.json")
    try:
        with open(mark, encoding="utf-8") as fh:
            if time.time() - json.load(fh).get("at", 0) < 3600:
                log.info("repair: already tried in the last hour")
                return False
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(mark, "w", encoding="utf-8") as fh:
            json.dump({"at": time.time()}, fh)
    except OSError:
        pass
    from .install import say
    try:
        found = latest(timeout=20)
        if not found:
            return False
        say("Resident Access 0 could not start. Downloading version %s again to repair it. Please wait."
            % found[0].lstrip("vV"))
        unpack(_get(found[1], 180))
    except Exception:
        log.exception("repair failed")
        say("Resident Access 0 could not start, and could not repair itself. Please download it again "
            "from its GitHub page.")
        return False
    log.info("repair: put version %s back", found[0])
    say("Repaired. Starting Resident Access 0.")
    return True


def refresh_addon():
    """Put the new controller add-on in the game folder, if it changed. Returns a problem to tell
    the player, or None."""
    from . import install
    game = install.load_install().get("game")
    if not install.is_game_folder(game):
        return None
    try:
        changed = install.put_addon(game)
        install.write_pointer(game)
    except OSError as exc:
        log.warning("could not update the add-on: %s", exc)
        return "Run Install Resident Access 0 again to finish the update."
    if changed and install.game_running():
        return "The game's controller add-on was updated too: close the game and start it again to use it."
    return None


def check(say, by_hand=False):
    """Returns True if an update was installed and the mod should restart."""
    if not is_release_copy():
        if by_hand:
            say("This copy of Resident Access 0 is not from a release, so it does not update itself.")
        return False
    try:
        clear_old()
    except Exception:
        pass
    if not by_hand and not updates_on():
        return False
    try:
        found = latest(timeout=15 if by_hand else 6)
    except Exception as exc:          # offline, GitHub busy: just carry on
        log.info("update check failed: %s", exc)
        if by_hand:
            say("I could not reach GitHub to look for updates. Check your internet connection.")
        return False
    if not found or parse(found[0]) <= parse(VERSION):
        if by_hand:
            say("You have the newest version of Resident Access 0, %s." % VERSION)
        return False
    tag, url = found
    say("Updating Resident Access 0 to version %s. Please wait." % tag.lstrip("vV"))
    try:
        unpack(_get(url, 180))
    except Exception:
        log.exception("update failed")
        say("The update could not be installed. Carrying on with version %s." % VERSION)
        return False
    problem = refresh_addon()
    say("Update installed." + (" " + problem if problem else ""))
    return True


def restart_mod():
    """Close a copy of the mod that is running the old version, and start the new one if the game
    is open (otherwise it starts with the game next time)."""
    import ctypes
    import subprocess
    import time
    from . import install
    k32 = ctypes.WinDLL("kernel32")
    k32.CreateEventW.restype = ctypes.c_void_p
    ev = k32.CreateEventW(None, True, False, "Local\\ResidentAccess0Quit")
    k32.SetEvent(ctypes.c_void_p(ev))
    time.sleep(1.5)
    k32.ResetEvent(ctypes.c_void_p(ev))
    if install.game_running():
        bat = os.path.join(ROOT, "Start Resident Access 0.bat")
        subprocess.Popen('cmd.exe /c start "Resident Access 0" /min "%s"' % bat, cwd=ROOT,
                         creationflags=0x08000000)


def main():
    from .install import say
    logging.basicConfig(filename=os.path.join(ROOT, "re0access.log"), level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s", encoding="utf-8")
    say("Looking for an update to Resident Access 0.")
    if check(say, by_hand=True):
        restart_mod()
        say("The new version is ready.")
    try:
        input("Press Enter to close. ")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
