"""Bots: fake players that fill the offline battle royale.

What they do so far:
  pre-lobby  join the island one after the other, then wander between walkable spots
  show       appear in the players presentation
  bus        board the bus and jump at random moments (a few early, most mid-route, some late)
  air        free fall, open their parachute and glide to a landing spot on the map
  ground     land and walk around a little

Ground heights come from the client mod (it ray-casts the scene, see `query_heights` in
offline_server.py): the server itself has no map data.
"""
import math
import random

UNIT_BASE = 2000            # bot unit ids: 2000, 2001, ... (the player is 1000)
MAX_BOTS = 99

# share/const.py
AIR_STATE_NOT_IN_AIR, AIR_STATE_FREE_DROP, AIR_STATE_PARACHUTE, AIR_STATE_ENDED = 0, 1, 2, 5
PLAYER_STATE_AIR, PLAYER_STATE_PARACHUTE, PLAYER_STATE_START = 11, 12, 13
MOVE_STATE_STOP, MOVE_STATE_MOVE = 1, 2  # share/prop_def.py PROP_V_MOVE_STATE_*

# ca/info_parachute_speed.py: free fall drops 180-360 u/s and moves up to 350 u/s forward,
# an open parachute drops 50-100 u/s and moves up to 200 u/s forward.
FREE_FALL_DROP = (260.0, 340.0)
FREE_FALL_MAX_XZ = 320.0
PARACHUTE_DROP = (70.0, 90.0)
PARACHUTE_OPEN_ABOVE_GROUND = (1600.0, 2600.0)
WALK_SPEED = 42.0          # the player's own run speed (calib.log: move_v=42)
GUESS_GROUND_Y = 1250.0     # map ground is ~770-1460 near the pre-lobby (offline_mod.log)

# share/prop_def.py PROP_V_JMP_STATE_*: what the client sends during a jump (unhandled.log)
JUMP_SEQUENCE = ((0.0, 1, 18.0), (0.35, 3, 22.0), (0.75, 4, 0.0), (0.85, 0, 0.0))  # (delay, state, height)
MOVE_STATE_JUMP = 3
ACTION_SHOOT, ACTION_DANCE = 18, 13  # ca/info_action_table: the pickaxe swing is the "shoot" action


def msgpack_shoot_mode(mode):
    """packb({'shoot_mode': mode}) as the client expects in S_FIGHT_ACTION params."""
    return b"\x81\xaashoot_mode" + bytes([mode])


MSGPACK_EMPTY_MAP = b"\x80"


def idle_actions(rng, now):
    """What a bot does while standing in the pre-lobby: (time, kind, arg) events."""
    r = rng.random()
    if r < 0.35:
        return [(now, "jump", None)]
    if r < 0.45:
        t = now
        out = []
        for _ in range(rng.randint(2, 3)):
            out.append((t, "jump", None))
            t += rng.uniform(0.9, 1.3)
        return out
    if r < 0.85:
        t, out = now, []
        for i in range(rng.randint(1, 5)):
            out.append((t, "swing", 1 + i % 2))
            t += rng.uniform(0.45, 0.7)
        return out
    if r < 0.93:
        return [(now, "dance", None), (now + rng.uniform(3.0, 6.0), "dance_end", None)]
    return []

PRE_LOBBY_CENTER = (13754.0, -10600.0)
PRE_LOBBY_RADIUS = 1500.0
PRE_LOBBY_Y_RANGE = (600.0, 1300.0)
MAP_LIMIT = 18500.0

_PREFIXES = ["xX", "", "", "", "", "Le", "Dark", "Pro", "Mr", "Its", "TTV_", "YT_", "", "Lil", "Big", "Sir"]
_WORDS = [
    "Sniper", "Lucas", "Nathan", "Enzo", "Hugo", "Leo", "Theo", "Noah", "Louis", "Jules", "Adam", "Ethan",
    "Emma", "Lea", "Chloe", "Ines", "Manon", "Jade", "Lina", "Sarah", "Camille", "Zoe", "Maya", "Nina",
    "Shadow", "Ghost", "Ninja", "Wolf", "Tiger", "Dragon", "Phoenix", "Viper", "Falcon", "Storm", "Blaze",
    "Killer", "Hunter", "Reaper", "Sniper", "Rusher", "Builder", "Camper", "Noob", "Gamer", "Legend", "King",
    "Queen", "Boss", "Zombie", "Panda", "Kitty", "Potato", "Taco", "Pizza", "Nugget", "Banana", "Cookie",
    "Destroyer", "Creative", "Blitz", "Turbo", "Rocket", "Laser", "Pixel", "Glitch", "Frost", "Toxic",
    "Carlos", "Diego", "Mateo", "Pablo", "Ana", "Sofia", "Kevin", "Jordan", "Tyler", "Jake", "Mike", "Alex",
]
_SUFFIXES = ["", "", "", "Xx", "_FR", "77", "99", "2008", "2010", "_YT", "TV", "123", "_pro", "x", "HD", "69", "13"]


