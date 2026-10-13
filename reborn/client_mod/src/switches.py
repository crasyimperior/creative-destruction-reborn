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


# ---------------------------------------------------------------- developer console (key: ²)
CONSOLE_TOGGLE_KEYS = (222,)  # VK_OEM_7 = the "²" key on a French AZERTY keyboard
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
        if cmd:
            offline_log('console command', cmd)
            try:
                import __builtin__
                PT = getattr(__builtin__, 'PT', None)
                if PT is None:
                    import proto.proto_list as PT
                from network import net
                net.send_prot(PT.LOGIN_PTO.C_LOGIN_WIZ_CMD, {'cmd': cmd})
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
        self.last_resp = resp if isinstance(resp, str) else str(resp)
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
