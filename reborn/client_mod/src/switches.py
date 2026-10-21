# -*- coding: utf-8 -*-
# Override of switches.py for offline play.
# Original constants are kept unchanged; the offline hooks are installed below.
IS_GLOBAL = True
USE_PYTHON_DCTOOL = True
FILTER_COMPETITOR_LIVE = False
CC_GAME_LIVE = True
CC_GAME_LIVE_YOUTUBE = True
CC_GAME_LIVE_FACEBOOK_LIVE = True
USE_SOCIAL_APP_DEEP_LINK = True
USE_UI_LANGUAGE_FILTER = True
OPEN_BIND_EMAIL_ACTIVITY = True
OPEN_FACEBOOK_FRIEND_LIST = True
ENABLE_COMPETITION_LIVE = True
ACT_CD_NOVA_VOTE = False

OFFLINE_LOG = 'Documents/offline_mod.log'


def offline_log(*args):
    try:
        import time
        f = open(OFFLINE_LOG, 'a')
        f.write('%s %s\n' % (time.strftime('%H:%M:%S'), ' '.join([str(a) for a in args])))
        f.close()
    except Exception:
        pass


class _NoOp(object):
    """Callable placeholder for SDK methods the offline build does not need."""

    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        if self.name.startswith('is_') or self.name.startswith('has_'):
            return False
        offline_log('sdk noop call', self.name, args[:2])
        return None


class OfflineSdkChannel(object):
    """Stands in for the NetEase UniSDK channel: always logged in with a local account."""

    def __init__(self):
        self.name = 'netease_global'
        self.platform = 'win32'
        self.sdk_version = 'offline'
        self.available_pay_channels = ''
        self.is_init = True
        self.is_login = False
        self.login_callback = None
        self.logout_callback = None
        self._props = {
            'UID': 'offline_player',
            'SESSION': 'offline_session',
            'DEVICE_ID': 'offline_device',
            'UDID': 'offline_udid',
            'SAUTH_JSON': '{}',
            'ORIGIN_GUEST_UID': 'offline_player',
            'LOGIN_STAT': '1',
        }

    def __getattr__(self, name):
        return _NoOp(name)

    def get_prop_str(self, key):
        return self._props.get(key, '')

    def set_prop_str(self, key, value):
        self._props[key] = value

    def get_prop_int(self, key):
        try:
            return int(self._props.get(key, 0))
        except Exception:
            return 0

    def set_prop_int(self, key, value):
        self._props[key] = value

    def initialize(self, *args):
        self.is_init = True
        hook_game_log()

    def login(self, *args):
        offline_log('sdk login -> success')
        hook_game_log()
        _poll_debug_traces()
        self.is_login = True
        cb = self.login_callback
        if cb is not None:
            def fire():
                try:
                    cb(0)
                except Exception:
                    import traceback
                    offline_log('login callback raised', traceback.format_exc())
            try:
                import g
                g.callback(0.1, fire)
            except Exception:
                fire()

    def logout(self, *args):
        self.is_login = False
        if self.logout_callback is not None:
            self.logout_callback(0)

    def has_feature(self, *args):
        return False


class _LogStream(object):
    """Redirects stderr (tracebacks) into the offline log while keeping the original stream."""

    def __init__(self, orig, tag):
        self.orig = orig
        self.tag = tag

    def write(self, s):
        try:
            if s.strip():
                offline_log(self.tag, s.rstrip())
        except Exception:
            pass
        try:
            if self.orig is not None:
                self.orig.write(s)
        except Exception:
            pass

    def flush(self):
        try:
            if self.orig is not None:
                self.orig.flush()
        except Exception:
            pass


def hook_game_log():
    """Mirror the game's own g.log messages into the offline log (called once g exists)."""
    try:
        import g
        log = getattr(g, 'log', None)
        if log is None or getattr(log, '_offline_hooked', False):
            return
        try:
            import __builtin__
            orig_tb = getattr(__builtin__, '__G__EXPORT_TB__', None)
            if orig_tb is not None and not getattr(orig_tb, '_offline_hooked', False):
                def export_tb(*args, **kwargs):
                    try:
                        import traceback
                        offline_log('[__G__EXPORT_TB__]', traceback.format_exc())
                    except Exception:
                        pass
                    return orig_tb(*args, **kwargs)
                export_tb._offline_hooked = True
                __builtin__.__G__EXPORT_TB__ = export_tb
        except Exception as e:
            offline_log('export_tb hook failed', repr(e))
        for level in ('critical', 'error', 'warning', 'info', 'e'):
            orig = getattr(log, level, None)
            if orig is None:
                continue

            def make(orig=orig, level=level):
                def wrapper(*args, **kwargs):
                    try:
                        offline_log('[g.log.%s]' % level, *args)
                    except Exception:
                        pass
                    return orig(*args, **kwargs)
                return wrapper
            setattr(log, level, make())
        log._offline_hooked = True
        offline_log('g.log hooked')
    except Exception as e:
        offline_log('g.log hook failed', repr(e))


def _trace_method(cls, name, describe=None):
    """Log calls (and exceptions) of cls.name for debugging."""
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, '_offline_traced', False):
        return

    def wrapper(self, *args, **kwargs):
        try:
            offline_log('[trace] %s.%s' % (cls.__name__, name), describe(self, *args) if describe else '')
        except Exception as e:
            offline_log('[trace] describe failed', repr(e))
        try:
            return orig(self, *args, **kwargs)
        except Exception:
            import traceback
            offline_log('[trace] %s.%s raised' % (cls.__name__, name), traceback.format_exc())
            raise
    wrapper._offline_traced = True
    setattr(cls, name, wrapper)


