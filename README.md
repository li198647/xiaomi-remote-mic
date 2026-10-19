# 遥控语音 (MiVoice) v1.0

把「小米蓝牙语音遥控器」（RC003 同款，VID 0x2717 / PID 0x32B8）的**语音键**变成
WorkBuddy 对话框的语音输入：按住说话，松手出字。

```
点进 WorkBuddy 输入框 → 按住遥控器语音键 → 说话 → 松手 → 文字出现在输入框
```

零依赖：只用 Python 标准库 ctypes 调 Win32 API（不需要 pywin32 / 任何第三方库）。

> 📁 **路径说明**：本文档写的是本机安装路径 `E:\workbuddywork\win10小修小补\mivoice\`。
> 如果你是 clone 下来的，把每个 `E:\workbuddywork\win10小修小补\mivoice\` 换成自己的克隆目录即可，
> 目录结构完全一致（`mivoice.py` / `mivoice.ini` / `start-mivoice.bat` / `tests/` / `vbcable/` 都在根目录）。

---

## 一、一次性安装（3 步，约 5 分钟）

### 1. 安装 VB-CABLE 虚拟声卡（必须）

VB-CABLE 把「遥控器的麦克风」变成 Windows 能识别的录音设备。

- 安装包已随本项目**整包解出**在：
  **`E:\workbuddywork\win10小修小补\mivoice\vbcable\`**
- 运行里面的 **`E:\workbuddywork\win10小修小补\mivoice\vbcable\VBCABLE_Setup_x64.exe`**
  （右键 → 以管理员身份运行。注意：inf/sys 驱动文件必须和解出的 exe 在同一目录，
  只拷 exe 单独运行会报 "Missing 'inf' file"）
- 按提示装完**重启一次电脑**（驱动安装必须重启）
- 重启后按一下遥控器语音键唤醒设备，运行
  `python E:\workbuddywork\win10小修小补\mivoice\mivoice.py --status`
  应能看到 `CABLE Input` / `CABLE Output`
- 安装包来源：官网 https://www.vb-audio.com/Cable/ 的 Pack45，
  已验证数字签名，SHA256 `734c35dfa6d98f48782a451633ceb471166ec70d60482fd89a1123d0ee3c4f41`

### 2. 把默认录音设备设为 CABLE Output

- 设置 → 系统 → 声音 → 输入 → 选 **CABLE Output (VB-Audio Virtual Cable)**
- （WorkBuddy 的语音输入跟浏览器一样，只认系统默认录音设备；
  如果 WorkBuddy 自己有麦克风选择器，在那里选 CABLE Output 也行）

### 3. 启动遥控语音

- 双击 `start-mivoice.bat`（不弹黑窗，托盘出现麦克风图标）
- 看日志确认就绪：托盘右键 → 查看运行日志，应有 `ATVV 已连接` + `ATVV 能力`

---

## 二、日常使用

1. **点进 WorkBuddy 的输入框**（让光标在对话框里 —— 这一步很关键，
   因为语音触发是发给「当前焦点窗口」的）
2. **按住遥控器语音键**，正常说话（单次上限约 60 秒，遥控器固件限制）
3. **松手** → 稍等半秒到一秒 → 文字出现在输入框里
4. **按遥控器 OK 键** → 直接发送（程序已把 OK 键自动换成 **Ctrl+Enter**，
   因为 WorkBuddy 的发送快捷键是 Ctrl+Enter，而 OK 键本身只发普通 Enter）

其他键：方向键上下=滚动对话（系统原生行为，本程序不拦截）。
返回键 / 音量键在 Windows 驱动层就被丢掉了，按了没反应属正常（见文末「已知限制」）。

托盘图标含义：🔵 蓝=待命 / 🔴 红=录音中 / ⚪ 灰=暂停或未连接。
悬停提示会显示「待命（成功 N 次）」。

---

## 三、排错

| 现象 | 原因 / 解法 |
|---|---|
| 按住语音键，WorkBuddy 麦克风按钮没反应 | 托盘右键 →「测试 Alt 触发」。若按钮有反应=触发链路 OK，问题在音频；若没反应，在 `mivoice.ini` 改 `alt_key` / `alt_mode`（见下文配置） |
| 有触发但没文字 | 默认录音设备不是 CABLE Output；或 VB-CABLE 没装/没重启 |
| 文字音质差、识别率低 | 调大 `gain`（默认 3.0，可到 5.0~8.0）；说话离遥控器近一点 |
| 遥控器休眠后失灵 | 遥控器 BLE 空闲会断链，**按任意键唤醒它会自动重连**（日志里会出现「遥控器按键唤醒」字样）。若按键后仍无反应：托盘右键 →「重新连接遥控器」 |
| 按 OK 键没发送 | ① 先确认 WorkBuddy 的发送快捷键真是 Ctrl+Enter：托盘右键 →「测试发送（ctrl+enter）」，光标在输入框里应能触发发送；② 日志里要能看到 `键盘钩子已安装` 和 `Raw Input 已注册`，缺哪个就是被安全软件拦了；③ 遥控器活动超过 `remote_active_ms`（默认 15 秒）也会不转 —— 按一下语音键或方向键刷新即可 |
| 想退出程序 | 托盘右键 → 退出；或热键 **Ctrl+Shift+M**（Ctrl+Alt 系列基本都被占了，实测 M/V/O/Q/X/Z/L 全部占用） |
| 托盘图标被折叠进「^」 | 点开「^」把麦克风图标拖到任务栏上，系统会记住 |

先跑 `python mivoice.py --probe`：不装 VB-CABLE 也能验证「遥控器麦克风能不能收到声音」
（会生成 `probe.wav`，播放听听）。

---

## 四、配置（mivoice.ini，改完重启程序生效）

| 键 | 默认 | 说明 |
|---|---|---|
| `alt_key` | `alt` | 触发 WorkBuddy 语音输入的键。WorkBuddy 按钮提示是「长按 Alt」，所以默认 alt |
| `alt_mode` | `hold` | `hold`=按住说话（会话期间一直按住，推荐）；`tap`=点按切换（开始点一下、结束点一下）—— 如果 WorkBuddy 是「切换」式而非「按住」式，改成 tap |
| `alt_tap_ms` | `60` | tap 模式每次按住时长 |
| `alt_pre_ms` | `150` | hold 模式先按多久再等音频（有些框架要长按才触发） |
| `gain` | `3.0` | 音频增益，遥控器电平偏低 |
| `enabled` | `1` | 0=暂停映射（托盘菜单可切） |
| `focus_workbuddy` | `0` | 1=每次说话时自动把 WorkBuddy 窗口提到前台 |
| `focus_title` | `WorkBuddy` | 聚焦用的窗口标题匹配串 |
| `output_device` | `CABLE Input` | waveOut 目标设备名（子串匹配） |
| `ok_to_send` | `1` | 0=关闭「OK 键→发送」（关闭后 OK 键就是普通 Enter） |
| `send_keys` | `ctrl+enter` | OK 键要换成的组合键。支持 `ctrl` / `shift` / `alt` / `win` + 单键，用 `+` 连接，例如 `ctrl+enter`、`shift+enter`、`enter` |
| `ok_send_title` | `WorkBuddy` | 只有前台窗口标题包含这个串时才转换（空=任意窗口，不建议） |
| `remote_active_ms` | `15000` | 遥控器多少毫秒内活动过才算「这一下是遥控器按的」 |
| `hotkey_quit` | `Ctrl+Shift+M` | 退出热键 |
| `autostart` | `0` | 开机自启（托盘菜单可切） |

---

## 四之二、OK 键是怎么变成 Ctrl+Enter 的（为什么不影响实体键盘）

难点：**低级键盘钩子分不出按键来自哪台设备**。直接在钩子里拦 Enter 会把实体键盘
一起废掉。所以用两条链路交叉判定：

```
Raw Input（WM_INPUT）── 带 hDevice，能查到设备路径里的 VID&012717
      │                  只记录「遥控器最近一次按键的时间」，不拦任何东西
      ▼
