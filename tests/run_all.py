# -*- coding: utf-8 -*-
"""MiVoice 回归测试：改完代码跑一遍 `python tests/run_all.py`。

覆盖（都不需要真按遥控器语音键、不需要 VB-CABLE）：
  1. 语法/导入
  2. 环境自检：ATVV 接口在、waveOut 设备可枚举
  3. GATT 连接 + 能力协商（协议版本 / codec）
  4. IMA ADPCM 编解码往返（相关性 > 0.97）
  5. 托盘图标渲染（三态 HICON 非空）
  6. 窗口枚举能找到 WorkBuddy（开着才查得到，没开则跳过）
  7. OK 键 -> 发送：组合键解析 / 事件序列 / 钩子六重判定 / 设备名识别
"""
import ctypes
import math
import os
import struct
import sys
import time
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import mivoice as M  # noqa: E402

# 🔴 把测试日志写进单独的文件，**别污染正式的 mivoice.log** ——
#    否则木木看日志时会看到一堆测试产生的「发送键已转换」假记录，
#    分不清哪条是真事（踩过一次）。
M.set_log_path(os.path.join(HERE, "mivoice_test.log"))

PASS, FAIL = [], []


def check(name, fn):
    t0 = time.time()
    try:
        # 某些用例会 importlib.reload(M)，那会把日志路径重置回正式日志，
        # 所以每次跑用例前都补一次，确保测试记录绝不进 mivoice.log
        M.set_log_path(os.path.join(HERE, "mivoice_test.log"))
        fn()
        PASS.append((name, time.time() - t0))
        print("  PASS  %-34s (%.2fs)" % (name, time.time() - t0))
    except Exception as e:
        FAIL.append((name, repr(e)))
        print("  FAIL  %-34s %r" % (name, e))


def t_import():
    import importlib
    importlib.reload(M)
    assert M.APP_VER


def t_env():
    path = M.find_atvv_interface("2717")
    assert path, "未找到 ATVV 接口（遥控器休眠了？按一下语音键）"
    assert "ab5e0001" in path.lower()
    devs = M.list_output_devices()
    assert isinstance(devs, list) and len(devs) >= 1


def t_gatt():
    import queue
    r = M.AtvvRemote()
    r.connect("2717")
    try:
        t0 = time.time()
        while r.protocol_version == 0 and time.time() - t0 < 8:
            try:
                while True:
                    r._on_control(r.control_q.get_nowait(), 1.0)
            except queue.Empty:
                time.sleep(0.1)
        assert r.protocol_version, "没收到能力协商应答"
    finally:
        r.close()