def random_name(rng, taken):
    for _ in range(50):
        name = rng.choice(_PREFIXES) + rng.choice(_WORDS) + rng.choice(_SUFFIXES)
        if rng.random() < 0.35:
            name += str(rng.randint(1, 9999))
        name = name[:16]
        if name not in taken:
            taken.add(name)
            return name
    return "Player%d" % rng.randint(1000, 99999)


def _pick(rng, ids):
    return rng.choice(ids) if ids else 0


class Wardrobe:
    """Random outfits built from the game's item list (data/items.json)."""

    def __init__(self, item_info, default_body, default_cid):
        self.default_body, self.default_cid = default_body, default_cid
        by = {}
        for item_id, info in item_info.items():
            if info.get("expired") or info.get("is_gen_tmp"):
                continue
            key = (info.get("slot"), info.get("gender"))
            if info.get("type") in (3, 4, 12) and key[0] and key[1]:
                by.setdefault(key, []).append(item_id)
            elif info.get("type") == 5:
                by.setdefault(("parachute", None), []).append(item_id)
        self.by = by

    def outfit(self, rng, gender):
        worn = dict(self.default_body[gender])
        worn["clothes_hair"] = _pick(rng, self.by.get(("clothes_hair", gender))) or worn["clothes_hair"]
        if rng.random() < 0.55:
            worn["clothes_all"] = _pick(rng, self.by.get(("clothes_all", gender)))
            worn.pop("clothes_upper", None)
            worn.pop("clothes_leg", None)
        else:
            worn["clothes_upper"] = _pick(rng, self.by.get(("clothes_upper", gender))) or worn["clothes_upper"]
            worn["clothes_leg"] = _pick(rng, self.by.get(("clothes_leg", gender))) or worn["clothes_leg"]
        for slot, chance in (("clothes_back", 0.45), ("clothes_head", 0.15), ("clothes_glasses", 0.15),
                             ("clothes_mask", 0.1)):
            if rng.random() < chance:
                worn[slot] = _pick(rng, self.by.get((slot, gender)))
        worn["clothes_base"] = self.default_cid[gender]
        parachute = _pick(rng, self.by.get(("parachute", None)))
        if parachute:
            worn["parachute"] = parachute
        return [{"slot": s, "item_id": i} for s, i in worn.items() if i]


class Bot:
    def __init__(self, index, name, gender, appearance, rng):
        self.unit_id = UNIT_BASE + index
        self.name, self.gender, self.appearance = name, gender, appearance
        self.score = rng.randint(0, 2500)
        self.gun = rng.choice(["scar", "ak47", "awp", "pump", "gatling", "xm8", "vss", "tsg"])  # gun item slots
        self.state = "offline"      # offline -> lobby -> bus -> air -> ground
        self.pos = None             # (x, y, z)
        self.dir = rng.uniform(0, 360)
        self.walk = None            # (start_pos, end_pos, t0, duration)
        self.next_walk_at = 0.0
        self.home = []              # walkable spots the bot walks between right now
        self.land_home = []         # walkable spots around its landing spot
        self.jump_at = None         # seconds after the bus start
        self.jump_time = None
        self.landing = None         # (x, y, z) on the map
        self.landing_guess = False  # y not known yet (collision not streamed in): asked again later
        self.flight = None          # see plan_flight
        self.sync_sn = 0            # the client drops position updates whose sync_sn does not grow
        self.events = []            # pending idle actions (jump, swing, dance)
        self.hp = 100
        self.weapon = None          # loot.json gun dict once the bot found one
        self.next_shot = 0.0
        self.goal_item = None       # scene item the bot walks to
        self.find_gun_at = None     # far from the player: the bot "finds" a gun at that time
        self.kills = 0
        self.dead_at = None
        self.grid = None            # height source while walking (island grid, then the GroundMap)
        self.in_aoi = False         # does the client currently have this bot