def _lobby_state(self, *args):
    import g
    bhp = getattr(g, 'before_home_player', None)
    return 'loaded=%s inited=%s players=%s transforms=%d args=%s' % (
        getattr(self, '_loaded', None), getattr(bhp, 'user_info_inited', None),
        list(getattr(self, '_players', {}).keys()), len(getattr(self, '_player_transformation', [])),
        str(args)[:200])


def _weapon_state(self, *args):
    try:
        import cconst
        forbid = bool(self.feature & cconst.FeatureOpt.CHANGE_WEAPON_FORBID)
    except Exception:
        forbid = '?'
    return 'args=%s equip=%s using=%s forbid=%s in_air=%s feature=%s' % (
        str(args)[:60], sorted(getattr(self, 'equip_dict', {}).keys()),
        getattr(getattr(self, 'Weapon', None), 'using_weapon_slot', None), forbid,
        getattr(self, 'states_in_air', None) is not None, getattr(self, 'feature', None))


def _install_weapon_traces():
    import entity.player_avatar as pa
    for name in ('request_set_weapon', 'set_weapon', 'switch_to_slot0', 'take_off_weapon'):
        _trace_method(pa.PlayerAvatar, name, _weapon_state)


def _install_debug_traces():
    import sys
    try:
        _install_weapon_traces()
    except Exception as e:
        offline_log('weapon traces failed', repr(e))
    mod = sys.modules.get('game_world.GWLobby')
    if mod is None:
        return False
    for name in ('create_all_lobby_player', 'create_lobby_player', 'on_finish_loading_scene',
                 'change_to', 'remove_all_lobby_player', 'remove_lobby_player'):
        _trace_method(mod.GWLobby, name, _lobby_state)
    lp = sys.modules.get('entity.lobby_player')
    if lp is not None:
        _trace_method(lp.LobbyPlayer, 'init_from_dict', lambda self, d: str(d)[:300])
    offline_log('debug traces installed')
    return True


# ---------------------------------------------------------------- gender switch on the profile page
MALE, FEMALE = 1, 2
DEFAULT_BASE_BY_GENDER = {MALE: 16, FEMALE: 17}  # share/game_item DEFAULT_CLOTHES_BASE_MALE/_FEMALE
GENDER_ICON = {MALE: 'ui1/common0/gender_male_pic.png', FEMALE: 'ui1/common0/gender_female_pic.png'}


def _toggle_gender(page):
    try:
        if not getattr(page, 'is_self', False):
            return
        cur = page.args.get('gender', MALE)
        new = FEMALE if cur == MALE else MALE
        from game_logic import game_cmd_manager
        game_cmd_manager.C_GAME_USE_ITEM(DEFAULT_BASE_BY_GENDER[new])
        page.args['gender'] = new
        page.root.gender.loadTexture(GENDER_ICON[new])
        offline_log('gender switched to', new)
    except Exception:
        import traceback
        offline_log('gender switch failed', traceback.format_exc())


def _make_gender_clickable(page):
    widget = page.root.gender
    if not getattr(page, 'is_self', False):
        widget.setTouchEnabled(False)
        return
    widget.setTouchEnabled(True)
    if getattr(page, '_offline_gender_hooked', False):
        return
    page._offline_gender_hooked = True
    try:
        from cocosui import ccui
        ended = ccui.TOUCH_EVENT_ENDED
    except Exception:
        ended = 2

    def on_touch(sender, flag):
        if flag == ended:
            _toggle_gender(page)
    raw = widget.getInst() if hasattr(widget, 'getInst') else widget
    raw.addTouchEventListener(on_touch)


def _install_profile_patch():
    import uiview.player_info_view_new as pv
    cls = pv.CPlayerInfoHomePage
    if getattr(cls.refresh_ui, '_offline_patched', False):
        return
    orig = cls.refresh_ui

    def refresh_ui(self, *args, **kwargs):
        ret = orig(self, *args, **kwargs)
        try:
            _make_gender_clickable(self)
        except Exception:
            import traceback
            offline_log('gender button failed', traceback.format_exc())
        return ret
    refresh_ui._offline_patched = True
    cls.refresh_ui = refresh_ui
    offline_log('profile gender switch installed')


# ---------------------------------------------------------------- "BOTS" button under SOLO (match mode panel)
BOT_COUNT_KEY = 'offline_bot_count'
BOT_STEPS = (('-10', -10), ('-1', -1), ('+1', 1), ('+10', 10), ('OK', None))
BOT_MENU_SCALE = 0.62
BOT_STATE = {'count': None, 'controls': [], 'synced': False}


def _bot_count():
    if BOT_STATE['count'] is None:
        try:
            import g
            BOT_STATE['count'] = int(g.user_data.read_data(BOT_COUNT_KEY, 0) or 0)
        except Exception:
            BOT_STATE['count'] = 0
    return BOT_STATE['count']


def _store_bot_count(n):
    BOT_STATE['count'] = n
    try:
        import g
        g.user_data.write_data(BOT_COUNT_KEY, n)
    except Exception:
        pass
    for ctrl in list(BOT_STATE['controls']):
        ctrl.refresh()


