# 遥控语音 (MiVoice) v1.7

把「小米蓝牙语音遥控器」（RC003 同款，VID 0x2717 / PID 0x32B8）的**语音键**变成
WorkBuddy 对话框的语音输入：按住说话，松手出字。

```
点进 WorkBuddy 输入框 → 按住遥控器语音键 → 说话 → 松手 → 文字出现在输入框
```

零依赖：只用 Python 标准库 ctypes 调 Win32 API（不需要 pywin32 / 任何第三方库）。

> 📁 **路径说明**：本文档写的是本机安装路径 `E:\workbuddywork\win10小修小补\mivoice\`。
> 如果你是 clone 下来的，把每个 `E:\workbuddywork\win10小修小补\mivoice\` 换成自己的克隆目录即可，
> 目录结构完全一致（`mivoice.py` / `mivoice.ini` / `start-mivoice.bat` / `tests/` /
> `tools/` / `vbcable/` 都在根目录）。文中出现的 `tools\xxx` 都是相对路径，指仓库里的
> `<克隆目录>\mivoice\tools\`；本机实际路径是 `E:\workbuddywork\win10小修小补\mivoice\tools\`。

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
| 按 OK 键没发送 | ① 先确认 WorkBuddy 的发送快捷键真是 Ctrl+Enter：托盘右键 →「测试发送（ctrl+enter）」，光标在输入框里应能触发发送；② 跑 **`tools\verify-remote-send.bat`**，它会实时盯着日志并直接给结论（哪一环断了）；③ 日志里要能看到 `键盘钩子已安装` 和 `Raw Input 已注册`，缺哪个就是被安全软件拦了 |
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
| `send_key` | `enter` | **触发发送的物理键**（就是遥控器上哪个键）。可写 `enter` / `space` / `f1`…`f12` / `up` / `home` / `apps` / `0xC0` … 详见上面的「遥控器完整键位表」 |
| `send_keys` | `ctrl+enter` | **实际发出去的组合键**（对应 WorkBuddy 的发送设置）。支持 `ctrl` / `shift` / `alt` / `win` + 单键，用 `+` 连接 |
| `ok_send_title` | `WorkBuddy` | 只有前台窗口标题包含这个串时才转换（空=任意窗口，不建议） |
| `send_confirm_ms` | `800` | 🔴 v1.7：**抬起之后**等多久去问「抬起那条 Raw Input 到了没」。超时走兜底 + 补发原键。（v1.6 是从「按下」算 400ms，人按住回车稍久一点就会误超时） |
| `remote_active_ms` | `10000` | 已废弃，不再参与判定（保留只为兼容老配置文件） |
| `send_fallback_ms` | `30000` | **兜底窗口**：Raw Input 迟迟不来时，「最近 30 秒内用过遥控器」就当成遥控器按的。说话→按 OK 的间隔常常超过 10 秒，所以这个窗口要给足 |
| `hide_remote_f5` | `1` | 1=录音期间吞掉遥控器的语音键(F5)，否则 WorkBuddy 会误判成组合键 |
| `hotkey_quit` | `Ctrl+Shift+M` | 退出热键 |
| `autostart` | `0` | 开机自启（托盘菜单可切） |

---

## 四之二、OK 键是怎么变成 Ctrl+Enter 的（v1.5 重写 → v1.6 修根因 → v1.7 换架构）

### 遥控器完整键位表（实测，2026-10-30）

用 `tools\scan_remote_keys.py` 把遥控器每个键各按一遍，实测得到的对照表
（`scan` = 硬件扫描码，`flags` = 原始事件的扩展位）：

| 遥控器上的键 | Windows 虚拟键码 | scan | flags | `send_key` 里怎么写 |
|---|---|---|---|---|
| **语音键**（按住说话） | `0x74` = **F5** | 0x3F | 0x00 / 0x20 | `f5` |
| **OK / 确认键** | `0x0D` = **Enter** | 0x1C | 0x00 | `enter` ← **默认发送键** |
| 上方向键 | `0x26` | 0x48 | 0x01 | `up` |
| 下方向键 | `0x28` | 0x50 | 0x01 | `down` |
| 左方向键 | `0x25` | 0x4B | 0x01 | `left` |
| 右方向键 | `0x27` | 0x4D | 0x01 | `right` |
| 主页键 | `0x24` = Home | 0x47 | 0x01 | `home` |
| 菜单键 | `0x5D` = Apps | 0x5D | 0x01 | `apps` |
| **TV 键** | `0xC0` | 0x29 | 0x00 | `0xC0`（Windows 没给标准名，但已确认是 TV 键） |
| 未知键 B | `0xFF` | 0x5E | 0x01 | `0xFF`（同上，物理身份待标定） |

🔴 **三个关键结论**：

1. **遥控器的 OK 键在 Windows 上就是一个普通 Enter**（`vk=0x0D, scan=0x1C, flags=0x00`），
   跟实体键盘的 Enter **完全同码**。所以「分出这一下是谁按的」**只能靠设备身份**
   （Raw Input 的 `hDevice` → 设备路径里的 `VID&012717`），光看 vk/扫描码/标志位没戏。
2. `0xC0` = **TV 键**（木木 2026-10-30 实机确认）；`0xFF` 还没有物理名字。
   想确认 `0xFF` 是哪个键，跑
   **`tools\map-remote-keys.bat`** ——
   它会一个一个点名让你按，按下就记录 VKey，并可顺手把发送键写进 `mivoice.ini`。
3. **音量键 / 返回键根本不在表里** —— 它们按下去在 Windows 上**一点反应都没有**，
   连低级键盘钩子都收不到。详见下面「已知限制」。

### v1.7 的判定链：吞下 → 放开抬起 → 用抬起的设备定性（当前版本）

```
低级键盘钩子（WH_KEYBOARD_LL）—— 同步先跑，能拿到 vk/扫描码，但分不出设备
      │  看到 Enter(=发送键) 且前置条件满足
      │  ① ok_to_send 开着？② 程序没暂停？
      │  ③ 前台窗口标题含 WorkBuddy？④ 输入法没在组词？
      │  ⑤ 用户自己没按着 Ctrl/Shift/Alt/Win？
      ├─ 按下 → **吞掉**（前台程序收不到，不会插入换行），登记一条 pending
      └─ 抬起 → **放行** ←🔴 v1.7 的关键
                     │
                     ▼
