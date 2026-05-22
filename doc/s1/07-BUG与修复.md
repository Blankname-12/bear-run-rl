# 07 — 关键 BUG 与修复

本文件记录了项目开发过程中遇到的全部关键问题及其解决方案，按类别分组。

---

## 一、截屏相关

### BUG-1: ADB screencap 延迟 682ms

**症状**：使用 `adb shell screencap -p` 截屏，每帧耗时约 682ms，端到端帧率仅 ~1.5 FPS。

**排查**：
```
ADB screencap -p (stdout):              682ms
ADB screencap -p + pull 文件:           750ms+
ADB exec-out screencap -p (无PTY):      630ms
ADB screencap 原始格式 (无-p):          800ms+ (8.3MB RGBA 传输)
```

瓶颈在设备端 PNG 编码（~300ms）和 TCP 传输 2.2MB PNG（~200ms），两者都是不可避免的物理限制。

**修复**：放弃 ADB screencap，采用 minicap 原生 socket 方案。延迟从 682ms 降至 ~17ms。

---

### BUG-2: minicap `-s` 参数误解导致服务端不启动

**症状**：带 `-s` 参数启动 minicap 后进程立即退出，socket 连接失败。

**排查**：反复尝试不同的命令组合，minicap 进程始终 m退出。读 minicap 帮助输出发现：
```
-s: Take a screenshot and output it to stdout. Needs -P.
```
`-s` 是"单次截图并输出到 stdout"，不是"socket 模式"的意思。

**修复**：去掉 `-s`。不带此参数时 minicap 默认进入 socket 服务端模式，监听抽象 socket `@minicap`。

---

### BUG-3: minicap.so 链接失败

**症状**：
```
CANNOT LINK EXECUTABLE "/data/local/tmp/minicap": library "minicap.so" not found
```

**排查**：minicap 和 minicap.so 都在 `/data/local/tmp/` 下，`LD_LIBRARY_PATH=/data/local/tmp` 也能通过 shell 执行。但 Android 9+ 对 linker 做了安全限制，非系统路径的 LD_LIBRARY_PATH 被忽略。

**修复**：
```bash
adb shell su -c 'mount -o rw,remount /'
adb shell su -c 'cp /data/local/tmp/minicap.so /system/lib64/minicap.so'
```
利用 LDPlayer 的 root 权限，将 so 放到系统库路径。

---

### BUG-4: minicap 60fps 占满 ADB 通道导致触控 ~1s 延迟

**症状**：用户按下 W 后约 1 秒游戏才有反应。截屏和 Python 端都正常（截屏组件测得的延迟约 20ms）。

**排查过程**：
1. 排除 Popen 延迟：实测 ~5ms
2. 排除 observe 延迟：实测 ~20ms
3. 排除 Python 逻辑错误：加时间戳，确认 send_action 在按键后立即执行
4. 排除游戏处理延迟：直接通过 ADB 发送 swipe 命令 → 快速响应
5. **发现**：minicap 不限制帧率时以 ~60fps 持续推送 JPEG 流。150KB/帧 × 60 = 9MB/s。ADB forward 和 exec-out 共用同一条 ADB 通道。9MB/s 几乎占满带宽，触控指令在 ADB 服务端被排队。

**修复**：`-r 15` 限制 minicap 帧率到 15fps，带宽从 9MB/s 降至 ~2.25MB/s。**这是项目中最关键的发现之一**——截屏和触控必须共享 ADB 带宽，需要平衡两者。

---

### BUG-5: get_frame() drain 时每帧解码 JPEG

**症状**：两次截屏间隔约 200ms 时，`get_frame()` 耗时约 135ms（预期 ~20ms）。

**排查**：drain 旧帧的逻辑中，每帧都调用了 `Image.open(io.BytesIO(jpeg)).convert("RGB")`（PIL JPEG decode）。12 帧积压 × 10ms/解码 = 120ms。

**修复**：drain 时只读 JPEG 字节并丢弃（不 decode），仅保留最后一帧的 JPEG 字节，跳出循环后一次性 decode。时间从 135ms 降至 ~20ms。

```python
# 修复前
for each buffered frame:
    latest = np.array(Image.open(jpeg).convert("RGB"))  # ×12 次 decode

# 修复后
for each buffered frame:
    latest_jpeg = jpeg  # 只保留字节
return np.array(Image.open(latest_jpeg).convert("RGB"))  # 1次 decode
```

---

### BUG-6: Windows DXGI 截的是桌面而非游戏原生画面

**症状**：红心检测完全失效。明明红心在画面上，程序却判断死亡。

**排查**：
1. 发现 CAPTURE_MODE="window"，截屏来源是 mss + DXGI
2. 截图分辨率是 633×1078（窗口大小），而非 1080×1920
3. DXGI 截图的色彩比 ADB/minicap 截图偏淡
4. HSV 检测的 S_MIN/V_MIN 是针对 ADB 截图调的

**修复**：切换到 CAPTURE_MODE="minicap"。

---

## 二、触控相关

### BUG-7: 滑动被识别为点击（SWIPE_DURATION_MS=50）

**症状**：游戏角色不做滑动操作，像被点击了一样。

**原因**：Android 的 `input swipe` 需要足够的持续时间来让手势识别器区分 swipe 和 tap。50ms 太短。

**修复**：SWIPE_DURATION_MS 从 50 → 100ms。经反复测试，100ms 在响应速度和识别准确率之间取得平衡。

---

### BUG-8: 持久化 ADB Shell 在 Windows 上失败

