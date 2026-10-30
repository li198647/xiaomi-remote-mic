# -*- coding: utf-8 -*-
"""小米蓝牙遥控器 —— 按键扫描器

把遥控器上的每个键都按一遍，这里逐个列出来：按键名 / vk / 扫描码 / 来自哪个设备。

用法：
    双击  E:\\workbuddywork\\win10小修小补\\tools\\scan-remote-keys.bat
    或命令行：
        python E:\\workbuddywork\\win10小修小补\\tools\\scan_remote_keys.py --seconds 120

原理：
  · **低级键盘钩子**（WH_KEYBOARD_LL）拿得到精确的 vkCode / scanCode / flags，
    但**分不出是哪个设备**按的。
  · **Raw Input**（WM_INPUT）带 hDevice，能解析出设备路径（里面有 VID&012717），
    所以能认出「这一下是遥控器按的」，但字段解析要小心。
  两条链路各取所长，按时间戳（同一毫秒）对齐，就得到完整的「键 -> 设备」对照。

退出：Ctrl+C，或窗口里按 Esc，或跑满 --seconds 秒（默认 180 秒）。
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_mivoice() -> str:
    """自适应定位 mivoice 目录（本脚本有两个合法位置，两边都要能跑）：

      · <项目>\\tools\\          —— 放在仓库外面时（本机原来的位置）
      · <项目>\\mivoice\\tools\\  —— 仓库自带，clone 下来就在这儿
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.dirname(here)):
        if os.path.isfile(os.path.join(cand, "mivoice.py")):
            return cand
        sub = os.path.join(cand, "mivoice")
        if os.path.isfile(os.path.join(sub, "mivoice.py")):
            return sub
    return os.path.join(os.path.dirname(here), "mivoice")


MIVOICE_DIR = _find_mivoice()
sys.path.insert(0, MIVOICE_DIR)

import mivoice as M          # noqa: E402  复用它的 Win32 声明

u = M.user32
k = M.kernel32
wt = M.wt

OUT_PATH = os.path.join(HERE, "remote_keys_scan.log")

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
VK_ESCAPE = 0x1B
WH_KEYBOARD_LL = 13
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

# ---- 好看的键名表（只列遥控器上真可能有的）----
VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter 回车", 0x1B: "Esc",
    0x20: "Space 空格", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End",
    0x24: "Home", 0x25: "左方向键", 0x26: "上方向键", 0x27: "右方向键",
    0x28: "下方向键", 0x2D: "Insert", 0x2E: "Delete",
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
    0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D", 0x45: "E", 0x46: "F",
    0x47: "G", 0x48: "H", 0x49: "I", 0x4A: "J", 0x4B: "K", 0x4C: "L",
    0x4D: "M", 0x4E: "N", 0x4F: "O", 0x50: "P", 0x51: "Q", 0x52: "R",
    0x53: "S", 0x54: "T", 0x55: "U", 0x56: "V", 0x57: "W", 0x58: "X",
    0x59: "Y", 0x5A: "Z",
    0x5B: "Win 左Win", 0x5C: "Win 右Win", 0x5D: "Apps 菜单键",
    0x60: "小键盘0", 0x61: "小键盘1", 0x62: "小键盘2", 0x63: "小键盘3",
    0x64: "小键盘4", 0x65: "小键盘5", 0x66: "小键盘6", 0x67: "小键盘7",
    0x68: "小键盘8", 0x69: "小键盘9", 0x6A: "小键盘*", 0x6B: "小键盘+",
    0x6C: "小键盘-", 0x6D: "小键盘.", 0x6E: "小键盘Enter",
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5 语音键?",
    0x75: "F6", 0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10",
    0x7A: "F11", 0x7B: "F12",
    0x90: "NumLock", 0x91: "ScrollLock", 0xA0: "Shift 左",
    0xA1: "Shift 右", 0xA2: "Ctrl 左", 0xA3: "Ctrl 右",
    0xA4: "Alt 左", 0xA5: "Alt 右", 0xA6: "Browser Back",
    0xA7: "Browser Forward", 0xA8: "Browser Refresh", 0xA9: "Browser Stop",
    0xAA: "Browser Search", 0xAB: "Browser Favorites", 0xAC: "Browser Home",
    0xAD: "音量减", 0xAE: "音量加", 0xAF: "静音",
    0xB0: "媒体下一曲", 0xB1: "媒体上一曲", 0xB2: "媒体停止",
    0xB3: "媒体播放/暂停",
    0xAD: "静音 / Volume-",
}
CONSUMER_NAMES = {
    0x30: "音量加(消费键)", 0x31: "音量减(消费键)", 0x32: "静音(消费键)",
    0x33: "播放(消费键)", 0x34: "暂停(消费键)", 0x35: "上一曲(消费键)",
    0x36: "下一曲(消费键)", 0x37: "停止(消费键)",
    0x38: "主页(消费键)", 0x39: "开始(消费键)", 0x3A: "收藏(消费键)",
    0x3C: "停止(消费键2)", 0x40: "搜索(消费键)", 0x41: "TV(消费键)",
    0x4A: "直播(消费键)", 0x4B: "指南(消费键)", 0x4C: "返回(消费键)",
    0x4D: "信息(消费键)", 0x4E: "设置(消费键)",
}


