# -*- coding: utf-8 -*-
"""一键验证「遥控器 OK 键 -> 发送」+「菜单/主页/TV 键 -> 滚轮|退格」这条链路通没通。

用法：双击 tools\\verify-remote-send.bat

它做三件事：
  1. 检查 MiVoice 是不是正在运行、版本是不是 **v1.7 以上**
     （v1.3/v1.4 靠时间窗猜，v1.5 有「设备名读失败被永久缓存」的 bug，
       v1.6 有「注入回声污染判据、第一次之后每次都失败」的 bug）
  2. 盯着 mivoice.log，把你按遥控器时冒出来的新行实时打出来
  3. 最后给结论：到底是哪一环出了问题

🔴 v1.7 的判定方式（2026-10-30 实测钉死，和 v1.6 完全不同）：
   **吞掉「按下」、放行「抬起」**，用抬起那条 Raw Input 的 hDevice
   拿到这一下按键的**确定**设备归属。
     -> 日志里会出现「发送键已转换：... （来源 up）」= 精确路径，整条链路通了
     -> 出现「（来源 timeout）」= 抬起确认没等到，靠时间窗兜住了（也能用）
   v1.6 的「来源 raw」在 v1.7 里**不应该再出现**（那条路已经不存在了）。

🔴 v1.8 新增：滚轮键
   音量键 / 返回键在 Windows 上压根不上报，所以改用两个闲置键：
     菜单键（Apps, 0x5D）-> 鼠标向上滚   TV 键（0xC0）-> 鼠标向下滚
   日志标记是「滚轮键已转换：... （来源 up）」。

🔴 v1.9 新增：主页键
   主页键（Home, 0x24）-> Backspace（向左删一个字）。
   日志标记是「主页键已转换：... （来源 up）」。
   ⚠️ 测之前最好先打几个字在输入框里，不然删不出效果（日志照记，但人看不见）。

🔴 还要盯一条新日志：「Raw Input：忽略 hdev=0 的注入回声」
   —— 这是**正常**的（那是我们自己 SendInput 注入的按键）。
   但如果同一秒还跟着一条「Raw 其它设备按下发送键」，就说明回声没被挡住。
"""
import ctypes
import io
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


def _ver_tuple(v) -> tuple:
    """把 "1.8" / "v1.8" 转成 (1, 8) 方便比大小。

    解析不出来时返回 (99,) —— **宁可放过也不误判**：
    版本号是给人看的，格式一旦变了不该把用户的验收直接拦死。
    """
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("vV").split("."))
    except Exception:
        return (99,)


# 至少要 v1.7（v1.7 起「吞下、放开抬、按抬起那条 Raw Input 定性」才对）
MIN_VER = (1, 7)
LOG = os.path.join(MIVOICE_DIR, "mivoice.log")
STATE = os.path.join(MIVOICE_DIR, "mivoice.state")
MUTEX_NAME = "Local\\MiVoice_SingleInstance_v1"
ERROR_ALREADY_EXISTS = 183

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)   # 必须，否则 get_last_error 拿不到 183


def running() -> bool:
    """MiVoice 是否在跑（靠命名互斥体判断，最准）。"""
    k = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not k:
        return False
    try:
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return True
        return False
    finally:
        kernel32.CloseHandle(k)


def state():
    """读运行状态文件（版本 / PID）。读不到就返回空 dict。"""
    import json
    try:
        with io.open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def pid_alive(pid) -> bool:
    """PID 还活着吗。"""
    if not pid:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def tail_from(path, offset):
    """从 offset 开始读新行，返回 (新行列表, 新的 offset)。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            data = f.read()
            offset = f.tell()
    except Exception:
        return [], offset
    lines = [ln for ln in data.splitlines() if ln.strip()]
    return lines, offset


def last_version(path):
    """从日志里找最近一次启动的版本号（备用手段，状态文件优先）。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return ""
    ver = ""
    for ln in txt.splitlines():
        if "启动 |" in ln:
            for tok in ln.split():
                if tok.startswith("v") and any(c.isdigit() for c in tok):
                    ver = tok
    return ver