**症状**：`subprocess.Popen(["adb", "shell"])` 进程立即退出（exit code 1）。

**原因**：ADB shell 检测到 stdin 是管道而非 TTY，认为"非交互式"，直接退出。

**尝试的替代方案**：
- `while read cmd; do eval $cmd; done` → 同样退出
- `adb shell -T`（强制 PTY）→ 无法通过管道写
- `adb shell cat` → 只能 echo，不执行命令

**修复**：放弃持久化方案，改用单次 Popen + exec-out。单次 Popen 的 ~5ms 开销可接受。

---

## 三、死亡检测相关

### BUG-9: PIL HSV H 通道与 config 阈值不匹配

**症状**：红心即使正常渲染，检测到的红像素数量也极少，死亡频繁误判。

**根因分析**：
```
PIL.Image.convert("HSV"):       H 范围 0-255（对应 0-360°）
cv2.cvtColor(rgb, COLOR_RGB2HSV): H 范围 0-180（对应 0-360°）

config.HEART_H_MAX1 = 10 是按 OpenCV 范围设计的（H=10 → 20°）
PIL 转换时 H=10 → 360°×10/255 ≈ 14°
红色检测范围从 20° 缩窄到 14°，丢失大量红色像素
```

**修复**：`_count_red_pixels` 中改用 `cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)`。

---

### BUG-10: DXGI 截图红心误判（S_MIN/V_MIN 不匹配）

**症状**：切回 minicap 后死亡检测不出（假阴性）。

**原因**：DXGI 截图色彩偏淡时 S_MIN 被降到 40、V_MIN 被降到 50。切回 minicap 后忘记恢复。宽松的阈值让大量非红色像素被误计为"红心"，导致红心永远"存在"。

**修复**：恢复 S_MIN=80, V_MIN=80（适合 minicap 原生截图的严格值）。

---

### BUG-11: step() 先执行动作再检测死亡

**症状**：
```
[Step 36] 动作=swipe_up  红心跳失=0/10  奖励=-10.00
```
角色已死（奖励=-10），但 step 36 仍然显示了 swipe_up 动作。

**原因**：`step()` 中先 `action_swipe(action_idx)` 发滑动，然后才 `_heart_lost(raw)` 检测死亡。

**修复**：重构为先截屏 → 检测死亡（含 _verify_death）→ 确认活着才执行动作。死亡时直接返回，不发送滑动。

---

### BUG-12: _verify_death 点击时机太早

**症状**：tap 打在空气上，死亡界面/弹窗没有响应。

**原因**：红心一丢失就立即点击，但死亡 UI 需要 ~0.8s 才能完全加载出来。tap 发送时按钮尚未渲染。

**修复**：在 tap 之前加入 `time.sleep(0.8)` 等待 UI 加载。

---

## 四、数据采集相关

### BUG-13: 自动 no-op 重置冷却导致操作被丢弃

**症状**：直线跑几秒后按 W 没反应。

**原因**：自动 no-op 录制和用户操作共用 `_last_action_time` 冷却计时器。no-op 每隔 0.4s 录制一次，每次录制都更新计时器。用户按键时 `elapsed < 0.35s`，按键被丢弃。

**修复**：分开两个计时器——用户操作冷却 vs 自动 no-op 间隔。

---

### BUG-14: 采集卡住无声失败（except: pass）

**症状**：玩了半小时发现没有录到数据。

**原因**：异步架构中后台线程的异常处理是 `except Exception: time.sleep(0.01)`，所有错误静默吞掉。minicap 断开或 socket 错误时，线程陷入无限空循环。

**修复**：重写为单线程同步方案；异常处理改为 `traceback.print_exc()`。

---

### BUG-15: 异步架构 no-op 占比仅 12%

**症状**：采集 241 条数据中 no_op 仅 31 条（12.9%），而合理占比应为 40-60%。

**原因**：`_verify_death()` 在后台线程中阻塞 ~2.2 秒。红心检测稍有波动就触发，期间后台线程冻结——不截屏、不录 no-op。主线程继续按键，动作照录，no-op 全丢。

**修复**：回归同步方案。`_verify_death()` 阻塞主线程是合理的（真死了就该等）。

---

### BUG-16: 批量读键导致操作过密（11帧/秒）

**症状**：数据中动作密集到 ~11 次/秒，不符合 Temple Run 游戏特性。

**原因**：复杂的批量读键 + 冷却 + 批次处理 + 30ms 窗口等待逻辑相互耦合，冷却机制被绕过了。

**修复**：简化为每次只读一个键的极简循环。每秒操作次数完全由玩家自己控制。

---

## 五、数据/训练相关

### BUG-17: 不加 --resume 直接覆盖旧数据

**症状**：第二次采集时之前的几百条数据被覆盖。

**原因**：`collect_data.py` 默认从头开始，直接写入 data/states.npy 和 data/actions.npy。

**修复**：每次采集自动创建独立 `data/session_YYYYMMDD_HHMMSS/` 目录，永不覆盖。`--resume` 续接到最近会话。

---

### BUG-18: 训练模式 step() 无广告二次验证

**症状**：PPO 训练中每次弹广告都被误判为死亡，触发重启，大大降低训练效率。

**原因**：`step()` 中直接使用 `_heart_lost(raw)` 的返回值作为 `done`，没有调用 `_verify_death()` 来区分真死亡和广告。

**修复**：`step()` 中加入 `_verify_death()` 调用。训练模式 `_heart_lost` 使用 3 帧投票（非 fast 模式）。