低级键盘钩子（WH_KEYBOARD_LL）── 看到 Enter 时问六个问题，全过才转换：
      ① ok_to_send 开着？  ② 程序没被暂停？
      ③ 前台窗口标题含 WorkBuddy？
      ④ 遥控器在 remote_active_ms 内活动过？
      ⑤ 输入法没在组词（否则 Enter 要留给它选词）
      ⑥ 当前没按着 Ctrl/Shift/Alt/Win（用户自己按组合键时不掺和）
      └─ 全过 → 吞掉原 Enter，SendInput 一次发出 Ctrl+Enter
```

工程细节（都是踩过的坑）：

- **钩子跑在独立线程**，回调里不写日志、不调可能阻塞的 API —— 低级钩子一旦卡住，
  整个系统的键盘都会停止响应
- **输入法状态由独立线程（每 200ms）提前算好**放进标志位，钩子只读内存，
  避免跨线程调 `ImmGetContext` 被目标程序拖住
- **注入事件带 `LLKHF_INJECTED` 标志直接放行**，防止自己发的 Ctrl+Enter 被自己再转一次（死循环）
- **长按 350ms 内只转一次**，避免系统自动重复连发
- 遥控器按键唤醒 ATVV 时**只做无损体检**（枚举设备接口是否还在），
  绝不 close 句柄 —— 按键事件和 ATVV 通知几乎同时到达，close 会把这关键一帧丢掉

---

## 五、它是怎么工作的

```
遥控器语音键按下
   ├─(键盘路径) 发一个 F5 —— 本程序故意不拦（低级钩子分不清是哪台设备，
   │             拦了会把实体键盘的 F5 一起废掉），F5 落在输入框里无害
   └─(音频路径) BLE ATVV 服务推麦克风音频
          → ctypes 直连 Windows 蓝牙 GATT 收通知
          → IMA ADPCM 解码（16kHz 单声道，120 字节/帧，高 nibble 在前）
          → 线性增益 + 限幅
          → waveOut 写进 VB-CABLE 的 CABLE Input
          → CABLE Output（默认麦克风）→ WorkBuddy 语音输入
   同时：控制码里看到「流开始」→ SendInput 模拟按住 Alt（触发 WorkBuddy 收音）
         「流结束」→ 等音频播完 → 松开 Alt（WorkBuddy 收尾转写）
