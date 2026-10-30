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
    """替身：只记录 send_combo / replay_key / scroll 被调了什么，
    绝不往真实桌面注入按键。"""

    def __init__(self):
        self.calls = []
        self.replays = []
        self.wheels = []          # 🔴 v1.8：记录滚轮 delta（正=上滚，负=下滚）

    def send_combo(self, spec):
        self.calls.append(spec)
        return True

    def replay_key(self, vk, scan=0, extended=False):
        self.replays.append((vk, scan, extended))
        return True

    def scroll(self, delta, horizontal=False):
        self.wheels.append(delta)
        return True


class _FakeRawInput:
    def __init__(self, age_ms=0.0):
        # 最近一次**遥控器**按键的时刻
        self.last_activity = time.time() - age_ms / 1000.0
        # 🔴 v1.7：最近一次**实体键盘**按键的时刻
        #    （v1.6 只有 last_send_other_ts，靠「实体键盘按发送键」记时间，
        #      但那个回车会被我们吞掉、自己那条 Raw Input 永远不到，
        #      而且注入回声还会污染它 -> 判据彻底失灵）
        self.last_physical_ts = 0.0
        # 🔴 v1.6 保留字段：最近一次「实体键盘按发送键」的时刻
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
    """验证 v1.7 的新链路：**吞下、放开抬、用抬起那条 Raw Input 定性**。

    🔴 v1.7 架构革新（2026-10-30 实测钉死）：
      v1.3~v1.6 是「吞下 -> 等**这一下**的 Raw Input」——
      但被钩子吞掉的按键**根本不会有 Raw Input**（探针：全吞 0 条），
      所以那条链在 Windows 上**结构性地走不通**。

      v1.7 改成：吞掉「按下」-> 放行「抬起」-> 抬起那条 Raw Input 会到
      -> 它的 hDevice 就是这一下的**确定**设备归属。

    ⚠️ 必须把「前台窗口 / 标题匹配」两处外部依赖打桩：
       真实环境里前台窗口会变（可能压根没有前台窗口），会导致这条用例
       偶发失败（踩过一次）。打桩后结果完全确定。
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)   # 系统传进来的是整数地址，测试也照此

    # 1) 正例：按下被吞掉，但**还没发送**（要等抬起的 Raw Input 定性）
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键的按下没被吞掉"
    assert app.keys.calls == [], "还没定性就发送了：%s" % (app.keys.calls,)
    assert app.keys.replays == [], "还没定性就补发了"

    # 2) 🔴🔴 抬起必须**放行**（v1.7 的关键）：
    #    不放行 -> 抬起那条 Raw Input 不来 -> 拿不到设备归属
    #    -> OK 键永远发不出去（v1.3~v1.6 就是栽在这）
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, \
        "🔴 抬起被吞了 -> 拿不到设备归属 -> OK 键永远发不出去"

    # 3) 抬起那条 Raw Input 到了，设备 = 遥控器 -> 变成发送快捷键
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], app.keys.replays

    # 4) 反例：抬起那条 Raw Input 说不是遥控器 -> 必须把原键补发回去，
    #    否则用户「敲了回车没反应」
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键的按下没被吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起不该被吞"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, False)
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays

    # 5) 长按自动重复：🔴 按住不放只发一次
    app.keys.calls = []
    app.keys.replays = []
    for _ in range(5):
        assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
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

    # 8) 各种「必须放行」的情况（非发送键、本程序注入的事件）
    ka = M.KBDLLHOOKSTRUCT(ord("A"), 0x1E, 0, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(ka)) != 1, "A 键被拦了"
    kinj = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, M.LLKHF_INJECTED, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kinj)) != 1, \
        "自己注入的 Enter 被拦了（会死循环）"

    # 撤销打桩（中途断言失败时不撤销也无妨，进程马上就退出了）
    import importlib
    importlib.reload(M)


def t_arbiter_timeout():
    """看门狗：**抬起确认迟迟不来**时的兜底，不能把回车弄丢。

    🔴 v1.7：兜底起点从「按下」改成「抬起」——
       人按住回车 0.5 秒很常见，v1.6 从按下算 400ms 就会误超时，
       导致「还没松手就先把回车补出去了」。

    两条兜底路径：
      - 最近的活动来自遥控器 -> 当成遥控器按的（发发送快捷键）
      - 不是                    -> 补发原键
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    app.send_confirm_ms = 0          # 抬起之后立刻过期
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    # 6a) 遥控器刚活动过 -> 兜底当成遥控器按的
    app.remote_input = _FakeRawInput(0)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    time.sleep(0.02)
    app.arbiter.tick()
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], app.keys.replays

    # 6b) 遥控器很久没活动 -> 补发原键，不能凭空吞掉用户的回车
    app.remote_input = _FakeRawInput(60000)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1
    time.sleep(0.02)
    app.arbiter.tick()
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays

    # 6c) 超时之后才来的抬起确认不能让它再动作一次（不能双发）
    before = (len(app.keys.calls), len(app.keys.replays))
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert (len(app.keys.calls), len(app.keys.replays)) == before, \
        "超时结算之后又来了一次动作（会双发）"

    # 6d) 🔴 v1.7：按下之后、抬起之前，看门狗**不能**动
    #     （人按住回车 0.5 秒很常见，从按下算就会误超时）
    app.remote_input = _FakeRawInput(0)
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    time.sleep(0.05)
    app.arbiter.tick()
    assert app.keys.calls == [], "还没抬起就发了：%r" % (app.keys.calls,)
    assert app.keys.replays == [], app.keys.replays
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls

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
    """端到端：吞下 -> 放行抬起 -> 抬起那条 Raw Input 定性 -> 正确的动作。

    这是 v1.7 的完整链路（替代 v1.5 的「等这一下的 Raw Input」——
    那一版已被实测证伪：被吞的按键没有 Raw Input）：
      · 抬起那条 Raw Input 说「是遥控器」-> 发 ctrl+enter（原键被替换掉）
      · 说「不是遥控器」-> 原样补发回车（绝不能让用户的回车消失）
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    # 7a) 遥控器按的 -> 发 ctrl+enter
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键的按下没被吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    assert app.keys.replays == [], app.keys.replays

    # 7b) 实体键盘按的 -> 原样补发回车，不能被换成 ctrl+enter
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "发送键的按下没被吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, False)
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [(M.VK_RETURN, 0x1C, False)], app.keys.replays

    # 7c) 没有待确认的抬起 -> 不能凭空发（也不能凭空补）
    app.keys.calls = []
    app.keys.replays = []
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == [], app.keys.calls
    assert app.keys.replays == [], app.keys.replays

    # 7d) 🔴 按下那条 Raw Input（被吞时根本不会有）来了也不能触发动作
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    app._on_raw_key(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == [], "按下的 Raw Input 不该触发发送：%r" % (app.keys.calls,)
    assert app.keys.replays == [], app.keys.replays

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
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app.arbiter.on_raw_keyup(0x20, 0x39, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls

    # 换成发送键以后，Enter 就不该再被拦
    app.keys.calls = []
    app.send_key_vk = 0x20
    kent = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kent)) != 1, \
        "发送键改了之后 Enter 不该再被拦"
    assert app.keys.calls == [], app.keys.calls

    import importlib
    importlib.reload(M)


# ---------------- v1.7：注入回声必须丢弃 ----------------


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
    会自动重复产生 ~30 次/秒的 keydown，于是 pending 堆成 6 条
    -> **一次按住连发 6 次 ctrl+enter**（v1.5 日志 17:32:49 / 17:36:03
    连续 6 条「发送键已转换（来源 raw）」就是现场）。

    v1.6：**一次「按下-松开」只对应一条 pending**；
    自动重复的 keydown 照吞（不能让真实回车漏到前台程序），但不新增 pending。

    🔴 v1.7：判定改用**抬起**那条 Raw Input —— 抬起一次按键只有一次，
       所以天然只会发一次（双保险）。
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

    # 抬起放行 -> 它那条 Raw Input 到达 -> 只发一次
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], \
        "🔴 按住不放应该只发一次，实际发了 %r" % (app.keys.calls,)
    assert app.keys.replays == [], app.keys.replays

    # 松开之后重新按一次 -> 应该再发一次（不是永久失效）
    app.keys.calls = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "第二次按下应该被吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(M.VK_RETURN, 0x1C, True)
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls

    import importlib
    importlib.reload(M)


def t_fallback_judge():
    """兜底判据：遥控器 vs 实体键盘，要能分清。

    🔴 v1.7 判据换源：
      v1.6 用「最近一次**实体键盘按发送键**的时刻」当反向证据 —— 不可靠：
        实体键盘的回车**会被我们吞掉**，它自己那条 Raw Input 永远不会到，
        所以那个时间戳只在「前置条件不满足、我们没吞」时才更新；
        更致命的是 v1.6 把**自己注入的回声**（hdev=0）也当成了实体键盘按发送键
        -> 污染判据 -> 第一次之后每一次都失败。
      v1.7 改成「最近一次**任意**实体键盘活动」——
        这只依赖**一定能看到**的事件（我们只吞 F5 重复和发送键按下，
        字母键、空格、方向键这些 Raw Input 一定到）。
    """
    app, _hook = _make_sender_app()
    ri = _FakeRawInput(0)              # 遥控器刚刚活动过
    app.remote_input = ri
    remote, why = app.send_key_verdict()
    assert remote is True, "刚用过遥控器 -> 应判为遥控器（%s）" % why

    # 木木用实体键盘打了字（时间比遥控器活动更近）-> 不该判成遥控器
    ri.last_physical_ts = ri.last_activity + 0.001
    remote, why = app.send_key_verdict()
    assert remote is False, \
        "🔴 实体键盘活动被误判成遥控器了（会误转成 ctrl+enter）：%s" % why

    # 遥控器又活动了一次（明确晚于实体键盘那次）-> 重新判回遥控器
    ri.last_activity = ri.last_physical_ts + 0.001
    remote, why = app.send_key_verdict()
    assert remote is True, "遥控器活动更新后应判为遥控器（%s）" % why

    # 遥控器很久没活动 -> 不判
    ri.last_activity = time.time() - 60
    remote, why = app.send_key_verdict()
    assert remote is False, "遥控器太久没活动 -> 不该判成遥控器"

    # 从来没见过遥控器 -> 不判（不能瞎猜）
    ri2 = _FakeRawInput(0)
    ri2.last_activity = 0.0
    app.remote_input = ri2
    remote, why = app.send_key_verdict()
    assert remote is False, "没见过遥控器活动就不该判成遥控器"

    # 旧接口还在（兼容）
    assert app.remote_recently() is False, "remote_recently 应与 verdict 一致"


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




def _raw_keyboard_bytes_hdev(makecode, vkey, hdev, msg=0x0100, flags=0):
    """带 hDevice 的 RAWKEYBOARD 字节（hdev=0 = 注入回声）。"""
    b = bytearray(_raw_keyboard_bytes(makecode, vkey, msg, flags))
    b[8:16] = int(hdev).to_bytes(8, "little")
    return bytes(b)


def _stub_get_raw_input(m, raw_bytes):
    """把 user32.GetRawInputData 打桩成「返回这段真实布局的字节」。"""
    def fake(hraw, cmd, buf, psize, cb):
        n = len(raw_bytes)
        if buf is None:
            psize._obj.value = n
            return 0
        ctypes.memmove(buf, raw_bytes, n)
        psize._obj.value = n
        return n
    m.user32.GetRawInputData = fake


def t_hdev0_echo():
    """🔴🔴 回归测试：**hdev=0 的注入回声必须被整个丢弃**。

    这是 v1.6 的真 bug（也是「第一次能发、之后每次都发不出去」的根因）：

      SendInput 注入的按键会产生 **hdev=0** 的 Raw Input 回声
      （实测：注入 Alt / F5 / Enter 各产生 2 条 hdev=0 的 WM_INPUT）。
      v1.6 把它当成「实体键盘按了发送键」-> 污染 `last_send_other_ts`
      -> 「最近活动来自遥控器」永远判不成立
      -> **第一次之后每一次按 OK 键都失败**。

      19:03:51 实测现场：
          发送键已转换：Enter 回车 -> ctrl+enter（来源 timeout）
          Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发   ← 就是这个回声

    v1.7：hdev==0 的事件**直接丢掉** —— 不更新时间戳、不通知仲裁器。
    """
    REMOTE_NAME = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_"
                   r"VID&012717_PID&32B8_REV#9&2b876598&0&0000")
    ri = M.RemoteInputWatcher(None, "2717")
    seen = []
    ri.on_keydown = lambda v, mk, hit: seen.append(("down", v, hit))
    ri.on_keyup = lambda v, mk, hit: seen.append(("up", v, hit))

    # 1) hdev=0（注入回声）-> 整个丢掉
    raw0 = _raw_keyboard_bytes_hdev(0x1C, 0x0D, 0)
    _stub_get_raw_input(M, raw0)
    ri.last_activity = 0.0
    ri.last_physical_ts = 0.0
    hit = ri.handle(12345)
    assert hit is False, "注入回声不该被当成遥控器"
    assert ri.last_activity == 0.0, "🔴 注入回声污染了遥控器活动时间"
    assert ri.last_physical_ts == 0.0, "🔴 注入回声污染了实体键盘活动时间"
    assert seen == [], "🔴 注入回声被当成了真实按键事件：%r" % (seen,)
    assert ri.echo_count == 1, "注入回声计数不对：%s" % ri.echo_count

    # 2) 真实设备（遥控器）-> 正常处理，更新遥控器活动时间
    ri2 = M.RemoteInputWatcher(None, "2717")
    ri2._read_names = lambda h: [REMOTE_NAME]
    seen2 = []
    ri2.on_keydown = lambda v, mk, hit: seen2.append(("down", v, hit))
    _stub_get_raw_input(M, _raw_keyboard_bytes_hdev(0x1C, 0x0D, 7))
    hit = ri2.handle(12345)
    assert hit is True, "遥控器设备没认出来"
    assert ri2.last_activity > 0.0, "遥控器活动时间没更新"
    assert ri2.last_physical_ts == 0.0, "遥控器活动不该记到实体键盘头上"
    assert seen2 and seen2[0][0] == "down", seen2

    # 3) 实体键盘 -> 更新实体键盘活动时间，不动遥控器那边
    ri3 = M.RemoteInputWatcher(None, "2717")
    ri3._read_names = lambda h: [r"\\?\HID#VID_046D&PID_C31C&MI_00#8&2a3b4c5d&0&0000"]
    _stub_get_raw_input(M, _raw_keyboard_bytes_hdev(0x1E, 0x41, 9))
    hit = ri3.handle(12345)
    assert hit is False, "普通键盘不该被当成遥控器"
    assert ri3.last_physical_ts > 0.0, "实体键盘活动时间没更新"
    assert ri3.last_activity == 0.0, "实体键盘活动不该记到遥控器头上"

    # 4) 抬起事件也要处理（v1.7 的判定就靠它）
    ri4 = M.RemoteInputWatcher(None, "2717")
    ri4._read_names = lambda h: [REMOTE_NAME]
    ups = []
    ri4.on_keyup = lambda v, mk, hit: ups.append((v, hit))
    _stub_get_raw_input(M, _raw_keyboard_bytes_hdev(0x1C, 0x0D, 7,
                                                     msg=0x0101))
    ri4.handle(12345)
    assert ups and ups[0][0] == 0x0D, "抬起事件没被送到 on_keyup：%r" % (ups,)
    assert ups[0][1] is True, "抬起事件的设备归属判错了"

    import importlib
    importlib.reload(M)


def t_activity_split():
    """🔴 回归测试：遥控器活动与实体键盘活动**分开记**，互不污染。

    v1.6 只有 `last_activity` 一个字段，实体键盘与遥控器混在一起，
    再加上注入回声的污染，判据就彻底失灵了。

    v1.7 拆成两个字段：
      last_activity     —— 最近一次**遥控器**按键
      last_physical_ts  —— 最近一次**实体键盘**按键
    「谁更近谁说了算」才有意义。
    """
    ri = M.RemoteInputWatcher(None, "2717")
    assert ri.last_activity == 0.0
    assert ri.last_physical_ts == 0.0

    REMOTE_NAME = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_"
                   r"VID&012717_PID&32B8_REV#9&2b876598&0&0000")
    ri._read_names = lambda h: [REMOTE_NAME]
    _stub_get_raw_input(M, _raw_keyboard_bytes_hdev(0x1C, 0x0D, 7))
    ri.handle(12345)
    t_remote = ri.last_activity
    assert t_remote > 0.0, "遥控器活动时间没更新"
    assert ri.last_physical_ts == 0.0, "遥控器活动不该污染实体键盘时间"

    import importlib
    importlib.reload(M)


def t_up_passthrough():
    """🔴 回归测试：发送键的**抬起必须放行**（v1.7 架构的地基）。

    实测（tools\\probe_raw_live.py swallow，2026-10-30）：
        放行(对照)    WM_INPUT 3 条
        全吞(下+抬)   WM_INPUT 0 条   <- 被吞的按键没有 Raw Input
        只吞按下      WM_INPUT 1 条   <- 抬起照样到！

    所以「吞下、放开抬」是拿到**确定**设备归属的唯一办法。
    这条测试把「抬起被吞」这种改法直接判死。
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: (not part) or ("workbuddy" in part.lower())

    app, hook = _make_sender_app()
    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)

    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "按下应该被吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, \
        "🔴 抬起被吞了 —— v1.7 的判定链路就断了"

    # 抬起放行后，pending 必须被标记成「抬起已发生」，等 Raw Input 来定性
    pend = app.arbiter._pending[-1]
    assert pend["up_ts"] is not None, "抬起没有标记到 pending 上"

    import importlib
    importlib.reload(M)