def _set_bot_count(n):
    n = max(0, min(99, n))
    _store_bot_count(n)
    try:
        send_server_cmd('bots %d' % n)
    except Exception:
        import traceback
        offline_log('bots cmd failed', traceback.format_exc())


def _on_bots_response(resp):
    """The console command "bots N" answers "Bots: N ...": keep the button in sync."""
    if resp.startswith('Bots: '):
        try:
            _store_bot_count(int(resp.split()[1]))
        except Exception:
            pass


def _bot_label(n):
    return 'BOTS : %d' % n


class _BotControls(object):
    """Main button ("BOTS : 20") + a row of -10 -1 +1 +10 OK shown when it is clicked."""

    def __init__(self, node, btn_cls):
        import cc
        from ui import ui_base
        self.node, self.open = node, False
        parent = node.panel_option_all
        csb = btn_cls.UI_FILE
        self.main = ui_base.CreateUIWidget(parent, csb, btn_cls, args={'text': '', 'on_press': self.toggle})
        self.main.reset({'text': _bot_label(_bot_count()), 'on_press': self.toggle})
        size = self.main.getContentSize()
        y = -size.height - 6
        self.main.setPosition(cc.Vec2(20, y))
        self.steps = []
        x = 20 + size.width + 10
        for text, delta in BOT_STEPS:
            press = self.close if delta is None else (lambda d=delta: _set_bot_count(_bot_count() + d))
            btn = ui_base.CreateUIWidget(parent, csb, btn_cls, args={'text': text, 'on_press': press})
            btn.reset({'text': text, 'on_press': press})
            btn.setScale(BOT_MENU_SCALE)
            btn.setPosition(cc.Vec2(x, y + size.height * (1 - BOT_MENU_SCALE) / 2))
            btn.setVisible(False)
            x += size.width * BOT_MENU_SCALE + 4
            self.steps.append(btn)

    def toggle(self):
        self.open = not self.open
        self.refresh()

    def close(self):
        self.open = False
        self.refresh()

    def set_visible(self, visible):
        self.main.setVisible(visible)
        if not visible:
            self.open = False
        self.refresh()

    def refresh(self):
        try:
            self.main.setText(_bot_label(_bot_count()))
            self.main.setSelected(self.open)
            for btn in self.steps:
                btn.setVisible(self.open and self.main.isVisible())
        except Exception:
            # the lobby panel was destroyed: forget these widgets
            if self in BOT_STATE['controls']:
                BOT_STATE['controls'].remove(self)


def _install_bot_button():
    import uiview.lobby_match_mode_cell as mc
    from ui import ui_base
    node_cls = mc.LobbyMatchModeNode
    if getattr(node_cls.set_data, '_offline_patched', False):
        return

    class BotButton(mc.TeamModeBtn):
        """Same look as the SOLO / DUO buttons, but just calls on_press."""

        def reset(self, args):
            super(ui_base.CUIWidget, self).reset(args)
            self.data = args
            self.game_mode, self.team_mode = 0, 0
            self.is_game_mode_selected, self.is_enable, self.is_selected = True, True, False
            self.on_press = args.get('on_press')
            self.setText(args.get('text', ''))
            self.setEnabled(True)
            self.setSelected(False)

        def on_click_btn_mode1(self):
            if self.on_press:
                self.on_press()

    orig = node_cls.set_data

    def set_data(self, data):
        ret = orig(self, data)
        try:
            has_solo = mc.GAME_MODE.SOLO in (self.allow_team_modes or [])
            ctrl = getattr(self, '_offline_bots', None)
            if ctrl is None and has_solo:
                ctrl = self._offline_bots = _BotControls(self, BotButton)
                BOT_STATE['controls'].append(ctrl)
            if ctrl is not None:
                ctrl.set_visible(has_solo)
            if not BOT_STATE['synced']:
                BOT_STATE['synced'] = True
                _set_bot_count(_bot_count())  # the server learns the saved choice
        except Exception:
            import traceback
            offline_log('bot button failed', traceback.format_exc())
        return ret
    set_data._offline_patched = True
    node_cls.set_data = set_data
    offline_log('bot button installed')


# ---------------------------------------------------------------- talking to the offline server
def send_server_cmd(text):
    """Console channel (C_LOGIN_WIZ_CMD on the lobby connection, alive during matches too)."""
    import __builtin__
    PT = getattr(__builtin__, 'PT', None)
    if PT is None:
        import proto.proto_list as PT
    from network import net
    net.send_prot(PT.LOGIN_PTO.C_LOGIN_WIZ_CMD, {'cmd': text})


def _ground_height(scene, mask, x, z, top, bottom):
    import math3d
    import cconst
    hit = scene.scene_col.hit_by_ray(math3d.vector(x, top, z), math3d.vector(x, bottom, z),
                                     cconst.COLOR_WHITE, cconst.COLLISION_FILTER_STATIC, mask, 0)
    if hit and hit[0]:
        return hit[1].y, 'col'
    # far away the collision is not streamed in: the navigation mesh covers the whole map
    y = _nav_height(x, z)
    if y is not None and bottom < y < top:
        return y, 'nav'
    # last resort: the terrain height map
    try:
        h = scene.terrain.get_height(math3d.vector(x, 0.0, z))[0]
    except Exception:
        if not GROUND_STATS.get('terrain_logged'):
            GROUND_STATS['terrain_logged'] = True
            import traceback
            offline_log('terrain height failed', traceback.format_exc())
        return None, None
    if h is not None and bottom < h < top:
        return h, 'terrain'
    return None, None