def main():
    print("=" * 66)
    print("遥控器 OK 键 -> 发送  一键验证")
    print("=" * 66)
    if not os.path.exists(LOG):
        print("找不到日志：%s" % LOG)
        input("\n按回车退出…")
        return 1

    ver = last_version(LOG)
    st = state()
    live_ver = st.get("ver")
    live_pid = st.get("pid")
    if st and not pid_alive(live_pid):
        print("[!] 状态文件是旧的（PID %s 已经不在了）—— "
              "上次是异常退出的。请重新启动 MiVoice。" % live_pid)
        st = {}
        live_ver = None
    if not running():
        print("[!] MiVoice 现在**没有运行**。")
        print("    请先双击：%s" % os.path.join(MIVOICE_DIR, "start-mivoice.bat"))
        print("    等托盘出现麦克风图标后再回来跑这个脚本。")
        input("\n按回车退出…")
        return 1
    if live_ver:
        print("[i] MiVoice 正在运行（PID %s，版本 v%s）"
              % (live_pid, live_ver))
    else:
        print("[i] MiVoice 正在运行（拿不到状态文件，按日志判断）")
    # 要求 v1.7 及以上：v1.3~v1.6 的判定逻辑都是坏的
    #   v1.3/v1.4 靠时间窗猜；v1.5 设备名读失败被永久缓存；
    #   v1.6 注入回声污染判据（第一次之后每次都失败）。
    if live_ver and _ver_tuple(live_ver) < MIN_VER:
        print("[!] 跑的是 v%s，太旧了（至少要 v1.7）—— 再测也是白测。" % live_ver)
        print("    请先在托盘图标上右键退出，再重新双击 start-mivoice.bat。")
        input("\n按回车退出…")
        return 1
    if not live_ver and ver and _ver_tuple(ver) < MIN_VER:
        print("[!] 日志里最后的版本是 %s，太旧了（至少要 v1.7）。" % ver)
        input("\n按回车退出…")
        return 1

    try:
        offset = os.path.getsize(LOG)
    except Exception:
        offset = 0

    print()
    print("请照着做（顺序别乱）：")
    print("  1) 鼠标点一下 WorkBuddy 的输入框，让光标在里面")
    print("  2) 按住遥控器的【语音键】说 2 秒钟，然后松手")
    print("  3) 等 1 秒，再按一次遥控器的【OK 键】")
    print("  4) 再按遥控器的【菜单键】【TV 键】各一下（验证滚轮映射，v1.8 新增）")
    print("     菜单键 = 鼠标向上滚（看上面的内容）")
    print("     TV 键  = 鼠标向下滚（看下面的内容）")
    print("  5) 最后按一下遥控器的【主页键】（验证退格映射，v1.9 新增）")
    print("     主页键 = Backspace（向左删一个字）")
    print("     ⚠️ 先在输入框里打几个字，不然删不出可见效果")
    print("  6) 盯着下面的窗口，什么都不用管")
    print()
    print("（最多等 120 秒；按 Ctrl+C 可以随时退出）")
    print("-" * 66)

    seen = {"f5_raw": 0, "ok_raw": 0, "converted_up": 0,
            "converted_timeout": 0, "converted_raw": 0,
            "replayed": 0, "other_raw": 0,
            "no_devname": 0, "devname_ok": 0, "watchdog": 0, "echo": 0,
            "scroll_up": 0, "scroll_down": 0, "backspace": 0}
    grace_until = 0.0
    t0 = time.time()
    while time.time() - t0 < 120:
        lines, offset = tail_from(LOG, offset)
        for ln in lines:
            print("  " + ln)
            if "Raw 遥控器语音键" in ln:
                seen["f5_raw"] += 1
            elif "Raw 遥控器按下发送键" in ln:
                seen["ok_raw"] += 1
            elif "Raw 其它设备按下发送键" in ln:
                seen["other_raw"] += 1
            elif "Raw 设备名读不出来" in ln:
                seen["no_devname"] += 1
            elif "Raw 设备名后来读出来了" in ln:
                seen["devname_ok"] += 1
            elif "忽略 hdev=0 的注入回声" in ln:
                seen["echo"] += 1
            elif "看门狗兜底" in ln:
                seen["watchdog"] += 1
            elif "发送键已转换" in ln:
                if "来源 up" in ln:
                    seen["converted_up"] += 1
                elif "来源 timeout" in ln:
                    seen["converted_timeout"] += 1
                elif "来源 raw" in ln:
                    seen["converted_raw"] += 1
            elif "补发原键" in ln:
                seen["replayed"] += 1
            elif "滚轮键已转换" in ln:
                if "向上滚" in ln:
                    seen["scroll_up"] += 1
                elif "向下滚" in ln:
                    seen["scroll_down"] += 1
            elif "主页键已转换" in ln:
                seen["backspace"] += 1
        got_main = (seen["ok_raw"] or seen["converted_up"]
                    or seen["converted_timeout"] or seen["converted_raw"]
                    or seen["scroll_up"] or seen["scroll_down"]
                    or seen["backspace"])
        if got_main and not grace_until:
            # 收到主结果后不要立刻收工 —— 再留 10 秒给滚轮键 / 主页键
            grace_until = time.time() + 10.0
            print("  ... 已收到结果，再等 10 秒顺手收【滚轮键 / 主页键】"
                  "（可以按遥控器的菜单键 / TV 键 / 主页键）...")
        elif grace_until and time.time() > grace_until:
            break
        time.sleep(0.25)

    print("-" * 66)
    print()
    print("【结论】")
    if seen["converted_up"]:
        print("  √ OK 键已转换成 ctrl+enter（精确路径：来源 up）")
        print("    -> 抬起那条 Raw Input 到了，设备归属**确定**是遥控器。")
        print("    -> 整条链路通了！")
    elif seen["converted_timeout"]:
        print("  ~ OK 键转换成功，但走的是兜底路径（来源 timeout）")
        print("    -> 说明抬起那条 Raw Input 没等到，靠「最近活动来自遥控器」兜住了。")
        print("    -> 也能用，但不如精确路径稳。")
    else:
        print("  × 没有「发送键已转换」记录 —— 这次没转成发送")
        if seen["replayed"]:
            print("    -> 反而被补发回车了，说明这一下被判成了「实体键盘」")
    if seen["converted_raw"]:
        print("  ! 出现了「来源 raw」—— v1.7 里这条路**已经不存在**")
        print("    -> 说明你跑的还是旧版本，或者日志是旧版本留下的")
    if seen["f5_raw"]:
        print("  √ 语音键（F5）被钩子吞掉时，抬起/首次按下照样有 Raw Input")
        print("    -> 说明遥控器设备被正确认出来了")
    else:
        print("  ? 没看到「Raw 遥控器语音键到达」")
        print("    -> 可能没按住语音键，或者遥控器设备名没读出来")
    if seen["replayed"]:
        print("  ! 有 %d 次被当成实体键盘补发了回车" % seen["replayed"])
        print("    -> 如果这正好是「按 OK 键」那一下，说明设备归属判错了")
    if seen["other_raw"]:
        print("  √ 有 %d 次识别出「这一下是实体键盘按的」并原样补发"
              % seen["other_raw"])
        print("    -> 设备归属判定是准的，实体键盘不会被误伤")
    if seen["no_devname"]:
        print("  ! 有 %d 次**设备名读不出来**（无法判定设备归属）"
              % seen["no_devname"])
        print("    -> v1.6 起就是「读失败不缓存、下次自动重试」。")
        print("       如果这里还在刷，说明这个 hDevice 一直读不出来")
        print("       （多半是蓝牙刚重连还没稳定，重启 MiVoice 即可）。")
    if seen["devname_ok"]:
        print("  √ 有 %d 次「设备名后来读出来了」-> 自动重试生效了"
              % seen["devname_ok"])
    if seen["echo"]:
        print("  √ 有 %d 次「忽略 hdev=0 的注入回声」—— 这是**正常**的" % seen["echo"])
        print("    -> 那是本程序自己 SendInput 注入的按键，"
              "v1.7 已经把它们挡在判据之外了")
    if seen["watchdog"]:
        print("  ~ 有 %d 次走了看门狗兜底（抬起确认没在 800ms 内到）"
              % seen["watchdog"])
        print("    -> 兜底也能把消息发出去，但不如精确路径（来源 up）稳")
    # ---- 🔴 v1.8：滚轮键 ----
    if seen["scroll_up"] or seen["scroll_down"]:
        print("  √ 滚轮键生效：向上滚 %d 次 / 向下滚 %d 次"
              % (seen["scroll_up"], seen["scroll_down"]))
        print("    -> 菜单键应向上滚、TV 键应向下滚（哪一个没动就看上面计数）")
    else:
        print("  ? 没看到「滚轮键已转换」")
        print("    -> 要么没按菜单键 / TV 键，要么这两个键没被识别成遥控器。")
        print("       注意：滚轮键**不需要**前台是 WorkBuddy，任何窗口都该生效。")
    # ---- 🔴 v1.9：主页键 -> Backspace ----
    if seen["backspace"]:
        print("  √ 主页键生效：删字 %d 次（Backspace）" % seen["backspace"])
        print("    -> 输入框里的字应该真的少了一个")
    else:
        print("  ? 没看到「主页键已转换」")
        print("    -> 要么没按主页键，要么这一下没被识别成遥控器。")
        print("       也可能 ini 里 backspace_enable=0 / backspace_key 写错了。")
    print()
    print("按键日志（看 SND / SCR / UPE / SWAL 标记）：%s"
          % os.path.join(MIVOICE_DIR, "mivoice_keys.log"))
    print("  SND = 发送键 / 退格键「按下」被吞；SCR = 滚轮键「按下」被吞；")
    print("  UPE = 映射键「抬起」放行（v1.7 的关键：抬起那条 Raw Input 才有设备归属）")
    print("完整日志：%s" % LOG)
    input("\n按回车退出…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