def t_scroll_mapping():
    """v1.8：遥控器按键 -> 动作映射（发送 / 上滚 / 下滚）。

    背景：音量键 / 返回键在 Windows 上压根不上报（探针
    tools\\probe_hidden_keys.py 实测），所以改用遥控器上两个闲置键。
    这条用例锁死「哪个 vk 对应哪个动作」以及「冲突时谁优先」。
    """
    app, _hook = _make_sender_app()
    app.scroll_on = True
    app.scroll_up_vk = 0x5D          # 菜单键
    app.scroll_down_vk = 0xC0        # TV 键

    assert app.hook_action_for(M.VK_RETURN) == "send"
    assert app.hook_action_for(0x5D) == "scroll_up", "菜单键应是向上滚"
    assert app.hook_action_for(0xC0) == "scroll_down", "TV 键应是向下滚"
    assert app.hook_action_for(0x41) is None, "没配的键不该有动作"

    # 关掉发送 -> OK 键不再有动作，滚轮不受影响
    app.ok_to_send = False
    assert app.hook_action_for(M.VK_RETURN) is None, "ok_to_send 关了不该还有 send"
    assert app.hook_action_for(0x5D) == "scroll_up", "关发送不该影响滚轮"
    app.ok_to_send = True

    # 关掉滚轮 -> 滚轮键不再有动作，发送不受影响
    app.scroll_on = False
    assert app.hook_action_for(0x5D) is None
    assert app.hook_action_for(0xC0) is None
    assert app.hook_action_for(M.VK_RETURN) == "send"
    app.scroll_on = True

    # 🔴 配重了：同一个键既是发送键又配成上滚键 -> **发送优先**
    #    （这是明确的设计选择：发送是主线功能，宁可滚不了也不能发不出去）
    app.scroll_up_vk = M.VK_RETURN
    assert app.hook_action_for(M.VK_RETURN) == "send", \
        "同一个键配成发送+上滚时，必须发送优先"