NAV = {'world': None, 'detour': None}


def _load_navmesh():
    """The battle world can load the map's navigation mesh (GWDts.load_scene_nav_mesh) but the
    game never does it in battle royale. Load it once per match if the file exists."""
    import g
    mgr = getattr(g, 'gameworld_mgr', None)
    world = getattr(mgr, 'current_world', None) if mgr else None
    if world is None or world is NAV['world']:
        return NAV['detour']
    NAV['world'], NAV['detour'] = world, None
    offline_log('navmesh: trying for', world.__class__.__name__)
    try:
        import cc
        scene_file = (getattr(world, '_info_dict', None) or {}).get('scene_file', '') or ''
        base = scene_file.replace(chr(92), '/').split('/')[-1].split('.')[0]
        fu = cc.FileUtils.getInstance()
        for name in [base, 'upgrade_4096']:
            path = 'detour/%s/%s.nav' % (name, name)
            exists = bool(name) and fu.isFileExist(path)
            offline_log('navmesh', path, 'exists' if exists else 'missing')
            if exists:
                world.load_scene_nav_mesh(name)
                NAV['detour'] = world.detour
                p = g.player.position if g.player else None
                if p is not None:
                    offline_log('navmesh loaded, height under player', world.detour.get_height(p.x, p.z), 'player y', p.y)
                break
    except Exception:
        import traceback
        offline_log('navmesh load failed', traceback.format_exc())
    return NAV['detour']


def _nav_height(x, z):
    try:
        detour = _load_navmesh()
        if detour is None:
            return None
        return detour.get_height(x, z)
    except Exception:
        if not GROUND_STATS.get('nav_logged'):
            GROUND_STATS['nav_logged'] = True
            import traceback
            offline_log('navmesh height failed', traceback.format_exc())
        return None


def _water_height(scene, x, y, z):
    import math3d
    try:
        return scene.terrain.get_height(math3d.vector(x, y, z))[3]
    except Exception:
        return None


GROUND_STATS = {'logged': 0}


def _answer_ground_query(resp):
    """'@ground <id> <top> <bottom> x,z;x,z;...' -> '@ground <id> y;y;n;w;...' (n = nothing, w = water).
    The server has no map data: it asks the client where the ground is for its bots."""
    parts = resp.split(' ', 4)
    qid, top, bottom = parts[1], float(parts[2]), float(parts[3])
    out = []
    sources = {}
    try:
        import g
        import math3d
        import cconst
        scene = g.current_scene
        mask = cconst.COLLISION_GROUP_ALL & ~(cconst.COLLISION_GROUP_PLAYER | cconst.COLLISION_GROUP_PLAYER_PART)
        for pt in (parts[4] if len(parts) > 4 else '').split(';'):
            if not pt:
                continue
            x, z = [float(v) for v in pt.split(',')]
            value = 'n'
            try:
                y, src = _ground_height(scene, mask, x, z, top, bottom)
                sources[src] = sources.get(src, 0) + 1
                if y is not None:
                    value = '%.1f' % y
                    if GROUND_STATS['logged'] < 12:
                        GROUND_STATS['logged'] += 1
                        offline_log('ground', x, z, '->', y, src)
                    water = _water_height(scene, x, y, z)
                    if water is not None and 20.0 < water - y < 3000.0:
                        value = 'w'
            except Exception:
                if GROUND_STATS.get('errors', 0) < 5:
                    GROUND_STATS['errors'] = GROUND_STATS.get('errors', 0) + 1
                    import traceback
                    offline_log('ground point failed', x, z, traceback.format_exc())
            out.append(value)
    except Exception:
        import traceback
        offline_log('ground query failed', traceback.format_exc())
    offline_log('ground query', qid, len(out), 'points', sources)
    send_server_cmd('@ground %s %s' % (qid, ';'.join(out)))


MAX_LAYERS = 6
LAYER_SKIP = 6.0  # start the next ray a little under the surface just found


def _answer_layers_query(resp):
    """'@layers <id> <top> <bottom> x,z;...' -> '@layers <id> y/y/y;n;...': every surface under each
    point, highest first (roof, floors, ground). The server finds house interiors with it."""
    parts = resp.split(' ', 4)
    qid, top, bottom = parts[1], float(parts[2]), float(parts[3])
    out = []
    try:
        import g
        import math3d
        import cconst
        col = g.current_scene.scene_col
        mask = cconst.COLLISION_GROUP_ALL & ~(cconst.COLLISION_GROUP_PLAYER | cconst.COLLISION_GROUP_PLAYER_PART)
        for pt in (parts[4] if len(parts) > 4 else '').split(';'):
            if not pt:
                continue
            x, z = [float(v) for v in pt.split(',')]
            ys = []
            start = top
            try:
                while len(ys) < MAX_LAYERS and start > bottom:
                    hit = col.hit_by_ray(math3d.vector(x, start, z), math3d.vector(x, bottom, z),
                                         cconst.COLOR_WHITE, cconst.COLLISION_FILTER_STATIC, mask, 0)
                    if not (hit and hit[0]):
                        break
                    ys.append(hit[1].y)
                    start = hit[1].y - LAYER_SKIP
            except Exception:
                pass
            out.append('/'.join(['%.1f' % y for y in ys]) if ys else 'n')
    except Exception:
        import traceback
        offline_log('layers query failed', traceback.format_exc())
    send_server_cmd('@layers %s %s' % (qid, ';'.join(out)))


