# Creative Destruction Reborn

Play **Creative Destruction** (PC, NetEase, servers closed in June 2022) again, offline, on your own computer.

This package contains **no game files**. It is a small local server plus a client mod that you install
into **your own copy** of the official PC game.

[Français](#français) · [English](#english)

---

## Français

### Ce qu'il faut

- Le jeu **Creative Destruction PC** officiel (version `3.0.210.0`), installé dans un dossier
  (celui qui contient `client.exe` et `script.npk`).
- **Python 3.8 ou plus récent** : <https://www.python.org/downloads/>
  Pendant l'installation de Python, coche **« Add python.exe to PATH »**.
- Une connexion internet **pour l'installation uniquement** (elle télécharge le module Python `pycryptodome`).

### Vérifier ta copie du jeu

Le jeu n'est plus distribué officiellement. Si tu as gardé ton installation, vérifie que c'est la bonne version
avec les empreintes SHA-256 ci-dessous (dans PowerShell : `Get-FileHash <fichier>`) :

| Fichier | SHA-256 |
|---|---|
| zip de l'installateur du jeu | `3f5008c3c3c953f005dbb3681b2a24ae4abd71199435c8948b60fb7173a17be5` |
| `script.npk` (15 607 644 octets) | `f47bcc7cbb261897072a53af269fc839a54e7cfada9d9a0268d8b7165b1738e1` |

### Installation

1. Décompresse l'archive **dans le dossier du jeu**, à côté de `client.exe`.
   Tu dois obtenir :
   ```
   Creative Destruction\
     client.exe
     script.npk
     ...
     reborn\
     Installer - Install.bat
     Lancer hors ligne - Play Offline.bat
     Desinstaller - Uninstall.bat
     README.md
   ```
2. Double-clique sur **`Installer - Install.bat`**.
   Il désactive la mise à jour du jeu (erreur 902) et installe le mod dans `Documents\`.
   Les fichiers d'origine sont sauvegardés dans `reborn\backup\`.

### Jouer

1. Double-clique sur **`Lancer hors ligne - Play Offline.bat`**.
   Une fenêtre noire réduite (le serveur) s'ouvre, puis le jeu.
   **Ne ferme pas et ne clique pas dans la fenêtre du serveur pendant que tu joues.**
2. Dans le jeu : **Confirmer** → **Jouer**. Crée ton personnage si on te le demande.
3. Pour une partie : **Lancer**. Tu arrives sur l'île de départ.
   Appuie sur **²** (touche sous Échap) pour ouvrir la console, tape **`start`** puis **Entrée** :
   compte à rebours, présentation des joueurs, bus, puis saut.

### Ce qui fonctionne

- Connexion sans compte NetEase, lobby, profil, changement de pseudo
- 999 999 999 diamants / or / étoiles, tous les skins et objets permanents débloqués
- Équiper tenues et skins, bouton ♂/♀ dans le profil pour changer de personnage
- Partie : île de départ, compte à rebours, présentation, bus, saut en parachute, pioche

### Prévu pour la suite

- **Bots** : des adversaires contrôlés par le serveur qui se déplacent, ramassent du butin et attaquent.
- **Une vraie partie battle royale contre des bots** : du bus jusqu'au dernier survivant, avec écran de victoire.
- **Butin au sol** : armes, munitions, soins et matériaux répartis sur la carte, qu'on peut ramasser.
- **Armes et dégâts** : tir, points de vie, armure, éliminations.
- **Zone** qui se resserre pendant la partie.
- **Construction** gérée par le serveur.

Pas prévu pour l'instant : amis, classement, boutique (inutile : tout est déjà débloqué).

### Désinstaller

Double-clique sur **`Desinstaller - Uninstall.bat`** : les fichiers d'origine du jeu sont restaurés.
Tu peux ensuite supprimer le dossier `reborn` et les fichiers `.bat`.

### Problèmes fréquents

| Problème | Solution |
|---|---|
| « Python 3 est requis » | Installe Python et coche « Add python.exe to PATH », puis relance l'installation. |
| Le jeu reste sur « Connexion... » | Le serveur n'est pas lancé : utilise toujours `Lancer hors ligne - Play Offline.bat`. |
| « Connexion perdue » en partie | Tu as cliqué dans la fenêtre du serveur (ça la met en pause). Ne clique pas dedans. |
| Erreur 902 au lancement | Relance `Installer - Install.bat`. |

Tes données (pseudo, équipement) sont dans `reborn\server\save\player.json`.
Les journaux sont dans `reborn\server\logs\` et `Documents\offline_mod.log`.

---

## English

### Requirements

- The official **Creative Destruction PC** game (version `3.0.210.0`), installed in a folder
  (the one containing `client.exe` and `script.npk`).
- **Python 3.8 or newer**: <https://www.python.org/downloads/>
  During setup, tick **"Add python.exe to PATH"**.
- An internet connection **for installation only** (it downloads the Python package `pycryptodome`).

### Check your copy of the game

The game is no longer officially distributed. If you kept your installation, check it is the right version
with the SHA-256 hashes below (in PowerShell: `Get-FileHash <file>`):

| File | SHA-256 |
|---|---|
| game installer zip | `3f5008c3c3c953f005dbb3681b2a24ae4abd71199435c8948b60fb7173a17be5` |
| `script.npk` (15,607,644 bytes) | `f47bcc7cbb261897072a53af269fc839a54e7cfada9d9a0268d8b7165b1738e1` |

### Install

1. Extract the archive **into the game folder**, next to `client.exe` (see the layout above).
2. Double-click **`Installer - Install.bat`**.
   It disables the game's update check (error 902) and installs the mod into `Documents\`.
   The original files are backed up into `reborn\backup\`.

### Play

1. Double-click **`Lancer hors ligne - Play Offline.bat`**.
   A minimized black window (the server) opens, then the game.
   **Do not close it and do not click inside it while playing.**
2. In game: **Confirm** → **Play**. Create your character if asked.
3. For a match: **Start**. You land on the starting island.
   Press **`²`** (key under Esc on AZERTY keyboards) to open the console, type **`start`** and **Enter**:
   countdown, players presentation, bus, then jump.

### Working

- Login without a NetEase account, lobby, profile, rename
- 999,999,999 diamonds / gold / stars, every permanent skin and item unlocked
- Equip outfits and skins, ♂/♀ button on the profile page to switch character
- Match: starting island, countdown, presentation, bus, parachute jump, pickaxe

### Planned

- **Bots**: server-controlled opponents that move around, pick up loot and fight.
- **A real battle royale match against bots**: from the bus to the last one standing, with a victory screen.
- **Ground loot**: weapons, ammo, heals and materials spread across the map, ready to pick up.
- **Weapons and damage**: shooting, health, armor, eliminations.
- **Storm** shrinking during the match.
- **Building** handled by the server.

Not planned for now: friends, leaderboards, shop (useless anyway: everything is unlocked).

### Uninstall

Double-click **`Desinstaller - Uninstall.bat`**: the original game files are restored.
Then you can delete the `reborn` folder and the `.bat` files.

### Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3 is required" | Install Python with "Add python.exe to PATH" ticked, then run the installer again. |
| Stuck on "Connecting..." | The server is not running: always start with `Lancer hors ligne - Play Offline.bat`. |
| "Connection lost" in a match | You clicked inside the server window (it pauses it). Don't click in it. |
| Error 902 at startup | Run `Installer - Install.bat` again. |

Your data (name, equipment) lives in `reborn\server\save\player.json`.
Logs: `reborn\server\logs\` and `Documents\offline_mod.log`.

---

### Contents

| Path | What |
|---|---|
| `reborn/server/` | local login / game / battle server (Python 3) |
| `reborn/server/data/protocol.json` | network message layouts (ids and field types) |
| `reborn/server/data/items.json` | item ids and types (used to unlock everything) |
| `reborn/client_mod/switches.nxs` | compiled client mod (source in `client_mod/src/switches.py`) |
| `reborn/client_mod/annoucement.txt` | the welcome message |
| `reborn/tools/install.py` | installer / uninstaller |

Fan project for game preservation, not affiliated with NetEase. You need your own copy of the game.