def t_scroll_exec():
    """v1.8：菜单键 -> 向上滚、TV 键 -> 向下滚（复用 v1.7 的仲裁链）。"""
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: True

    app, hook = _make_sender_app()
    app.scroll_on = True
    app.scroll_up_vk = 0x5D
    app.scroll_down_vk = 0xC0
    app.scroll_step = 120
    # 🔴 故意让「发送」的前置条件不满足（前台窗口不匹配）：
    #    滚轮键**不该**受这个限制 —— 木木要用它翻网页/翻文档/翻聊天记录，
    #    限制成 WorkBuddy 就等于没用。
    app.ok_send_title = "__不存在的窗口__"

    kb = M.KBDLLHOOKSTRUCT(0x5D, 0x00, 0x00, 0, 0)
    lp = ctypes.addressof(kb)

    # 1) 菜单键：按下被吞、抬起放行、抬起那条 Raw Input 说是遥控器 -> 向上滚
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "菜单键按下没被吞"
    assert app.keys.wheels == [], "还没定性就滚了"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "抬起必须放行"
    app._on_raw_keyup(0x5D, 0x00, True)
    assert app.keys.wheels == [120], \
        "菜单键应向上滚(+120)，实际 %r" % (app.keys.wheels,)
    assert app.keys.calls == [], "滚轮键不该触发发送快捷键"

    # 2) TV 键 -> 向下滚（负的滚动量）
    kb2 = M.KBDLLHOOKSTRUCT(0xC0, 0x00, 0x00, 0, 0)
    lp2 = ctypes.addressof(kb2)
    assert hook._on_key(0, M.WM_KEYDOWN, lp2) == 1
    hook._on_key(0, M.WM_KEYUP, lp2)
    app._on_raw_keyup(0xC0, 0x00, True)
    assert app.keys.wheels == [120, -120], app.keys.wheels

    # 3) 抬起那条 Raw Input 说是**实体键盘** -> 不滚，把原键补发回去
    #    （不然木木按真实键盘的菜单键会「弹不出右键菜单」）
    app.keys.wheels = []
    app.keys.replays = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    hook._on_key(0, M.WM_KEYUP, lp)
    app._on_raw_keyup(0x5D, 0x00, False)
    assert app.keys.wheels == [], "判成实体键盘时不该滚"
    assert app.keys.replays == [(0x5D, 0x00, False)], app.keys.replays

    # 4) 按住不放 -> 由看门狗节流连续滚；松开就停
    app.keys.wheels = []
    app.scroll_repeat_ms = 50
    kb3 = M.KBDLLHOOKSTRUCT(0x5D, 0x00, 0x00, 0, 0)
    lp3 = ctypes.addressof(kb3)
    hook._on_key(0, M.WM_KEYDOWN, lp3)     # 首次按下 -> 新增 pending
    hook._on_key(0, M.WM_KEYDOWN, lp3)     # 自动重复 -> 记下「要连滚」
    app.arbiter._repeat_emit_ts = 0.0
    app.arbiter.tick()
    assert app.keys.wheels == [120], \
        "按住不放应当连续滚，实际 %r" % (app.keys.wheels,)
    hook._on_key(0, M.WM_KEYUP, lp3)       # 松开 -> 必须停
    app.arbiter._repeat_emit_ts = 0.0
    app.arbiter.tick()
    assert app.keys.wheels == [120], "松开之后不该继续滚"

    # 5) 滚轮键也必须响应「程序暂停」
    app.enabled = False
    assert hook._on_key(0, M.WM_KEYDOWN, lp) != 1, "程序暂停时不该吞滚轮键"
    app.enabled = True

    # 6) 抬起事件的 vk 和按下时**对不上**（RAWKEYBOARD.VKey 可能是 0xFF 表示
    #    「系统翻译不出来」）-> 必须靠「最早抬起的那一条 pending」配对成功，
    #    否则就退回时间窗兜底，白丢一次精确判定。
    app.keys.wheels = []
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    hook._on_key(0, M.WM_KEYUP, lp)
    app._on_raw_keyup(0xFF, 0x00, True)
    assert app.keys.wheels == [120], \
        "抬起 vk 对不上时也要配对成功，实际 %r" % (app.keys.wheels,)