```

关键取舍：**触发不看 F5 键盘事件，只看 ATVV 音频会话的起止**，所以实体键盘
F5 完全不受影响，也不需要装 Interception 之类的过滤驱动。

---

## 六、文件说明

| 文件 | 作用 |
|---|---|
| `mivoice.py` | 主程序（单文件，纯 ctypes 零依赖） |
| `start-mivoice.bat` | 启动器（pythonw 无窗口启动） |
| `mivoice.ini` | 配置（首次运行自动生成） |
| `mivoice.log` | 运行日志（排错就看它） |
| `vbcable/` | VB-CABLE 安装包（官方原版 + 签名校验值） |

命令行：`--probe` 录一段验证麦克风 / `--alt-test` 测触发 / `--status` 环境自检 /
`--selftest N` 跑 N 秒自动退出 / `--debug` 详细日志。

---

## 已知限制（v1.0）

- 语音键自带的 F5 会照常发给焦点窗口（输入框里无害；如果哪天发现它在别的软件里
  碍事，可以加「会话期间屏蔽 F5」的钩子，见 mivoice.ini 将来的 `suppress_f5` 项）
- **OK 键转换的副作用**：遥控器活动过（默认 15 秒内）且在 WorkBuddy 里时，
  **实体键盘的 Enter 也会被一起换成 Ctrl+Enter**。这是「用户态分不出设备」的必然代价；
  嫌碍事就按托盘菜单暂停，或把 `remote_active_ms` 调小到 3000
- 返回键 / 音量键在 Windows 驱动层就被丢弃，任何用户态方案都拿不到，与本程序无关
- 单次录音上限约 60 秒（遥控器固件限制）
