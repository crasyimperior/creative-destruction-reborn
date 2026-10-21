"""Loot: chests inside houses and items on the ground, generated around the player as he moves.

The server has no map data. For each map cell near the player it asks the client mod for every
surface under a grid of points ("@layers": roof, floors, ground), then:
  indoor floor = a surface with another one 2.5 m - 12 m above it (a ceiling)  -> chests + loot
  outdoor      = a single surface                                            -> a little loot
Chests open with the pickaxe (C_FIGHT_CLOSE_COMBAT_HIT) and burst their items out.
"""
import json
import math
import os
import random

CELL = 1600.0               # map cell generated at once
SAMPLE_STEP = 90.0          # grid spacing of the surface probes inside a cell
GEN_RADIUS = 1              # cells around the player's cell (3 x 3)
MIN_CELL_COVERAGE = 0.5     # below that the collision is not streamed in yet: try the cell again later

INDOOR_GAP = (25.0, 120.0)  # ceiling height above an indoor floor
CHEST_SPACING = 450.0
CHEST_CHANCE = 0.35
INDOOR_ITEM_SPACING = 90.0
INDOOR_ITEM_CHANCE = 0.9
OUTDOOR_ITEM_SPACING = 220.0
OUTDOOR_ITEM_CHANCE = 0.35
CHEST_HP = 4                # pickaxe hits to open a chest

ITEM_UNIT_BASE = 5000000
CHEST_UNIT_BASE = 6000000
BAG_UNIT_BASE = 7000000
EQUIP_UNIT_BASE = 8000000

GUN_SLOTS = (101, 102, 103, 104, 105)   # share/const.py EQUIP_SLOT_WEAPON_1..5
RARITY_WEIGHTS = {2: 60, 3: 30, 4: 10}

HERE = os.path.dirname(os.path.abspath(__file__))


def load_data():
    with open(os.path.join(HERE, "data", "loot.json"), encoding="utf-8") as f:
        return json.load(f)


def cell_of(x, z):
    return int(math.floor(x / CELL)), int(math.floor(z / CELL))


def cell_samples(cell, rng):
    cx, cz = cell
    n = int(CELL / SAMPLE_STEP)
    pts = []
    for i in range(n):
        for j in range(n):
            pts.append((cx * CELL + (i + rng.random()) * SAMPLE_STEP, cz * CELL + (j + rng.random()) * SAMPLE_STEP))
    return pts


def classify(points, layers):
    """(indoor floors, outdoor ground) from the surfaces found under each point (highest first)."""
    indoor, outdoor = [], []
    for (x, z), ys in zip(points, layers):
        if not ys:
            continue
        if len(ys) == 1:
            outdoor.append((x, ys[0], z))
            continue
        for k in range(1, len(ys)):
            if INDOOR_GAP[0] <= ys[k - 1] - ys[k] <= INDOOR_GAP[1]:
                indoor.append((x, ys[k], z))
    return indoor, outdoor


def spaced(points, spacing, chance, rng):
    rng.shuffle(points)
    kept = []
    for p in points:
        if rng.random() > chance:
            continue
        if all((p[0] - q[0]) ** 2 + (p[2] - q[2]) ** 2 >= spacing * spacing for q in kept):
            kept.append(p)
    return kept


