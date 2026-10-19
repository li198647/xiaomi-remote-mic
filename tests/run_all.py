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

PASS, FAIL = [], []


def check(name, fn):
    t0 = time.time()
    try:
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
    """替身：只记录 send_combo 被调了什么，绝不往真实桌面注入按键。"""

    def __init__(self):
        self.calls = []

    def send_combo(self, spec):
        self.calls.append(spec)
        return True


class _FakeRawInput:
    def __init__(self, age_ms=0.0):
        self.last_activity = time.time() - age_ms / 1000.0


def _make_app():
    return M.App(types.SimpleNamespace(debug=False, selftest=0))


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
    """直接喂一个伪造的 KBDLLHOOKSTRUCT 给钩子回调，验证判定链与吞键。

    ⚠️ 必须把「前台窗口 / 标题匹配 / 修饰键状态」三处外部依赖打桩：
    真实环境里前台窗口会变（可能压根没有前台窗口）、用户可能正按着某个键，
    会导致这条用例偶发失败（踩过一次）。打桩后结果完全确定。
    """
    M.user32.GetForegroundWindow = lambda: 1
    M.title_matches = lambda hwnd, part: True
    M.user32.GetAsyncKeyState = lambda vk: 0

    app = _make_app()
    app.keys = _FakeKeys()
    app.ok_to_send = True
    app.enabled = True
    app.ime_flag = False
    app.ok_send_title = ""          # 空 = 任意前台窗口都算，便于自动化测试
    app.remote_active_ms = 15000
    app.remote_input = _FakeRawInput(0)
    hook = M.EnterSendHook(app)     # 只造对象，不 install()（不装真钩子）

    kb = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, 0, 0, 0)
    lp = ctypes.addressof(kb)   # 系统传进来的是整数地址，测试也照此

    # 1) 正例：遥控器刚活动过 + 在 WorkBuddy 里按 OK -> 吞掉并转成发送
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1, "OK 键没被吞掉"
    assert app.keys.calls == ["ctrl+enter"], app.keys.calls
    # 2) 配对的抬起也要吞，防残留状态
    assert hook._on_key(0, M.WM_KEYUP, lp) == 1, "抬起没吞"
    assert hook._on_key(0, M.WM_KEYUP, lp) != 1, "没吞过的抬起不该拦"
    # 3) 长按自动重复：350ms 内不再重复发送
    hook._last_convert = time.time()
    assert hook._on_key(0, M.WM_KEYDOWN, lp) == 1
    assert app.keys.calls == ["ctrl+enter"], "长按重复发送了"

    # 4) 各种「必须放行」的情况
    def assert_pass(reason, **patch):
        saved = {k: getattr(app, k) for k in patch}
        for k, v in patch.items():
            setattr(app, k, v)
        hook._last_convert = 0.0
        hook._swallow_up = False
        n = len(app.keys.calls)
        try:
            got = hook._on_key(0, M.WM_KEYDOWN, lp)
            assert got != 1, "%s 时不该拦截" % reason
            assert len(app.keys.calls) == n, "%s 时不该发送" % reason
        finally:
            for k, v in saved.items():
                setattr(app, k, v)

    assert_pass("遥控器 15 秒内没活动", remote_input=_FakeRawInput(30000))
    assert_pass("输入法在组词", ime_flag=True)
    assert_pass("总开关关闭", ok_to_send=False)
    assert_pass("程序已暂停", enabled=False)
    assert_pass("没拿到 Raw Input", remote_input=None)

    # 5) 非 Enter 键、本程序注入的事件，都必须放行
    app.remote_input = _FakeRawInput(0)
    hook._last_convert = 0.0
    ka = M.KBDLLHOOKSTRUCT(ord("A"), 0x1E, 0, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(ka)) != 1, "A 键被拦了"
    kinj = M.KBDLLHOOKSTRUCT(M.VK_RETURN, 0x1C, M.LLKHF_INJECTED, 0, 0)
    assert hook._on_key(0, M.WM_KEYDOWN, ctypes.addressof(kinj)) != 1, \
        "自己注入的 Enter 被拦了（会死循环）"

    # 撤销打桩（中途断言失败时不撤销也无妨，进程马上就退出了）
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
    check("OK 键：遥控器设备识别", t_remote_device_match)
    print("=" * 60)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  !! %s: %s" % (n, e))
        sys.exit(1)


if __name__ == "__main__":
    main()