# ---------------------------------------------------------------- no anti-cheat, no telemetry
def _noop(*args, **kwargs):
    return None


def _install_no_telemetry():
    """Offline there is nobody to report to. Turned off:
    - anti-cheat (antisdk): every 10 min it probes the 65535 possible process ids of the PC,
      plus registry / file monitors and screenshots on kill streaks (MouseCounter)
    - performance monitor (samples every frame, uploads to sigma-performance.*.netease.com)
    - hubble logger (uploads to the same NetEase host)
    - key / mouse input logger and the p2 logger (device, acceleration, key info reports)"""
    done = []
    try:
        import anti_cheat_helper.antisdk.antisdk_helper as ah
        for name in ('init_sdk', 'start_check', 'detect_all_process_info', 'try_registry_log', 'try_monitor_file'):
            if hasattr(ah, name):
                setattr(ah, name, _noop)
        ah.stop_check()
        ah._enable_check = False
        done.append('antisdk')
    except Exception as e:
        offline_log('no-telemetry antisdk', repr(e))
    try:
        import anti_cheat_helper.rawinput_helper as rh
        rh.MouseCounter.start = _noop
        done.append('mouse counter')
    except Exception as e:
        offline_log('no-telemetry mouse counter', repr(e))
    try:
        import helper.performance_monitor as pm
        pm.stop_monitoring_timer()
        pm.start_monitoring_timer = _noop
        pm.upload_data = _noop
        pm.IS_MONITORING = False
        done.append('performance monitor')
    except Exception as e:
        offline_log('no-telemetry performance monitor', repr(e))
    try:
        import g
        from helper.hubble_logger import HubbleLogger
        HubbleLogger.send_data = _noop
        HubbleLogger.add_data = _noop
        HubbleLogger.start_upload = _noop
        if getattr(g, 'hubble_logger', None):
            g.hubble_logger.stop_upload()
        done.append('hubble')
    except Exception as e:
        offline_log('no-telemetry hubble', repr(e))
    try:
        import helper.InputLogger as il
        il.Logger.is_enable = lambda self: False
        il.Logger.add = _noop
        done.append('input logger')
    except Exception as e:
        offline_log('no-telemetry input logger', repr(e))
    try:
        from helper import p2logger
        p2logger.CP2Logger.on_finish = _noop
        done.append('p2logger')
    except Exception as e:
        offline_log('no-telemetry p2logger', repr(e))
    offline_log('telemetry / anti-cheat off:', ', '.join(done))


ATTACK_HIT_SCENE_MODEL = 12  # share/const.py


def _install_destruct_hits():
    """Pickaxe hits on the scenery (trees, rocks, houses) only carry the model id: tell the server
    the model's hit points and material (ca/info_scene_model, via the destruct data the client
    loaded) so it can damage / destroy it and give materials."""
    import fight.fight_cmd_manager as fcm
    if getattr(fcm.C_FIGHT_CLOSE_COMBAT_HIT, '_offline_patched', False):
        return
    orig = fcm.C_FIGHT_CLOSE_COMBAT_HIT

    def C_FIGHT_CLOSE_COMBAT_HIT(hit_unit, is_jump, hit_part, extra_hit_info=None, hit_model_id=0, fight_stage=1):
        ret = orig(hit_unit, is_jump, hit_part, extra_hit_info, hit_model_id, fight_stage)
        if hit_part == ATTACK_HIT_SCENE_MODEL and hit_model_id:
            try:
                import g
                from ca import info_scene_model
                cid = g.structure_container._config_id.get(hit_model_id)
                info = info_scene_model.get_data(cid) or {} if cid is not None else {}
                (x, y, z), (dx, dy, dz) = extra_hit_info[0], extra_hit_info[1]
                send_server_cmd('@dthit %d %d %d %.1f %.1f %.1f %.3f %.3f %.3f' % (
                    hit_model_id, info.get('max_hp', 0) or 0, info.get('hit_sound', 0) or 0, x, y, z, dx, dy, dz))
            except Exception:
                import traceback
                offline_log('destruct hit failed', traceback.format_exc())
        return ret
    C_FIGHT_CLOSE_COMBAT_HIT._offline_patched = True
    fcm.C_FIGHT_CLOSE_COMBAT_HIT = C_FIGHT_CLOSE_COMBAT_HIT
    offline_log('destruct hits installed')


# ---------------------------------------------------------------- noclip (console: noclip)
NOCLIP_SPEED = 600.0      # vertical units per second (x3 while sprinting)
NOCLIP_BOOST = 5.0        # extra horizontal distance per walked distance: 6x the normal speed
NOCLIP_TICK = 0.03