def vk_name(vk):
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x5A:
        return "字母 %s" % chr(vk)
    if 0x60 <= vk <= 0x69:
        return "小键盘 %d" % (vk - 0x60)
    return "未知"


# =====================================================================
# 全局状态
# =====================================================================
STATE = {
    "dev_cache": {},        # hdev -> 设备名
    "hook_events": [],      # [(t, vk, scan, flags, kind)]  低级钩子（同步，先到）
    "paired": set(),        # 已经跟 Raw Input 配对过的 hook 事件（存 id）
    "unpaired_raw": [],     # 找不到对应 hook 事件的 Raw Input
    "rows": [],             # 汇总结果
}
LOG_FH = None


def emit(line=""):
    print(line, flush=True)
    try:
        if LOG_FH:
            LOG_FH.write(line + "\n")
            LOG_FH.flush()
    except Exception:
        pass


def dev_name(hdev):
    if hdev in STATE["dev_cache"]:
        return STATE["dev_cache"][hdev]
    name = ""
    try:
        size = wt.UINT(0)
        u.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, None,
                                 ctypes.byref(size))
        if size.value:
            buf = ctypes.create_unicode_buffer(size.value + 1)
            if u.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, buf,
                                         ctypes.byref(size)):
                name = buf.value
    except Exception as e:
        emit("  !! 取设备名失败: %r" % (e,))
    STATE["dev_cache"][hdev] = name
    return name


def is_remote_name(name):
    n = (name or "").lower()
    return "vid&012717" in n or "vid_012717" in n