Raw Input（WM_INPUT）—— 异步后处理，带 hDevice，能认出遥控器
      **抬起那一条**到了 → 查设备路径是不是 VID&012717
         ├─ 是遥控器 → SendInput 发出 Ctrl+Enter（来源 up，**精确**）
         └─ 不是    → **把刚才吞掉的回车原样补发回去**（来源 up）
                     │
                     ▼
看门狗（100ms 定时器）—— 抬起后超过 send_confirm_ms 还没等到确认
      ├─ 最近一次活动来自遥控器 → 当成遥控器按的，发出 Ctrl+Enter（来源 timeout）
      └─ 不是 → 补发原键（来源 timeout）
```

> 🔴 **为什么「按下」不给 Raw Input、「抬起」能给？**
> Windows 的 Raw Input 是由 user32 在**钩子链放行之后**才生成的 ——
> 钩子返回非 0（吞掉），user32 就不会为这一下生成 WM_INPUT。
> 按下被吞 → 没有 WM_INPUT；抬起放行 → **有 WM_INPUT**，
> 而它带的 `hDevice` 就是**这一下按键**按下去的设备。
> 实测见下面「v1.7 的架构革新」。

### v1.5 的判定链：先吞 → 异步按设备确认 → 不对就补发（❌ 已被实测证伪，仅存档）

```
低级键盘钩子（WH_KEYBOARD_LL）—— 同步先跑，能拿到 vk/扫描码，但分不出设备
      │  看到 Enter(=发送键) 且前置条件满足
      │  ① ok_to_send 开着？② 程序没暂停？
      │  ③ 前台窗口标题含 WorkBuddy？④ 输入法没在组词？
      │  ⑤ 用户自己没按着 Ctrl/Shift/Alt/Win？
      └─ 全过 → **先吞掉这一下**，登记一条 pending
                     │
                     ▼
Raw Input（WM_INPUT）—— 异步后处理，带 hDevice，能认出遥控器
      同一 vk 的原始事件到了 → 查设备路径是不是 VID&012717
         ├─ 是遥控器 → SendInput 发出 Ctrl+Enter（来源 raw，精确）
         └─ 不是    → **把刚才吞掉的回车原样补发回去**（来源 raw）
                     │
                     ▼