class Noclip(object):
    """Minecraft-style flight: no gravity, no collision. Hold the player's own jump key to go up,
    the crouch key to go down, release both to hover; the move keys work as usual.
    The jump / crouch actions are taken over in PlayerAvatar.on_action_move_msg, so whatever
    keys the player bound in the settings are used."""

    def __init__(self):
        self.on = False
        self.up = self.down = False
        self.last_tick = None
        self.last_pos = None

    def set_dir(self, up, pressed):
        if up:
            self.up = pressed
        else:
            self.down = pressed

    def _ctrl(self):
        import g
        return g.player.EntityCtrl if g.player else None

    def toggle(self):
        ctrl = self._ctrl()
        if ctrl is None or ctrl.character is None:
            return 'noclip: only in a match'
        import cconst
        self.on = not self.on
        self.up = self.down = False
        if self.on:
            ctrl.dettach_gravity()
            ctrl.character.mask = 0
            self.last_tick = self.last_pos = None
            self.tick()
        else:
            ctrl.character.mask = cconst.COLLISION_FILTER_STATIC
            ctrl.attach_gravity()
        return 'noclip ON (jump = up, crouch = down)' if self.on else 'noclip OFF'

    def tick(self):
        if not self.on:
            return
        import time
        now = time.time()
        dt = min(now - self.last_tick, 0.1) if self.last_tick else 0.0
        self.last_tick = now
        up, down = self.up, self.down
        try:
            import g
            import math3d
            p = g.player.position
            x, y, z = p.x, p.y, p.z
            # horizontal boost: whatever the player walked since the last tick, add it again x NOCLIP_BOOST
            last = self.last_pos
            if dt and last is not None:
                dx, dz = x - last[0], z - last[2]
                if dx * dx + dz * dz > 0.01:
                    x += dx * NOCLIP_BOOST
                    z += dz * NOCLIP_BOOST
            if dt and up != down:
                speed = NOCLIP_SPEED * (3.0 if g.player.is_dash() else 1.0)
                y += (speed if up else -speed) * dt
            if (x, y, z) != (p.x, p.y, p.z):
                pos = math3d.vector(x, y, z)
                g.player.position = pos
                g.player.EntityCtrl.set_real_pos(pos)
            self.last_pos = (x, y, z)
        except Exception:
            import traceback
            offline_log('noclip move failed', traceback.format_exc())
        try:
            import g
            g.callback(NOCLIP_TICK, self.tick)
        except Exception:
            pass


NOCLIP = Noclip()


def _install_noclip_controls():
    """Jump / crouch actions (whatever keys are bound) drive the noclip flight when it is on.
    Patched on the class before the match: the player registers self.on_action_move_msg later."""
    import entity.player_avatar as pa
    import helper.KeyboardDef as KD
    cls = pa.PlayerAvatar
    if getattr(cls.on_action_move_msg, '_offline_patched', False):
        return
    orig = cls.on_action_move_msg

    def on_action_move_msg(self, event_id, is_key_down, is_force_change=False):
        if NOCLIP.on and event_id in (KD.ACTION_MOVE_JUMP, KD.ACTION_MOVE_CROUCH):
            NOCLIP.set_dir(event_id == KD.ACTION_MOVE_JUMP, is_key_down)
            return None
        return orig(self, event_id, is_key_down, is_force_change)
    on_action_move_msg._offline_patched = True
    cls.on_action_move_msg = on_action_move_msg
    offline_log('noclip controls installed')


# ---------------------------------------------------------------- botview (console: botview)
BOTVIEW_PERIOD = 0.5
BOTVIEW_LINES = 12
BOT_STATE_FR = {'lobby': 'lobby', 'bus': 'bus', 'air': 'en l\'air', 'ground': 'au sol'}


class BotView(object):
    """Wallhack for testing: bots outlined through walls (the X-ray scope effect) + a list of the
    nearest ones with distance and clock direction. Positions come from the server (@botpos)."""

    def __init__(self):
        self.on = False
        self.label = None
        self.outlined = set()

    def toggle(self):
        self.on = not self.on
        if self.on:
            self.tick()
            return 'botview ON'
        self._clear_outlines()
        self._show_text(None)
        return 'botview OFF'

    def tick(self):
        if not self.on:
            return
        try:
            send_server_cmd('@botpos')
        except Exception:
            pass
        try:
            import g
            g.callback(BOTVIEW_PERIOD, self.tick)
        except Exception:
            pass

    def _avatar(self, uid):
        from common.avatar_manager import AvatarManager
        return AvatarManager().get_avatar(uid)

    def _clear_outlines(self):
        for uid in list(self.outlined):
            try:
                ent = self._avatar(uid)
                if ent:
                    ent.show_xray_outline(False)
            except Exception:
                pass
        self.outlined.clear()

    def on_positions(self, resp):
        if not self.on:
            return
        bots_ = []
        for part in resp[len('@botpos '):].split(';'):
            f = part.split(',')
            if len(f) == 6:
                bots_.append((int(f[0]), f[1], float(f[2]), float(f[3]), float(f[4]), f[5]))
        alive = set()
        for uid, _, _, _, _, _ in bots_:
            try:
                ent = self._avatar(uid)
                if ent and ent.Model.loaded:
                    alive.add(uid)
                    if not getattr(ent, 'show_xray', False):
                        ent.show_xray_outline(True)
                    self.outlined.add(uid)
            except Exception:
                pass
        try:
            self._show_text(self._describe(bots_))
        except Exception:
            import traceback
            offline_log('botview text failed', traceback.format_exc())

    def _describe(self, bots_):
        import g
        import math
        if not g.player:
            return 'BOTVIEW: %d bots' % len(bots_)
        p = g.player.position
        fwd = g.player.forward
        facing = math.atan2(fwd.x, fwd.z)
        counts = {}
        rows = []
        for uid, name, x, y, z, state in bots_:
            counts[state] = counts.get(state, 0) + 1
            dx, dz = x - p.x, z - p.z
            dist = math.sqrt(dx * dx + dz * dz)
            rel = math.degrees(math.atan2(dx, dz) - facing) % 360.0
            hour = int(round(rel / 30.0)) % 12 or 12
            rows.append((dist, '%-16s %5d m  %2dh  %+4d m  %s' % (
                name[:16], int(dist / 10), hour, int((y - p.y) / 10), BOT_STATE_FR.get(state, state))))
        rows.sort()
        head = 'BOTVIEW  %d bots  (%s)' % (len(bots_), ', '.join(
            '%s %d' % (BOT_STATE_FR.get(k, k), v) for k, v in sorted(counts.items())))
        return '\n'.join([head] + [r for _, r in rows[:BOTVIEW_LINES]])

    def _show_text(self, text):
        try:
            import cc
            from cocosui import ccui
            if self.label is None or not self.label.isCCObjValid():
                if text is None:
                    return
                scene = cc.Director.getInstance().getRunningScene()
                label = ccui.Text.create()
                label.setFontSize(20)
                label.setAnchorPoint(cc.Vec2(1, 1))
                size = cc.Director.getInstance().getWinSize()
                label.setPosition(cc.Vec2(size.width - 20, size.height * 0.80))
                label.setColor(cc.Color3B(255, 220, 60))
                label.enableOutline(cc.Color4B(0, 0, 0, 255), 2)
                scene.addChild(label, 100000)
                self.label = label
            self.label.setVisible(text is not None)
            if text is not None:
                self.label.setString(text)
        except Exception:
            import traceback
            offline_log('botview label failed', traceback.format_exc())