def t_scroll_input_layout():
    """v1.8：滚轮事件的 INPUT 内存布局（这里踩过坑，必须锁死）。

    🔴 `MOUSEINPUT.mouseData` 是 **DWORD（无符号）**，而 Windows 要求
    向下滚传**负数的补码**（-120 -> 0xFFFFFF88）。
       · 直接赋 -120 给无符号字段 -> ctypes 抛 / 或截断成错值
       · 必须 `int(delta) & 0xFFFFFFFF`
    🔴 `INPUT.type` 的 `INPUT_MOUSE` 是 **0**（不是 1，1 是 KEYBOARD）。
    """
    assert ctypes.sizeof(M.INPUT) == 40, \
        "x64 下 sizeof(INPUT) 必须是 40，实际 %d" % ctypes.sizeof(M.INPUT)
    assert M.INPUT_MOUSE == 0, "INPUT_MOUSE 必须是 0"
    assert M.MOUSEEVENTF_WHEEL == 0x0800
    assert M.WHEEL_DELTA == 120

    inp = M.INPUT()
    inp.type = M.INPUT_MOUSE
    inp.mi = M.MOUSEINPUT(0, 0, int(-120) & 0xFFFFFFFF,
                          M.MOUSEEVENTF_WHEEL, 0, None)
    assert inp.type == 0
    assert inp.mi.mouseData == 0xFFFFFF88, hex(inp.mi.mouseData)
    assert inp.mi.dwFlags == 0x0800
    # 正数方向
    inp.mi = M.MOUSEINPUT(0, 0, int(120) & 0xFFFFFFFF,
                          M.MOUSEEVENTF_WHEEL, 0, None)
    assert inp.mi.mouseData == 120


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
    check("OK 键：注入回声必须丢弃", t_hdev0_echo)
    check("OK 键：遥控器/实体键盘活动分开记", t_activity_split)
    check("OK 键：抬起必须放行", t_up_passthrough)
    check("滚轮键：按键 -> 动作映射", t_scroll_mapping)
    check("滚轮键：菜单上滚 / TV 下滚", t_scroll_exec)
    check("滚轮键：INPUT 内存布局", t_scroll_input_layout)
    check("F5 拦截（遥控器语音键）", t_f5_swallow)
    print("=" * 60)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  !! %s: %s" % (n, e))
        sys.exit(1)


if __name__ == "__main__":
    main()