# =====================================================================
# Raw Input：认设备
# =====================================================================
def handle_raw_input(lparam):
    """处理 WM_INPUT：认出设备，并把最近的一次低级钩子事件配对上来。

    ⚠️ 顺序很关键：**低级钩子是同步先跑的**（输入线程当场回调），
    WM_INPUT 消息是事后才 post 到窗口、异步处理的。
    所以要「用 Raw Input 去找 hook 事件」，反过来就配不上。
    """
    try:
        size = wt.UINT(0)
        hraw = wt.HANDLE(lparam)
        if u.GetRawInputData(hraw, RID_INPUT, None, ctypes.byref(size), 24) != 0:
            return
        if not size.value:
            return
        buf = ctypes.create_string_buffer(size.value)
        if u.GetRawInputData(hraw, RID_INPUT, buf, ctypes.byref(size), 24) == 0xFFFFFFFF:
            return
        raw = buf.raw
        dtype = M.struct_unpack_i(raw, 0)
        hdev = M.struct_unpack_q(raw, 8)
        name = dev_name(hdev)
        remote = is_remote_name(name)
        t = time.time()

        # 候选字段解释（按 x64 标准布局打印出来，肉眼就能定出偏移）
        cand = {
            "o24": M.struct_unpack_i(raw, 24),
            "o26": raw[26] | (raw[27] << 8),
            "o28": M.struct_unpack_i(raw, 28),
            "o32": M.struct_unpack_i(raw, 32),
            "o16": M.struct_unpack_i(raw, 16),
        }
        hexs = raw[:48].hex()

        # 找时间上最接近的 hook 事件
        best = match_raw(t)
        if best is None:
            STATE["unpaired_raw"].append((t, name, remote, hexs, cand))
            return
        _, vk, scan, flags, kind = best
        STATE["paired"].add(id(best))
        src = "遥控器" if remote else "其他设备"
        # 显示去抖：遥控器语音键按住时会自动重复 ~30 次/秒，
        # 不折叠的话刷屏看不清。只折叠「显示」，记录照旧。
        sig = (vk, kind)
        last = STATE.get("_last_sig")
        if last and last[0] == sig and (t - last[1]) < 0.30:
            STATE["_dup"] = STATE.get("_dup", 0) + 1
        else:
            STATE["_last_sig"] = (sig, t)
            dup = STATE.get("_dup", 0)
            STATE["_dup"] = 0
            if dup:
                emit("        （上面那个键又自动重复了 %d 次，已折叠）" % dup)
            emit("  %-7s vk=0x%02X %-16s scan=0x%02X flags=0x%02X  <- %-8s "
                 "raw[24:]=%s"
                 % (kind, vk, vk_name(vk), scan, flags, src, hexs[48:]))
        STATE["rows"].append({
            "vk": vk, "scan": scan, "flags": flags, "src": src,
            "name": vk_name(vk), "dev": name, "cand": cand,
        })
    except Exception as e:
        emit("  !! Raw Input 解析异常: %r" % (e,))


# =====================================================================
# 低级键盘钩子：拿精确的 vk / scan / flags
# =====================================================================
def flush_unpaired():
    """兜底：钩子抓到、但没等到对应 Raw Input 的按键，也照样打出来。

    没有这一段的话，只要设备归属对不上（Raw Input 没来），
    屏幕就一个字都没有，等于白按。所以「键」一定要显示出来，
    设备来源只是锦上添花。
    """
    now = time.time()
    keep = []
    for ev in STATE["hook_events"]:
        t, vk, scan, flags, kind = ev
        if now - t < 0.25:
            keep.append(ev)
            continue
        if id(ev) in STATE["paired"]:
            continue
        emit("  %-7s vk=0x%02X %-16s scan=0x%02X flags=0x%02X  <- 设备未知"
             "（没等到 Raw Input）" % (kind, vk, vk_name(vk), scan, flags))
        STATE["rows"].append({
            "vk": vk, "scan": scan, "flags": flags, "src": "未知",
            "name": vk_name(vk), "dev": "", "cand": None,
        })
        STATE["paired"].add(id(ev))
    STATE["hook_events"] = keep


def match_raw(t):
    """找时间上最接近的 hook 事件（钩子同步先跑，Raw Input 异步后到）。"""
    best = None
    for ev in STATE["hook_events"]:
        if abs(ev[0] - t) < 0.15:
            if best is None or abs(ev[0] - t) < abs(best[0] - t):
                best = ev
    return best


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM,
                              wt.LPARAM)
_hook_ref = None


