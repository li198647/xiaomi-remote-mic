# -*- coding: utf-8 -*-
"""一键核对「WorkBuddy 语音输入只绿一下就停」这个修复有没有生效。

用法（纯 cmd，双击也行）：
    python E:\\workbuddywork\\win10小修小补\\tools\\check_voice_fix.py

只看**最近一次会话**，不会被历史上失败过的记录干扰。看三处证据：
  1. WorkBuddy 渲染进程日志 -> 有 altHotkey hold-start，且没有 combo while holding
  2. WorkBuddy 渲染进程日志 -> 有 stream final received length=N（N>0 = 真的出字了）
  3. MiVoice 键盘取证日志    -> 遥控器的 F5 事件被标成 SWAL（= 被 MiVoice 吞掉了）

退出码：0 = 三项都通过；1 = 有项没过。
"""
import os
import re
import sys

LOGDIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "logs")
RENDER_LOGS = [os.path.join(LOGDIR, "renderer.log"),
               os.path.join(LOGDIR, "renderer.old.log")]
KEYLOG = r"E:\workbuddywork\win10小修小补\mivoice\mivoice_keys.log"
MVLOG = r"E:\workbuddywork\win10小修小补\mivoice\mivoice.log"

VOICE_KEYS = ("altHotkey", "stream final", "handleStop", "stream ready",
              "pcm append")


def read_lines(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception as e:
        print("  读取失败 %s: %r" % (path, e))
        return None


def collect_render_lines():
    """把 renderer.log + renderer.old.log 的语音相关行汇总（renderer.log 在前）。"""
    out = []
    for p in RENDER_LOGS:
        lines = read_lines(p)
        if lines is None:
            print("  （找不到 %s）" % p)
            continue
        for ln in lines:
            if any(k in ln for k in VOICE_KEYS):
                out.append((os.path.basename(p), ln.rstrip()))
    return out


def last_session(rows):
    """只取最近一次会话：最后一次 altHotkey keydown 之后的所有行。"""
    idx = None
    for i, (_, ln) in enumerate(rows):
        if "altHotkey keydown" in ln:
            idx = i
    if idx is None:
        return rows          # 没有 keydown 就全给（可能是老格式）
    return rows[idx:]


def last_f5_group(lines):
    """从键盘日志里取最近一次 F5 按下。

    遥控器语音键的一次按住 = 一条 kind=down 开头 + 一堆 sysdown 自动重复 + 一条 up。
    所以「最后一条 down 到文件末尾」就是最近那一次。
    """
    f5 = [ln.rstrip() for ln in lines if re.search(r"vk=0x74", ln)]
    if not f5:
        return []
    start = 0
    for i, ln in enumerate(f5):
        if re.search(r"\bdown\s+", ln) and "sysdown" not in ln:
            start = i
    return f5[start:]


def main():
    print("=" * 70)
    print("语音输入修复核对（MiVoice v1.2 / F5 拦截）")
    print("=" * 70)

    ok = True

    # ---- 1 & 2：WorkBuddy 侧 ----
    print("\n[1] WorkBuddy 渲染进程日志 —— 最近一次会话")
    rows = collect_render_lines()
    if not rows:
        print("  !! 没有找到任何语音事件 —— 可能还没测过")
        ok = False
        sess = []
    else:
        sess = last_session(rows)
        for name, ln in sess[-25:]:
            print("  %-18s %s" % (name, ln[:300]))

        hold = [r for r in sess if "hold-start" in r[1]]
        combo = [r for r in sess if "combo while holding" in r[1]]
        aborted = [r for r in sess if "aborted(combo)" in r[1]]
        finals = [r for r in sess if "stream final received" in r[1]]

        print("\n  altHotkey hold-start       : %d 次" % len(hold))
        print("  combo while holding -> stop : %d 次  （= 被 F5 自动重复打断）"
              % len(combo))
        print("  keyup aborted(combo)        : %d 次" % len(aborted))
        if not hold:
            print("  !! 没有 hold-start —— 录音压根没真正开始")
            ok = False
        else:
            print("  OK  hold-start 已触发")
        if combo or aborted:
            print("  !! 仍然出现 combo 打断 —— F5 没被完全吞掉")
            ok = False
        else:
            print("  OK  没有 combo 打断")

        print("\n[2] 最终识别结果")
        if not finals:
            print("  !! 没有 stream final received —— 没出字")
            ok = False
        else:
            for _, ln in finals[-3:]:
                print("  " + ln[:300])
            m = re.search(r"length=(\d+)", finals[-1][1])
            if not m or int(m.group(1)) <= 0:
                print("  !! length <= 0，没识别到内容")
                ok = False
            else:
                print("  OK  识别到 %s 个字符" % m.group(1))

    # ---- 3：MiVoice 侧 ----
    print("\n[3] MiVoice 键盘取证日志 —— 最近一次遥控器语音键按下")
    lines = read_lines(KEYLOG)
    if lines is None:
        print("  !! 找不到 %s" % KEYLOG)
        ok = False
    else:
        grp = last_f5_group(lines)
        if not grp:
            print("  （还没测过 —— 没有 F5 事件记录）")
        else:
            sw = [ln for ln in grp if "SWAL" in ln]
            pas = [ln for ln in grp if "SWAL" not in ln]
            print("  本次 F5 事件数 : %d" % len(grp))
            print("  被吞(SWAL)     : %d" % len(sw))
            print("  放行           : %d" % len(pas))
            for ln in grp[-14:]:
                print("  " + ln)
            if not sw:
                print("  !! 一个 F5 都没被吞 —— 拦截没生效")
                ok = False
            else:
                print("  OK  F5 被 MiVoice 吞掉了，WorkBuddy 收不到")

    # ---- 附：MiVoice 自己的会话日志 ----
    mv = read_lines(MVLOG)
    if mv:
        print("\n[附] MiVoice 会话日志（最近 6 行）")
        for ln in mv[-6:]:
            print("  " + ln.rstrip())

    print("\n" + "=" * 70)
    print("结论：%s" % ("✅ 三项全过 —— 修复生效" if ok
                        else "❌ 仍有问题，看上面标记 !! 的地方"))
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