BOTVIEW = BotView()

# console commands run by the mod itself (everything else goes to the server)
LOCAL_COMMANDS = {'noclip': NOCLIP.toggle, 'botview': BOTVIEW.toggle}
LOCAL_ARG_COMMANDS = {}  # 'name': fn(arg)


# ---------------------------------------------------------------- developer console (key: ² or ~)
CONSOLE_TOGGLE_KEYS = (222, 192)  # VK_OEM_7 = "²" (French AZERTY), VK_OEM_3 = "~" / "`" (QWERTY)
VK_BACK, VK_RETURN, VK_ESCAPE, VK_SPACE = 8, 13, 27, 32
MSG_KEY_DOWN = 4


class OfflineConsole(object):
    def __init__(self):
        self.open = False
        self.text = ''
        self.last_resp = ''
        self.label = None

    def _ensure_label(self):
        if self.label is not None:
            try:
                if self.label.isCCObjValid():
                    return self.label
            except Exception:
                pass
        import cc
        from cocosui import ccui
        scene = cc.Director.getInstance().getRunningScene()
        label = ccui.Text.create()
        label.setFontSize(26)
        label.setAnchorPoint(cc.Vec2(0, 0))
        size = cc.Director.getInstance().getWinSize()
        label.setPosition(cc.Vec2(20, size.height * 0.45))
        label.setColor(cc.Color3B(120, 255, 120))
        label.enableOutline(cc.Color4B(0, 0, 0, 255), 2)
        scene.addChild(label, 100000)
        self.label = label
        return label

    def refresh(self):
        try:
            label = self._ensure_label()
            label.setVisible(self.open)
            lines = ['[Reborn Console]  (Enter = run, Esc/² = close)']
            if self.last_resp:
                lines.append(self.last_resp)
            lines.append('> ' + self.text + '_')
            label.setString('\n'.join(lines))
        except Exception:
            import traceback
            offline_log('console label failed', traceback.format_exc())

    def toggle(self):
        self.open = not self.open
        self.refresh()

    def submit(self):
        cmd = self.text.strip()
        self.text = ''
        name, _, arg = cmd.partition(' ')
        if name in LOCAL_ARG_COMMANDS:
            try:
                self.last_resp = LOCAL_ARG_COMMANDS[name](arg.strip())
            except Exception:
                import traceback
                offline_log('local command failed', cmd, traceback.format_exc())
                self.last_resp = 'error: ' + cmd
            cmd = ''
        if cmd in LOCAL_COMMANDS:
            try:
                self.last_resp = LOCAL_COMMANDS[cmd]()
            except Exception:
                import traceback
                offline_log('local command failed', cmd, traceback.format_exc())
                self.last_resp = 'error: ' + cmd
            cmd = ''
        if cmd:
            offline_log('console command', cmd)
            try:
                send_server_cmd(cmd)
                self.last_resp = '... ' + cmd
            except Exception:
                import traceback
                offline_log('console send failed', traceback.format_exc())
                self.last_resp = 'error: command not sent'
        self.refresh()

    def on_key(self, msg, key_code):
        """Return True when the key was consumed by the console."""
        if msg == MSG_KEY_DOWN and key_code in CONSOLE_TOGGLE_KEYS:
            self.toggle()
            return True
        if not self.open:
            return False
        if msg != MSG_KEY_DOWN:
            return True
        if key_code == VK_ESCAPE:
            self.toggle()
        elif key_code == VK_RETURN:
            self.submit()
        elif key_code == VK_BACK:
            self.text = self.text[:-1]
            self.refresh()
        elif key_code == VK_SPACE:
            self.text += ' '
            self.refresh()
        elif 65 <= key_code <= 90:
            self.text += chr(key_code + 32)
            self.refresh()
        elif 48 <= key_code <= 57 or 96 <= key_code <= 105:
            self.text += str((key_code - 48) % 48 if key_code < 96 else key_code - 96)
            self.refresh()
        return True

    def on_server_response(self, cmd):
        resp = cmd.get('resp', '')
        resp = resp if isinstance(resp, str) else str(resp)
        if resp.startswith('@ground '):
            _answer_ground_query(resp)
            return
        if resp.startswith('@layers '):
            _answer_layers_query(resp)
            return
        if resp.startswith('@botpos'):
            BOTVIEW.on_positions(resp)
            return
        self.last_resp = resp
        _on_bots_response(resp)
        self.refresh()