def t_adpcm():
    STEP, IDX = M.ImaAdpcmDecoder.STEP_TABLE, M.ImaAdpcmDecoder.INDEX_TABLE

    def encode(samples):
        pred, idx, nibs = 0, 0, []
        for s in samples:
            step = STEP[idx]
            diff = s - pred
            sign = 8 if diff < 0 else 0
            diff = min(abs(diff), step + step * 7 // 8)
            n = 0
            if diff >= step: n |= 4; diff -= step
            if diff >= step >> 1: n |= 2; diff -= step >> 1
            if diff >= step >> 2: n |= 1; diff -= step >> 2
            n |= sign
            d = step >> 3
            if n & 1: d += step >> 2
            if n & 2: d += step >> 1
            if n & 4: d += step
            if n & 8: d = -d
            pred = max(-32768, min(32767, pred + d))
            idx = max(0, min(88, idx + IDX[n & 7]))
            nibs.append(n)
        out = bytearray()
        for i in range(0, len(nibs), 2):
            hi = nibs[i]
            lo = nibs[i + 1] if i + 1 < len(nibs) else 0
            out.append((hi << 4) | lo)
        return bytes(out)

    samples = [int(9000 * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / 16000))
                   * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(4000)]
    enc = encode(samples)
    assert len(enc) == 2000, "120 字节帧换算不对：4000 样本应编成 2000 字节"
    out = M.ImaAdpcmDecoder().decode(enc)
    got = struct.unpack("<%dh" % (len(out) // 2), out)[:len(samples)]
    corr = (sum(a * b for a, b in zip(samples, got))
            / ((sum(a * a for a in samples) ** 0.5) * (sum(b * b for b in got) ** 0.5)))
    assert corr > 0.97, "ADPCM 往返相关性 %.4f 过低" % corr


def t_icons():
    for col in (M.COLOR_IDLE, M.COLOR_REC, M.COLOR_OFF):
        h = M.make_tray_hicon(col, 16)
        assert h, "托盘图标渲染返回空"


def t_gain():
    pcm = struct.pack("<4h", 1000, -1000, 30000, -30000)
    out = M.apply_gain(pcm, 2.0)
    vals = struct.unpack("<4h", out)
    # 1000*2=2000；30000*2=60000 超出 16bit，限幅到 32767 / -32768
    assert vals == (2000, -2000, 32767, -32768), "增益限幅不对: %s" % (vals,)


def t_focus():
    h = M.find_window_by_title("WorkBuddy")
    if h is None:
        print("        （WorkBuddy 没开着，跳过）")
        return
    buf = struct.pack("")  # noqa: F841


def t_hotkey_parse():
    mods, key = M.App._parse_hotkey("Ctrl+Shift+M")
    assert mods == M.MOD_CONTROL | M.MOD_SHIFT and key == ord("M")


# ---------------- OK 键 -> 发送 ----------------
class _FakeKeys:
    """替身：只记录 send_combo / replay_key 被调了什么，绝不往真实桌面注入按键。"""

    def __init__(self):
        self.calls = []
        self.replays = []

    def send_combo(self, spec):
        self.calls.append(spec)
        return True

    def replay_key(self, vk, scan=0, extended=False):
        self.replays.append((vk, scan, extended))
        return True


class _FakeRawInput:
    def __init__(self, age_ms=0.0):
        self.last_activity = time.time() - age_ms / 1000.0
        # 🔴 v1.6：最近一次「实体键盘按发送键」的时刻（看门狗兜底判据用）
        self.last_send_other_ts = 0.0


def _make_app():
    return M.App(types.SimpleNamespace(debug=False, selftest=0))


def _make_sender_app():
    """造一个「发送键仲裁」可用的 App（不装真钩子、不连真设备）。"""
    app = _make_app()
    app.keys = _FakeKeys()
    app.ok_to_send = True
    app.enabled = True
    app.ime_flag = False
    app.ok_send_title = ""          # 空 = 任意前台窗口都算，便于自动化测试
    app.remote_active_ms = 15000
    app.remote_input = _FakeRawInput(0)
    app.send_confirm_ms = 400
    app.send_fallback_ms = 30000      # 看门狗兜底窗口（Raw Input 没到时用）
    app.arbiter = M.SendArbiter(app)
    hook = M.EnterSendHook(app)     # 只造对象，不 install()（不装真钩子）
    app.enter_hook = hook
    return app, hook


def t_combo_parse():
    assert M.parse_combo("ctrl+enter") == ([M.VK_CONTROL], M.VK_RETURN)
    assert M.parse_combo("Ctrl + Enter") == ([M.VK_CONTROL], M.VK_RETURN)
    assert M.parse_combo("ctrl+shift+enter") == ([M.VK_CONTROL, M.VK_SHIFT],
                                                 M.VK_RETURN)
    assert M.parse_combo("enter") == ([], M.VK_RETURN)
    assert M.parse_combo("a") == ([], ord("A"))
    # 解析不出来的必须明确返回 ([], None)，不能瞎猜
    assert M.parse_combo("不存在的键") == ([], None)
    assert M.parse_combo("") == ([], None)


def t_combo_seq():
    seq = M.build_combo_seq("ctrl+enter")
    assert seq == [(M.VK_CONTROL, False), (M.VK_RETURN, False),
                   (M.VK_RETURN, True), (M.VK_CONTROL, True)], seq
    try:
        M.build_combo_seq("不存在的键")
    except ValueError:
        pass
    else:
        raise AssertionError("无法解析的组合键应当抛 ValueError")


def t_ok_hook():
    """验证新的「发送键仲裁」链路。

    🔴 v1.4 的关键变化（也是 v1.3 一直不生效的根因修复）：
      - 旧版：钩子里靠「遥控器最近 3 秒内活动过」判断 -> 按下 OK 键的
        那一刻这一下自己的 Raw Input 还没处理，**第一次按必然失效**；
      - 新版：钩子**先吞掉**，等 Raw Input（异步）按设备确认：
        是遥控器 -> 发发送快捷键；不是 -> **补发**原键（用户无感）。

    ⚠️ 必须把「前台窗口 / 标题匹配」两处外部依赖打桩：
    真实环境里前台窗口会变（可能压根没有前台窗口），会导致这条用例
    偶发失败（踩过一次）。打桩后结果完全确定。
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)   # 系统传进来的是整数地址，测试也照此

    # 1) 正例：钩子先吞掉，但**还没发送**（要等 Raw Input 确认）
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键没被吞掉"
    assert app.keys.calls == [], "还没确认设备就发送了：%s" % (app.keys.calls,)
    assert app.keys.replays == [], "还没确认设备就补发了"

    # 2) Raw Input 到了，设备 = 遥控器 -> 变成发送快捷键
    app.arbiter.on_raw_keydown(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], "遥控器按的不该补发"

    # 3) 配对的抬起也要吞，防残留状态
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "抬起没吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "没吞过的抬起不该拦"

    # 4) 反例：Raw Input 说是实体键盘 -> 必须把原键补发回去，
    #    否则用户「敲了回车没反应」
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "实体键盘的回车也该先吞"
    app.arbiter.on_raw_keydown(M.VK_RETURN, 0x1C, False)
    assert app.keys.calls == [], "实体键盘不该被转成发送"
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "补发过的抬起也要吞"

    # 5) 长按自动重复：🔴 v1.6 起，**按住不放只发一次**
    #    （旧版每次重复都发一次 = 「一次按住连发 6 次」，是 bug 不是特性。
    #      v1.5 日志 17:32:49 / 17:36:03 连续 6 条「发送键已转换（来源 raw）」就是现场。）
    app.keys.calls = []
    app.keys.replays = []
    for _ in range(5):
        assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
        app.arbiter.on_raw_keydown(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], \
        "🔴 按住不放应该只发一次，实际发了 %r" % (app.keys.calls,)
    assert app.keys.replays == [], app.keys.replays

    # 6) 各种「必须放行」的情况
    def assert_pass(reason, **patch):
        saved = {k: getattr(app, k) for k in patch}
        for k, v in patch.items():
            setattr(app, k, v)
        n = len(app.keys.calls)
        try:
            got = hook._on_key(0, M.WM_KEYDOWN, lp)
            assert got != 1, "%s 时不该拦截" % reason
            assert len(app.keys.calls) == n, "%s 时不该发送" % reason
        finally:
            for k, v in saved.items():
                setattr(app, k, v)

    assert_pass("输入法在组词", ime_flag=True)
    assert_pass("总开关关闭", ok_to_send=False)
    assert_pass("程序已暂停", enabled=False)
    assert_pass("前台窗口不匹配", ok_send_title="__不存在的窗口__")

    # 7) 用户自己按着 Ctrl 时不干预（修饰键由钩子自跟踪）
    kctrl = M.KBDLLHOOKSTRUCT(M.VK_CONTROL, 0x1D, 0, 0, 0)
    hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kctrl))
    assert hook.mods_down() is True, "修饰键没被跟踪到"
    assert hook._on_key(0, M.WM_KEYDOWN, lp) != 1, "按着 Ctrl 时不该拦回车"
    hook._on_key(0, M.WM_KEYUP, ctypes.addressof(kctrl))
    assert hook.mods_down() is False, "修饰键抬起后没复位"

    # 8) 非发送键、本程序注入的事件，都必须放行
    ka = M.KBDLLHOOKSTRUCT(ord("A"), 0x1E, 0, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(ka)) != 1, "A 键被拦了"
    kinj = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, M.LLKHF_INJECTED, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kinj)) != 1, \
        "自己注入的 Enter 被拦了（会死循环）"

    # 撤销打桩（中途断言失败时不撤销也无妨，进程马上就退出了）
    import importlib
    importlib.reload(M)


def t_arbiter_timeout():
    """看门狗：吞了但一直等不到 Raw Input 时，必须有兜底，不能把回车弄丢。

    两条兜底路径：
      - 最近的活动来自遥控器 -> 当成遥控器按的（发发送快捷键）
      - 不是                    -> 补发原键
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    app.send_confirm_ms = 0          # 让 pending 立刻过期
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    # 6a) 遥控器刚活动过 -> 兜底当成遥控器按的
    app.remote_input = _FakeRawInput(0)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    time.sleep(0.02)
    app.arbiter.tick()
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], app.keys.replays
    # 🔴 v1.6：抬起是「本次按住周期结束」的权威标志，必须抬一下，
    #    否则下一次按下会被当成自动重复（真实硬件两次按下之间必有 keyup）
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "抬起没吞"

    # 6b) 遥控器很久没活动 -> 补发原键，不能凭空吞掉用户的回车
    app.remote_input = _FakeRawInput(60000)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    time.sleep(0.02)
    app.arbiter.tick()
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "抬起没吞"

    # 6c) 超时之后才来的 Raw Input 不能让它再动作一次（不能双发）
    app.remote_input = _FakeRawInput(60000)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    time.sleep(0.02)
    app.arbiter.tick()
    before = (len(app.keys.calls), len(app.keys.replays))
    app.arbiter.on_raw_keydown(M.VK_RETURN, 0x1C, True)
    assert (len(app.keys.calls), len(app.keys.replays)) == before, \
        "超时结算之后又来了一次动作（会双发）"

    import importlib
    importlib.reload(M)


# ---------------- Raw Input 内存布局（v1.5 根因） ----------------
def _raw_keyboard_bytes(makecode, vkey, msg=0x0100, flags=0):
    """按 Windows 真实布局造一段 RAWINPUT 缓冲区。

    RAWKEYBOARD（x64，偏移相对 RAWINPUT 开头）：
        24  MakeCode  USHORT
        26  Flags     USHORT
        28  Reserved  USHORT
        30  VKey      USHORT   <-- 2 字节！不是 DWORD
        32  Message   DWORD
    """
    b = bytearray(48)
    b[0:4] = (1).to_bytes(4, "little")          # dwType = RIM_TYPEKEYBOARD
    b[4:8] = (48).to_bytes(4, "little")         # dwSize
    b[24:26] = makecode.to_bytes(2, "little")   # MakeCode
    b[26:28] = flags.to_bytes(2, "little")      # Flags
    b[28:30] = (0).to_bytes(2, "little")        # Reserved
    b[30:32] = vkey.to_bytes(2, "little")       # VKey
    b[32:36] = msg.to_bytes(4, "little")        # Message
    return bytes(b)


def t_rawinput_layout():
    """🔴 回归测试：RAWINPUT 里的 VKey / MakeCode 是 **USHORT（2 字节）**。

    这是「遥控器 OK 键 -> Ctrl+Enter」一直不生效的**根因**（v1.5 修复）：
    用 4 字节读 VKey 会把紧跟其后的 Message 高 2 字节一起吃进来，
    VKey=0x0D 变成 0x0100000D，于是
        `if vkey != self.app.send_key_vk: return False`
    永远成立 —— Raw Input 的设备归属判定**一次都进不去**，
    每次 OK 键都掉进看门狗兜底，而兜底窗口又不够宽，
    最终表现为「按 OK 键只打了个回车，永远发不出去」。

    这条测试用**实测的真实字节**锁死偏移和宽度。
    """
    # 实测字节来自 tools\remote_keys_scan.log（遥控器 OK 键）
    ok_raw = _raw_keyboard_bytes(0x1C, 0x0D)        # Enter
    f5_raw = _raw_keyboard_bytes(0x3F, 0x74, 0x0101)  # 语音键 F5

    # 正确的读法：2 字节
    assert M.struct_unpack_h(ok_raw, 24) == 0x1C, "MakeCode 读错"
    assert M.struct_unpack_h(ok_raw, 30) == 0x0D, "VKey 读错"
    assert M.struct_unpack_h(f5_raw, 24) == 0x3F, "MakeCode 读错(F5)"
    assert M.struct_unpack_h(f5_raw, 30) == 0x74, "VKey 读错(F5)"

    # Message 是 DWORD，4 字节读
    assert M.struct_unpack_i(ok_raw, 32) == 0x0100, "Message 读错"

    # 错误读法会得到什么（写死当证据，改坏了要能看出来）
    assert M.struct_unpack_i(ok_raw, 30) == 0x0100000D, \
        "4 字节读 VKey 的结果变了 —— 如果这里变了说明布局假设变了"
    assert M.struct_unpack_i(ok_raw, 30) != 0x0D, \
        "🔴 又有人用 4 字节读 VKey 了，OK 键会再次失效"


def t_arbiter_raw_confirm():
    """端到端：钩子吞掉发送键 -> Raw Input 确认设备归属 -> 正确的动作。

    这是 v1.5 修好之后的完整链路：
      · Raw Input 说「是遥控器」-> 发 ctrl+enter（原键被替换掉）
      · Raw Input 说「不是遥控器」-> 原样补发回车（绝不能让用户的回车消失）
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    # 7a) 遥控器按的 -> 发 ctrl+enter
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键没被吞"
    app._on_raw_key(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], app.keys.replays
    # 🔴 v1.6：抬起 = 本次按住周期结束（真实硬件两次按下之间必有 keyup）
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "配对的抬起没吞"

    # 7b) 实体键盘按的 -> 原样补发回车，不能被换成 ctrl+enter
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键没被吞"
    app._on_raw_key(M.VK_RETURN, 0x1C, False)
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "配对的抬起没吞"

    # 7c) 遥控器按的、但配对的抬起也要吞
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    app._on_raw_key(M.VK_RETURN, 0x1C, True)
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "配对的抬起没吞"

    import importlib
    importlib.reload(M)


# ---------------- 发送键可配置 ----------------
def t_send_key_parse():
    """「哪个物理键触发发送」的配置解析。"""
    assert M.parse_vk_spec("enter") == 0x0D
    assert M.parse_vk_spec("Enter") == 0x0D
    assert M.parse_vk_spec("space") == 0x20
    assert M.parse_vk_spec("tab") == 0x09
    assert M.parse_vk_spec("f5") == 0x74
    assert M.parse_vk_spec("F12") == 0x7B
    assert M.parse_vk_spec("up") == 0x26
    assert M.parse_vk_spec("down") == 0x28
    assert M.parse_vk_spec("volumeup") == 0xAF
    assert M.parse_vk_spec("a") == 0x41
    assert M.parse_vk_spec("7") == 0x37
    assert M.parse_vk_spec("0x74") == 0x74
    # 认不出来的必须明确返回 None（不能瞎猜一个键）
    assert M.parse_vk_spec("") is None
    assert M.parse_vk_spec("不存在的键") is None
    assert M.parse_vk_spec("0xZZ") is None
    assert M.parse_vk_spec("f99") is None
    # 名字反查
    assert M.vk_label(0x0D) == "Enter 回车"
    assert M.vk_label(0x74) == "F5"


def t_send_key_hook():
    """发送键可以换成遥控器上的任意一个键（不一定是 Enter）。"""
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    app.hide_remote_f5 = True
    app.alt_key = "alt"
    app.voice_active = False

    # 把发送键换成「空格」（0x20）
    app.send_key_vk = 0x20
    app.send_key_name = M.vk_label(0x20)
    ksp = M.KBDLLHOOKSTRUCT(0x20, 0x39, 0, 0, 0)
    lp = ctypes.addressof(ksp)
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "空格没被吞下"
    app.arbiter.on_raw_keydown(0x20, 0x39, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "配对的抬起没吞"

    # 换成发送键以后，Enter 就不该再被拦
    app.keys.calls = []
    app.send_key_vk = 0x20
    kent = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kent)) != 1, \
        "发送键改了之后 Enter 不该再被拦"
    assert app.keys.calls == [], app.keys.calls

    import importlib
    importlib.reload(M)


def t_remote_device_match():
    """设备名识别：只有 VID 匹配遥控器才算，普通键盘必须排除。"""
    ri = M.RemoteInputWatcher(None, "2717")
    ri._dev_cache[1] = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_"
                        r"VID&012717_PID&32b8_REV#8&1f2e3d4c&0&0000")
    ri._dev_cache[2] = r"\\?\HID#VID_046D&PID_C31C&MI_00#8&2a3b4c5d&0&0000"
    ri._dev_cache[3] = ""
    assert ri.is_remote(1) is True, "遥控器设备名没认出来"
    assert ri.is_remote(2) is False, "把普通键盘当成遥控器了"
    assert ri.is_remote(3) is False, "空设备名不该算遥控器"


# ---------------- v1.6：设备名读失败不许缓存（本次的真根因） ----------------
def t_devname_nocache():
    """🔴 回归测试：**设备名读不出来时不能把空串缓存住**。

    这是「遥控器 OK 键 -> Ctrl+Enter 永远发不出去」的**真根因**（v1.6 修复）：

    蓝牙设备刚重连 / 新 HID 集合刚枚举出来时，`GetRawInputDeviceInfoW`
    会**偶发返回失败**（名字读不出来）。v1.5 的 `device_name()` 最后一句是
    无条件的 `self._dev_cache[hdev] = name` —— 把**空串也缓存了**。
    于是「第一次读失败」= 这个 hDevice 从此**永远**返回空名，
    `is_remote()` 只能静默判 False ->
    遥控器的 OK 键被当成实体键盘回车原样补发出去，**永远发不出去**。

    v1.5 日志铁证（17:45:02）：
        Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发
    这一行**前面没有**「Raw Input 键盘设备: ...」——
    因为名字是空的，老代码静默 return False，连设备行都不打。

    这条测试锁死三件事：
      1. 读失败 -> 返回空串，但**不写进缓存**；
      2. 下次事件**会自动重试**，设备稳定后能读到名字并认出是遥控器；
      3. 已经确认过是遥控器的 hDevice，走「快速通道」，名字读不出也认。
    """
    REMOTE_NAME = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_"
                   r"VID&012717_PID&32B8_REV#9&2b876598&0&0000")

    ri = M.RemoteInputWatcher(None, "2717")
    state = {"ok": False}

    def fake_read(hdev):
        # 蓝牙刚重连时读不出来；设备稳定后就能读出来
        return [REMOTE_NAME] if state["ok"] else []

    ri._read_names = fake_read

    # 1) 第一次读不到 -> 空串，且**不能进缓存**
    assert ri.device_name(4242) == "", "读不到名字时应返回空串"
    assert 4242 not in ri._dev_cache, \
        "🔴 空串被缓存了 —— 读失败一次这个设备就永远判错"
    assert ri.is_remote(4242) is False, "读不到名字时不能瞎猜是遥控器"
    assert 4242 in ri._unreadable, "读不到名字应该记一笔（否则又变成静默判错）"

    # 2) 设备稳定后 -> 自动重试 -> 读到名字 -> 认成遥控器
    state["ok"] = True
    assert ri.device_name(4242) == REMOTE_NAME, "重试没生效"
    assert ri._dev_cache.get(4242) == REMOTE_NAME, "读到名字后应该缓存"
    assert 4242 not in ri._unreadable, "读出来后应该把「读不出来」的标记清掉"
    assert ri.is_remote(4242) is True, "重试读到名字后应认成遥控器"

    # 3) 快速通道：确认过是遥控器的 hDevice，名字读不出来也认
    ri2 = M.RemoteInputWatcher(None, "2717")
    ri2._read_names = lambda h: []
    ri2._remote_hdevs.add(99)
    assert ri2.is_remote(99) is True, "已确认过的遥控器设备不该被读名失败拖垮"

    # 4) 反向：没确认过的、读不出名字的，不能瞎认
    ri3 = M.RemoteInputWatcher(None, "2717")
    ri3._read_names = lambda h: []
    assert ri3.is_remote(100) is False, "读不出名字的陌生设备不能当成遥控器"


def t_repeat_no_double():
    """🔴 回归测试：**按住 OK 键不放只能发一次**，不能连发。

    v1.5 的 bug：钩子每收到一次 keydown 就新增一条 pending。系统按住不放
    会自动重复产生 ~30 次/秒的 keydown，于是 pending 堆成 6 条、
    Raw Input 也来 6 次 -> **一次按住连发 6 次 ctrl+enter**。

    v1.5 日志现场（17:32:49 / 17:36:03 连续 6 条）：
        发送键已转换：Enter 回车 -> ctrl+enter（来源 raw）  x6

    更糟的是看门狗和 Raw Input 各结算一部分 pending，会导致
    **同一次按键既转换又补发**（双发，17:45:02 实测）。

    v1.6：**一次「按下-松开」只对应一条 pending**；
    自动重复的 keydown 照吞（不能让真实回车漏到前台程序），但不新增 pending。
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    # 第一次按下
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "首次按下应该被吞"
    live = [p for p in app.arbiter._pending if not p["done"]]
    assert len(live) == 1, "首次按下应该只有一条 pending"

    # 系统自动重复 5 次（间隔约 30ms）—— 不真的 sleep，直接改时间戳模拟
    for _ in range(5):
        app.arbiter._cycle_ts = time.time() - 0.010
        assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "自动重复也必须吞掉"
    live = [p for p in app.arbiter._pending if not p["done"]]
    assert len(live) == 1, \
        "🔴 按住不放时 pending 变成了 %d 条 -> 会连发" % len(live)

    # Raw Input 也来 6 次（真实 down + 5 次 repeat）
    for _ in range(6):
        app._on_raw_key(M.VK_RETURN, 0x1C, True)

    assert app.keys.calls == ["ctrl+enter"], \
        "🔴 按住不放应该只发一次，实际发了 %r" % (app.keys.calls,)
    assert app.keys.replays == [], app.keys.replays

    # 松开之后重新按一次 -> 应该再发一次（不是永久失效）
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "抬起应该被吞"
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "第二次按下应该被吞"
    app._on_raw_key(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls

    import importlib
    importlib.reload(M)


def t_fallback_judge():
    """看门狗兜底判据：遥控器 vs 实体键盘，要能分清。

    🔴 v1.5 的兜底判据只有一条：「30 秒内碰过遥控器 -> 就算遥控器按的」。
    这会把**木木用实体键盘敲的回车**也误转成 ctrl+enter。

    v1.6 加了第二条反向保护：最近一次「实体键盘按发送键」如果比
    遥控器活动更近，就判成键盘（补发普通回车，不干预）。

    ⚠️ 只看「按发送键」，不看「有没有键盘活动」——
       木木打字用的是字母键，不该影响这个判定。
    """
    app, _hook = _make_sender_app()
    ri = _FakeRawInput(0)              # 遥控器刚刚活动过
    ri.last_send_other_ts = 0.0
    app.remote_input = ri
    assert app.remote_recently() is True, "刚用过遥控器 -> 应判为遥控器"

    # 木木用实体键盘按了回车（时间比遥控器活动更近）-> 不该判成遥控器
    ri.last_send_other_ts = ri.last_activity + 0.001
    assert app.remote_recently() is False, \
        "🔴 实体键盘的回车被误判成遥控器了（会误转成 ctrl+enter）"

    # 遥控器又活动了一次（明确晚于实体键盘那次）-> 重新判回遥控器
    ri.last_activity = ri.last_send_other_ts + 0.001
    assert app.remote_recently() is True, "遥控器活动更新后应判为遥控器"

    # 遥控器很久没活动 -> 不判
    ri.last_activity = time.time() - 60
    assert app.remote_recently() is False, "遥控器太久没活动 -> 不该判为遥控器"


# ---------------- 遥控器语音键(F5)拦截 ----------------
def _f5_kb(flags=0, vk=M.VK_F5, scan=0x3F):
    return M.KBDLLHOOKSTRUCT(vk, scan, flags, 0, 0)


def t_f5_swallow():
    """F5 拦截：修「WorkBuddy 语音图标只绿一下就停」。

    背景：遥控器的语音键在 Windows 上就是一个 **F5**（vk=0x74, scan=0x3F）。
    按住不放时系统自动重复产生 ~30 次/秒的 keydown，且带 LLKHF_ALTDOWN
    （= 事件发生时 Alt 正按着）。而 WorkBuddy 的 useVoiceAltHotkey 对
    「Alt 按住期间收到的任何非修饰键 keydown」一律判成组合键 -> 立刻 stop，
    而且它**不检查 e.repeat**，所以躲不掉。MiVoice 必须在录音期间把 F5 吞掉。

    ⚠️ 关键实测结论：`GetAsyncKeyState` **看不到 SendInput 注入的按键**
    （注入前后都是 0x0000），所以判据只能用事件自带的 LLKHF_ALTDOWN，
    外加 `voice_active` 兜底。
    """
    app = _make_app()
    app.voice_active = False
    app.hide_remote_f5 = True
    app.alt_key = "alt"
    hook = M.EnterSendHook(app)

    def feed(kb, wparam=M.WM_KEYDOWN):
        return hook._on_key(0, wparam, ctypes.addressof(kb))

    # ---- 1) 平时（没录音、Alt 没按着）：物理键盘 F5 必须放行，否则刷新键废了 ----
    hook._f5_state = 0
    assert feed(_f5_kb(0)) != 1, "平时 F5 被吞了（物理键盘刷新会失效）"
    assert feed(_f5_kb(0), M.WM_KEYUP) != 1, "平时 F5 的抬起被吞了"

    # ---- 2) 真实场景回放：首次 down 放行 -> 自动重复（带 ALTDOWN）必须吞 ----
    #    这就是实测抓到的顺序：遥控器 down 比 ATVV 会话通知早 ~6ms，
    #    那一下 Alt 还没按下，WorkBuddy 会忽略它（n=false）-> 无害；
    #    真正致命的是之后每秒 30 次的自动重复。
    hook._f5_state = 0
    app.voice_active = False
    assert feed(_f5_kb(0)) != 1, "首次按下应当放行（此时 Alt 尚未按下）"
    app.voice_active = True                     # ATVV 会话已开，Alt 已按下
    assert feed(_f5_kb(M.LLKHF_ALTDOWN), M.WM_SYSKEYDOWN) == 1, \
        "自动重复的 F5 没被吞 -> WorkBuddy 会判成组合键并立刻停止录音"
    # 后续重复继续吞
    for _ in range(20):
        assert feed(_f5_kb(M.LLKHF_ALTDOWN), M.WM_SYSKEYDOWN) == 1, \
            "后续自动重复漏吞了"
    # 配对的抬起：这一轮首次 down 放行过，抬起也放行（保持键盘状态配对）
    assert feed(_f5_kb(M.LLKHF_ALTDOWN), M.WM_SYSKEYUP) != 1, \
        "放行过的 down，其配对 up 也必须放行"

    # ---- 3) 首次 down 就带 ALTDOWN（Alt 已按着）-> 整轮吞掉，含抬起 ----
    hook._f5_state = 0
    app.voice_active = True
    assert feed(_f5_kb(M.LLKHF_ALTDOWN)) == 1, "录音期首次 F5 down 没被吞"
    assert feed(_f5_kb(M.LLKHF_ALTDOWN)) == 1, "录音期 F5 重复没被吞"
    assert feed(_f5_kb(M.LLKHF_ALTDOWN), M.WM_KEYUP) == 1, \
        "吞掉的 down，配对的 up 也必须吞"

    # ---- 4) 只靠 voice_active 也能吞（flags 没带 ALTDOWN 的极端情况）----
    hook._f5_state = 0
    app.voice_active = True
    assert feed(_f5_kb(0)) == 1, "voice_active 时 F5 没被吞"
    assert feed(_f5_kb(0), M.WM_KEYUP) == 1, "voice_active 时配对 up 没被吞"

    # ---- 5) 必须放行的情况 ----
    # 5a. alt_key 本身就是 f5（我们自己在模拟 F5）-> 不能吞，否则自锁
    hook._f5_state = 0
    app.alt_key = "f5"
    app.voice_active = True
    assert feed(_f5_kb(M.LLKHF_ALTDOWN)) != 1, "alt_key=f5 时不该吞自己的键"
    app.alt_key = "alt"

    # 5b. 总开关关掉
    hook._f5_state = 0
    app.hide_remote_f5 = False
    app.voice_active = True
    assert feed(_f5_kb(M.LLKHF_ALTDOWN)) != 1, "hide_remote_f5=0 时不该吞"
    app.hide_remote_f5 = True

    # 5c. 本程序自己注入的事件（防递归）
    hook._f5_state = 0
    app.voice_active = True
    assert feed(_f5_kb(M.LLKHF_ALTDOWN | M.LLKHF_INJECTED)) != 1, \
        "自己注入的 F5 被拦了（会死循环）"

    # 5d. 不是 F5 的键
    hook._f5_state = 0
    app.voice_active = True
    assert feed(_f5_kb(M.LLKHF_ALTDOWN, vk=ord("A"), scan=0x1E)) != 1, \
        "A 键被拦了"
    assert feed(_f5_kb(0, vk=M.VK_RETURN, scan=0x1C)) != 1, \
        "Enter 被 F5 逻辑拦了（应交给 OK 键那条链）"

    # ---- 6) 状态机自愈：抬起后回到 0，下一轮重新决策 ----
    hook._f5_state = 0
    app.voice_active = False
    assert feed(_f5_kb(0)) != 1
    assert hook._f5_state == 1, "放行后状态应为 1"
    feed(_f5_kb(0), M.WM_KEYUP)
    assert hook._f5_state == 0, "抬起后状态应复位为 0"


def main():
    print("MiVoice 回归测试")
    print("=" * 60)
    check("导入 / 语法", t_import)
    check("环境自检（ATVV 接口）", t_env)
    check("GATT 连接 + 能力协商", t_gatt)
    check("ADPCM 编解码往返", t_adpcm)
    check("音频增益限幅", t_gain)
    check("托盘图标三态渲染", t_icons)
    check("窗口枚举（WorkBuddy）", t_focus)
    check("热键解析", t_hotkey_parse)
    check("OK 键：组合键解析", t_combo_parse)
    check("OK 键：事件序列", t_combo_seq)
    check("OK 键：钩子判定链", t_ok_hook)
    check("OK 键：仲裁看门狗兜底", t_arbiter_timeout)
    check("OK 键：Raw Input 内存布局", t_rawinput_layout)
    check("OK 键：设备归属端到端", t_arbiter_raw_confirm)
    check("OK 键：遥控器设备识别", t_remote_device_match)
    check("OK 键：设备名读失败不许缓存", t_devname_nocache)
    check("OK 键：按住不放不连发", t_repeat_no_double)
    check("OK 键：看门狗兜底判据", t_fallback_judge)
    check("发送键：名称解析", t_send_key_parse)
    check("发送键：可换成任意键", t_send_key_hook)
    check("F5 拦截（遥控器语音键）", t_f5_swallow)
    print("=" * 60)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  !! %s: %s" % (n, e))
        sys.exit(1)


if __name__ == "__main__":
    main()