def make_bots(count, wardrobe, player_name, seed=None):
    rng = random.Random(seed)
    taken = {player_name}
    bots = []
    for i in range(max(0, min(MAX_BOTS, count))):
        gender = 1 if rng.random() < 0.6 else 2
        bots.append(Bot(i, random_name(rng, taken), gender, wardrobe.outfit(rng, gender), rng))
    return bots


# ------------------------------------------------------------------ geometry helpers
def heading_deg(dx, dz):
    """Model yaw / move_dir in degrees (0 = +z)."""
    return math.degrees(math.atan2(dx, dz)) % 360.0


def lerp3(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


class HeightGrid:
    """Ground heights of the pre-lobby island, one sample every `step` units, so walking bots
    follow the relief instead of floating over dips. The flat collision plane around the
    island (y = 696.3) and roofs / tree tops are left out."""
    STEP = 50.0
    RADIUS = 1300.0
    Y_RANGE = (705.0, 900.0)
    MAX_STEP_DY = 22.0      # higher than this between two samples = wall, roof edge or cliff

    def __init__(self, center=PRE_LOBBY_CENTER):
        self.cx, self.cz = center
        self.h = {}

    def cells(self):
        n = int(self.RADIUS / self.STEP)
        return [(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1)
                if (i * i + j * j) * self.STEP * self.STEP <= self.RADIUS * self.RADIUS]

    def cell_pos(self, c):
        return self.cx + c[0] * self.STEP, self.cz + c[1] * self.STEP

    def fill(self, cells, heights):
        for c, y in zip(cells, heights):
            if y is not None and self.Y_RANGE[0] <= y <= self.Y_RANGE[1]:
                self.h[c] = y

    def height(self, x, z):
        fi, fj = (x - self.cx) / self.STEP, (z - self.cz) / self.STEP
        i, j = int(math.floor(fi)), int(math.floor(fj))
        ti, tj = fi - i, fj - j
        corners = [((i, j), (1 - ti) * (1 - tj)), ((i + 1, j), ti * (1 - tj)),
                   ((i, j + 1), (1 - ti) * tj), ((i + 1, j + 1), ti * tj)]
        known = [(self.h[c], w) for c, w in corners if c in self.h]
        if len(known) < 4:
            return None
        return sum(y * w for y, w in known)

    def spots(self):
        """Cells on flat-enough ground (all 4 neighbours known and close in height)."""
        out = []
        for (i, j), y in self.h.items():
            near_ = [self.h.get((i + di, j + dj)) for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            if all(n is not None and abs(n - y) < self.MAX_STEP_DY for n in near_):
                x, z = self.cell_pos((i, j))
                out.append((x, y, z))
        return out

    def path_ok(self, a, b):
        dist = math.hypot(b[0] - a[0], b[2] - a[2])
        n = max(1, int(dist / (self.STEP / 2)))
        last = a[1]
        for k in range(1, n + 1):
            y = self.height(a[0] + (b[0] - a[0]) * k / n, a[2] + (b[2] - a[2]) * k / n)
            if y is None or abs(y - last) > self.MAX_STEP_DY:
                return False
            last = y
        return True


def near(spots, pos, radius):
    return [s for s in spots if (s[0] - pos[0]) ** 2 + (s[2] - pos[2]) ** 2 <= radius * radius]


def in_pre_lobby_area(x, z):
    return abs(x - PRE_LOBBY_CENTER[0]) < PRE_LOBBY_RADIUS + 500 and abs(z - PRE_LOBBY_CENTER[1]) < PRE_LOBBY_RADIUS + 500


def jump_delay(rng, door, duration):
    """Seconds after the bus start: like real players, a rush at the door, most mid-route, a few late."""
    r = rng.random()
    if r < 0.18:
        t = door + rng.uniform(0.3, 6.0)
    elif r < 0.85:
        t = door + (duration - door) * rng.betavariate(2.0, 2.4)
    else:
        t = duration - rng.uniform(1.0, 9.0)
    return min(max(t, door + 0.3), duration - 0.5)


def landing_candidates(rng, jump_pos, route_dir, tries=3):
    fx, fz = route_dir
    sx, sz = -fz, fx
    out = []
    for _ in range(tries):
        for _ in range(20):
            fwd, side = rng.uniform(-800, 4500), rng.uniform(-7000, 7000)
            x = jump_pos[0] + fx * fwd + sx * side
            z = jump_pos[2] + fz * fwd + sz * side
            if abs(x) < MAP_LIMIT and abs(z) < MAP_LIMIT and not in_pre_lobby_area(x, z):
                out.append((x, z))
                break
    return out


class GroundMap:
    """Ground height around the bots near the player, filled cell by cell from the client's surface
    probes (lowest surface = the ground, also inside houses). Bots walk on it instead of floating."""
    STEP = 40.0
    CELL = 640.0            # 16 x 16 samples per cell

    def __init__(self):
        self.h = {}
        self.done = set()
        self.retry_at = {}

    def cell_of(self, x, z):
        return int(math.floor(x / self.CELL)), int(math.floor(z / self.CELL))

    def cell_points(self, cell):
        n = int(self.CELL / self.STEP)
        return [(cell[0] * self.CELL + i * self.STEP, cell[1] * self.CELL + j * self.STEP)
                for i in range(n) for j in range(n)]

    def fill(self, cell, points, layers, now):
        hits = 0
        for (x, z), ys in zip(points, layers):
            if ys:
                self.h[(int(round(x / self.STEP)), int(round(z / self.STEP)))] = ys[-1]
                hits += 1
        if hits >= 0.5 * len(points):
            self.done.add(cell)
        else:
            self.retry_at[cell] = now + 8.0  # collision not streamed in yet
        return hits

    def path_ok(self, a, b):
        return True

    def height(self, x, z):
        fi, fj = x / self.STEP, z / self.STEP
        i, j = int(math.floor(fi)), int(math.floor(fj))
        ti, tj = fi - i, fj - j
        corners = [((i, j), (1 - ti) * (1 - tj)), ((i + 1, j), ti * (1 - tj)),
                   ((i, j + 1), (1 - ti) * tj), ((i + 1, j + 1), ti * tj)]
        known = [(self.h[c], w) for c, w in corners if c in self.h]
        if not known:
            return None
        total = sum(w for _, w in known)
        if total <= 1e-6:
            return known[0][0]
        return sum(y * w for y, w in known) / total


def ring_points(center, radii=(500.0, 1200.0), n=8):
    """Fallback spots around a landing spot whose ground is unknown (water, not streamed in)."""
    out = []
    for r in radii:
        for k in range(n):
            a = 2 * math.pi * k / n
            out.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
    return out


def spots_around(rng, center, n=3, rmin=120.0, rmax=450.0):
    pts = []
    for _ in range(n):
        r, a = rng.uniform(rmin, rmax), rng.uniform(0, 2 * math.pi)
        pts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
    return pts


def plan_flight(rng, start, landing, open_now=False):
    """Straight glide from the bus to the landing spot: free fall, then the parachute.
    open_now: re-plan of a bot already under its parachute."""
    y_open = landing[1] + rng.uniform(*PARACHUTE_OPEN_ABOVE_GROUND)
    y_open = start[1] if open_now else min(y_open, start[1] - 500.0)
    y_open = max(y_open, landing[1] + 50.0)
    drop1, drop2 = rng.uniform(*FREE_FALL_DROP), rng.uniform(*PARACHUTE_DROP)
    t1 = max((start[1] - y_open) / drop1, 0.01)
    t2 = max((y_open - landing[1]) / drop2, 1.0)
    dx, dz = landing[0] - start[0], landing[2] - start[2]
    dist = math.hypot(dx, dz)
    d1 = min(FREE_FALL_MAX_XZ * t1, dist * 0.8)
    k1 = d1 / dist if dist > 1 else 0.0
    open_pos = (start[0] + dx * k1, y_open, start[2] + dz * k1)
    return {"start": start, "open": open_pos, "land": landing, "t1": t1, "t2": t2,
            "yaw": heading_deg(dx, dz)}


def flight_sample(flight, t):
    """(state, pos, velocity, pitch, done) t seconds after the jump."""
    t1, t2 = flight["t1"], flight["t2"]
    if t < t1:
        a, b, k, dur, state, pitch = flight["start"], flight["open"], t / t1, t1, AIR_STATE_FREE_DROP, -55.0
    elif t < t1 + t2:
        a, b, k, dur, state, pitch = flight["open"], flight["land"], (t - t1) / t2, t2, AIR_STATE_PARACHUTE, -10.0
    else:
        return AIR_STATE_ENDED, flight["land"], (0.0, 0.0, 0.0), 0.0, True
    velo = tuple((y - x) / dur for x, y in zip(a, b))
    return state, lerp3(a, b, k), velo, pitch, False