def _hook_cb(ncode, wparam, lparam):
    """低级钩子只负责「记一笔」，不打印 —— 打印交给 Raw Input 那条链路，
    因为只有它知道这一下是哪个设备按的。"""
    try:
        if ncode == 0:
            kb = ctypes.cast(lparam, ctypes.POINTER(M.KBDLLHOOKSTRUCT)).contents
            kind = {WM_KEYDOWN: "down", WM_KEYUP: "up",
                    WM_SYSKEYDOWN: "sysdown", WM_SYSKEYUP: "sysup"}.get(
                        wparam, str(wparam))
            STATE["hook_events"].append((time.time(), kb.vkCode, kb.scanCode,
                                         kb.flags, kind))
            if len(STATE["hook_events"]) > 400:
                del STATE["hook_events"][:200]
    except Exception:
        pass
    return u.CallNextHookEx(None, ncode, wparam, lparam)


def hook_thread():
    global _hook_ref
    _hook_ref = HOOKPROC(_hook_cb)
    h = u.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_ref,
                            k.GetModuleHandleW(None), 0)
    if not h:
        emit("！！低级钩子安装失败")
        return
    msg = wt.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


# =====================================================================
# 主窗口 + 消息循环
# =====================================================================
def _wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        handle_raw_input(lp)
        return 0
    if msg == WM_KEYDOWN and wp == VK_ESCAPE:
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


# 用 mivoice 里现成的 WNDPROC / WNDCLASSW（argtypes 都已配好）
WNDPROC = M.WNDPROC
_wndproc_ref = WNDPROC(_wndproc)


def selftest():
    """不用按遥控器也能验证核心逻辑（时间戳配对 + 去抖 + 三态机）。"""
    ok = True

    # 1) 时间戳配对：Raw Input 晚到 5ms 也要能配上 hook 事件
    STATE["hook_events"] = []
    STATE["paired"] = set()
    t0 = time.time()
    ev = (t0, 0x74, 0x3F, 0x00, "down")
    STATE["hook_events"].append(ev)
    m = match_raw(t0 + 0.005)
    assert m is not None, "5ms 内的 hook 事件没配上"
    assert m[1] == 0x74, "配错了事件"
    # 差太远的配不上
    assert match_raw(t0 + 5.0) is None, "不该把 5 秒前的事件配上"

    # 2) 已配对的不再重复输出
    #    注意必须用**同一个对象**（id 变了就认不出来了）
    old = (t0 - 10, 0x74, 0x3F, 0x00, "down")
    STATE["paired"].add(id(old))
    STATE["hook_events"] = [old]
    rows_before = len(STATE["rows"])
    flush_unpaired()
    assert len(STATE["rows"]) == rows_before, "已配对的事件被重复输出了"

    # 3) 没配对的要输出（兜底：宁可多打也不能什么都不打）
    STATE["hook_events"] = [(time.time() - 10, 0x41, 0x1E, 0x00, "down")]
    rows_before = len(STATE["rows"])
    flush_unpaired()
    assert len(STATE["rows"]) == rows_before + 1, "未配对的事件没输出"

    # 4) 设备名识别
    assert is_remote_name(r"\\?\HID#VID&012717_PID&32B8#8&1f2e3d4c&0&0000")
    assert not is_remote_name(r"\\?\HID#VID_046D&PID_C31C&MI_00#8&2a3b4c5d&0&0000")
    assert not is_remote_name("")

    # 5) 名字反查
    assert vk_name(0x74) != "未知"
    assert vk_name(0x0D) == "Enter 回车"

    print("扫描器自检：全部通过")
    return 0


