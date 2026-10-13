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
import json
import os
import socket
import struct
import threading
import time
import traceback

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
def gate_on_connect(conn):
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

ITEM_TYPE_PARACHUTE, ITEM_TYPE_CLOTHES_BASE, ITEM_TYPE_FLYING_MOUNT = 5, 11, 907
FULL_SUIT_SLOT, PART_SLOTS = "clothes_all", ("clothes_upper", "clothes_leg")


def equipped_character(gender):
    """Body parts + worn clothes for a gender: defaults overridden by what the player equipped."""
    worn = dict(DEFAULT_BODY[gender])
    worn.update({k: v for k, v in SAVE.get("character", {}).get(str(gender), {}).items()})
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
# Pre-lobby island: the exterior show stage (ca/info_exterior_person) sits there; spawn just above it.
PRE_LOBBY_SPAWN = [13754.0, 950.0, -10580.0]
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


def send_frame(conn, messages):
    """World-state messages travel inside S_FIGHT_FRAME (the client tags them with frame_idx)."""
    conn.frame_idx = getattr(conn, "frame_idx", 0) + 1
    bufs = [{"msg_buf": PROTO.encode(name, data)} for name, data in messages]
    conn.sock.sendall(PROTO.encode("S_FIGHT_FRAME", {"frame_idx": conn.frame_idx, "msg_list": bufs}))
    log("[%s] -> FRAME %d %s" % (conn.kind, conn.frame_idx, [n for n, _ in messages]))


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
    conn.send("S_FIGHT_NOTIFY_SHOW_EXTERIOR", {"info": [{
        "name": SAVE["name"], "score": 0, "gun": "scar", "gender": SAVE["gender"], "country_code": "FR",
        "appearance": player_appearance()}]})
    conn.send("S_FIGHT_START_SHOW_EXTERIOR", {})
    if not wait_until(start_at + MATCH_SETTINGS["show"]):
        return
    # 2. the bus
    new_airline_route()
    bus = MATCH_SETTINGS["bus"]
    t0 = time.time()
    conn.airline_t0 = t0
    conn.send("S_FIGHT_STATE_INFO", {"stage": STAGE_START, "start_stage_time": int(t0)})
    conn.send("S_FIGHT_AIRLINE_PREPARE", {"airlines": [{"airline_id": AIRLINE_ID, "start_jump_pos": ROUTE["start"],
                                                        "end_jump_pos": ROUTE["end"]}]})
    sec, ms = ts(t0)
    conn.send("S_FIGHT_AIRLINE_START", {"airline_id": AIRLINE_ID, "airlines_info": [{
        "airline_id": AIRLINE_ID, "time_ts": sec, "time_ms": ms, "flight_velo": airline_velo(),
        "pos": ROUTE["start"], "player_state": PLAYER_STATE_AIR, "before_para": AIRLINE_DOOR_DELAY,
        "open_door_time": int(t0) + AIRLINE_DOOR_DELAY, "force_jump_time": int(t0 + bus),
        "left_player_num": 1, "is_spec_airline": 0, "airstrike_time": 0, "broken_time": 0,
        "duration": int(bus)}]})
    while conn.alive and time.time() < t0 + bus:
        time.sleep(1.0)
        now = time.time()
        sec, ms = ts(now)
        conn.sock.sendall(PROTO.encode("S_FIGHT_AIRLINE_UPDATE", {"airlines_info": [{
            "time_ts": sec, "time_ms": ms, "flight_velo": airline_velo(), "pos": airline_pos(t0, now),
            "left_player_num": 0 if getattr(conn, "jumped", False) else 1, "airline_id": AIRLINE_ID}]}))
    # 3. end of the route: everybody still aboard is ejected
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
    start_at = time.time() + PREPARE_SECONDS
    conn.send("S_FIGHT_WAIT_BEFORE_START", {"start_time": int(start_at)})
    threading.Thread(target=run_match_timeline, args=(conn, start_at), daemon=True).start()
    return "Countdown started: the match begins in %d s." % PREPARE_SECONDS


CONSOLE_COMMANDS = {"start": cmd_start}


@handler("C_LOGIN_WIZ_CMD")
def on_console_command(conn, msg):
    text = msg.get("cmd", b"").decode("utf-8", "replace").strip()
    name, _, args = text.partition(" ")
    fn = CONSOLE_COMMANDS.get(name.lower())
    resp = fn(args) if fn else "Unknown command: %s" % name
    log("console %r -> %s" % (text, resp))
    conn.send("S_LOGIN_WIZ_CMD_RESP", {"result": 1 if fn else 0, "resp": resp})


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