class Loot:
    def __init__(self, seed=None):
        self.data = load_data()
        self.rng = random.Random(seed)
        self.guns = {g["item_id"]: g for g in self.data["guns"]}
        self.done_cells = set()
        self.walk_points = []   # known ground / floor points: bots walk between them
        self.items = {}     # unit_id -> dict(item_id, amount, pos, bullet_num)
        self.chests = {}    # unit_id -> dict(pos, hp, lay_dir)
        self.next_item = ITEM_UNIT_BASE
        self.next_chest = CHEST_UNIT_BASE

    # ---------------------------------------------------------------- what drops
    def random_gun(self):
        rarity = self.rng.choices(list(RARITY_WEIGHTS), weights=list(RARITY_WEIGHTS.values()))[0]
        pool = [g for g in self.data["guns"] if g["rarity"] == rarity] or self.data["guns"]
        return self.rng.choice(pool)

    def random_drop(self):
        """One loot spot: a list of (item_id, amount)."""
        r = self.rng.random()
        if r < 0.65:  # a gun always comes with some ammo of its own type
            gun = self.random_gun()
            ammo = gun["ammo"]
            return [(gun["item_id"], 1), (ammo, self.data["ammo"].get(str(ammo), 20))]
        if r < 0.75:
            ammo = self.rng.choice(list(self.data["ammo"]))
            return [(int(ammo), self.data["ammo"][ammo])]
        if r < 0.88:
            h = self.rng.choice(self.data["heals"])
            return [(h["item_id"], h["amount"])]
        s = self.rng.choice(self.data["shields"])
        return [(s["item_id"], s["amount"])]

    def chest_contents(self):
        """Always a gun with its ammo, plus one heal: a shield potion, 5 bandages or 1 med kit."""
        gun = self.random_gun()
        ammo = gun["ammo"]
        out = [(gun["item_id"], 1), (ammo, self.data["ammo"].get(str(ammo), 20) * 2)]
        r = self.rng.random()
        if r < 1 / 3.0:
            s = self.rng.choice(self.data["shields"])
            out.append((s["item_id"], s["amount"]))
        elif r < 2 / 3.0:
            out.append((4001, 5))   # bandages
        else:
            out.append((4002, 1))   # med kit
        return out

    # ---------------------------------------------------------------- spawning
    def new_item(self, item_id, amount, pos):
        uid = self.next_item
        self.next_item += 1
        gun = self.guns.get(item_id)
        self.items[uid] = {"item_id": item_id, "amount": amount, "pos": pos,
                           "bullet_num": gun["capacity"] if gun else 0, "lay_dir": self.rng.randint(0, 359)}
        return uid

    def populate(self, cell, points, layers):
        """Returns (new chest ids, new item ids), or None when the cell is not loaded enough yet."""
        probed = sum(1 for ys in layers if ys)
        if probed < MIN_CELL_COVERAGE * len(points):
            return None
        self.done_cells.add(cell)
        indoor, outdoor = classify(points, layers)
        self.walk_points += outdoor + indoor
        chests, items = [], []
        for p in spaced(list(indoor), CHEST_SPACING, CHEST_CHANCE, self.rng):
            uid = self.next_chest
            self.next_chest += 1
            self.chests[uid] = {"pos": p, "hp": CHEST_HP, "lay_dir": self.rng.randint(0, 359)}
            chests.append(uid)
        taken = [self.chests[c]["pos"] for c in chests]
        spots = [p for p in spaced(list(indoor), INDOOR_ITEM_SPACING, INDOOR_ITEM_CHANCE, self.rng)
                 if all((p[0] - q[0]) ** 2 + (p[2] - q[2]) ** 2 > 80 ** 2 for q in taken)]
        spots += spaced(list(outdoor), OUTDOOR_ITEM_SPACING, OUTDOOR_ITEM_CHANCE, self.rng)
        for p in spots:
            for k, (item_id, amount) in enumerate(self.random_drop()):
                pos = (p[0] + 18 * k, p[1], p[2] + 10 * k)  # a gun and its ammo side by side
                items.append(self.new_item(item_id, amount, pos))
        return chests, items

    def burst_chest(self, uid):
        """Chest opened: its items land around it."""
        chest = self.chests[uid]
        x, y, z = chest["pos"]
        out = []
        contents = self.chest_contents()
        for k, (item_id, amount) in enumerate(contents):
            a = 2 * math.pi * k / max(1, len(contents)) + self.rng.uniform(-0.3, 0.3)
            r = self.rng.uniform(25, 45)
            out.append(self.new_item(item_id, amount, (x + r * math.cos(a), y, z + r * math.sin(a))))
        return out


def near_points(points, pos, radius, limit=40):
    r2 = radius * radius
    out = [p for p in points if (p[0] - pos[0]) ** 2 + (p[2] - pos[2]) ** 2 <= r2]
    return out[:limit] if len(out) <= limit else random.sample(out, limit)


class Inventory:
    """What the player picked up: guns in weapon slots, everything else in the bag."""

    def __init__(self):
        self.weapons = {}   # slot -> dict(uid, item_id, bullet_num, capacity)
        self.bag = {}       # item_id -> dict(uid, amount)
        self.next_bag = BAG_UNIT_BASE
        self.next_equip = EQUIP_UNIT_BASE

    def add_gun(self, gun, slot=0):
        if slot not in GUN_SLOTS or slot in self.weapons:
            free = [s for s in GUN_SLOTS if s not in self.weapons]
            if not free:
                return None
            slot = free[0]
        uid = self.next_equip
        self.next_equip += 1
        self.weapons[slot] = {"uid": uid, "item_id": gun["item_id"], "bullet_num": gun["capacity"],
                              "capacity": gun["capacity"]}
        return slot

    def add_bag(self, item_id, amount):
        entry = self.bag.get(item_id)
        is_new = entry is None
        if is_new:
            entry = self.bag[item_id] = {"uid": self.next_bag, "amount": 0}
            self.next_bag += 1
        entry["amount"] += amount
        return entry, is_new
