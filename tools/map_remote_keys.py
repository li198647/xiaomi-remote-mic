# -*- coding: utf-8 -*-
"""小米蓝牙遥控器 —— 引导式按键标定器

为什么需要它？
    遥控器的键在 Windows 里会变成一组「虚拟键码（VKey）」。大部分能对上名字
    （F5 / Enter / Home / 方向键…），但总有几个是 **没有标准名字的怪值**
    （比如 0xC0、0xFF）—— 只能靠「你按的是哪个物理键」来反推。
    所以本工具反过来做：**我点名，你按键**，把「键名 ↔ VKey」直接对齐。

用法：
    双击  E:\\workbuddywork\\win10小修小补\\tools\\map-remote-keys.bat
    或命令行：
        python E:\\workbuddywork\\win10小修小补\\tools\\map_remote_keys.py

流程：
    1. 工具依次点名每个物理键（「现在请按【语音键】」）
    2. 你按一下那个键 —— 工具立刻记下它的 VKey / 扫描码 / 来自哪个设备
    3. 不想标某个键 → 按**实体键盘的 N 键**跳过（等超时也会自动跳）
    4. 全程结束后：
         · 打印完整对照表
         · 存一份 remote_keymap.ini（名字 = VKey）
         · 问你「把哪个键设成发送键」，回车可改成直接写进 mivoice.ini

原理：
    **Raw Input（WM_INPUT）一条链路就够了**，不用低级钩子：
    RAWINPUT 里 hDevice 能认出「是不是遥控器」，VKey 在偏移 30，
    MakeCode 在偏移 24，Message 在偏移 32。全是现成的。

    ⚠️ 只有「键盘页」（Usage Page 0x01）的键才有 VKey/MakeCode。
       消费键（Usage Page 0x0C）走的是 RAWHID，字段布局完全不同 —— 本工具
       只处理键盘页。好在 F5 / Enter / Home / 方向键 / 菜单键 / 那两个怪值
       全都在键盘页上。
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

OUT_INI = os.path.join(HERE, "remote_keymap.ini")
MIVOICE_INI = os.path.join(MIVOICE_DIR, "mivoice.ini")

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1

VK_N = 0x4E           # 实体键盘的 N —— 拿来当「跳过这一项」

# RAWINPUT 里 RAWKEYBOARD 的字段偏移（x64）
OFF_MAKECODE = 24
OFF_VKEY = 30
OFF_MESSAGE = 32

# ---- 要标定的物理键清单（小米蓝牙语音遥控器）----
# 顺序按遥控器上从���到下的排布，方便你照着按。
REMOTE_BUTTONS = [
    "语音键（按住说话那个）",
    "OK / 确认键（中间那个）",
    "主页键（房子图标）",
    "菜单键（三条横线）",
    "返回键（箭头往左）",
    "音量 +",
    "音量 -",
    "静音键",
    "上方向键",
    "下方向键",
    "左方向键",
    "右方向键",
    "TV 键",
    "功能键（长按/短按能区分的那个）",
    "电源键",
]

TIMEOUT = 12.0        # 每一项等多久（秒）
COOLDOWN = 0.6        # 记完一项后的冷却，挡掉长按的自动重复

LOG_FH = None
EVENTS = []           # raw 事件队列
DEV_CACHE = {}


def emit(line=""):
    print(line, flush=True)
    try:
        if LOG_FH:
            LOG_FH.write(line + "\n")
            LOG_FH.flush()
    except Exception:
        pass


def dev_name(hdev):
    if hdev in DEV_CACHE:
        return DEV_CACHE[hdev]
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
    except Exception:
        pass
    DEV_CACHE[hdev] = name
    return name


def is_remote_name(name):
    n = (name or "").lower()
    return "vid&012717" in n or "vid_012717" in n


def handle_raw_input(lparam):
    """把一个 WM_INPUT 解析成事件塞进队列。"""
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
        if M.struct_unpack_i(raw, 0) != RIM_TYPEKEYBOARD:
            return                       # 消费键走 RAWHID，布局不同，跳过
        hdev = M.struct_unpack_q(raw, 8)
        makecode = M.struct_unpack_i(raw, OFF_MAKECODE) & 0xFFFF
        vkey = M.struct_unpack_i(raw, OFF_VKEY) & 0xFFFF
        wmsg = M.struct_unpack_i(raw, OFF_MESSAGE)
        if wmsg not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return
        name = dev_name(hdev)
        EVENTS.append({
            "t": time.time(), "hdev": hdev, "dev": name,
            "remote": is_remote_name(name), "vkey": vkey,
            "makecode": makecode, "msg": wmsg,
        })
    except Exception as e:
        emit("  !! Raw Input 解析异常: %r" % (e,))


def pop_event():
    if EVENTS:
        return EVENTS.pop(0)
    return None


# =====================================================================
# 主窗口 + 消息循环
# =====================================================================
def _wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        handle_raw_input(lp)
        return 0
    if msg == WM_KEYDOWN and wp == 0x1B:      # Esc -> 退出
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


WNDPROC = M.WNDPROC
_wndproc_ref = WNDPROC(_wndproc)


def wait_one(timeout):
    """等一次「遥控器按键」。

    返回 (vkey, makecode, devname) 表示抓到了；
    返回 None 表示跳过（按了 N / 超时 / Esc）。
    """
    t0 = time.time()
    cooldown = 0.0
    msg = wt.MSG()
    while time.time() - t0 < timeout:
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == WM_QUIT:
                return None
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        now = time.time()
        if now < cooldown:
            time.sleep(0.01)
            continue
        ev = pop_event()
        if ev is None:
            time.sleep(0.005)
            continue
        if not ev["remote"]:
            # 实体键盘事件：只有 N 键算「跳过这一项」，其它忽略
            # （这样你在别处打字不会打断标定）
            if ev["vkey"] == VK_N:
                return None
            continue
        cooldown = now + COOLDOWN
        return ev["vkey"], ev["makecode"], ev["dev"]
    return None


def selftest():
    """不用按遥控器也能验证解析逻辑。"""
    assert M.struct_unpack_i(b"\x03\x00\x00\x00", 0) == 3
    # 模拟一个 RAWINPUT 键盘数据段：makecode / vkey / message
    raw = bytearray(40)
    raw[24:26] = (0x3F).to_bytes(2, "little")
    raw[30:32] = (0x74).to_bytes(2, "little")
    raw[32:36] = (1).to_bytes(4, "little")
    assert M.struct_unpack_i(raw, OFF_MAKECODE) & 0xFFFF == 0x3F
    assert M.struct_unpack_i(raw, OFF_VKEY) & 0xFFFF == 0x74
    assert M.struct_unpack_i(raw, OFF_MESSAGE) == 1
    assert is_remote_name(r"\\?\HID#VID&012717_PID&32B8#8&1f2e3d4c&0&0000")
    assert not is_remote_name(r"\\?\HID#VID_046D&PID_C31C&MI_00#8&2a3b4c5d&0&0000")
    assert not is_remote_name("")
    print("标定器自检：全部通过")
    return 0


def write_map(results):
    """把结果写成 remote_keymap.ini（名字 = VKey）。"""
    try:
        with open(OUT_INI, "w", encoding="utf-8") as f:
            f.write("# 小米遥控器键位表（由 map_remote_keys.py 自动生成）\n")
            f.write("# 格式：<键名> = <VKey 十六进制>\n")
            f.write("# send_key 填这里面的值就行\n\n")
            for name, info in results:
                if info:
                    f.write("%s = 0x%02X\n" % (name, info[0]))
        emit("键位表已写入：%s" % OUT_INI)
    except Exception as e:
        emit("  !! 写键位表失败: %r" % (e,))


def apply_send_key(vkey):
    """把 send_key 写进 mivoice.ini（只替换那一行，别的不动）。"""
    try:
        with open(MIVOICE_INI, "r", encoding="utf-8") as f:
            lines = f.readlines()
        out = []
        done = False
        for ln in lines:
            low = ln.strip().lower()
            if low.startswith("send_key") and "=" in low and not done:
                out.append("send_key = 0x%02X\n" % vkey)
                done = True
            else:
                out.append(ln)
        if not done:
            out.append("send_key = 0x%02X\n" % vkey)
        with open(MIVOICE_INI, "w", encoding="utf-8") as f:
            f.writelines(out)
        emit("已把 send_key = 0x%02X 写进 %s" % (vkey, MIVOICE_INI))
        emit("⚠️ 需要重启 MiVoice 才生效。")
    except Exception as e:
        emit("  !! 改 ini 失败: %r" % (e,))


def main():
    global LOG_FH
    try:
        LOG_FH = open(os.path.join(HERE, "remote_keymap.log"), "w",
                      encoding="utf-8")
    except Exception:
        LOG_FH = None

    emit("=" * 66)
    emit("小米遥控器 —— 引导式按键标定")
    emit("=" * 66)
    emit("我点名，你按键。每一项等 %d 秒，按**实体键盘的 N 键**可以跳过。" % int(TIMEOUT))
    emit("遥控器不用一直醒着，按哪个按哪个就行。")
    emit("随时按 Esc 退出。")
    emit("-" * 66)

    hinst = k.GetModuleHandleW(None)
    clsname = "RemoteKeyMapWnd"
    wc = M.WNDCLASSW()
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = hinst
    wc.lpszClassName = clsname
    u.RegisterClassW(ctypes.byref(wc))
    hwnd = u.CreateWindowExW(0, clsname, "RemoteKeyMap", 0, 0, 0, 0, 0,
                              None, None, hinst, None)
    if not hwnd:
        emit("！！窗口创建失败")
        return 2

    rids = (M.RAWINPUTDEVICE * 1)()
    rids[0] = M.RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)  # 键盘页
    ok = u.RegisterRawInputDevices(rids, 1, ctypes.sizeof(M.RAWINPUTDEVICE))
    emit("Raw Input 注册: %s" % ("成功" if ok else "失败"))
    if not ok:
        emit("！！拿不到按键事件，先关掉别的键盘监听程序再试。")
        return 3
    emit("-" * 66)

    results = []
    for idx, name in enumerate(REMOTE_BUTTONS, 1):
        emit("")
        emit("[%d/%d] 现在请按遥控器上的【%s】"
             % (idx, len(REMOTE_BUTTONS), name))
        got = wait_one(TIMEOUT)
        if got is None:
            emit("      -> 跳过")
            results.append((name, None))
            continue
        vkey, makecode, dev = got
        emit("      -> 收到！ VKey=0x%02X   scan=0x%02X" % (vkey, makecode))
        results.append((name, (vkey, makecode)))

    # ---- 汇总 ----
    emit("")
    emit("=" * 66)
    emit("键位对照表")
    emit("=" * 66)
    emit("  %-34s %-10s %s" % ("物理键", "VKey", "扫描码"))
    got_count = 0
    for name, info in results:
        if info:
            got_count += 1
            emit("  %-34s 0x%02X       0x%02X" % (name, info[0], info[1]))
        else:
            emit("  %-34s %-10s %s" % (name, "--", "(没抓到：可能这个键被系统层"
                                        "丢弃了，或遥控器没上报)"))
    emit("")
    emit("  抓到 %d / %d 个键" % (got_count, len(results)))
    write_map(results)

    # ---- 问要不要设成发送键 ----
    emit("")
    emit("=" * 66)
    emit("把哪个键设成「发送键」？（= 按它就等于按 Ctrl+Enter）")
    emit("  · 直接输入上面表格里的键名，例如「OK / 确认键（中间那个）」")
    emit("  · 想用 VKey 就直接填 0x??，例如 0x%02X" % (results[0][1][0]
                                                     if results[0][1] else 0x0D))
    emit("  · 回车 = 这次不改")
    emit("-" * 66)
    try:
        ans = input("> ").strip()
    except Exception:
        ans = ""
    if not ans:
        emit("好，不改 send_key。")
        return 0

    # 名字匹配
    target = None
    for name, info in results:
        if info and (ans == name or ans in name or name in ans):
            target = info[0]
            emit("匹配到【%s】-> 0x%02X" % (name, target))
            break
    if target is None and ans.lower().startswith("0x"):
        try:
            target = int(ans, 16)
        except ValueError:
            target = None
    if target is None:
        emit("没认出「%s」，send_key 没改。" % ans)
        return 0
    apply_send_key(target)
    emit("")
    emit("完整日志：%s" % os.path.join(HERE, "remote_keymap.log"))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        emit("")
        emit("已中断")