def main():
    global LOG_FH
    seconds = 180
    for i, a in enumerate(sys.argv):
        if a == "--seconds" and i + 1 < len(sys.argv):
            seconds = int(sys.argv[i + 1])

    try:
        LOG_FH = open(OUT_PATH, "w", encoding="utf-8")
    except Exception:
        LOG_FH = None

    emit("=" * 66)
    emit("小米遥控器按键扫描器")
    emit("=" * 66)
    emit("请把遥控器上的每个键都按一遍（每个键按一下、松一下，别长按）")
    emit("  · 语音键 / OK 键 / 方向键 / 主页 / 返回 / 音量 / TV / 功能键 …")
    emit("  · 按一次记一行；长按只会重复记同一个键，无所谓")
    emit("  · 结束：按 Esc，或等 %d 秒自动退出" % seconds)
    emit("-" * 66)

    # 窗口（不可见，只用来收 WM_INPUT）
    hinst = k.GetModuleHandleW(None)
    clsname = "RemoteKeyScanWnd"
    wc = M.WNDCLASSW()
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = hinst
    wc.lpszClassName = clsname
    u.RegisterClassW(ctypes.byref(wc))
    hwnd = u.CreateWindowExW(0, clsname, "RemoteKeyScan", 0, 0, 0, 0, 0,
                              None, None, hinst, None)
    if not hwnd:
        emit("！！窗口创建失败")
        return 2

    # Raw Input：键盘 + Consumer Control（消费键）都注册
    rids = (M.RAWINPUTDEVICE * 2)()
    rids[0] = M.RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)   # 键盘
    rids[1] = M.RAWINPUTDEVICE(0x0C, 0x01, RIDEV_INPUTSINK, hwnd)   # 消费键
    ok = u.RegisterRawInputDevices(rids, 2, ctypes.sizeof(M.RAWINPUTDEVICE))
    emit("Raw Input 注册: %s（键盘 + 消费键）" % ("成功" if ok else "失败"))
    emit("-" * 66)

    # 钩子线程
    import threading
    th = threading.Thread(target=hook_thread, daemon=True, name="hook")
    th.start()
    time.sleep(0.3)

    t0 = time.time()
    msg = wt.MSG()
    while True:
        # 带超时的消息循环
        r = u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
        if r:
            if msg.message == WM_QUIT:
                break
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        flush_unpaired()
        if time.time() - t0 > seconds:
            break
        time.sleep(0.005)

    # ---- 汇总 ----
    emit("")
    emit("=" * 66)
    emit("汇总")
    emit("=" * 66)
    emit("  这次抓到的键盘设备：")
    for hdev, nm in STATE["dev_cache"].items():
        emit("    hdev=%-6s %s -> %s"
             % (hdev, nm[:96], "遥控器" if is_remote_name(nm) else "其它"))
    if STATE["unpaired_raw"]:
        emit("")
        emit("  有 %d 条 Raw Input 没能配对上按键事件（正常：注入的键不会有）"
             % len(STATE["unpaired_raw"]))
    emit("")

    seen = {}
    for r in STATE["rows"]:
        if r["src"] != "遥控器":
            continue
        seen.setdefault((r["vk"], r["scan"]), r)
    if not seen:
        emit("  （一条遥控器事件都没抓到 —— 检查：")
        emit("    1) 遥控器是不是已经连上并唤醒（按一下任意键）")
        emit("    2) MiVoice 是不是也在跑（它也会注册 Raw Input，不冲突）")
        emit("    3) 日志 %s 里的原始十六进制能不能看出设备名" % OUT_PATH)
    else:
        emit("  遥控器按出来的键：")
        emit("  %-18s %-8s %-10s %s" % ("按键名", "vk", "scan", "flags"))
        for (vk, scan), r in sorted(seen.items()):
            emit("  %-18s 0x%02X     0x%02X      0x%02X"
                 % (r["name"], vk, scan, r["flags"]))
    emit("")
    emit("  其它设备按出来的键（对照用）")
    other = {}
    for r in STATE["rows"]:
        if r["src"] == "遥控器":
            continue
        other.setdefault((r["vk"], r["scan"]), r)
    for (vk, scan), r in sorted(other.items()):
        emit("    %-18s 0x%02X  scan=0x%02X  (%s)" % (r["name"], vk, scan,
                                                       r["src"]))
    emit("")
    emit("完整日志：%s" % OUT_PATH)
    emit("=" * 66)
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        emit("")
        emit("已中断")
