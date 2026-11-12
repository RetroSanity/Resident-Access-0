"""Setting up Resident Access 0 on a new computer.

Finds Resident Evil 0 HD Remaster on Steam, puts the controller add-on
(xinput1_3.dll) in its folder, and writes ResidentAccess0.txt beside it so
the add-on knows where this folder is: from then on the mod starts by itself
whenever the game starts. A controller add-on that was already there and is
not ours is kept as xinput1_3.dll.before-resident-access.

  python -m re0access.install              set up (or repair)
  python -m re0access.install --uninstall  take the add-on out of the game folder
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys

from .version import VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSTALL_JSON = os.path.join(ROOT, "install.json")
ADDON = os.path.join(ROOT, "game", "xinput1_3.dll")
APPID = "339340"                                    # Resident Evil 0 / biohazard 0 HD REMASTER on Steam
GAME_EXE = "re0hd.exe"
GAME_FOLDER = "Resident Evil 0"
DLL = "xinput1_3.dll"
DLLS = ("xinput1_3.dll",)
POINTER = "ResidentAccess0.txt"
BACKUP = DLL + ".before-resident-access"
OURS = b"ResidentAccess0Pad"                       # in every build of our add-on

_speech = None


def say(text):
    global _speech
    print(text)
    try:
        if _speech is None:
            from .speech import Speech
            _speech = Speech()
        _speech.say(text)
    except Exception:
        pass


def ask(text):
    say(text)
    try:
        return input("> ").strip().strip('"')
    except EOFError:
        return ""


def load_install():
    try:
        with open(INSTALL_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_install(data):
    with open(INSTALL_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


# ------------------------------------------------------------ finding the game
def steam_roots():
    out = []
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    out.append(os.path.normpath(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            out.append(os.path.join(os.environ[env], "Steam"))
    seen, uniq = set(), []
    for p in out:
        if p.lower() not in seen:
            seen.add(p.lower())
            uniq.append(p)
    return uniq


def steam_libraries():
    libs = []
    for root in steam_roots():
        libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    # Steam libraries made on other drives, in case Steam's own list is out of date
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for name in ("SteamLibrary", "Steam", r"Games\Steam", r"Program Files (x86)\Steam", r"Program Files\Steam"):
            libs.append("%s:\\%s" % (letter, name))
    seen, uniq = set(), []
    for p in libs:
        if p.lower() not in seen and os.path.isdir(os.path.join(p, "steamapps")):
            seen.add(p.lower())
            uniq.append(p)
    return uniq


def find_games():
    found = []
    for lib in steam_libraries():
        folders = [GAME_FOLDER]
        acf = os.path.join(lib, "steamapps", "appmanifest_%s.acf" % APPID)
        try:
            with open(acf, encoding="utf-8", errors="replace") as fh:
                m = re.search(r'"installdir"\s+"([^"]+)"', fh.read())
            if m:
                folders.insert(0, m.group(1))
        except OSError:
            pass
        for name in folders:
            d = os.path.join(lib, "steamapps", "common", name)
            if os.path.isfile(os.path.join(d, GAME_EXE)) and os.path.normcase(d) not in map(os.path.normcase, found):
                found.append(d)
    return found


def is_game_folder(d):
    return bool(d) and os.path.isfile(os.path.join(d, GAME_EXE))


def choose_game(cfg):
    found = find_games()
    old = cfg.get("game")
    if is_game_folder(old):
        found = [old] + [f for f in found if os.path.normcase(f) != os.path.normcase(old)]
    if len(found) == 1:
        say("Found Resident Evil 0 HD Remaster in " + found[0] + ".")
        return found[0]
    if len(found) > 1:
        for i, d in enumerate(found, 1):
            say("%d: %s" % (i, d))
        while True:
            pick = ask("I found more than one copy of the game. Type the number of the one you play, then press Enter.")
            if pick.isdigit() and 1 <= int(pick) <= len(found):
                return found[int(pick) - 1]
    while True:
        path = ask("I could not find Resident Evil 0 HD Remaster. Paste the game's folder (the one with re0hd.exe in it), "
                   "then press Enter. Or just press Enter to stop.")
        if not path:
            return None
        if path.lower().endswith(".exe"):
            path = os.path.dirname(path)
        if is_game_folder(path):
            return os.path.abspath(path)
        say("There is no re0hd.exe in that folder.")


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + GAME_EXE, "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return GAME_EXE in out.lower()
    except Exception:
        return False


# ------------------------------------------------------------ the add-on
def is_ours(path):
    try:
        with open(path, "rb") as fh:
            return OURS in fh.read()
    except OSError:
        return False


def same_file(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def put_addon(game):
    """Copy our add-on (both names) into the game folder. Returns True if a file changed."""
    changed = False
    for name in DLLS:
        src = os.path.join(ROOT, "game", name)
        if os.path.isfile(src):
            changed = put_one(game, name, src) or changed
    return changed


def put_one(game, name, src):
    """Copy one DLL in. Works while the game is running too: Windows lets a loaded DLL be renamed,
    so the old one is moved aside (and deleted next time). Returns True if the file changed."""
    ADDON, BACKUP = src, name + ".before-resident-access"
    dst = os.path.join(game, name)
    old = dst + ".old"
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass
    if os.path.exists(dst):
        if same_file(dst, ADDON):
            return False
        if not is_ours(dst) and not os.path.exists(os.path.join(game, BACKUP)):
            shutil.copy2(dst, os.path.join(game, BACKUP))     # someone else's: keep it
        try:
            os.remove(dst)
        except OSError:
            if os.path.exists(old):
                os.replace(dst, dst + ".old2")
            else:
                os.replace(dst, old)                         # in use by the game
    tmp = dst + ".new"
    shutil.copyfile(ADDON, tmp)
    os.replace(tmp, dst)
    return True


def write_pointer(game):
    with open(os.path.join(game, POINTER), "w", encoding="utf-8") as fh:
        fh.write(ROOT + "\n")


def take_out(game):
    for name in DLLS:
        dst = os.path.join(game, name)
        if os.path.exists(dst) and is_ours(dst):
            try:
                os.remove(dst)
            except OSError:
                os.replace(dst, dst + ".old")
        backup = os.path.join(game, name + ".before-resident-access")
        if os.path.exists(backup) and not os.path.exists(dst):
            os.replace(backup, dst)
    pointer = os.path.join(game, POINTER)
    if os.path.exists(pointer):
        os.remove(pointer)


# ------------------------------------------------------------ admin rights
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin(args):
    """Run this setup again with administrator rights (the game folder may need them)."""
    code = "import sys; sys.path.insert(0, %r); from re0access import install; install.main(%r)" % (ROOT, list(args))
    params = subprocess.list2cmdline(["-c", code])
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, ROOT, 1)
    return r > 32


# ------------------------------------------------------------ setup
def install(args):
    say("Resident Access 0 %s setup." % VERSION)
    if not os.path.isfile(ADDON):
        say("The game folder inside Resident Access 0 is missing. Please unzip all of Resident Access 0 and run this again.")
        return
    cfg = load_install()
    game = given_game(args) or choose_game(cfg)
    if not game:
        say("Setup stopped. Nothing was changed.")
        return
    try:
        changed = put_addon(game)
        write_pointer(game)
    except PermissionError:
        if not is_admin() and "--elevated" not in args:
            say("Windows needs permission to change the game folder. Please say yes on the next screen.")
            if run_as_admin(list(args) + ["--elevated", "--game=" + game]):
                return
        say("I could not write to the game folder. Right click Install Resident Access 0 and choose Run as administrator.")
        return
    cfg["game"] = game
    cfg["version"] = VERSION
    save_install(cfg)

    if changed and game_running():
        say("The game is open. Close it and start it again so the new controller add-on is used.")
    say("All done. Start Resident Evil 0 HD Remaster from Steam, and Resident Access 0 starts with it. "
        "It says Resident Access 0 ready when it has found the game. "
        "It speaks through your screen reader, or Windows' own voice if none is running. "
        "The mod updates itself when a new version comes out.")


def uninstall(args):
    say("Taking Resident Access 0 out of the game.")
    cfg = load_install()
    game = given_game(args) or (cfg.get("game") if is_game_folder(cfg.get("game")) else None)
    if not game:
        games = [g for g in find_games() if os.path.exists(os.path.join(g, POINTER)) or is_ours(os.path.join(g, DLL))]
        game = games[0] if games else choose_game({})
    if not game:
        return
    try:
        take_out(game)
    except PermissionError:
        if not is_admin() and "--elevated" not in args:
            say("Windows needs permission to change the game folder. Please say yes on the next screen.")
            if run_as_admin(list(args) + ["--elevated", "--game=" + game]):
                return
        say("I could not change the game folder. Right click Uninstall Resident Access 0 and choose Run as administrator.")
        return
    say("Done. The game no longer starts Resident Access 0. You can delete this folder now.")


def given_game(args):
    """The game folder passed on (when setup runs again with administrator rights)."""
    for a in args:
        if a.startswith("--game=") and is_game_folder(a.split("=", 1)[1]):
            return a.split("=", 1)[1]
    return None


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    try:
        if "--uninstall" in args:
            uninstall(args)
        else:
            install(args)
    except Exception as exc:
        say("Setup stopped because of an error: %s" % exc)
    try:
        input("Press Enter to close. ")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