看门狗（100ms 定时器）—— pending 超过 send_confirm_ms 还没等到原始事件
      ├─ 最近一次原始事件来自遥控器 → 当成遥控器按的，发出 Ctrl+Enter（来源 timeout）
      └─ 不是 → 补发原键（来源 timeout）
```

> 🔴🔴 **这段架构在 Windows 上结构性地走不通**（2026-10-30 实测钉死）：
> 被钩子吞掉的按键，**Raw Input 一条都不会到** —— 所以第 2 步永远等不到，
> 100% 掉进看门狗兜底。v1.4/v1.5 日志里那些「来源 raw」其实是
> **别的**未吞按键的事件被 FIFO 误配给 pending 的假象
> （证据：它们总是成对出现「来源 raw 转换」+「来源 raw 补发」）。

🔴 **为什么 v1.3 / v1.4 一直不生效**（这一条是 v1.5 的核心修复）：

**根因是一个 4 字节 vs 2 字节的读取 bug。**

`RAWKEYBOARD` 里的 `MakeCode` 和 `VKey` 都是 **USHORT（2 字节）**，但 v1.4
用 `struct_unpack_i()`（读 4 字节）去读 VKey —— 于是把紧跟其后的
`Message` 的高 2 字节也吃进来了：

```
真实字节（实测，遥控器 OK 键）:  1c000000 0000 0d00 00010000
                                        ↑MakeCode ↑Flags ↑VKey ↑Message
                                  VKey 实际是 2 字节: 0d 00  -> 0x000D
                                  4 字节读法读到的是: 0d 00 00 01 -> 0x0100000D ❌
```

于是 `on_raw_keydown()` 里这句**永远成立**：

```python
if vkey != self.app.send_key_vk:   # 0x0100000D != 0x0D
    return False                    # -> Raw Input 的设备归属判定一次都进不去