CONSOLE = OfflineConsole()


def _install_console():
    import game
    if getattr(game.on_key_msg, '_offline_console', False):
        return
    orig = game.on_key_msg

    def on_key_msg(msg, key_code):
        try:
            if CONSOLE.on_key(msg, key_code):
                return
        except Exception:
            import traceback
            offline_log('console key failed', traceback.format_exc())
        return orig(msg, key_code)
    on_key_msg._offline_console = True
    game.on_key_msg = on_key_msg
    import login.loginproc as loginproc
    loginproc.S_LOGIN_WIZ_CMD_RESP = CONSOLE.on_server_response
    offline_log('console installed')


AIRCRAFT_FOV = 80  # default camera is 50; the free fall resets it to 60 by itself


def _install_aircraft_fov():
    import entity.player_avatar as pa
    cls = pa.PlayerAvatar
    if getattr(cls.start_in_aircraft, '_offline_patched', False):
        return
    orig = cls.start_in_aircraft

    def start_in_aircraft(self, *args, **kwargs):
        ret = orig(self, *args, **kwargs)

        def widen():
            try:
                import g
                if getattr(g, 'is_in_aircraft', False):
                    g.gameworld_mgr.current_world.active_camera.set_fov(AIRCRAFT_FOV)
            except Exception:
                import traceback
                offline_log('aircraft fov failed', traceback.format_exc())
        try:
            import g
            g.callback(0.5, widen)
        except Exception:
            widen()
        return ret
    start_in_aircraft._offline_patched = True
    cls.start_in_aircraft = start_in_aircraft
    offline_log('aircraft fov installed')


def _install_no_start_tips():
    """S_FIGHT_WAIT_BEFORE_START opens the tutorial tips popup (guide_welcom): keep only the countdown."""
    import fight.fight_cmd_manager as fcm
    if getattr(fcm.S_FIGHT_WAIT_BEFORE_START, '_offline_patched', False):
        return
    import uiview.guide_welcom as gw
    orig_handler = fcm.S_FIGHT_WAIT_BEFORE_START
    orig_show = gw.show_guide_welcom
    state = {'muted': False}

    def show_guide_welcom(*args, **kwargs):
        if state['muted']:
            return None
        return orig_show(*args, **kwargs)

    def S_FIGHT_WAIT_BEFORE_START(cmd):
        state['muted'] = True
        try:
            return orig_handler(cmd)
        finally:
            state['muted'] = False
    S_FIGHT_WAIT_BEFORE_START._offline_patched = True
    gw.show_guide_welcom = show_guide_welcom
    fcm.S_FIGHT_WAIT_BEFORE_START = S_FIGHT_WAIT_BEFORE_START
    offline_log('start tips popup disabled')


def _force_battle_tcp():
    # The offline battle server only speaks TCP; skip the KCP (UDP) attempts.
    import network.net_rt as net_rt
    net_rt.default_trans_prot = 'tcp'
    offline_log('battle transport forced to tcp')


def _poll_debug_traces():
    try:
        if _install_debug_traces():
            try:
                _install_console()
            except Exception:
                import traceback
                offline_log('console install failed', traceback.format_exc())
            try:
                _install_no_start_tips()
            except Exception:
                import traceback
                offline_log('start tips patch failed', traceback.format_exc())
            try:
                _install_aircraft_fov()
            except Exception:
                import traceback
                offline_log('aircraft fov install failed', traceback.format_exc())
            try:
                _force_battle_tcp()
            except Exception:
                import traceback
                offline_log('force tcp failed', traceback.format_exc())
            try:
                _install_profile_patch()
            except Exception:
                import traceback
                offline_log('profile patch failed', traceback.format_exc())
            try:
                _install_bot_button()
            except Exception:
                import traceback
                offline_log('bot button install failed', traceback.format_exc())
            try:
                _install_noclip_controls()
            except Exception:
                import traceback
                offline_log('noclip controls install failed', traceback.format_exc())
            try:
                _install_destruct_hits()
            except Exception:
                import traceback
                offline_log('destruct hits install failed', traceback.format_exc())
            try:
                _install_no_telemetry()
            except Exception:
                import traceback
                offline_log('no telemetry install failed', traceback.format_exc())
            return
    except Exception as e:
        offline_log('debug traces failed', repr(e))
    try:
        import g
        g.callback(0.5, _poll_debug_traces)
    except Exception:
        pass


def _install_offline_hooks():
    import sys
    if not isinstance(sys.stderr, _LogStream):
        sys.stderr = _LogStream(sys.stderr, '[stderr]')
    try:
        import social
        _channel = OfflineSdkChannel()
        social.get_channel = lambda *a: _channel
        offline_log('offline hooks installed')
    except Exception as e:
        offline_log('hook install failed', repr(e))


_install_offline_hooks()
