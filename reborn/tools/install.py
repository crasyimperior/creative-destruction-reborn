"""Creative Destruction Reborn - installer / uninstaller.

Run from the game folder (the one containing client.exe):
    python reborn\\tools\\install.py            install
    python reborn\\tools\\install.py --uninstall restore the original files
"""
import base64
import json
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REBORN = os.path.dirname(TOOLS)
GAME = os.path.dirname(REBORN)
BACKUP = os.path.join(REBORN, "backup")
DOCS = os.path.join(GAME, "Documents")
CLIENT_MOD = os.path.join(REBORN, "client_mod")

# AES key of the game's patch_config.json (patcher/PatchExt.py, AES-256-CFB8)
PATCH_KEY = b'\xa7\xddLss6\x05\xec\xea\x8a\xd2m\x07\x82\xc3\x83\x16\xf7af\x8b\x8b\x10\xe0\xaa\xfe\x1ffDZY!'

# files the installer writes into the game folder: (source in client_mod, destination)
MOD_FILES = [
    ("switches.nxs", os.path.join(DOCS, "script", "switches.nxs")),
    ("nonpatch_user_data.dat", os.path.join(DOCS, "nonpatch_user_data.dat")),
    ("annoucement.txt", os.path.join(DOCS, "annoucement.txt")),
]


def say(msg):
    print("[Reborn] " + msg)


def need_crypto():
    try:
        from Crypto.Cipher import AES  # noqa: F401
    except ImportError:
        say("Installing pycryptodome (needed to edit patch_config.json)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pycryptodome"])
    from Crypto.Cipher import AES
    return AES


# the game copies patch_config.json into Documents on first run and reads that copy afterwards
PATCH_CONFIGS = [os.path.join(GAME, "patch_config.json"), os.path.join(DOCS, "patch_config.json")]


def patch_config(ignore_patch):
    for path in PATCH_CONFIGS:
        if os.path.exists(path):
            patch_config_file(path, ignore_patch)


def patch_config_file(path, ignore_patch):
    AES = need_crypto()
    raw = base64.b64decode(open(path, "rb").read())
    iv, body = raw[:16], raw[16:]
    cfg = json.loads(AES.new(PATCH_KEY, AES.MODE_CFB, iv, segment_size=8).decrypt(body))
    cfg["is_ignore_patch"] = 1 if ignore_patch else 0
    enc = AES.new(PATCH_KEY, AES.MODE_CFB, iv, segment_size=8).encrypt(json.dumps(cfg).encode())
    open(path, "wb").write(base64.b64encode(iv + enc))


def backup(path):
    if os.path.exists(path):
        dst = os.path.join(BACKUP, os.path.relpath(path, GAME))
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)


def install():
    if not os.path.exists(os.path.join(GAME, "client.exe")) or not os.path.exists(os.path.join(GAME, "script.npk")):
        say("ERROR: put the 'reborn' folder inside the Creative Destruction folder (next to client.exe).")
        return 1
    for path in PATCH_CONFIGS:
        backup(path)
    for _, dst in MOD_FILES:
        backup(dst)
    patch_config(ignore_patch=True)
    say("patch_config.json: update check disabled (fixes error 902)")
    for src, dst in MOD_FILES:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(CLIENT_MOD, src), dst)
        say("installed " + os.path.relpath(dst, GAME))
    say("Done. Start the game with 'Lancer hors ligne - Play Offline.bat'.")
    return 0


def uninstall():
    for _, dst in MOD_FILES:
        if os.path.exists(dst):
            os.remove(dst)
    if os.path.isdir(BACKUP):
        for root, _, files in os.walk(BACKUP):
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join(GAME, os.path.relpath(src, BACKUP))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                say("restored " + os.path.relpath(dst, GAME))
    else:
        patch_config(ignore_patch=False)
    say("Original game restored.")
    return 0


if __name__ == "__main__":
    sys.exit(uninstall() if "--uninstall" in sys.argv else install())