```

结果：每次按 OK 键都掉进 400ms 看门狗 → 兜底判据「遥控器 10 秒内活动过」
在木木「说话 → 松手 → 按 OK」这个节奏下已经过期 → 判定成「实体键盘」→
**补发一个普通回车**。所以看起来「按 OK 键只打了个回车，永远发不出去」。

> 有意思的是：`tools\map_remote_keys.py`（标定器）读的是
> `struct_unpack_i(raw, 30) & 0xFFFF` —— 多了个掩码，所以它**能**正确读出键位表。
> 主程序漏了这个掩码，两边行为不一致才一直没被发现。
> 现在 `mivoice.py` 加了 `struct_unpack_h()`（读 2 字节）统一处理，
> 并在 `tests\run_all.py` 里用**实测字节**写死了回归用例。

v1.5 同时把看门狗兜底窗口从 `remote_active_ms`(10s) 拆成独立的
`send_fallback_ms`(30s)，避免「说话到按 OK 键」间隔稍长就漏判。

### v1.6 的两处修复（2026-10-30）

v1.5 修好了 VKey 读取，但实测日志里**仍然**出现「遥控器 OK 键被判成实体键盘」。
继续排查后定位到**两个新问题**，v1.6 一起修掉：

#### 修复一：设备名读失败被永久缓存（真根因）

`GetRawInputDeviceInfoW` 读设备路径**会偶发失败**（蓝牙设备刚重连、新 HID 集合
刚枚举出来时，路径还没准备好）。v1.5 的 `device_name()` 最后一句是**无条件**的：

```python
self._dev_cache[hdev] = name      # ❌ 空串也被缓存了
```

于是「第一次读失败」= 这个 hDevice 从此**永远**返回空名 →
`is_remote()` 只能静默判 False → 遥控器的 OK 键被当成实体键盘回车原样补发出去，
**永远发不出去**。

**日志铁证**（v1.5，2026-10-30 17:45:02）：

```
Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发
```

这一行**前面没有**「Raw Input 键盘设备: ...」—— 因为名字是空的，老代码
`if not name: return False` **静默返回，连设备行都不打**，所以日志上看不出原因。

> 对照实验：`tools\scan_remote_keys.py` 用**完全相同**的 API 和调用姿势，
> 在**同一个 WM_INPUT 位置**却能稳定认出遥控器 → 说明不是 API 用法问题，
> 是**偶发失败 + 空串被缓存**。

✅ **v1.6 的修法**：

| | v1.5 | v1.6 |
|---|---|---|
| 读到名字 | 缓存 | 缓存 |
| **读不到名字** | **缓存空串（永久判错）** | **不缓存 → 下次事件自动重试** |
| 读不出来时 | 静默判「非遥控器」 | 正式日志：`Raw 设备名读不出来 hdev=...` |
| 后来读出来了 | 无（永远不会） | 日志：`Raw 设备名后来读出来了 hdev=...` |
| 已确认过的遥控器 hDevice | 还得再赌一次名字 | **快速通道直接认** |

另外把「读设备名」和「缓存」拆成两个方法（`_read_names()` / `device_name()`），
这样能用打桩写回归测试，不用真的插设备。

#### 修复二：按住 OK 键会连发好几次

v1.5 里钩子每收到一次 keydown 就新增一条 pending。系统按住不放会自动重复
产生 ~30 次/秒的 keydown，于是 pending 堆成 6 条、Raw Input 也来 6 次 →
**一次按住连发 6 次 Ctrl+Enter**。

**日志现场**（v1.5，17:32:49 / 17:36:03）：

```
发送键已转换：Enter 回车 -> ctrl+enter（来源 raw）   x6
```

更糟的是看门狗和 Raw Input 各结算一部分 pending，会导致**同一次按键既转换
又补发**（双发，17:45:02 实测：先「来源 timeout」转换，又「来源 raw」补发）。

✅ **v1.6 的修法**：**一次「按下-松开」只对应一条 pending**。自动重复的 keydown
照吞（不能让真实回车漏到前台程序），但不再新增 pending。

> 怎么区分「按住不放的自动重复」和「松开后再按一次」？
> **靠 keyup**：系统的自动重复**不产生 keyup**，人的两次独立按键**一定产生 keyup**。
> 所以 `on_hook_up` 清掉按住周期标志，`on_hook_down` 看到标志还在就判定是重复。
> 时间阈值（300ms）只兜底「keyup 事件丢失」，不是主要判据。

#### 顺带：看门狗兜底判据收紧

v1.5 的兜底判据只有一条：「30 秒内碰过遥控器 → 就算遥控器按的」，
这会把木木用**实体键盘**敲的回车也误转成 Ctrl+Enter。

v1.6 加了反向保护：**最近一次「实体键盘按发送键」如果比遥控器活动更近，
就判成键盘**（原样补发，不干预）。只看「按发送键」——木木打字用的是字母键，
不该影响这个判定。

### 🔴 v1.7 的架构革新（2026-10-30，本次的**真根因修复**）

v1.6 修好了 VKey 读取和设备名缓存，但实测日志里**仍然**出现「遥控器 OK 键被判成
实体键盘」。继续排查后用探针做了三组**决定性实验**，把整个架构的前提推翻并重建。

#### 实验一：被吞掉的按键，Raw Input 一条都不会到

`tools\probe_raw_live.py swallow`
（装一个低级键盘钩子，注入 F5，看收不收得到 Raw Input）：

```
模式               WM_INPUT       其中F5        F5抬起数
放行(对照)                  3          3            1
全吞(下+抬)                 0          0            0
只吞按下                    1          1            1   ← 抬起照样到！
```

**结论一（否证）**：被钩子吞掉的按键，**没有** Raw Input。
→ v1.3~v1.6 的「吞掉 → 等 Raw Input 确认自己」**结构上永远走不通**。

**日志交叉印证**：19:03 那次会话里，遥控器语音键（F5）被吞了 265 次重复，
主日志里**一条**「Raw 遥控器语音键」都没有；而唯一一条在 19:03:40 ——
正是那次**第一次按下被放行**（`HW`）的那一下。

#### 实验二：只吞「按下」、放开「抬起」→ 抬起那一条照样到

同一张表的第三行：**抬起放行时，抬起那条 Raw Input 照样到达**，
而它带的 `hDevice` 就是**这一下按键**按下去的设备。

**结论二（正解）**：改成「**吞下、放开抬**」，用抬起那条 Raw Input 拿**确定**的
设备归属 —— 不再靠时间窗猜。

#### 实验三：SendInput 注入的按键会产生 hdev=0 的 Raw Input 回声

```
Alt(0xA4)   SendInput 返回=1  收到 WM_INPUT=2  hdev=0 的=2
F5(0x74)    SendInput 返回=1  收到 WM_INPUT=2  hdev=0 的=2
Enter(0x0D) SendInput 返回=1  收到 WM_INPUT=2  hdev=0 的=2
```

**结论三（v1.6 的真 bug）**：v1.6 把这个**自己注入的回声**当成
「实体键盘按了发送键」，污染了判据 → 「最近活动来自遥控器」永远判不成立
→ **第一次能发、之后每一次都失败**。

19:03:51 的实测现场，一秒之内：

```
19:03:51  看门狗兜底：等不到 Raw Input，按「遥控器」处理 -> 发 ctrl+enter
19:03:51  发送键已转换：Enter 回车 -> ctrl+enter（来源 timeout）
19:03:51  Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发   ← 就是这个回声
```

✅ **v1.7 的三处修法**：

| | v1.6 | v1.7 |
|---|---|---|
| 判定用的 Raw Input | 按下的（**根本不会有**） | **抬起的**（放行了，所以一定有） |
| 注入回声 hdev=0 | 当成「实体键盘按发送键」→ 污染判据 | **整个丢弃**（不更新时间戳、不通知仲裁器） |
| 活动记录 | 只有 `last_activity` 一个字段 | 拆成 `last_activity`（遥控器）/ `last_physical_ts`（实体键盘） |
| 看门狗起点 | 从「按下」算 400ms（按住久一点就误超时） | 从「**抬起**」算 800ms |
| 兜底判据 | 「实体键盘**按发送键**」的时间戳（不可靠：那个回车被我们吞了） | 「最近一次**任意**实体键盘活动」（可靠：字母键的 Raw Input 一定到） |

### 为什么不会误伤实体键盘

- 实体键盘的 Enter 也会被**先吞掉**，但**抬起**那条 Raw Input 一到就判定
  「不是遥控器」，**立刻原样补发**回去 —— 用户只晚几毫秒，完全无感
- 补发走 `SendInput`，带 `LLKHF_INJECTED`，本程序自己的钩子会放行，不会递归
- 看门狗兜底：万一抬起确认一直没来（Raw Input 没注册成功等），
  也会在 `send_confirm_ms` 后补发，**绝不会把用户的回车弄丢**
- 实体键盘的**其它键**（字母、空格、方向键）我们从不吞，它们的 Raw Input
  一定到 → `last_physical_ts` 永远准确 → 兜底判据不会失灵
- 修饰键状态由钩子**自己跟踪**（钩子能看到系统里每一个按键事件），
  不用 `GetAsyncKeyState` —— 那个 API 的异步键状态是「按线程」维护的，
  别的线程读出来经常是 0（实测踩过）

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
| `mivoice.state` | 运行状态（版本 + PID），托盘图标被折叠时也能查出跑的是哪一版 |
| `vbcable/` | VB-CABLE 安装包（官方原版 + 签名校验值） |

命令行：`--probe` 录一段验证麦克风 / `--alt-test` 测触发 / `--status` 环境自检 /
`--selftest N` 跑 N 秒自动退出 / `--debug` 详细日志。

配套工具（都在仓库的 `tools\` 目录里，即 `<克隆目录>\mivoice\tools\`；
脚本会自己找到上层的 `mivoice.py`，所以放在仓库内外都能跑）：

| 工具 | 干什么 |
|---|---|
| `verify-remote-send.bat` | **一键验证「OK 键→发送」整条链路**，实时盯日志并给结论 |
| `map-remote-keys.bat` | 引导式标定：点名让你按每个键，记下 VKey，可顺手把发送键写进 `mivoice.ini` |
| `scan-remote-keys.bat` | 自由扫描器：把遥控器每个键按一遍，输出完整对照表 |
| `diagnose_mivoice.py` | 语音链路联合监测（Alt 状态 + 焦点窗口 + 实际音频） |
| `audio_default_device.py` | 查 / 改系统默认录音设备 |
| `probe_raw_live.py` | 🔴 Raw Input 行为探针（`swallow` 模式实测「吞键挡不挡 Raw Input」，是 v1.7 架构的证据来源） |

---

## 已知限制（v1.6）

- **遥控器语音键就是 F5，录音期间会被本程序吞掉**（`hide_remote_f5 = 1`）。
  原因：WorkBuddy 的语音热键是「按住 Alt」，它把「Alt 按住期间收到的任何其它键
  keydown」一律判成组合键 → 立刻停止录音，而且**不检查 `e.repeat`**。遥控器按住
  语音键时系统会自动重复产生 ~30 次/秒的 F5 keydown（带 `LLKHF_ALTDOWN`），
  于是刚开录就被自己的重复掐断 —— 表现就是「WorkBuddy 语音图标只绿一下就没了」。
  所以本程序在录音期间把 F5 整个吞掉，让 WorkBuddy 根本收不到它。
  - 平时（没在录音）物理键盘的 F5 刷新功能**完全不受影响**。
  - 不想吞就把 `mivoice.ini` 里的 `hide_remote_f5` 改成 `0`。
- **实体键盘的回车会晚几毫秒送达**：v1.5 为了拿到精确的设备归属，会先把回车吞掉、
  等 Raw Input 确认「不是遥控器」再补发回去。延迟通常在几毫秒，肉眼无感；
  但如果你觉得「回车偶尔卡一下」，把 `send_confirm_ms` 调小（比如 `150`）即可
- **音量键 / 返回键在 Windows 上完全没有反应**（木木 2026-10-30 实测）：
  这两个键**连低级键盘钩子都收不到**，说明它们没走「键盘页」（Usage Page 0x01），
  要么是消费键页（Usage Page 0x0C）被 `kbdhid.sys` 在内核层吃掉了，
  要么驱动/固件根本没上报。
  用 **`tools\probe-remote-hid.bat`** 实测一下：
  它同时注册键盘页 + 消费键页，能收到的话我们就能自己接管音量/返回；
  收不到就说明用户态无解，得看驱动层面的办法了。
- 单次录音上限约 60 秒（遥控器固件限制）

### 排错：语音图标只绿一下就停（历史血案）

v1.2 之前的「只绿一下」有两个不同的原因，别混：

| 现象 | 根因 | 怎么确认 |
|---|---|---|
| WorkBuddy 一直绿、但没声音 | 系统默认录音设备不是 CABLE Output | `python tools\audio_default_device.py` 看 Console/Multimedia/Communications 三个角色 |
| WorkBuddy 只绿一下就灭 | 遥控器的 F5 自动重复被 WorkBuddy 判成组合键 | `C:\Users\Administrator\.workbuddy\logs\renderer.old.log` 里搜 `altHotkey combo while holding -> stop` |

第二条的官方诊断日志会在**轮转后**落到 `renderer.old.log`，别只看 `renderer.log`。

---

## 把遥控器上任意一个键绑成「发送键」

WorkBuddy 的发送键（默认 Ctrl+Enter）要由遥控器上的某个键来触发。
**哪个键触发**写在 `mivoice.ini`：

```ini
send_key = enter        ; 触发发送的物理键
send_keys = ctrl+enter  ; 实际发出去的组合键（对应 WorkBuddy 的发送设置）
```

`send_key` 可以写这些：

| 写法 | 对应键 |
|---|---|
| `enter` / `return` | 回车（默认，遥控器的 OK 键） |
| `space` | 空格 |
| `tab` | Tab |
| `f1` … `f12` | 功能键 |
| `up` `down` `left` `right` | 方向键 |
| `home` `end` `pageup` `pagedown` | 翻页键 |
| `volumeup` `volumedown` `mute` | 音量键 |
| `play` `playpause` `stop` `next` `prev` | 媒体键 |
| `a` … `z` / `0` … `9` | 字母键 / 数字键 |
| `0x74` | 十六进制 vk 码（万能写法） |

### 怎么知道遥控器上某个键是什么 vk？

用**按键扫描器**——它同时走两条链路，把「键」和「设备来源」对上：

- **低级键盘钩子**：拿到精确的 vk / 扫描码，但分不出是哪个设备按的
- **Raw Input**：拿到设备路径（含 `VID&012717`），能认出遥控器，但字段要小心解析

两条链路按时间戳对齐，就得到「遥控器每个键 = 什么 vk」。

**三种启动方式**（任选其一）：

1. 托盘菜单 → **「扫描遥控器按键（查每个键是什么）」**（最方便）
2. 双击 `tools\scan-remote-keys.bat`
3. 命令行：
   ```
   python tools\scan_remote_keys.py --seconds 120
   ```

扫描时把遥控器上的每个键**各按一下**（别长按），窗口会逐行列出
`vk / 扫描码 / 来自哪个设备`，最后还有汇总表。
完整日志：`tools\remote_keys_scan.log`

查到之后把 `send_key` 改成对应名字，改完重启 MiVoice 即可。
