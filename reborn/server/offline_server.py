"""Creative Destruction offline server.

Ports (127.0.0.1):
  8000  HTTP  login list / zone list
  4000  TCP   login server (C_LOGIN_AUTH -> S_LOGIN_AUTH)
  4001  TCP   gate / game server (queue, enter/create role, player profile, ...)

Every message is logged to server/logs/server.log; messages without a handler are
listed in server/logs/unhandled.log so they can be implemented next.
"""
import datetime
import http.server
import itertools
import json
import math
import os
import random
import socket
import struct
import threading
import time
import traceback

import bots
import loot
from nexproto import Protocol

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG_DIR = os.path.join(HERE, "logs")
SAVE_PATH = os.path.join(HERE, "save", "player.json")
PROTO_DIR = os.path.join(ROOT, "decomp", "scripts_py", "proto")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)

HOST = "127.0.0.1"
HTTP_PORT, LOGIN_PORT, GATE_PORT = 8000, 4000, 4001
ZONE_ID = 1

PROTOCOL_JSON = os.path.join(HERE, "data", "protocol.json")
PROTO = Protocol(PROTOCOL_JSON if os.path.exists(PROTOCOL_JSON) else PROTO_DIR)
_log_lock = threading.Lock()


def log(msg, file="server.log", console=False):
    """Log to file. The console only gets important lines: a Windows console paused by a
    mouse click (QuickEdit) blocks print() and would freeze the whole server."""
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg)
    with _log_lock:
        if console:
            try:
                print(line, flush=True)
            except Exception:
                pass
        with open(os.path.join(LOG_DIR, file), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def short(v, n=300):
    s = repr(v)
    return s if len(s) <= n else s[:n] + "..."


# ------------------------------------------------------------------ save data
# Currencies are INT32 on the wire. 999 999 999 leaves headroom so the client never
# overflows when it adds rewards locally (INT32 max is 2 147 483 647).
MAX_CURRENCY = 999999999
DEFAULT_SAVE = {"uid": 10001, "aid": 10001, "name": "", "gender": 1, "guide_finish": 255,
                "gold": MAX_CURRENCY, "star": MAX_CURRENCY, "diamond": MAX_CURRENCY}


def load_save():
    if os.path.exists(SAVE_PATH):
        with open(SAVE_PATH, encoding="utf-8-sig") as f:
            return dict(DEFAULT_SAVE, **json.load(f))
    return dict(DEFAULT_SAVE)


def write_save(data):
    with open(SAVE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


SAVE = load_save()


# ------------------------------------------------------------------ connection
class LockedSocket:
    """Battle timelines send from background threads: serialize writes per connection."""

    def __init__(self, sock):
        self._sock = sock
        self._lock = threading.Lock()

    def sendall(self, data):
        with self._lock:
            self._sock.sendall(data)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class Connection:
    def __init__(self, sock, addr, kind):
        self.sock, self.addr, self.kind = LockedSocket(sock), addr, kind
        self.buf = b""
        self.alive = True

    def send(self, name, data=None):
        self.sock.sendall(PROTO.encode(name, data or {}))
        log("[%s] -> %s %s" % (self.kind, name, short(data or {})))

    def messages(self):
        while True:
            chunk = self.sock.recv(65536)
            if not chunk:
                return
            self.buf += chunk
            while len(self.buf) >= 2:
                total = struct.unpack_from("<H", self.buf)[0]
                if total < 2 or len(self.buf) < total:
                    break
                body, self.buf = self.buf[2:total], self.buf[total:]
                yield body


HANDLERS = {}


def handler(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def send_seed(conn):
    # need_encrypt=0 -> the client keeps the stream in clear (no RC4)
    conn.send("S_SYS_GAME_ENCRYPT_SEED", {"need_encrypt": 0, "in_seed": 0, "out_seed": 0, "need_handshake": 0})


# ------------------------------------------------------------------ login server
@handler("C_LOGIN_AUTH")
def on_login_auth(conn, msg):
    has_role = bool(SAVE.get("name"))
    zone = {"zone_id": ZONE_ID, "gate_hostid": 1, "gate_ip": HOST, "gate_port": GATE_PORT,
            "role_uid": SAVE["uid"] if has_role else 0}
    if has_role:
        zone["role_info"] = {"name": SAVE["name"], "level": 1}
    conn.send("S_LOGIN_AUTH", {
        "err_code": 0, "err_msg": "", "warn_msg": "", "unisdk_login_json": "",
        "auth_hostid": 1, "auth_sn": "offline_sn", "zone_list": [zone],
        "origin_account": "offline", "aid": SAVE["aid"], "sdk_username": "offline",
    })


# ------------------------------------------------------------------ gate / game server
GATE = {"conn": None}  # the lobby connection stays open during a match: the mod answers queries on it


def gate_on_connect(conn):
    GATE["conn"] = conn
    send_seed(conn)
    now = int(time.time())
    conn.send("S_LOGIN_GATE_PREPARE", {"is_test_branch": 0, "timestamp": now, "timezone": 0})
    conn.send("S_LOGIN_JOIN_QUEUE", {"conn_key": "offline_conn", "queue_pos": -1, "line_time": 0.0})


def enter_game(conn):
    conn.send("S_LOGIN_RES", {"err_code": 0, "err_msg": "", "info": {"client_version": "", "login_key": "offline_key"},
                              "is_relogin": 0, "server_region": "EU"})
    conn.send("S_AVATAR_GAME_PLAYER", {
        "uid": SAVE["uid"], "aid": SAVE["aid"], "name": SAVE["name"], "gender": SAVE["gender"],
        "guide_finish": SAVE["guide_finish"], "is_inner_host": 0, "is_daily_first": 0, "good_uid": 0,
        "is_ov_user": 0, "verify_info": "", "verify_type": 0, "do_rename_guide": 1, "in_battle": 0,  # 1 = rename guide done (0 makes the avatar icon glow)
    })
    send_rename_count(conn)


@handler("C_LOGIN_CREATE")
def on_login_create(conn, msg):
    name = msg.get("name", b"").decode("utf-8", "replace") or "Joueur"
    SAVE["name"] = name
    SAVE["gender"] = msg.get("gender", 1) or 1
    write_save(SAVE)
    log("created role %r" % name)
    enter_game(conn)


@handler("C_LOGIN_ENTER")
def on_login_enter(conn, msg):
    enter_game(conn)


@handler("C_LOGIN_RAND_NAME")
def on_rand_name(conn, msg):
    conn.send("S_LOGIN_RAND_NAME", {"name": "Joueur%d" % (int(time.time()) % 10000)})


DEFAULT_CID = {1: 16, 2: 17}  # share/game_item.py DEFAULT_CLOTHES_BASE_MALE / _FEMALE


@handler("C_GAME_GET_PZ_MAIN_PAGE")
def on_profile_page(conn, msg):
    conn.send("S_GAME_GET_PZ_MAIN_PAGE", {
        "uid": SAVE["uid"], "name": SAVE["name"], "lv": SAVE.get("lv", 1), "exp": SAVE.get("exp", 0),
        "gender": SAVE["gender"], "cid": SAVE.get("cid") or DEFAULT_CID.get(SAVE["gender"], 16),
        "country": "", "province": "", "city": "", "sign": SAVE.get("sign", ""), "value_des": "",
        "fteam_id": "", "fteam_name": "", "profile_photo_url": "", "good_uid": 0, "is_ov_user": 0,
        "verify_info": "", "verify_type": 0, "head_on_frame": 0, "country_code": "FR",
    })


# default body parts per gender (ca/info_game_item "é»˜è®¤äºšæ´²ç”·æ€§/å¥³æ€§" head/face/upper/leg)
DEFAULT_BODY = {
    1: {"clothes_hair": 3, "clothes_face": 4, "clothes_upper": 5, "clothes_leg": 6},
    2: {"clothes_hair": 7, "clothes_face": 8, "clothes_upper": 9, "clothes_leg": 10},
}


def player_cid():
    return SAVE.get("cid") or DEFAULT_CID.get(SAVE["gender"], 16)


# share/game_item.py ITEM_TYPE_* that are cosmetics/unlockables (no boxes, coupons, tickets, buffs)
UNLOCK_TYPES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 16, 21, 22, 23, 29, 30, 33, 202, 203, 204, 906, 907, 911}
HEAD_FRAME_TYPE = 202


def load_unlocked_items():
    path = os.path.join(HERE, "data", "items.json")
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    owned = sorted(int(k) for k, v in items.items()
                   if v.get("type") in UNLOCK_TYPES and not v.get("expired") and not v.get("is_gen_tmp"))
    frames = [i for i in owned if items[str(i)].get("type") == HEAD_FRAME_TYPE]
    return owned, frames


ALL_ITEMS, ALL_HEAD_FRAMES = load_unlocked_items()
with open(os.path.join(HERE, "data", "items.json"), encoding="utf-8") as _f:
    ITEM_INFO = {int(k): v for k, v in json.load(_f).items()}

WARDROBE = bots.Wardrobe(ITEM_INFO, DEFAULT_BODY, DEFAULT_CID)


CUSTOM_SUITS = set()   # full outfits from data/custom_items.json (they bring their own hair)


def load_custom_items():
    """Optional data/custom_items.json: extra outfits the client knows about (added by a client-side
    script, e.g. fan-made models). Same format as items.json; they are owned like everything else.
    Kept out of WARDROBE so bots never wear them."""
    path = os.path.join(HERE, "data", "custom_items.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        extra = {int(k): v for k, v in json.load(f).items() if not k.startswith("_")}
    ITEM_INFO.update(extra)
    CUSTOM_SUITS.update(i for i, v in extra.items() if v.get("slot") == "clothes_all")
    ALL_ITEMS.extend(i for i in sorted(extra) if i not in ALL_ITEMS)
    log("custom items: %s" % ", ".join(str(i) for i in sorted(extra)), console=True)


load_custom_items()

ITEM_TYPE_PARACHUTE, ITEM_TYPE_CLOTHES_BASE, ITEM_TYPE_FLYING_MOUNT = 5, 11, 907
FULL_SUIT_SLOT, PART_SLOTS = "clothes_all", ("clothes_upper", "clothes_leg")
DEFAULT_HEAD_SLOTS = ("clothes_hair", "clothes_face")


def equipped_character(gender):
    """Body parts + worn clothes for a gender: defaults overridden by what the player equipped."""
    worn = dict(DEFAULT_BODY[gender])
    worn.update({k: v for k, v in SAVE.get("character", {}).get(str(gender), {}).items()})
    # The client always starts from the character base's own hair and face (clothes_base item_list).
    # A hair / face sent in the fashion data counts as picked on purpose and wins over an outfit's
    # own one in the lobby (FashionComponent: have_item_hair), so only send them when not the default.
    for s_ in DEFAULT_HEAD_SLOTS:
        if worn.get(s_) == DEFAULT_BODY[gender][s_]:
            worn.pop(s_)
    return {k: v for k, v in worn.items() if v}


# The lobby only dresses the model when the active outfit index is 1..3 (LobbyPlayer.update_fashion:
# `if self.fashion_data and self.clothes_index`). Outfits live in clothes_suit with the outfit number
# appended to the slot name ("clothes_all1"). We keep everything the player wears in outfit 1.
ACTIVE_SUIT = 1


def suit_payload(gender=None, items=None):
    genders = [gender] if gender else list(DEFAULT_CID)
    out = []
    for g_ in genders:
        worn = items if items is not None else equipped_character(g_)
        out.append({"gender": g_, "info": [{"slot": "%s%d" % (s, ACTIVE_SUIT), "item_id": i}
                                           for s, i in worn.items()]})
    return out


def suit_index_payload():
    # one entry per character base (cid) the player owns, all pointing at outfit 1
    bases = {k: v.get("gender") or 1 for k, v in ITEM_INFO.items() if v.get("type") == ITEM_TYPE_CLOTHES_BASE}
    bases.update({c: g_ for g_, c in DEFAULT_CID.items()})
    return [{"cid": c, "index": ACTIVE_SUIT, "gender": g_} for c, g_ in sorted(bases.items())]


def character_payload():
    return [{"gender": g_, "info": [{"slot": s, "item_id": i} for s, i in equipped_character(g_).items()]}
            for g_ in DEFAULT_CID]


def share_character_payload():
    return [{"slot": s, "item_id": i} for s, i in SAVE.get("share_character", {}).items() if i]


def parachute_payload():
    para = SAVE.get("parachute", [])
    return [{"gender": g_, "parachute_ls": para} for g_ in DEFAULT_CID]


@handler("C_GAME_GET_AVATAR_INFO")
def on_avatar_info(conn, msg):
    cid = player_cid()
    now = int(time.time())
    owned = set(ALL_ITEMS) | set(DEFAULT_CID.values())
    base_items = [{"item_id": i, "count": 1} for i in sorted(owned)]
    # Every gender needs a suit entry and a character entry, otherwise the client's
    # PlayerInfoComponent.init_suit raises KeyError and drops the whole profile.
    suit_index = suit_index_payload()
    character = character_payload()
    conn.send("S_GAME_GET_AVATAR_INFO", {
        "uid": SAVE["uid"], "name": SAVE["name"], "avatar_pic_id": 0,
        "lv": SAVE.get("lv", 1), "exp": SAVE.get("exp", 0), "next_exp": 100,
        "mode_infos": [], "gold": SAVE["gold"], "star": SAVE["star"], "diamond": SAVE["diamond"],
        "bag": base_items, "training": [], "exterior_score": 0, "gun_score": 0,
        "gender": SAVE["gender"], "cid": cid, "share_character": share_character_payload(),
        "character": character, "optional_parachute_info": parachute_payload(),
        "clothes_suit_index": suit_index, "battle_clothes_suit_index": suit_index,
        "clothes_suit": suit_payload(), "online": 0, "medal": 0, "create_time": now,
        "birthday": "", "fragment": 0, "rare_fragment": 0,
        "head_own_frames": [{"head_frame_id": f, "expire": 0} for f in ALL_HEAD_FRAMES],
        "head_on_frame": SAVE.get("head_on_frame", 0), "pass_type": 0, "pass_level": 1, "pass_exp": 0,
        "exp_bonus": 0.0, "verify_info": "", "verify_type": 0, "country_code": "FR",
        "version_reward": 1, "profile_photo_url": "", "selected_match_queues": [],
    })


@handler("C_GAME_USE_ITEM")
def on_use_item(conn, msg):
    item_id = msg.get("item_id", 0)
    info = ITEM_INFO.get(item_id)
    if not info:
        conn.send("S_GAME_USE_ITEM_FAIL", {"item_id": item_id})
        return
    update = {}
    itype, slot, gender = info.get("type"), info.get("slot"), info.get("gender")
    if itype == ITEM_TYPE_CLOTHES_BASE:
        SAVE["cid"] = item_id
        if gender:
            SAVE["gender"] = gender
        update.update({"cid": item_id, "gender": SAVE["gender"]})
    elif itype in (ITEM_TYPE_PARACHUTE, ITEM_TYPE_FLYING_MOUNT):
        SAVE["parachute"] = [item_id]
        update["optional_parachute_info"] = parachute_payload()
        if slot:
            SAVE.setdefault("share_character", {})[slot] = item_id
            update["share_character"] = [{"slot": slot, "item_id": item_id}]
    elif slot and gender:
        worn = SAVE.setdefault("character", {}).setdefault(str(gender), {})
        changes = {slot: item_id}
        # a full outfit replaces top+bottom and vice versa (item_id 0 = slot removed on the client)
        if slot == FULL_SUIT_SLOT:
            changes.update({s: 0 for s in PART_SLOTS})
            for s_ in DEFAULT_HEAD_SLOTS:  # default hair / face still known by the client: clear them
                if worn.get(s_, DEFAULT_BODY[gender][s_]) == DEFAULT_BODY[gender][s_]:
                    changes[s_] = 0
        elif slot in PART_SLOTS:
            changes[FULL_SUIT_SLOT] = 0
            for other in PART_SLOTS:  # coming back from a full outfit: restore the default other half
                if other != slot and worn.get(other) == 0:
                    worn.pop(other)
                    changes[other] = DEFAULT_BODY[gender][other]
        worn.update(changes)
        update["character"] = [{"gender": gender, "info": [{"slot": s, "item_id": i} for s, i in changes.items()]}]
        update["clothes_suit"] = suit_payload(gender, changes)
    elif slot:
        SAVE.setdefault("share_character", {})[slot] = item_id
        update["share_character"] = [{"slot": slot, "item_id": item_id}]
    else:
        conn.send("S_GAME_USE_ITEM_FAIL", {"item_id": item_id})
        return
    write_save(SAVE)
    log("equipped %d (%s, slot=%s)" % (item_id, info.get("name"), slot))
    conn.send("S_GAME_UPDATE_AVATAR_INFO", {"update": update})
    conn.send("S_GAME_USE_ITEM_SUCC", {"item_id": item_id})


@handler("C_GAME_TOUCH_ITEM")
@handler("C_GAME_SET_WAREHOUSE_CLOTHES_INDEX")
@handler("C_GAME_P2_LOG")
@handler("C_GAME_NET_DELAY")
def on_ignored(conn, msg):
    """Client notifications that need no answer offline."""


# ------------------------------------------------------------------ matchmaking -> battle server
BATTLE_PORT = 4002          # battle server, TCP (the client mod forces TCP instead of KCP)
BATTLE_KCP_PORT = 4003      # advertised but unused
DEFAULT_BATTLE_ID = 1016    # ca/info_battle_battle: å…¨æ–°å¯æ˜Žæ˜Ÿä¹‹è°· (main map, scene upgrade_4096)
BATTLE_SCENES = {1013: "bc", 1016: "upgrade_4096", 1018: "upgrade_4096", 1019: "cxd", 1020: "cxd_new"}
FIGHT_STATE = {"fight_id": 0}


@handler("C_GAME_START_MATCH2")
def on_start_match(conn, msg):
    queues = msg.get("match_queues") or [{"game_mode": 16, "team_modes": [1]}]
    game_mode = queues[0].get("game_mode", 16)
    team_mode = (queues[0].get("team_modes") or [1])[0]
    FIGHT_STATE.update({"game_mode": game_mode, "team_mode": team_mode})
    conn.send("S_GAME_MATCH_SUCCESS", {"game_mode": game_mode, "team_mode": team_mode})
    conn.send("S_GAME_SWITCH_TO_BATTLE", {"ip": HOST, "port": BATTLE_KCP_PORT, "login_key": "offline_battle",
                                          "tcp_port": BATTLE_PORT})


def battle_on_connect(conn):
    conn.send("S_SYS_BATTLE_ENCRYPT_SEED", {"need_encrypt": 0, "in_seed": 0, "out_seed": 0, "need_handshake": 0})


PLAYER_UNIT_ID = 1000


@handler("C_FIGHT_REQ_LOGIN")
@handler("C_FIGHT_LOGIN")
def on_fight_login(conn, msg):
    FIGHT_STATE["fight_id"] += 1
    battle_id = DEFAULT_BATTLE_ID
    spawn = SAVE.get("debug_spawn", [0.0, 3000.0, 0.0])
    conn.send("S_FIGHT_JOIN_FIGHT", {
        "fight_id": FIGHT_STATE["fight_id"], "unit_id": PLAYER_UNIT_ID, "frame_idx": 0,
        "battle_id": battle_id, "scene_name": BATTLE_SCENES[battle_id],
        "x": spawn[0], "y": spawn[1], "z": spawn[2],
        "mode": FIGHT_STATE.get("game_mode", 16), "is_relogin": 0, "fight_type": FIGHT_TYPE_MATCH,
        "training_id": 0, "total_fight_num": 1, "open_build_elf": 0, "quality": 2,
        "m_consume_rate": 100, "extra": {"start_time_ts": int(time.time())}, "camp_id": 1,
        "team_mode": FIGHT_STATE.get("team_mode", 1), "fight_sn": "offline-%d" % FIGHT_STATE["fight_id"],
    })


PLAYER_LIVE_STATE_ALIVE = 1
FIGHT_TYPE_MATCH = 2
STAGE_PREPARE, STAGE_START = 1, 2
PREPARE_SECONDS = 15
# Pre-lobby island, near the exterior show stage (ca/info_exterior_person). The stage itself
# (13754, -10580) sometimes dropped the player under a building: spawn a bit north, just above
# the open ground where the player landed in testing (y = 803).
PRE_LOBBY_SPAWN = [13756.0, 850.0, -10487.0]
HOE_ITEM_ID, HOE_UNIT_ID, EQUIP_SLOT_WEAPON_0 = 1001, 1001000, 100
PLAYER_MAX_HP = 100


@handler("C_SYS_BATTLE_ECHO")
def on_battle_echo(conn, msg):
    now = time.time()
    conn.sock.sendall(PROTO.encode("S_SYS_BATTLE_ECHO", {
        "timestamp": msg.get("timestamp", 0), "svr_ts": int(now), "svr_ms": int((now % 1) * 1000),
        "echo_id": msg.get("echo_id", 0)}))


def player_appearance():
    gender = SAVE["gender"]
    worn = dict(equipped_character(gender))
    worn["clothes_base"] = player_cid()
    worn.update({s: i for s, i in SAVE.get("share_character", {}).items() if i})
    return [{"slot": s, "item_id": i} for s, i in worn.items()]


_frame_lock = threading.Lock()
FRAME_CHUNK = 16  # a packet is at most 64 KB: split big batches (bots joining) over several frames


def send_frame(conn, messages, quiet=False):
    """World-state messages travel inside S_FIGHT_FRAME (the client tags them with frame_idx)."""
    for i in range(0, len(messages), FRAME_CHUNK):
        part = messages[i:i + FRAME_CHUNK]
        with _frame_lock:
            conn.frame_idx = getattr(conn, "frame_idx", 0) + 1
            bufs = [{"msg_buf": PROTO.encode(name, data)} for name, data in part]
            conn.sock.sendall(PROTO.encode("S_FIGHT_FRAME", {"frame_idx": conn.frame_idx, "msg_list": bufs}))
        if not quiet:
            log("[%s] -> FRAME %d %s" % (conn.kind, conn.frame_idx, [n for n, _ in part]))


def battle_start_prepare(conn):
    """Called once the client finished loading the map: spawn the player and start the countdown."""
    if getattr(conn, "fight_started", False):
        return
    conn.fight_started = True
    now = time.time()
    spawn = SAVE.get("debug_spawn", PRE_LOBBY_SPAWN)
    conn.send("S_FIGHT_STATE_INFO", {"stage": STAGE_PREPARE, "start_stage_time": int(now)})
    send_frame(conn, [("S_FIGHT_PLAYER_INIT", {
        "unit_id": PLAYER_UNIT_ID,
        "move_info": {"pos_x": spawn[0], "pos_y": spawn[1], "pos_z": spawn[2],
                      "pos_ts_sec": int(now), "pos_ts_ms": int((now % 1) * 1000), "move_dir": 0.0,
                      "parachute_state": 0},
        "avatar_basic": {"live_state": PLAYER_LIVE_STATE_ALIVE, "hp": PLAYER_MAX_HP, "max_hp": PLAYER_MAX_HP,
                         "shield": 0, "max_shield": 100, "strength": 100, "gender": SAVE["gender"],
                         "cid": player_cid(), "appearance": player_appearance(), "camp_id": 1,
                         "server_control": 0, "player_state": PLAYER_STATE_WAIT,
                         "using_weapon_slot": EQUIP_SLOT_WEAPON_0},
        "avatar_equip_set": [hoe_equip()],
    })])
    ACTIVE_BATTLE["conn"] = conn  # countdown starts with the console command "start"
    conn.stage = "prepare"
    conn.player_pos = None
    conn.loot, conn.inv = loot.Loot(), loot.Inventory()
    conn.ground = bots.GroundMap()
    threading.Thread(target=loot_brain, args=(conn,), daemon=True).start()
    threading.Thread(target=ground_mapper, args=(conn,), daemon=True).start()
    conn.bots = bots.make_bots(SAVE.get("bot_count", 0), WARDROBE, SAVE["name"])
    if conn.bots:
        log("%d bots in this match" % len(conn.bots), console=True)
        threading.Thread(target=bot_brain, args=(conn,), daemon=True).start()


ACTIVE_BATTLE = {"conn": None}


AIR_STATE_FREE_DROP = 1
PLAYER_STATE_WAIT, PLAYER_STATE_AIR = 10, 11
MATCH_SETTINGS = {"show": 15.0, "bus": 32000.0 / 390.0}  # seconds (bus = 390 units/s across 32000)
# Bus route: a random line from one map edge to the opposite one (valid_range is +-20000)
AIRLINE_ID = 1
AIRLINE_HEIGHT = 13000.0
AIRLINE_EDGE = 16000.0
AIRLINE_DOOR_DELAY = 5
ROUTE = {"start": (-AIRLINE_EDGE, AIRLINE_HEIGHT, 0.0), "end": (AIRLINE_EDGE, AIRLINE_HEIGHT, 0.0)}


def new_airline_route():
    import math
    import random
    angle = random.uniform(0, 2 * math.pi)
    offset = random.uniform(-0.35, 0.35) * AIRLINE_EDGE  # not always through the centre
    dx, dz = math.cos(angle), math.sin(angle)
    px, pz = -dz * offset, dx * offset
    ROUTE["start"] = (px - dx * AIRLINE_EDGE, AIRLINE_HEIGHT, pz - dz * AIRLINE_EDGE)
    ROUTE["end"] = (px + dx * AIRLINE_EDGE, AIRLINE_HEIGHT, pz + dz * AIRLINE_EDGE)


def airline_pos(t0, now):
    k = min(max((now - t0) / MATCH_SETTINGS["bus"], 0.0), 1.0)
    return tuple(a + (b - a) * k for a, b in zip(ROUTE["start"], ROUTE["end"]))


def airline_velo():
    return tuple((b - a) / MATCH_SETTINGS["bus"] for a, b in zip(ROUTE["start"], ROUTE["end"]))


def ts(t):
    return int(t), int((t % 1) * 1000)


def run_match_timeline(conn, start_at):
    """Countdown -> exterior show -> bus -> forced jump. Runs in its own thread."""
    def wait_until(t):
        while conn.alive and time.time() < t:
            time.sleep(0.1)
        return conn.alive

    if not wait_until(start_at):
        return
    # 1. players presentation (music + models on the pre-lobby stage)
    conn.stage = "show"
    conn.send("S_FIGHT_NOTIFY_SHOW_EXTERIOR", {"info": exterior_show_info(conn)})
    conn.send("S_FIGHT_START_SHOW_EXTERIOR", {})
    if not wait_until(start_at + MATCH_SETTINGS["show"]):
        return
    # 2. the bus (route picked by cmd_start, so the bots could plan their landing meanwhile)
    bus = MATCH_SETTINGS["bus"]
    t0 = time.time()
    conn.airline_t0 = t0
    conn.stage = "bus"
    conn.send("S_FIGHT_STATE_INFO", {"stage": STAGE_START, "start_stage_time": int(t0)})
    conn.send("S_FIGHT_AIRLINE_PREPARE", {"airlines": [{"airline_id": AIRLINE_ID, "start_jump_pos": ROUTE["start"],
                                                        "end_jump_pos": ROUTE["end"]}]})
    sec, ms = ts(t0)
    conn.send("S_FIGHT_AIRLINE_START", {"airline_id": AIRLINE_ID, "airlines_info": [{
        "airline_id": AIRLINE_ID, "time_ts": sec, "time_ms": ms, "flight_velo": airline_velo(),
        "pos": ROUTE["start"], "player_state": PLAYER_STATE_AIR, "before_para": AIRLINE_DOOR_DELAY,
        "open_door_time": int(t0) + AIRLINE_DOOR_DELAY, "force_jump_time": int(t0 + bus),
        "left_player_num": aboard_count(conn), "is_spec_airline": 0, "airstrike_time": 0, "broken_time": 0,
        "duration": int(bus)}]})
    while conn.alive and time.time() < t0 + bus:
        time.sleep(1.0)
        now = time.time()
        sec, ms = ts(now)
        conn.sock.sendall(PROTO.encode("S_FIGHT_AIRLINE_UPDATE", {"airlines_info": [{
            "time_ts": sec, "time_ms": ms, "flight_velo": airline_velo(), "pos": airline_pos(t0, now),
            "left_player_num": aboard_count(conn), "airline_id": AIRLINE_ID}]}))
    # 3. end of the route: everybody still aboard is ejected
    conn.stage = "air"
    if conn.alive:
        conn.send("S_FIGHT_AIRLINE_LEAVE", {"airline_id": AIRLINE_ID})


@handler("C_FIGHT_START_PARACHUTE")
def on_start_parachute(conn, msg):
    extra = msg.get("extra") or {}
    if "pos_x" in extra:
        pos = (extra["pos_x"], extra["pos_y"], extra["pos_z"])
    else:
        pos = airline_pos(getattr(conn, "airline_t0", time.time()), time.time())
    conn.jumped = True
    sec, ms = ts(time.time())
    conn.send("S_FIGHT_START_PARACHUTE", {
        "player_unit_id": PLAYER_UNIT_ID, "renter": 0, "type": msg.get("type", 0), "state": AIR_STATE_FREE_DROP,
        "action": 0, "pos_x": pos[0], "pos_y": pos[1], "pos_z": pos[2], "time_ts": sec, "time_ms": ms})


def hoe_equip():
    return {"unit_id": HOE_UNIT_ID, "item_id": HOE_ITEM_ID, "amount": 1, "equip_slot": EQUIP_SLOT_WEAPON_0,
            "appearance": SAVE.get("share_character", {}).get("hoe", 0)}


@handler("C_FIGHT_END_PARACHUTE")
def on_end_parachute(conn, msg):
    # bots whose landing ground is still unknown: guess the height the player landed at
    if getattr(conn, "player_pos", None):
        conn.ground_guess_y = conn.player_pos[1]
    # the jump clears the client's equipment: give the pickaxe back on landing
    # ...and put it back in hand: the weapon bar keeps the pickaxe "selected" from the pre-lobby,
    # so key 1 does nothing until the server sets using_weapon_slot again.
    send_frame(conn, [
        ("S_FIGHT_UPDATE_EQUIP", {"player_unit_id": PLAYER_UNIT_ID, "info": hoe_equip()}),
        ("S_FIGHT_PLAYER_UPDATE", {"unit_id": PLAYER_UNIT_ID, "move_info": {},
                                   "avatar_basic": {"using_weapon_slot": EQUIP_SLOT_WEAPON_0}}),
    ])


@handler("C_FIGHT_UPDATE_PARACHUTE_STATE")
def on_update_parachute(conn, msg):
    """The client simulates its own fall/parachute; nothing to relay with no other players."""


def cmd_start(args):
    conn = ACTIVE_BATTLE["conn"]
    if conn is None or not conn.alive:
        return "No match in progress: press Start in the lobby first."
    if getattr(conn, "match_started", False):
        return "The match has already started."
    conn.match_started = True
    start_at = time.time() + PREPARE_SECONDS
    new_airline_route()
    conn.send("S_FIGHT_WAIT_BEFORE_START", {"start_time": int(start_at)})
    threading.Thread(target=run_match_timeline, args=(conn, start_at), daemon=True).start()
    if conn.bots:
        threading.Thread(target=plan_bot_landings, args=(conn,), daemon=True).start()
    return "Countdown started: the match begins in %d s." % PREPARE_SECONDS


def cmd_bots(args):
    try:
        n = max(0, min(bots.MAX_BOTS, int(args.strip())))
    except ValueError:
        return "Bots: %d (usage: bots <0-%d>)" % (SAVE.get("bot_count", 0), bots.MAX_BOTS)
    SAVE["bot_count"] = n
    write_save(SAVE)
    return "Bots: %d (next match)" % n


CONSOLE_COMMANDS = {"start": cmd_start, "bots": cmd_bots}


@handler("C_LOGIN_WIZ_CMD")
def on_console_command(conn, msg):
    text = msg.get("cmd", b"").decode("utf-8", "replace").strip()
    if text.startswith("@ground ") or text.startswith("@layers "):
        on_heights_reply(text)
        return
    if text.startswith("@dthit "):
        on_destruct_hit(text)
        return
    if text == "@botpos":  # polled by the client's botview, not logged
        conn.sock.sendall(PROTO.encode("S_LOGIN_WIZ_CMD_RESP", {"result": 1, "resp": bot_positions_reply()}))
        return
    name, _, args = text.partition(" ")
    fn = CONSOLE_COMMANDS.get(name.lower())
    resp = fn(args) if fn else "Unknown command: %s" % name
    log("console %r -> %s" % (text, resp))
    conn.send("S_LOGIN_WIZ_CMD_RESP", {"result": 1 if fn else 0, "resp": resp})


# ------------------------------------------------------------------ ground heights (asked to the client mod)
_height_ids = itertools.count(1)
HEIGHT_QUERIES = {}


def query_heights(points, top, bottom, timeout=6.0):
    """Ground y for each (x, z), ray-cast by the client mod from `top` down to `bottom`.
    None for a miss or water. Returns None when the mod did not answer (old mod, no lobby link)."""
    gate = GATE["conn"]
    if not points or gate is None or not gate.alive:
        return None
    qid = next(_height_ids)
    slot = HEIGHT_QUERIES[qid] = {"event": threading.Event(), "heights": None}
    body = ";".join("%.0f,%.0f" % p for p in points)
    gate.sock.sendall(PROTO.encode("S_LOGIN_WIZ_CMD_RESP", {
        "result": 1, "resp": "@ground %d %.0f %.0f %s" % (qid, top, bottom, body)}))
    ok = slot["event"].wait(timeout)
    HEIGHT_QUERIES.pop(qid, None)
    if not ok:
        log("height query %d: no answer from the client mod" % qid)
        return None
    hits = sum(1 for h in slot["heights"] if h is not None)
    log("height query %d: %d/%d points on ground" % (qid, hits, len(points)))
    return slot["heights"]


def on_heights_reply(text):
    parts = text.split(" ", 2)
    slot = HEIGHT_QUERIES.get(int(parts[1]))
    if slot is None:
        return
    heights = []
    for v in (parts[2].split(";") if len(parts) > 2 and parts[2] else []):
        if text.startswith("@layers"):  # every surface under the point, highest first: "y/y/y" or "n"
            try:
                heights.append([float(y) for y in v.split("/")])
            except ValueError:
                heights.append([])
            continue
        try:
            heights.append(float(v))
        except ValueError:
            heights.append(None)  # "n" = no hit, "w" = water
    slot["heights"] = heights
    slot["event"].set()


def query_layers(points, top, bottom, timeout=8.0):
    """Every surface (roof, floors, ground) under each (x, z), highest first. None = no answer."""
    gate = GATE["conn"]
    if not points or gate is None or not gate.alive:
        return None
    out = []
    for i in range(0, len(points), LAYERS_QUERY_CHUNK):
        part = points[i:i + LAYERS_QUERY_CHUNK]
        qid = next(_height_ids)
        slot = HEIGHT_QUERIES[qid] = {"event": threading.Event(), "heights": None}
        body = ";".join("%.0f,%.0f" % p for p in part)
        gate.sock.sendall(PROTO.encode("S_LOGIN_WIZ_CMD_RESP", {
            "result": 1, "resp": "@layers %d %.0f %.0f %s" % (qid, top, bottom, body)}))
        ok = slot["event"].wait(timeout)
        HEIGHT_QUERIES.pop(qid, None)
        if not ok or slot["heights"] is None or len(slot["heights"]) != len(part):
            return None
        out += slot["heights"]
    return out


LAYERS_QUERY_CHUNK = 500


# ------------------------------------------------------------------ loot: chests and items on the map
LOOT_TICK = 2.0
LOOT_RAY_TOP, LOOT_RAY_BOTTOM = 8000.0, -2000.0


LOOT_DATA = loot.load_data()


def treasure_msg(uid, chest):
    x, y, z = chest["pos"]
    return {"unit_id": uid, "item_id": LOOT_DATA["chest_item_id"], "pos_x": x, "pos_y": y, "pos_z": z,
            "lay_dir": chest["lay_dir"], "hp": chest["hp"], "is_airdrop": 0, "landed": 1, "landed_pass": 0,
            "hit_appearance": 0, "create_time": time.time(), "light_time": ""}


def scene_item_msg(uid, item, from_pos=None):
    x, y, z = item["pos"]
    extra = {"oa_temp": 0}
    if from_pos:
        extra.update({"from_x": from_pos[0], "from_y": from_pos[1], "from_z": from_pos[2], "from_time": time.time()})
    return {"unit_id": uid, "item_id": item["item_id"], "amount": item["amount"], "pos_x": x, "pos_y": y,
            "pos_z": z, "lay_dir": item["lay_dir"], "extra": extra, "bullet_num": item["bullet_num"]}


# ------------------------------------------------------------------ destructible scenery
DT_GRID_SIZE, DT_GRID_ID_OFFSET = 350.0, 10000   # share/const.py
DT_AOI_RADIUS = 2100.0
PICKAXE_DAMAGE = 50
DEFAULT_MODEL_HP = 150
MATERIALS = (7001, 7002, 7003)                   # wood, stone, metal (info_scene_model hit_sound)
COMMON_MATERIAL = 7020    # share/const.py: the only material the build bar counts in this version
MATERIAL_PER_HIT = 6
MATERIAL_ON_DESTROY = 12


def dt_grid_id(rx, rz):
    return ((rx + DT_GRID_ID_OFFSET) << 16) | (rz + DT_GRID_ID_OFFSET)


def dt_grid_of(x, z):
    return dt_grid_id(int(x // DT_GRID_SIZE), int(z // DT_GRID_SIZE))


def dt_aoi_tick(conn):
    """The client only loads the destructible data (data/destruct/<scene>/*.mp) of the grids the
    server says the player is in: without this nothing can be damaged."""
    x, _, z = conn.player_pos
    n = int(DT_AOI_RADIUS // DT_GRID_SIZE)
    cx, cz = int(x // DT_GRID_SIZE), int(z // DT_GRID_SIZE)
    now = {dt_grid_id(cx + i, cz + j) for i in range(-n, n + 1) for j in range(-n, n + 1)}
    old = getattr(conn, "dt_grids", set())
    enter, leave = sorted(now - old), sorted(old - now)
    if enter or leave:
        conn.dt_grids = now
        conn.sock.sendall(PROTO.encode("S_FIGHT_UPDATE_AOI_GRID_IDS", {
            "leave_grid_ids": leave, "enter_grid_ids": enter, "need_update_count": [0] * len(enter)}))


def give_material(conn, mat, amount, pos):
    entry, is_new = conn.inv.add_bag(mat, amount)
    info = {"unit_id": entry["uid"], "item_id": mat, "amount": entry["amount"], "gain_amount": amount}
    conn.sock.sendall(PROTO.encode("S_FIGHT_ADD_BAG_ITEM" if is_new else "S_FIGHT_UPDATE_BAG_ITEM",
                                   info if is_new else {"info": info}))
    conn.sock.sendall(PROTO.encode("S_FIGHT_GAIN_MATERIAL", {
        "player_unit_id": PLAYER_UNIT_ID, "mat_id": mat, "mat_num": amount, "pos": list(pos)}))


def on_destruct_hit(text):
    """'@dthit gid max_hp material x y z dx dy dz' from the mod: one pickaxe hit on the scenery."""
    conn = ACTIVE_BATTLE["conn"]
    if conn is None or not conn.alive:
        return
    f = text.split()
    gid, max_hp, mat = int(f[1]), int(f[2]) or DEFAULT_MODEL_HP, int(f[3])
    x, y, z, dx, dy, dz = (float(v) for v in f[4:10])
    hps = conn.__dict__.setdefault("dt_hp", {})
    if hps.get(gid, max_hp) <= 0:
        return
    hp = hps[gid] = max(0, hps.get(gid, max_hp) - PICKAXE_DAMAGE)
    grid = dt_grid_of(x, z)
    conn.sock.sendall(PROTO.encode("S_FIGHT_INFORM_DT_UNIT_DAMAGE", {
        "grid_id": grid, "model_id": gid, "view_grid_id": "", "max_hp": max_hp, "hp": hp,
        "hit_info": {"hit_x": x, "hit_y": y, "hit_z": z, "shock_x": dx, "shock_y": dy, "shock_z": dz,
                     "from_player_unit_id": PLAYER_UNIT_ID, "from_equip_item_id": HOE_ITEM_ID, "appearance": 0}}))
    give_material(conn, COMMON_MATERIAL, MATERIAL_PER_HIT + (MATERIAL_ON_DESTROY if hp == 0 else 0), (x, y, z))
    if hp == 0:
        conn.sock.sendall(PROTO.encode("S_FIGHT_INFORM_RELATED_DESTROY", {
            "struct_unit_infos": [], "appearance": 0,
            "scene_model_infos": [{"grid_id": grid, "ids": [gid], "view_grid_ids": [""]}]}))
        log("destroyed scene model %d (max_hp %d, material %d)" % (gid, max_hp, mat))


def loot_brain(conn):
    """Once the player is on the map: fill the cells around him with chests and items."""
    while conn.alive:
        time.sleep(LOOT_TICK)
        if conn.player_pos is None:
            continue
        try:
            dt_aoi_tick(conn)
        except Exception:
            log("dt aoi failed\n%s" % traceback.format_exc())
        if not getattr(conn, "jumped", False):
            continue
        px, _, pz = conn.player_pos
        pc = loot.cell_of(px, pz)
        todo = [(pc[0] + i, pc[1] + j) for i in range(-loot.GEN_RADIUS, loot.GEN_RADIUS + 1)
                for j in range(-loot.GEN_RADIUS, loot.GEN_RADIUS + 1)]
        todo = [c for c in todo if c not in conn.loot.done_cells]
        todo.sort(key=lambda c: (c[0] - pc[0]) ** 2 + (c[1] - pc[1]) ** 2)
        for cell in todo[:2]:
            points = loot.cell_samples(cell, conn.loot.rng)
            layers = query_layers(points, LOOT_RAY_TOP, LOOT_RAY_BOTTOM)
            if layers is None:
                break
            result = conn.loot.populate(cell, points, layers)
            if result is None:
                continue  # collision not streamed in yet, next time
            chests, items = result
            if chests:
                conn.sock.sendall(PROTO.encode("S_FIGHT_AOI_ADD_TREASURE_LIST", {
                    "treasure_list": [treasure_msg(c, conn.loot.chests[c]) for c in chests]}))
            for uid in items:
                conn.sock.sendall(PROTO.encode("S_FIGHT_AOI_ADD_SCENE_ITEM", scene_item_msg(uid, conn.loot.items[uid])))
            log("loot: cell %s -> %d chests, %d items" % (cell, len(chests), len(items)))


@handler("C_FIGHT_PICK_SCENE_ITEM")
def on_pick_item(conn, msg):
    uid = msg.get("unit_id", 0)
    item = getattr(conn, "loot", None) and conn.loot.items.get(uid)
    if not item:
        conn.send("S_FIGHT_PICK_ITEM_RESULT", {"unit_id": uid, "succ": 0, "amount": 0, "item_id": 0, "effect": 0})
        return
    item_id, amount = item["item_id"], item["amount"]
    gun = conn.loot.guns.get(item_id)
    if gun:
        slot = conn.inv.add_gun(gun, msg.get("slot", 0))
        if slot is None:
            conn.send("S_FIGHT_PICK_ITEM_RESULT", {"unit_id": uid, "succ": 0, "amount": 0, "item_id": item_id, "effect": 0})
            return
        w = conn.inv.weapons[slot]
        send_frame(conn, [("S_FIGHT_UPDATE_EQUIP", {"player_unit_id": PLAYER_UNIT_ID, "info": {
            "unit_id": w["uid"], "item_id": item_id, "amount": 1, "equip_slot": slot,
            "bullet_num": item["bullet_num"], "capacity": w["capacity"]}})])
    else:
        if item_id in MATERIALS:
            item_id = COMMON_MATERIAL
        entry, is_new = conn.inv.add_bag(item_id, amount)
        info = {"unit_id": entry["uid"], "item_id": item_id, "amount": entry["amount"], "gain_amount": amount}
        if is_new:
            conn.send("S_FIGHT_ADD_BAG_ITEM", info)
        else:
            conn.send("S_FIGHT_UPDATE_BAG_ITEM", {"info": info})
    del conn.loot.items[uid]
    conn.send("S_FIGHT_AOI_DEL_SCENE_ITEM", {"unit_id": uid})
    conn.send("S_FIGHT_PICK_ITEM_RESULT", {"unit_id": uid, "succ": 1, "amount": amount, "item_id": item_id, "effect": 0})


@handler("C_FIGHT_CLOSE_COMBAT_HIT")
def on_close_combat_hit(conn, msg):
    """Pickaxe hit: chests lose a point, the last one opens them (scenery: see on_destruct_hit)."""
    uid = msg.get("hit_unit", 0)
    chest = getattr(conn, "loot", None) and conn.loot.chests.get(uid)
    if not chest or chest["hp"] <= 0:
        return
    chest["hp"] -= 1
    conn.send("S_FIGHT_AOI_UPDATE_TREASURE", treasure_msg(uid, chest))
    if chest["hp"] <= 0:
        open_chest(conn, uid, chest)


STRUCT_COST = 10            # materials per wall / floor / ramp, as in the real game
STRUCT_HP = 200
STRUCT_UNIT_BASE = 10000000


@handler("C_FIGHT_CREATE_STRUCT")
def on_create_struct(conn, msg):
    """Building: each piece costs STRUCT_COST common materials; refused when the bag is short."""
    if not hasattr(conn, "inv"):
        return
    results = []
    for info in msg.get("create_infos") or []:
        entry = conn.inv.bag.get(COMMON_MATERIAL)
        cost = 0 if info.get("is_free") else STRUCT_COST
        if cost and (entry is None or entry["amount"] < cost):
            results.append({"ans": 0, "ticket": info.get("ticket", 0)})
            continue
        if cost:
            entry["amount"] -= cost
            conn.send("S_FIGHT_UPDATE_BAG_ITEM", {"info": {"unit_id": entry["uid"], "item_id": COMMON_MATERIAL,
                                                           "amount": entry["amount"], "gain_amount": 0}})
        conn.next_struct = getattr(conn, "next_struct", STRUCT_UNIT_BASE) + 1
        results.append({"ans": 1, "ticket": info.get("ticket", 0), "create_info": {
            "dt_type_id": info.get("dt_type_id", 0), "dt_sub_type_id": info.get("dt_sub_type_id", 0),
            "level": info.get("level", 1), "unit_id": conn.next_struct, "max_hp": STRUCT_HP, "hp": STRUCT_HP,
            "id_x": info.get("id_x", 0), "id_y": info.get("id_y", 0), "id_z": info.get("id_z", 0),
            "angle": info.get("angle", 0.0), "painting_id": info.get("painting_id", 0), "build_finish": 1,
            "structure_style": info.get("structure_style", 0), "player_unit_id": PLAYER_UNIT_ID, "color": 0}})
    if results:
        conn.send("S_FIGHT_CREATE_STRUCT_RESULT", {"result": results})


@handler("C_FIGHT_SYNC_BUILD_STATE")
def on_sync_build_state(conn, msg):
    """Build mode on/off: nothing to do server side."""


@handler("C_FIGHT_START_OPEN_TREASURE")
@handler("C_FIGHT_CANCEL_OPEN_TREASURE")
def on_start_open_treasure(conn, msg):
    """The client shows its own progress bar while the open key is held."""


@handler("C_FIGHT_OPEN_TREASURE")
def on_open_treasure(conn, msg):
    uid = msg.get("unit_id", 0)
    chest = getattr(conn, "loot", None) and conn.loot.chests.get(uid)
    if not chest or chest["hp"] <= 0:
        return
    chest["hp"] = 0
    conn.send("S_FIGHT_AOI_UPDATE_TREASURE", treasure_msg(uid, chest))
    open_chest(conn, uid, chest)


def open_chest(conn, uid, chest):
    x, y, z = chest["pos"]
    new = conn.loot.burst_chest(uid)
    conn.send("S_FIGHT_BURST_ITEMS", {"unit_id": PLAYER_UNIT_ID, "items": [
        scene_item_msg(i, conn.loot.items[i], from_pos=(x, y + 20, z)) for i in new]})


# ------------------------------------------------------------------ bots in a match
BOT_TICK = 0.1
BOT_CAMP_BASE = 100     # every bot in its own camp (= enemies); the player is camp 1
BOT_HOE_UNIT_BASE = 1002000
WALK_SAMPLE = 0.25      # seconds between two position samples of a walking bot
GROUND_RETRY = 3.0      # seconds between two height queries for guessed landing spots


def bot_hoe(b):
    return {"unit_id": BOT_HOE_UNIT_BASE + b.unit_id, "item_id": HOE_ITEM_ID, "amount": 1,
            "equip_slot": EQUIP_SLOT_WEAPON_0, "appearance": 0}


def bot_move_info(b, pos, now, extra=None):
    sec, ms = ts(now)
    b.sync_sn += 1
    info = {"pos_x": pos[0], "pos_y": pos[1], "pos_z": pos[2], "pos_ts_sec": sec, "pos_ts_ms": ms,
            "move_dir": b.dir, "sync_sn": b.sync_sn}
    info.update(extra or {})
    return info


def bot_air_extra(b, state, velo, pitch):
    return {"parachute_state": state, "free_drop_state": 1, "velo_x": velo[0], "velo_y": velo[1],
            "velo_z": velo[2], "yaw_angle": b.dir, "pitch_angle": pitch, "roll_angle": 0.0}


def bot_init_msg(b, now, air=None, player_state=PLAYER_STATE_WAIT):
    move = bot_move_info(b, b.pos, now, air or {"parachute_state": bots.AIR_STATE_NOT_IN_AIR})
    return ("S_FIGHT_PLAYER_INIT", {
        "unit_id": b.unit_id, "move_info": move,
        "avatar_basic": {"live_state": PLAYER_LIVE_STATE_ALIVE, "hp": PLAYER_MAX_HP, "max_hp": PLAYER_MAX_HP,
                         "shield": 0, "max_shield": 100, "strength": 100, "gender": b.gender,
                         "cid": DEFAULT_CID[b.gender], "appearance": b.appearance,
                         "camp_id": BOT_CAMP_BASE + b.unit_id - bots.UNIT_BASE, "server_control": 0,
                         "player_state": player_state, "using_weapon_slot": EQUIP_SLOT_WEAPON_0,
                         "move_sync_sn": b.sync_sn},
        "avatar_equip_set": [bot_hoe(b)],
    })


def bot_update_msg(b, move_info, basic):
    if "move_state" in basic:
        basic["move_sync_sn"] = move_info.get("sync_sn", b.sync_sn)
    return ("S_FIGHT_PLAYER_UPDATE", {"unit_id": b.unit_id, "move_info": move_info, "avatar_basic": basic})


def bot_action_msg(b, action, params, cancel=0):
    return ("S_FIGHT_ACTION", {"action": action, "unit_id": b.unit_id, "params": params, "cancel": cancel,
                               "force": 0})


def bot_walk_step(b, now, rng):
    """Walk between known walkable spots; a position sample every WALK_SAMPLE, the client interpolates."""
    if b.walk:
        start, end, t0, dur = b.walk
        if now < getattr(b, "next_sample", 0):
            return []
        b.next_sample = now + WALK_SAMPLE
        k = (now - t0) / dur
        if k >= 1.0:
            b.pos, b.walk = end, None
            b.next_walk_at = now + rng.uniform(1.5, 8.0)
            return [bot_update_msg(b, bot_move_info(b, end, now, {"move_v": 0.0}),
                                   {"move_state": bots.MOVE_STATE_STOP})]
        x, y, z = bots.lerp3(start, end, k)
        grid = getattr(b, "grid", None)
        y = (grid.height(x, z) if grid else None) or y  # follow the relief
        b.pos = (x, y, z)
        return [bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": bots.WALK_SPEED}),
                               {"move_state": bots.MOVE_STATE_MOVE})]
    if now < b.next_walk_at or not b.home or b.events:
        return []
    if rng.random() < 0.45:  # stay here and do something: jump around, swing the pickaxe, dance
        b.events = bots.idle_actions(rng, now)
        b.next_walk_at = now + rng.uniform(2.0, 7.0)
        return []
    choices = [s for s in bots.near(b.home, b.pos, 450.0) if s != b.pos] or b.home
    end = rng.choice(choices)
    grid = getattr(b, "grid", None)
    if grid:  # no walking through walls or over cliffs: try a few targets with a clear path
        for _ in range(6):
            if grid.path_ok(b.pos, end):
                break
            end = rng.choice(choices)
        else:
            b.next_walk_at = now + rng.uniform(1.0, 4.0)
            return []
    dist = ((end[0] - b.pos[0]) ** 2 + (end[2] - b.pos[2]) ** 2) ** 0.5
    if dist < 20:
        b.next_walk_at = now + rng.uniform(1.0, 5.0)
        return []
    b.dir = bots.heading_deg(end[0] - b.pos[0], end[2] - b.pos[2])
    b.walk = (b.pos, end, now, dist / bots.WALK_SPEED)
    b.next_sample = now + WALK_SAMPLE
    if rng.random() < 0.3:  # sometimes jump while running
        b.events = [(now + rng.uniform(0.3, dist / bots.WALK_SPEED * 0.8), "jump", None)]
    return [bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": bots.WALK_SPEED}),
                           {"move_state": bots.MOVE_STATE_MOVE})]


def bot_events_step(b, now):
    """Idle actions due now. A jump becomes 4 timed jump states with a small height arc."""
    msgs = []
    while b.events and b.events[0][0] <= now:
        _, kind, arg = b.events.pop(0)
        if kind == "jump":
            b.events[:0] = [(now + d, "jump_state", (st, h)) for d, st, h in bots.JUMP_SEQUENCE]
        elif kind == "jump_state":
            state, height = arg
            if b.walk:  # running: the walk keeps sending positions, only switch the animation state
                msgs.append(bot_update_msg(b, {}, {"jump_state": state,
                                                   "move_state": bots.MOVE_STATE_JUMP if state else bots.MOVE_STATE_MOVE}))
            else:
                pos = (b.pos[0], b.pos[1] + height, b.pos[2])
                msgs.append(bot_update_msg(b, bot_move_info(b, pos, now, {"move_v": 0.0}), {
                    "jump_state": state,
                    "move_state": bots.MOVE_STATE_JUMP if state else bots.MOVE_STATE_STOP}))
        elif kind == "swing":
            msgs.append(bot_action_msg(b, bots.ACTION_SHOOT, bots.msgpack_shoot_mode(arg)))
        elif kind == "dance":
            msgs.append(bot_action_msg(b, bots.ACTION_DANCE, bots.MSGPACK_EMPTY_MAP))
        elif kind == "dance_end":
            msgs.append(bot_action_msg(b, bots.ACTION_DANCE, bots.MSGPACK_EMPTY_MAP, cancel=1))
    return msgs


HEIGHT_QUERY_CHUNK = 700  # points per query: keeps each message far below 64 KB


ISLAND_GRID_TRIES = 8     # the island collision is sometimes not loaded yet right after the spawn
ISLAND_GRID_RETRY = 2.0


def island_grid(conn, rng):
    """Height map of the pre-lobby island, asked to the client mod in a few chunks."""
    for attempt in range(ISLAND_GRID_TRIES):
        grid = bots.HeightGrid()
        cells = grid.cells()
        for i in range(0, len(cells), HEIGHT_QUERY_CHUNK):
            part = cells[i:i + HEIGHT_QUERY_CHUNK]
            heights = query_heights([grid.cell_pos(c) for c in part], 1500.0, 400.0)
            if heights is None:
                break
            grid.fill(part, heights)
        spots = grid.spots()
        log("bots: island height map try %d: %d/%d cells, %d walkable spots" % (
            attempt + 1, len(grid.h), len(cells), len(spots)))
        if len(spots) >= 5:
            return grid, spots
        if not conn.alive or conn.stage != "prepare":
            break
        time.sleep(ISLAND_GRID_RETRY)
    # no usable answer: stand around the player, at the height he stands on (no relief)
    base = getattr(conn, "player_pos", None) or PRE_LOBBY_SPAWN
    log("bots: no island heights from the client, spawning around the player at y=%.0f" % base[1])
    return None, [(base[0] + rng.uniform(-150, 150), base[1], base[2] + rng.uniform(-150, 150)) for _ in range(40)]


def send_player_num(conn, alive):
    conn.sock.sendall(PROTO.encode("S_FIGHT_PLAYER_NUM", {"alive_num": alive, "total_num": alive, "kill_num": 0}))


def aboard_count(conn):
    n = 0 if getattr(conn, "jumped", False) else 1
    return n + sum(1 for b in getattr(conn, "bots", []) if b.state == "bus")


def exterior_show_info(conn):
    """Players presentation: the 10 best scores, the player always included."""
    me = {"name": SAVE["name"], "score": 0, "gun": "scar", "gender": SAVE["gender"], "country_code": "FR",
          "appearance": player_appearance()}
    others = sorted(getattr(conn, "bots", []), key=lambda b: -b.score)[:9]
    infos = [(b.score, {"name": b.name, "score": b.score, "gun": b.gun, "gender": b.gender,
                        "country_code": random.choice(["FR", "BE", "CA", "US", "BR", "ES", "DE", "MA"]),
                        "appearance": b.appearance}) for b in others]
    me["score"] = random.randint(0, 2500)
    infos.append((me["score"], me))
    return [info for _, info in sorted(infos, key=lambda x: -x[0])]


def resolve_landings(bots_, rng, timeout=10.0):
    """Ask the client for the ground under each guessed landing spot (+ a few spots to walk to).
    Collision is only streamed around the player, so far spots stay guessed until he comes closer."""
    todo = [b for b in bots_ if b.landing_guess]
    if not todo:
        return 0
    # the exact spot may be water or not streamed in: also try a ring around it, keep the closest ground
    points, owners = [], []
    for b in todo:
        for x, z in [(b.landing[0], b.landing[2])] + bots.ring_points((b.landing[0], b.landing[2])):
            points.append((x, z))
            owners.append(b)
    heights = query_heights(points, AIRLINE_HEIGHT, -3000.0, timeout=timeout) or [None] * len(points)
    best = {}
    for b, (x, z), y in zip(owners, points, heights):
        if y is None:
            continue
        d = (x - b.landing[0]) ** 2 + (z - b.landing[2]) ** 2
        if b.unit_id not in best or d < best[b.unit_id][0]:
            best[b.unit_id] = (d, (x, y, z))
    found = []
    for b in todo:
        if b.unit_id in best:
            b.landing = best[b.unit_id][1]
            b.landing_guess = False
            found.append(b)
    if found:
        points = []
        for b in found:
            points += [(p, b) for p in bots.spots_around(rng, (b.landing[0], b.landing[2]))]
        hs = query_heights([p for p, _ in points], AIRLINE_HEIGHT, -3000.0, timeout=timeout) or [None] * len(points)
        for ((x, z), b), y in zip(points, hs):
            if y is not None and abs(y - b.landing[1]) < 150:
                b.land_home.append((x, y, z))
    return len(found)


def plan_bot_landings(conn):
    """During the countdown: jump time + a landing spot for every bot."""
    rng = random.Random()
    bus = MATCH_SETTINGS["bus"]
    (sx, _, sz), (ex, _, ez) = ROUTE["start"], ROUTE["end"]
    length = ((ex - sx) ** 2 + (ez - sz) ** 2) ** 0.5
    route_dir = ((ex - sx) / length, (ez - sz) / length)
    for b in conn.bots:
        b.jump_at = bots.jump_delay(rng, AIRLINE_DOOR_DELAY, bus)
        jump_pos = bots.lerp3(ROUTE["start"], ROUTE["end"], b.jump_at / bus)
        x, z = bots.landing_candidates(rng, jump_pos, route_dir, tries=1)[0]
        b.landing, b.landing_guess = (x, bots.GUESS_GROUND_Y, z), True
    found = resolve_landings(conn.bots, rng)
    log("bots: %d/%d landing heights known before the bus" % (found, len(conn.bots)))


def bot_jump(conn, b, now, rng):
    pos = airline_pos(conn.airline_t0, now)
    b.state, b.jump_time, b.home = "air", now, []
    if b.landing is None:
        b.landing, b.landing_guess = (pos[0], getattr(conn, "ground_guess_y", bots.GUESS_GROUND_Y), pos[2]), True
    b.flight = bots.plan_flight(rng, pos, b.landing)
    b.dir, b.pos = b.flight["yaw"], pos
    state, _, velo, pitch, _ = bots.flight_sample(b.flight, 0.0)
    return bot_init_msg(b, now, bot_air_extra(b, state, velo, pitch), PLAYER_STATE_AIR)


def bot_replan(b, now, rng):
    """The landing height just became known: glide from where the bot is to the real ground."""
    if b.state == "air":
        under_canopy = b.pos[1] - b.landing[1] < bots.PARACHUTE_OPEN_ABOVE_GROUND[1]
        b.flight = bots.plan_flight(rng, b.pos, b.landing, open_now=under_canopy)
        b.jump_time = now
    elif b.state == "ground":
        b.pos = (b.pos[0], b.landing[1], b.pos[2])
        b.home = [b.landing] + b.land_home
        return [bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": 0.0}),
                               {"move_state": bots.MOVE_STATE_STOP})]
    return []


def bot_air_step(b, now, rng):
    state, pos, velo, pitch, done = bots.flight_sample(b.flight, now - b.jump_time)
    b.pos = pos
    if done:
        b.state = "ground"
        b.home = [b.landing] + b.land_home
        b.next_walk_at = now + rng.uniform(1.0, 4.0)
        return [bot_update_msg(b, bot_move_info(b, pos, now, bot_air_extra(b, bots.AIR_STATE_ENDED, velo, 0.0)),
                               {"player_state": bots.PLAYER_STATE_PARACHUTE}),
                bot_update_msg(b, bot_move_info(b, pos, now + 0.05, {"parachute_state": bots.AIR_STATE_NOT_IN_AIR,
                                                                     "move_v": 0.0}),
                               {"player_state": bots.PLAYER_STATE_START, "move_state": bots.MOVE_STATE_STOP})]
    ps = PLAYER_STATE_AIR if state == bots.AIR_STATE_FREE_DROP else bots.PLAYER_STATE_PARACHUTE
    return [bot_update_msg(b, bot_move_info(b, pos, now, bot_air_extra(b, state, velo, pitch)), {"player_state": ps})]


AIR_SAMPLE = 0.3

# ------------------------------------------------------------------ bots on the ground: loot and fight
BOT_SIGHT = 450.0           # 45 m: bots shoot each other closer than this
BOT_LOOT_RANGE = 900.0
NEAR_PLAYER = 4000.0        # closer than this the bots use the real loot and walkable points
BOT_EQUIP_UNIT_BASE = 9000000
BOT_GUN_SLOT = 101
GUN_DAMAGE = {"11": 16, "12": 45, "13": 13, "14": 18, "15": 60}    # pistol, shotgun, smg, rifle, sniper
GUN_RATE = {"11": 0.45, "12": 0.9, "13": 0.15, "14": 0.22, "15": 1.6}
DEAD_BODY_SECONDS = 6.0
PLAYER_LIVE_STATE_DEAD = 3


def near_player(conn, pos):
    pp = getattr(conn, "player_pos", None)
    return pp is not None and (pos[0] - pp[0]) ** 2 + (pos[2] - pp[2]) ** 2 < NEAR_PLAYER ** 2


def alive_count(conn):
    return 1 + sum(1 for b in conn.bots if b.state not in ("dead", "gone"))


def bot_go_to(b, end, now):
    gy = b.grid.height(end[0], end[2]) if b.grid else None
    if gy is not None:
        end = (end[0], gy, end[2])
    dist = ((end[0] - b.pos[0]) ** 2 + (end[2] - b.pos[2]) ** 2) ** 0.5
    if dist < 5:
        return []
    b.dir = bots.heading_deg(end[0] - b.pos[0], end[2] - b.pos[2])
    b.walk = (b.pos, end, now, dist / bots.WALK_SPEED)
    b.next_sample = now + WALK_SAMPLE
    return [bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": bots.WALK_SPEED}),
                           {"move_state": bots.MOVE_STATE_MOVE})]


def bot_equip(b, gun):
    b.weapon = gun
    return [("S_FIGHT_UPDATE_EQUIP", {"player_unit_id": b.unit_id, "info": {
                "unit_id": BOT_EQUIP_UNIT_BASE + b.unit_id, "item_id": gun["item_id"], "amount": 1,
                "equip_slot": BOT_GUN_SLOT, "bullet_num": gun["capacity"], "capacity": gun["capacity"]}}),
            bot_update_msg(b, {}, {"using_weapon_slot": BOT_GUN_SLOT})]


def bot_pick_goal(conn, b):
    """Arrived on the item it was walking to: take it if nobody else did."""
    uid, b.goal_item = b.goal_item, None
    item = conn.loot.items.pop(uid, None)
    if item is None:
        return []
    msgs = [("S_FIGHT_AOI_DEL_SCENE_ITEM", {"unit_id": uid})]
    gun = conn.loot.guns.get(item["item_id"])
    if gun and b.weapon is None:
        msgs += bot_equip(b, gun)
    return msgs


def find_enemy(conn, b):
    best, best_d = None, BOT_SIGHT * BOT_SIGHT
    for o in conn.bots:
        if o is b or o.state != "ground" or o.hp <= 0:
            continue
        d = (o.pos[0] - b.pos[0]) ** 2 + (o.pos[2] - b.pos[2]) ** 2
        if d < best_d:
            best, best_d = o, d
    return best, best_d ** 0.5


def bot_die(conn, victim, killer, now):
    victim.hp, victim.state, victim.dead_at, victim.walk = 0, "dead", now, None
    killer.kills += 1
    if victim.in_aoi:
        send_frame(conn, [bot_update_msg(victim, {}, {"hp": 0, "live_state": PLAYER_LIVE_STATE_DEAD})], quiet=True)
    msgs = []
    if near_player(conn, victim.pos):
        conn.sock.sendall(PROTO.encode("S_FIGHT_KILL_NOTICE", {
            "notice": killer.name + " {1} {2}", "kill_type": 0, "kill_count": killer.kills, "kill_count_tip": 0,
            "reason": "", "die_type": 1, "victim_name": victim.name}))
    send_player_num(conn, alive_count(conn))
    log("bots: %s killed %s (%d alive)" % (killer.name, victim.name, alive_count(conn)))
    return msgs


def bot_fire(conn, b, enemy, dist, now, rng):
    if now < b.next_shot:
        return []
    cls = b.weapon.get("class", "14")
    b.next_shot = now + GUN_RATE.get(cls, 0.3) * rng.uniform(0.8, 1.5)
    b.walk = None
    b.dir = bots.heading_deg(enemy.pos[0] - b.pos[0], enemy.pos[2] - b.pos[2])
    msgs = []
    if near_player(conn, b.pos):
        msgs.append(bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": 0.0}),
                                   {"move_state": bots.MOVE_STATE_STOP}))
        msgs.append(bot_action_msg(b, bots.ACTION_SHOOT, bots.msgpack_shoot_mode(1)))
    if rng.random() < max(0.15, 0.55 - dist / 1500.0):
        enemy.hp -= GUN_DAMAGE.get(cls, 18)
        if enemy.hp <= 0:
            msgs += bot_die(conn, enemy, b, now)
        elif enemy.in_aoi:
            send_frame(conn, [bot_update_msg(enemy, {}, {"hp": enemy.hp})], quiet=True)
    return msgs


def bot_ground_step(conn, b, now, rng):
    """Landed bot: shoot the closest bot in sight, otherwise find a gun, otherwise roam."""
    if b.state == "dead":
        if now - b.dead_at > DEAD_BODY_SECONDS:
            b.state = "gone"
            return [("S_FIGHT_UNIT_DESTROY", {"unit_id": b.unit_id})]
        return []
    if b.weapon:
        enemy, dist = find_enemy(conn, b)
        if enemy:
            return bot_fire(conn, b, enemy, dist, now, rng)
    if b.walk:
        msgs = bot_walk_step(b, now, rng)
        if not b.walk and b.goal_item:
            msgs += bot_pick_goal(conn, b)
        return msgs
    if now < b.next_walk_at:
        return []
    b.next_walk_at = now + rng.uniform(0.5, 3.0)
    gy = b.grid.height(b.pos[0], b.pos[2]) if b.grid else None
    if gy is not None and abs(gy - b.pos[1]) > 4.0:  # the ground under the bot just became known
        b.pos = (b.pos[0], gy, b.pos[2])
        return [bot_update_msg(b, bot_move_info(b, b.pos, now, {"move_v": 0.0}), {"move_state": bots.MOVE_STATE_STOP})]
    close = near_player(conn, b.pos)
    if b.weapon is None:
        if close:  # walk to the nearest gun on the ground
            r2 = BOT_LOOT_RANGE * BOT_LOOT_RANGE
            guns = [(uid, it) for uid, it in conn.loot.items.items() if it["item_id"] in conn.loot.guns
                    and (it["pos"][0] - b.pos[0]) ** 2 + (it["pos"][2] - b.pos[2]) ** 2 < r2]
            if guns:
                uid, it = min(guns, key=lambda u: (u[1]["pos"][0] - b.pos[0]) ** 2 + (u[1]["pos"][2] - b.pos[2]) ** 2)
                b.goal_item = uid
                return bot_go_to(b, it["pos"], now)
        if b.find_gun_at is None:
            b.find_gun_at = now + rng.uniform(6.0, 35.0)
        elif now >= b.find_gun_at:  # far away (or nothing around): it found one in a house
            return bot_equip(b, conn.loot.random_gun())
    # roam: known ground near the player, otherwise a short hop at the same height
    pts = loot.near_points(conn.loot.walk_points, b.pos, 700.0) if close else []
    if pts:
        return bot_go_to(b, rng.choice(pts), now)
    a = rng.uniform(0, 6.283)
    r = rng.uniform(150, 600)
    return bot_go_to(b, (b.pos[0] + r * math.cos(a), b.pos[1], b.pos[2] + r * math.sin(a)), now)


def bot_brain(conn):
    """Keeps the bots running whatever happens to one of them (errors are logged once per bot)."""
    try:
        bot_brain_loop(conn)
    except Exception:
        log("bot brain crashed\n%s" % traceback.format_exc(), console=True)


def bot_step_safe(conn, b, fn, *args):
    try:
        return fn(*args)
    except Exception:
        if not getattr(b, "error_logged", False):
            b.error_logged = True
            log("bot %d (%s) step failed\n%s" % (b.unit_id, b.state, traceback.format_exc()))
        return []


# The real server only sent the players around you (AOI). Sending all 99 bots several times a second
# made the client spend its frames on avatars it does not even show.
AOI_GROUND = 5000.0     # 500 m
AOI_AIR = 15000.0       # parachutes are seen from much further


def bot_aoi_filter(conn, b, msgs, now):
    """Only bots around the player exist on the client: created when they come close, removed when
    they go away; their simulation keeps running either way."""
    if b.state == "gone":
        b.in_aoi = False
        return msgs
    pp = getattr(conn, "player_pos", None)
    radius = AOI_AIR if b.state == "air" else AOI_GROUND
    if b.in_aoi:
        radius *= 1.1  # hysteresis: no create/destroy flicker at the border
    want = (b.state in ("air", "ground", "dead") and b.pos is not None and pp is not None
            and (b.pos[0] - pp[0]) ** 2 + (b.pos[2] - pp[2]) ** 2 < radius * radius)
    if want and not b.in_aoi:
        b.in_aoi = True
        if any(name == "S_FIGHT_PLAYER_INIT" for name, _ in msgs):
            return msgs
        return bot_enter_aoi(b, now)
    if not want and b.in_aoi:
        b.in_aoi = False
        return [("S_FIGHT_UNIT_DESTROY", {"unit_id": b.unit_id})]
    return msgs if b.in_aoi else []


def bot_enter_aoi(b, now):
    if b.state == "air":
        state, _, velo, pitch, _ = bots.flight_sample(b.flight, now - b.jump_time)
        ps = PLAYER_STATE_AIR if state == bots.AIR_STATE_FREE_DROP else bots.PLAYER_STATE_PARACHUTE
        return [bot_init_msg(b, now, bot_air_extra(b, state, velo, pitch), ps)]
    if b.state == "dead":
        return []
    out = [bot_init_msg(b, now, player_state=bots.PLAYER_STATE_START)]
    if b.weapon:
        out += bot_equip(b, b.weapon)
    return out


GROUND_MAP_TICK = 1.0


def ground_mapper(conn):
    """Fills conn.ground (bots.GroundMap) under the bots close to the player, 2 cells per second."""
    while conn.alive:
        time.sleep(GROUND_MAP_TICK)
        if conn.stage in ("prepare", "show") or getattr(conn, "player_pos", None) is None:
            continue
        gm, now = conn.ground, time.time()
        want = {}
        for b in conn.bots:
            if not getattr(b, "in_aoi", False) or b.state not in ("ground", "air"):
                continue
            x, z = (b.landing[0], b.landing[2]) if b.state == "air" and b.landing else (b.pos[0], b.pos[2])
            cell = gm.cell_of(x, z)
            if cell in gm.done or now < gm.retry_at.get(cell, 0):
                continue
            px, _, pz = conn.player_pos
            want[cell] = (x - px) ** 2 + (z - pz) ** 2
        for cell in sorted(want, key=want.get)[:2]:
            points = gm.cell_points(cell)
            layers = query_layers(points, LOOT_RAY_TOP, LOOT_RAY_BOTTOM)
            if layers is None:
                break
            gm.fill(cell, points, layers, now)


def bot_brain_loop(conn):
    """One thread drives every bot of the match, stage by stage."""
    rng = random.Random()
    time.sleep(1.5)  # let the map finish loading
    grid, spots = island_grid(conn, rng)
    waiting = list(conn.bots)
    rng.shuffle(waiting)
    next_join = time.time()
    joined = 0
    lobby_cleared = False
    next_air_sample = 0.0
    resolver = None
    while conn.alive:
        now = time.time()
        msgs = []
        if conn.stage in ("prepare", "show"):
            # pre-lobby: players keep arriving for a while, then walk, jump and swing around the island
            while waiting and now >= next_join:
                b = waiting.pop()
                b.state, b.pos, b.home, b.grid = "lobby", rng.choice(spots), spots, grid
                b.in_aoi = True
                b.next_walk_at = now + rng.uniform(0.5, 6.0)
                msgs.append(bot_init_msg(b, now))
                joined += 1
                next_join += rng.uniform(0.05, 0.6)
                send_player_num(conn, 1 + joined)
            for b in conn.bots:
                if b.state == "lobby":
                    msgs += bot_step_safe(conn, b, bot_events_step, b, now)
                    msgs += bot_step_safe(conn, b, bot_walk_step, b, now, rng)
        else:
            if not lobby_cleared:
                # everybody boards the bus: the pre-lobby bodies disappear
                lobby_cleared = True
                for b in conn.bots:
                    if b.state == "lobby":
                        msgs.append(("S_FIGHT_UNIT_DESTROY", {"unit_id": b.unit_id}))
                    b.state, b.walk, b.events, b.grid, b.in_aoi = "bus", None, [], None, False
                    if b.jump_at is None:
                        b.jump_at = bots.jump_delay(rng, AIRLINE_DOOR_DELAY, MATCH_SETTINGS["bus"])
                send_player_num(conn, 1 + len(conn.bots))
            # guessed landing heights: ask again regularly, the collision streams in around the player
            if (resolver is None or not resolver.is_alive()) and any(b.landing_guess for b in conn.bots):
                if now >= getattr(conn, "next_ground_retry", 0):
                    conn.next_ground_retry = now + GROUND_RETRY
                    guessed = [b for b in conn.bots if b.landing_guess]

                    def resolve(guessed=guessed):
                        if resolve_landings(guessed, rng, timeout=5.0):
                            conn.replan = [b for b in guessed if not b.landing_guess]
                    resolver = threading.Thread(target=resolve, daemon=True)
                    resolver.start()
            for b in getattr(conn, "replan", None) or []:
                msgs += bot_replan(b, now, rng)
            conn.replan = None
            guess_y = getattr(conn, "ground_guess_y", None)
            if guess_y is not None and not getattr(conn, "guess_applied", False):
                conn.guess_applied = True  # better guess than the default one for still unknown spots
                for b in conn.bots:
                    if b.landing_guess:
                        b.landing = (b.landing[0], guess_y, b.landing[2])
                        if b.state in ("air", "ground"):
                            msgs += bot_replan(b, now, rng)
            t_bus = now - conn.airline_t0
            air_tick = now >= next_air_sample
            if air_tick:
                next_air_sample = now + AIR_SAMPLE
            for b in conn.bots:
                bm = []
                if b.state == "bus" and (t_bus >= b.jump_at or conn.stage == "air"):
                    bm = bot_step_safe(conn, b, lambda: [bot_jump(conn, b, now, rng)])
                elif b.state == "air" and air_tick:
                    bm = bot_step_safe(conn, b, bot_air_step, b, now, rng)
                elif b.state in ("ground", "dead"):
                    if b.grid is None:
                        b.grid = conn.ground
                    bm = bot_step_safe(conn, b, bot_ground_step, conn, b, now, rng)
                msgs += bot_step_safe(conn, b, bot_aoi_filter, conn, b, bm, now)
        if msgs:
            send_frame(conn, msgs, quiet=True)
        time.sleep(BOT_TICK)


def bot_positions_reply():
    """For the client's "botview": name, position and state of every bot."""
    conn = ACTIVE_BATTLE["conn"]
    if conn is None or not conn.alive or not getattr(conn, "bots", None):
        return "@botpos "
    parts = []
    for b in conn.bots:
        if b.pos is None or b.state == "offline":
            continue
        parts.append("%d,%s,%.0f,%.0f,%.0f,%s" % (b.unit_id, b.name.replace(",", " ").replace(";", " "),
                                                 b.pos[0], b.pos[1], b.pos[2], b.state))
    return "@botpos " + ";".join(parts)


@handler("C_FIGHT_MOVE_BASIC")
def on_player_move(conn, msg):
    pos = (msg.get("info") or {}).get("pos")
    if pos and any(pos):
        conn.player_pos = tuple(pos)


@handler("C_FIGHT_OP_MOVE")
def on_player_op_move(conn, msg):
    info = msg.get("info") or {}
    if info.get("pos") and any(info["pos"]):
        conn.player_pos = tuple(info["pos"])
    # calibration notes for the bots (walk speed, heading convention); a few lines per match
    n = getattr(conn, "calib_lines", 0)
    if n < 40 and "move_dir" in info:
        conn.calib_lines = n + 1
        log("player move_dir=%.2f move_v=%.1f pos=%s last=%s" % (
            info["move_dir"] / 100.0, info.get("move_v", 0) / 10.0, info.get("pos"),
            getattr(conn, "player_pos", None)), "calib.log")


@handler("C_FIGHT_NOTIFY_CUSTOM_BUILD_INFO")
@handler("C_FIGHT_SYNC_CAMERA")
def on_fight_client_ready(conn, msg):
    battle_start_prepare(conn)


@handler("C_FIGHT_GET_RTA")
@handler("C_FIGHT_SYNC_STATE")
def on_fight_ignored(conn, msg):
    """Voice chat / client state notifications: nothing to do offline."""


RENAME_SUCC, RENAME_ERR_INPUT_EMPTY, RENAME_ERR_EXCEED_LEN_LIMIT = 0, 100, 101


def send_rename_count(conn, tip=0):
    conn.send("S_GAME_RENAME_CNT_INFO", {"free_rename_cnt": 99, "pay_rename_cnt": 99, "tip_flag": tip})


@handler("C_GAME_DO_RENAME_GUIDE")
@handler("C_GAME_DIRECT_BUY_RENAMECARD")
def on_rename_card(conn, msg):
    send_rename_count(conn)


@handler("C_GAME_APPLY_RENAME")
def on_apply_rename(conn, msg):
    new_name = msg.get("new_name", b"").decode("utf-8", "replace").strip()
    if not new_name:
        conn.send("S_GAME_RENAME_RESULT", {"err_code": RENAME_ERR_INPUT_EMPTY, "new_name": ""})
        return
    if len(new_name) > 20:
        conn.send("S_GAME_RENAME_RESULT", {"err_code": RENAME_ERR_EXCEED_LEN_LIMIT, "new_name": ""})
        return
    SAVE["name"] = new_name
    write_save(SAVE)
    log("renamed to %r" % new_name)
    conn.send("S_GAME_RENAME_RESULT", {"err_code": RENAME_SUCC, "new_name": new_name})
    send_rename_count(conn)


@handler("C_SYS_GAME_ECHO")
def on_echo(conn, msg):
    now = time.time()
    conn.sock.sendall(PROTO.encode("S_SYS_GAME_ECHO", {
        "timestamp": msg.get("timestamp", 0), "svr_ts": int(now), "svr_ms": int((now % 1) * 1000)}))


# ------------------------------------------------------------------ servers
def serve_tcp(port, kind, on_connect=None):
    s = socket.socket()
    # On Windows SO_REUSEADDR lets a second server steal the port; insist on exclusive use instead.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, port))
    s.listen(8)
    log("%s listening on %d" % (kind, port), console=True)
    while True:
        c, a = s.accept()
        threading.Thread(target=client_loop, args=(Connection(c, a, kind), on_connect), daemon=True).start()


# High-frequency client messages: not logged at all (they arrive ~20 times per second).
QUIET_MESSAGES = {"C_SYS_GAME_ECHO", "C_SYS_BATTLE_ECHO", "C_FIGHT_SYNC_CAMERA", "C_FIGHT_MOVE_BASIC",
                  "C_FIGHT_UPDATE_PARACHUTE_STATE", "C_GAME_NET_DELAY", "C_FIGHT_OP_MOVE"}


def client_loop(conn, on_connect):
    log("[%s] connect %s" % (conn.kind, conn.addr))
    try:
        if on_connect:
            on_connect(conn)
        for body in conn.messages():
            name, cmd, sub, data = PROTO.decode(body, "C")
            if name not in QUIET_MESSAGES:
                log("[%s] <- %s (%d,%d) %s" % (conn.kind, name or "?", cmd, sub, short(data)))
            fn = HANDLERS.get(name)
            if fn:
                try:
                    fn(conn, data or {})
                except Exception:
                    log("handler %s failed\n%s" % (name, traceback.format_exc()))
            elif name not in QUIET_MESSAGES:
                log("%s (%d,%d) %s" % (name or "?", cmd, sub, short(data, 600)), "unhandled.log")
    except (ConnectionError, OSError) as e:
        log("[%s] socket error %s" % (conn.kind, e))
    conn.alive = False
    log("[%s] disconnect %s" % (conn.kind, conn.addr))


LOGINLIST = "1 %s %d\n" % (HOST, LOGIN_PORT)
ZONELIST = "%d Offline pc 202001010000 1\n" % ZONE_ID
HTTP_FILES = {"loginlist_rel_pc_na.txt": LOGINLIST, "loginlist.txt": LOGINLIST,
              "client_zonelist_rel_pc_na.txt": ZONELIST, "client_zonelist.txt": ZONELIST}


class HttpHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?")[0].rsplit("/", 1)[-1]
        body = HTTP_FILES.get(name)
        log("HTTP GET %s -> %s" % (self.path, 200 if body else 404))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    log("Creative Destruction Reborn - offline server. Leave this window open while playing.", console=True)
    log("loaded %d protocol messages" % len(PROTO.by_name), console=True)
    threading.Thread(target=serve_tcp, args=(LOGIN_PORT, "login", send_seed), daemon=True).start()
    threading.Thread(target=serve_tcp, args=(GATE_PORT, "gate", gate_on_connect), daemon=True).start()
    threading.Thread(target=serve_tcp, args=(BATTLE_PORT, "battle", battle_on_connect), daemon=True).start()
    log("HTTP listening on %d" % HTTP_PORT, console=True)
    http.server.ThreadingHTTPServer((HOST, HTTP_PORT), HttpHandler).serve_forever()


if __name__ == "__main__":
    main()
