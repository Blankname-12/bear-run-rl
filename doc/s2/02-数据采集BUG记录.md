# 02-数据采集 BUG 深度分析

> **项目**: 熊大快跑 RL | **会话**: S2 (第一新对话) | **文件**: collect_data.py, game_env.py, fast_io.py, train.py

---

## BUG 总览与连锁关系

```
BUG1(memmap 57GB) ──→ 修复: capacity 500 + uint8 ──→ BUG2(数据丢失风险)
     │                                                       │
     │                                               修复: 增量 tofile
     │                                                       │
     │                                              BUG11(异步未异步) ←──┐
     │                                                    │              │
     └──→ BUG5(minicap崩溃) ──→ 修复: 精简ADB ──→ 截屏2+秒 ──┘         │
              │                                                          │
              ├──→ BUG6(Window截屏) ──→ 窗口358×674 ← 模拟器太小       │
              │                                                          │
              └──→ BUG3(死亡检测干扰) ←── observe()耦合 ←──┐           │
                       │                                     │           │
                  修复: check_death参数 ──→ BUG9(线程竞态) ─┘           │
                       │                                                  │
                  架构问题: GameEnv职责过多 ←── BUG10(逻辑耦合) ────────┘
                                                                          │
BUG4(帧栈复制) ←── 性能优化牺牲正确性                                     │
BUG7(Ctrl+C死循环) ←── 信号处理 + 阻塞IO + post-episode代码              │
BUG8(键盘msvcrt) ←── Windows控制台IO编解码                                │
```

---

## BUG 1: memmap 预分配 57GB 磁盘爆炸

### 1.1 调用链追溯

```
collect_data.py:collect()
  → np.memmap(states_path, dtype=np.float32, mode="w+", shape=(10000, 15, 320, 320))
     → 操作系统: CreateFile + SetFilePointer + SetEndOfFile (预分配 57GB 稀疏文件)
     → errno 28: ENOSPC (No space left on device)
```

**数据量计算**：
- `10000` (INITIAL_CAPACITY) × `15` (4帧×3通道RGB + 3赛道one-hot) × `320` (FRAME_HEIGHT) × `320` (FRAME_WIDTH) × `4` (float32字节) = **61,440,000,000 字节 = 57.2 GB**
- 用户磁盘空闲空间远小于 57GB，直接在 `np.memmap.__new__` 中的 `fid.flush()` 阶段报错

### 1.2 为什么 capacity 被设为 10000？

回顾配置链：
- 旧版 128×128 时，capacity=10000 只需要 10000 × 15 × 128 × 128 × 4 = **9.4 GB**。虽然偏高但勉强可用。
- 升级到 320×320 后，仅修改了 `FRAME_WIDTH/HEIGHT`，**未重新计算 memmap 预分配量**。
- 这属于典型的**配置耦合**问题：`MEMORY_CAPACITY` 的值隐含依赖 `FRAME_WIDTH/HEIGHT`，但代码中没有显式约束或动态计算。

### 1.3 替代方案分析

| 方案 | 优点 | 缺点 | 是否采用 |
|------|------|------|---------|
| **A: 缩小 capacity 到 500 + dtype 改 uint8** | 最小改动，uint8 节省 75% 空间 | 仍需预分配，只是推迟了问题 | ✅ 第一阶段 |
| **B: 动态扩容 memmap** | 按需增长，不浪费空间 | 扩容时需要复制全量数据，代码复杂 | ❌ 尝试后放弃 |
| **C: 分 chunk 保存（每 200 条一个文件）** | 内存中只保留当前 chunk，OOM 风险极低 | 退出时需合并 chunk 文件，I/O 次数多 | ❌ 曾尝试 |
| **D: raw bytes + tofile（最终方案）** | 零预分配，边采集边写盘，代码最简单 | 退出时需 fromfile 重建 numpy 数组 | ✅ 最终采用 |

**为什么最终选 D**：方案 A/B/C 都试图在"预分配"框架内解决问题，但根本矛盾在于采集规模不可预知（可能采 5 分钟也可能采 30 分钟）。方案 D 彻底消灭了预分配概念，文件大小自然增长，从根本上避免了所有容量预估问题。

### 1.4 架构教训

- **禁止隐式容量依赖**：任何预分配操作的 capacity 参数必须基于可用资源动态计算，不能硬编码。正确的做法是 `capacity = min(MAX_CAPACITY, available_disk_space / bytes_per_sample / safety_factor)`。
- **uint8 存储是通用最佳实践**：图像数据天然适合 uint8（0-255 无损），在存储层用 uint8、计算层转 float32，是存储效率与计算精度的最优平衡。
- **采集阶段应避免预分配**：采集过程的规模不可预知（人工操控时长不固定），使用流式写入（append-only file）是唯一安全的选择。

---

## BUG 2: 数据全部丢失风险

### 2.1 调用链追溯

```
collect_data.py:main loop
  → states_list.append(state)    # 仅追加到 Python list（RAM）
  → actions_list.append(action)  # 同上
  → [任意异常]                    # minicap 崩溃 / ADB 超时 / 模拟器卡死
  → finally: save_data()         # 唯一落盘点
```

**失败模式分类**：

| 失败类型 | finally 是否执行 | 数据是否丢失 | 发生概率 |
|---------|-----------------|-------------|---------|
| Python 异常（如 ValueError） | ✅ 执行 | ✅ 不丢 | 低 |
| 进程被 kill -9 | ❌ 不执行 | ❌ 全部丢失 | 低 |
| ADB 管道 hang（subprocess.run 阻塞） | ❌ 线程卡死 | ❌ 全部丢失 | **高** |
| minicap 崩溃后回退链超时（150s/帧） | ❌ 线程卡死 | ❌ 全部丢失 | 中 |
| 用户按 Ctrl+C 多次后强制关窗口 | ❌ 不执行 | ❌ 全部丢失 | 中 |

核心问题：**数据只在采集终点持久化，而采集过程是最不可靠的阶段**。

### 2.2 为什么最初设计如此？

原始代码的设计假设来自**训练模式**：训练中每局很短（几十步），且训练数据不珍贵（可以重新生成）。这个假设被错误地带入了采集模式——采集模式下数据来自人工操作，不可复现，每一条都珍贵。

### 2.3 替代方案分析

| 方案 | 持久化时机 | 丢失窗口 | 实现复杂度 |
|------|-----------|---------|-----------|
| **A: finally 保存（原始）** | 进程退出时 | 整个采集过程 | 最低 |
| **B: 每 N 步 np.save** | 每 200 步 | 最多 199 步 | 低 |
| **C: memmap + 每步 flush** | 每步 | 取决于 OS 缓冲区刷新 | 中（memmap 预分配问题） |
| **D: raw file + 每步 tofile + periodic flush** | 每步 | 取决于 flush 间隔 | 低 |
| **E: 每条记录独立文件 + 最终合并** | 每步 | 单条记录 | 低（文件数爆炸） |

**为什么最终选 D**：
- 方案 B 需要把数据攒到 200 条才写，仍有一定丢失窗口
- 方案 C 是 BUG1 的延续，预分配问题未解决
- 方案 E 在 4500 条记录时会产生 4500×2=9000 个文件（states+actions），文件系统压力大
- 方案 D：`(state*255).astype(np.uint8).tofile(f)` 把序列化开销降到零，`f.flush()` 确保操作系统缓冲区落盘，每 50 步 `f.flush()` 一次

### 2.4 架构教训

- **Crash-only software 原则**：假设程序可能在任何时刻崩溃，每次写入都应立即持久化。`finally` 块是最后防线，不应是唯一防线。
- **采集系统 vs 训练系统的可靠性需求不同**：训练数据可重新生成（环境是确定的），采集数据不可复现（人工操作），前者可以容忍丢失，后者不行。
- **Python list 不该作为"半持久化"缓冲**：list 在进程内存中，进程死亡即消失。如果需要缓冲，应该用 `shelve`、`sqlite3` 或直接写文件。

---

## BUG 3: observe() 死亡检测干扰数据采集

### 3.1 调用链追溯

```
collect_data.py:main loop (用户按 W)
  → env.send_action(0)                          # 发送上滑指令
  → env.observe(save_ss=False)                  # 截屏更新状态
     → raw = get_screen_numpy()                 # ADB 截屏
     → if not warmup:
          if self._heart_lost(raw, fast=True):  # ★ 检测红心是否消失
               done = self._verify_death()      # ★ 点"取消"按钮测试死亡
                  → self._tap(134, 1850)        # ★ 自动点击 UI 按钮
                     → adb shell input tap 134 1850
```

**触发条件**：采集模式下，用户停止操作（等待障碍物通过），画面静止。`_heart_lost()` 检查左上角红心区域——但当前画面可能恰好处于游戏暂停/广告弹窗/加载场景，红心确实不显示。程序误判断为"玩家死亡"，自动点击了 (134, 1850) 坐标，恰好命中"取消"按钮，导致游戏弹出暂停菜单。

### 3.2 为什么 GameEnv 要把截屏和死亡检测耦合？

`GameEnv` 最初为**训练模式**设计。训练时：
- Agent 死了 → 需要自动重启以继续下一局
- 死亡检测是 `step()` 的必要组成部分
- `observe()` 是训练 pipeline 的内部辅助函数

当 `collect_data.py` 被编写时，它直接复用了 `GameEnv.observe()`，没有意识到这个函数在采集模式下会"自作主张"。

### 3.3 替代方案分析

| 方案 | 优点 | 缺点 | 是否采用 |
|------|------|------|---------|
| **A: 给 observe 加 check_death 参数** | 最小改动，不改 GameEnv 架构 | 布尔参数陷阱，函数行为分裂 | ✅ 采用（临时） |
| **B: collect_data 不使用 GameEnv，直接调底层的 get_screen_numpy()** | 彻底解耦，采集代码完全独立 | 需要重写帧栈管理、预处理逻辑 | ✅ 最终方案 |
| **C: 拆分 GameEnv 为 GameCapture + DeathDetector** | 符合单一职责原则 | 改动量大，影响训练代码 | ❌ 时间不够 |
| **D: 采集时 mock/stub 掉死亡检测方法** | 不改 GameEnv API | Python monkey-patching 脆弱 | ❌ 太 dirty |

**为什么最终从方案 A 迁移到了方案 B**：
- 方案 A 是应急修复，但采集代码仍然依赖 GameEnv，后续 BUG 9/10/11 都与此有关
- 方案 B 让 `collect_data.py` 完全独立：只有 `subprocess.run("adb exec-out screencap -p")` + `preprocess_frame()` + 键盘输入 + 文件写入。代码从 300 行降到 150 行，且不再受 GameEnv 的任何内部变更影响。

### 3.4 架构教训

- **单一职责原则**：`GameEnv.observe()` 同时做了截屏 + 死亡检测 + 帧栈管理 + 截图保存，四个职责。当其中一项（死亡检测）在采集模式下不适用时，整个方法就不能复用。
- **Boolean 参数是 API 设计坏味道**：`check_death=True/False` 让同一个方法有两种截然不同的行为。正确的设计是提取出 `GameCapture` 类（只做截屏），让 `TrainingEnv` 和 `CollectionRecorder` 分别组合使用。
- **采集逻辑应该独立于训练逻辑**：这是本项目最重要的教训。两者目的完全不同（训练=自动交互，采集=记录人工操作），共享代码路径只会互相掣肘。

---

## BUG 4: 帧栈初始化——1 帧复制 4 次

### 4.1 时序信息丢失的数学影响

帧堆叠（Frame Stack）的本质是为 CNN 提供**时序差分信息**。设真实的 4 帧为 $f_0, f_1, f_2, f_3$（间隔 50ms），则帧栈提供：
- 一阶差分：$f_1 - f_0, f_2 - f_1, f_3 - f_2$ → 障碍物接近速度
- 二阶差分：$(f_2-f_1)-(f_1-f_0)$ → 障碍物加速度

当 4 帧完全相同（$f_0=f_1=f_2=f_3$）时，所有差分恒为零。对 CNN 而言，输入是一个"静止画面重复 4 次"，等价于单帧信息，帧栈的时序价值完全丧失。

### 4.2 为什么当初会这样写？

```python
# 原始代码
def _init_frame_stack(self):
    raw = get_screen_numpy()           # minicap connect() 耗时 ~3s
    processed = preprocess_frame(raw)  # 裁剪+缩放
    self._frame_stack = [processed.copy() for _ in range(4)]
```

**原因**：`get_screen_numpy()` 首次调用时 minicap 需要 connect()（推送二进制 + 启动服务端 + socket 握手），耗时约 3 秒。如果循环调用 4 次，初始化就需要 `4 × 3s = 12s`。开发者为了"加速启动"牺牲了正确性。

**实际影响**：看似只影响前几条记录，但在 RL 训练中，环境 reset 后第一步就遇到障碍物是常见情况。此时帧栈中 3/4 的帧是"假的"，CNN 无法正确感知障碍物接近速度，导致第一步决策近乎随机。

### 4.3 修复后的代码

```python
def _init_frame_stack(self):
    self._frame_stack = []
    for _ in range(config.FRAME_STACK):
        raw = get_screen_numpy()           # 每次截真帧
        processed = preprocess_frame(raw)
        self._frame_stack.append(processed.copy())
        if len(self._frame_stack) < config.FRAME_STACK:
            time.sleep(0.05)  # 帧间 50ms 间隔
```

初始化时间增加了约 `3s + 3×50ms = 3.15s`（后续 3 帧不再需要 connect，每次只需 ~50ms），但获得了真实的时序信息。

### 4.4 架构教训

- **不能用"加速"理由牺牲正确性**：初始化慢 3 秒是可接受的（只发生一次），但训练数据质量差是不可逆的（影响所有后续学习）。
- **性能优化必须可测量可验证**：如果当时有测试验证"帧栈前 4 帧是否互不相同"，这个 BUG 会在开发阶段被发现。

---

## BUG 5: minicap 崩溃 + ADB 回退链性能灾难

### 5.1 minicap 崩溃的深层原因

minicap 是 STF (Smartphone Test Farm) 项目的 C 语言原生工具，通过 SurfaceFlinger 的 `ISurfaceComposer` 接口直接读取帧缓冲。它被编译为单一的 `minicap` 二进制文件和平台相关的 `minicap.so` 共享库。

**崩溃链分析**：

```
minicap 启动
  → dlopen("minicap.so")                              # 加载共享库
  → 调用 SurfaceComposerClient::createDisplay()       # JNI 调用
  → Android 9 (SDK 28) SurfaceFlinger API 差异
     → x86_64 模拟器的 SurfaceFlinger 实现不完全兼容
        → SIGSEGV (段错误)
           → 或返回未初始化内存 → 垃圾 header 值 (70778880×125829120)
```

**为什么 header 返回垃圾值而非崩溃**：minicap 的 `getFrame()` 可能在某些错误路径中返回了未初始化的栈内存。24 字节中 version 可能是有效的（socket 连接成功），但分辨率字段来自未初始化的局部变量。这导致了"部分有效"的诡异状态——连接成功、帧可能可读、但分辨率完全错误。

### 5.2 回退链的性能分析

原始 `_adb_screencap()` 的设计：

```
Method 0: exec-out screencap -p          # ~400ms 最快
  → 失败时进入 Method 1
Method 1: shell screencap -p             # ~600ms
  → 失败时进入 Method 2  
Method 2: screencap -p /sdcard/xx.png → pull → read  # ~800ms + I/O
  → 失败时进入 Method 3
Method 3: screencap /sdcard/xx.raw → pull → parse  # ~1000ms + parse
```

每种方法 3 次重试，每次超时 10 秒。关键问题是：**四种方法的失败原因通常相同**（ADB 连接断开或模拟器 screencap 服务无响应）。当 method 0 因为 ADB 断连而失败时，methods 1-3 也几乎肯定会失败。所以回退链**没有提升可靠性，只是把"一次失败"变成了"12 次重试 + 30+ 秒延迟"**。

### 5.3 替代方案分析

| 方案 | 延迟 | 可靠性 | 优缺点 |
|------|------|--------|--------|
| minicap | ~50ms | ❌ 频繁崩溃 | 最快但不稳定 |
| Window DXGI (mss) | ~17ms | ❌ 窗口太小 | 最快但分辨率错误 |
| ADB with 4 methods × 3 retries | 2-30s | ✅ 最终会成功 | 极慢 |
| ADB exec-out only + 1 retry | ~400ms | ✅ 稳定 | **选此方案** |
| scrcpy H.264 | 未知 | ❌ ffmpeg 管道调不通 | 未成功过 |

### 5.4 架构教训

- **回退链的价值取决于"各路径独立失败"**：如果多条 fallback 共享同一个根因（如 ADB 连接断开），则回退链只是重试链，应统一为单一方法 + 有限重试。
- **Fast-fail > Slow-retry**：在实时系统中，快速报告失败（让上层决定重试策略）比在底层反复重试更好。底层的重试次数×超时时间会级联到整个 pipeline。
- **二进制兼容性必须在项目早期验证**：minicap 是第三方预编译二进制，应在第一周内完成"在目标模拟器上连续运行 1 小时不崩溃"的稳定性测试。

---

## BUG 6: Window DXGI 截屏——窗口像素 ≠ 游戏像素

### 6.1 坐标系混乱的根因

LDPlayer 模拟器的渲染管线：

```
游戏 Unity 引擎 → 渲染到 1080×1920 FBO (Frame Buffer Object)
  → Android SurfaceFlinger → 合成到模拟器窗口
     → 模拟器窗口大小由用户拖动决定 (本例: 358×674)
        → Windows DWM → 屏幕显示
```

**mss (DXGI) 捕获的是"模拟器窗口"的像素（358×674），而不是"游戏 FBO"的像素（1080×1920）。**

所有游戏坐标（GAME_CROP、HEART_REGION、按钮位置）都是基于 1080×1920 设计的。当截到 358×674 的图时：
- 游戏有效区域从 ~960×1500 缩成了 ~320×500
- 障碍物从 50-80px 变成了 15-25px（比 320×320 模型输入还小）
- 红心区域从 120×50 变成了 40×17 px——HSV 检测几乎不可能准确

### 6.2 为什么窗口模式被启用？

`capture_frame()` 中，当 CAPTURE_MODE 为 "window" 或 "minicap" 时，minicap 失败后会自动回退到 window 捕获。这意味着当 minicap 崩溃时，程序静默切换到了 window 模式——**无人知晓截屏质量已经严重下降**，直到训练效果极差才反向排查。

### 6.3 架构教训

- **自动回退必须伴随着质量检查**：切换截屏方案后，应验证分辨率、宽高比是否符合预期，不一致时应立即报错而非静默继续。
- **截屏方案的"分辨率"应该是显式契约**：调用方期望 1080×1920 的帧，截屏层有责任保证返回的是有效分辨率的帧。如果做不到，应该报错而非返回一张"看起来差不多"的低分辨率图。
- **Window 截屏不适用于模拟器 RL**：模拟器的窗口大小不可控（用户可能拖动、最小化、多开），依赖窗口截屏意味着训练会随用户操作而变化。

---

## BUG 7: Ctrl+C 信号处理死循环

### 7.1 时序分析

用户按下 Ctrl+C 到程序真正退出的完整时序：

```
T=0.000s  用户按 Ctrl+C (第1次)
T=0.001s  Python 收到 SIGINT → signal_handler → interrupted=True
          打印: "[Train] 收到中断信号，正在安全退出..."
T=0.001s  检查点: 当前在 env.step() 内的 get_screen_numpy() → _adb_screencap()
          正在 subprocess.run() 中阻塞，等待 ADB 返回
          信号处理器已执行，但主线程仍在 C 扩展中
T=2.500s  ADB 返回，step() 完成
T=2.501s  while not done and not interrupted: → interrupted=True → 退出内层循环
          ★ 但 post-episode 代码开始执行:
T=2.501s  episode_rewards.append(...)        # 统计更新
T=2.600s  avg100 = np.mean(...)              # 计算均值
T=3.000s  logger.log(...)                      # 写入 CSV
T=3.000s  if avg20 > best: save_best_model()  # 保存最佳模型
T=3.500s  if done and not interrupted: env.restart_game()  # ★ 重启游戏!
T=3.500s    → time.sleep(1.5)                 # 等待 UI
T=5.000s    → self._tap(RESTART_BUTTON)       # 点"重新开始"
T=5.400s    → time.sleep(1.0)                 # 等待动画
T=6.400s    → self._tap(START_BUTTON)         # 点"开始跑酷"
T=6.800s    → time.sleep(1.5)                 # 等待加载
T=8.300s  重启完成

T=8.300s  while not interrupted: → interrupted=True → 应该退出...
          但如果 restart_game 和 reset 成功, 下一局已经开始
          （因为 interrupted 只在循环开头检查）

T=0.500s  用户按 Ctrl+C (第2次, 看到没退出)
T=0.501s  signal_handler → _interrupt_count=2 → os._exit(1)
          进程强制退出
```

**关键发现**：`interrupted=True` 到外层循环检查之间有 **8.3 秒的 gap**。在这 8.3 秒内，用户连按 5-6 次 Ctrl+C，每次触发打印，看起来像"程序死循环 Ctrl+C 无效"。

### 7.2 修复策略分析

```python
# 修复后的信号处理器
_interrupt_count = 0

def signal_handler(sig, frame):
    global interrupted, _interrupt_count
    _interrupt_count += 1
    if _interrupt_count == 1:
        print("\n[Train] 收到中断信号 (再按一次强制退出)")
        interrupted = True
    else:
        print("\n[Train] 强制退出!")
        os._exit(1)

# 修复后的 post-episode
if interrupted:
    break  # ★ 立即跳出，不执行统计、保存、重启

# 修复后的 restart 调用
if done and not interrupted:
    env.restart_game()  # ★ 双重保护
```

### 7.3 架构教训

- **信号处理器应设为幂等的**：多次触发不应产生副作用（如重复打印、重复保存）。
- **interrupted 标志位后应最小化继续执行的代码量**：理论上，`interrupted=True` 后只应执行不可跳过的清理逻辑（关闭文件句柄、释放资源），不应执行统计、模型保存等"正常流程"代码。
- **优雅退出应有分级机制**：第一次 Ctrl+C = 请求退出（完成当前 step 后退出），第二次 = 强制退出（立即 os._exit），第三次（如果需要）= kill -9。

---

## BUG 8: 键盘检测——msvcrt.getch vs getwch

### 8.1 编解码路径分析

Windows 控制台的键盘输入有两种读取方式：

```python
# 方式 1: getch() → bytes → 需要 decode
key_bytes = msvcrt.getch()          # 返回 b'w', b'\xe0', 等
if key_bytes == b'\xe0':            # 扩展键前缀
    key_bytes = msvcrt.getch()      # b'H' (上), b'P' (下), b'K' (左), b'M' (右)
    return {'H': 'UP', ...}.get(key_bytes)
return key_bytes.decode('utf-8')    # ★ 在中文 Windows 上可能失败

# 方式 2: getwch() → str → 无需 decode
key_str = msvcrt.getwch()            # 返回 'w', '\xe0', 等
if key_str in ('\x00', '\xe0'):     # 扩展键前缀 (str)
    key_str = msvcrt.getwch()       # 'H', 'P', 'K', 'M'
    return {'H': 'UP', ...}.get(key_str)
return key_str.lower()
```

**为什么 getch().decode('utf-8') 会失败**：
- Windows 控制台的代码页可能是 CP936 (GBK)，不是 UTF-8
- 对于 ASCII 范围内的字符（如 'w', 'a', 's', 'd'），UTF-8 和 GBK 编码相同，所以"通常"能工作
- 但对于某些非 ASCII 按键（如中文输入法下残留的字符），decode('utf-8') 会抛出 UnicodeDecodeError
- 原代码用 `try/except UnicodeDecodeError: return None` 吞掉了这个错误，导致某些按键"莫名其妙不响应"

### 8.2 为什么用 msvcrt 而不是 pynput/pygame？

| 库 | 优点 | 缺点 |
|----|------|------|
| `msvcrt` | Windows 内置，零依赖，~0ms 延迟 | 仅 Windows，API 低级 |
| `pynput` | 跨平台，高级 API | 需要 pip install，有 threading 问题 |
| `pygame` | 游戏级 API，支持组合键 | 已确认在 Python 3.12 上因 ImpImporter 移除而崩溃 |
| `keyboard` | 跨平台，支持热键 | 需要管理员权限（Windows） |

在项目环境中（conda RL, Python 3.12），`pygame` 已经崩溃，`pynput` 和 `keyboard` 需要额外安装且可能有问题，`msvcrt` 是唯一"确定可用"的选择。

### 8.3 架构教训

- **I/O 抽象层值得付出跨平台兼容性的代价**：如果最初用 `pynput` 或抽象出 `KeyReader` 接口，就不至于被 Windows 控制台 API 的细节困扰。
- **不要用 try/except 吞掉编解码错误**：`except UnicodeDecodeError: return None` 把"按键成功检测但解码失败"和"没有按键"两种状态混为一谈，导致问题无法排查。正确的做法是捕获后 fallback 到 `key_bytes.decode('latin-1')`（latin-1 永远成功）。

---

## BUG 9: 线程安全——后台截屏 × 主线程键盘 × 共享状态

### 9.1 竞态条件类型分析

**Race 1: 部分写入**
```python
# 后台线程
with _lock:
    _latest['state'] = state     # step 1: 写入 state
    _latest['raw'] = raw         # step 2: 写入 raw
    _latest['ts'] = time.time()  # step 3: 更新时间戳

# 主线程
with _lock:
    state = _latest['state']     # step a: 读取 state
    raw = _latest['raw']         # step b: 读取 raw
    ts = _latest['ts']           # step c: 读取时间戳
```
_lock 保证原子性，Race 1 已修复。

**Race 2: 帧-动作对齐错误**（未修复）
```
T=0ms  后台: capture frame #42, 障碍物在左赛道
T=50ms 主线程: 读取 frame #42, 用户看到障碍物在左, 按 D 右移
T=50ms 主线程: record(state=frame#42, action=right)
T=50ms 主线程: send_action(right)
T=100ms 后台: capture frame #43, 反映右移结果
T=150ms 主线程: 读取 frame #43
```
这个时序是正确的——用户基于 frame#42 看到障碍物在左做出右移决策。

```
T=0ms  后台: capture frame #42, 障碍物刚出现（还很小）
T=300ms 主线程: 用户看到画面, 按 W 跳
T=300ms 主线程: record(state=frame#42, action=jump)  ★ frame#42 已有 300ms 延迟
T=300ms 主线程: send_action(jump)
T=350ms 后台: capture frame #43, 跳跃结果
```
**问题**：当截屏延迟（400ms）大于主线程轮询间隔时，主线程总是读到"过时的帧"。用户基于过时帧做出决策，但障碍物位置已经前进了。

### 9.2 为什么异步方案被放弃

异步架构的理论优势（截屏和键盘互不阻塞）在实践中被截屏延迟（2+ 秒）完全抵消。当截屏比键盘轮询慢一个数量级时，"异步"变成了"主线程干等截屏线程产出新帧"。最后回归同步模型：键盘→动作→截屏→下一轮，每步 ~400ms。

### 9.3 架构教训

- **异步的价值取决于生产者和消费者的速率比**：当生产者（截屏）比消费者（键盘）慢时，异步没有意义——消费者总是要等的。
- **共享可变状态不应是一个字典**：如果强用异步，应该用 `queue.Queue(maxsize=1)` 做帧传递，最新的帧自动覆盖旧的，消费者不会读到半新半旧的状态。
- **线程模型的复杂度应匹配问题需求**：数据采集只需要 ~2fps，同步模型完全够用。线程化是在"优化一个不存在的瓶颈"。

---

## BUG 10: 采集逻辑与训练逻辑的架构耦合

### 10.1 耦合的成因

`GameEnv` 类在项目初期被设计为"游戏环境的统一抽象"，同时服务于训练和采集。但两者的需求在关键维度上冲突：

| 维度 | 训练 (train.py) | 采集 (collect_data.py) |
|------|----------------|----------------------|
| 死亡处理 | 自动检测 + 自动重启 | **禁止干预**，用户手动重启 |
| 截图保存 | 环形缓冲 100 张 PNG | 全部原始截图存 JPEG |
| 动作来源 | Agent 策略网络 | 键盘输入 |
| 帧率目标 | 尽可能快 | ~2fps 即可 |
| 错误处理 | 静默重试 | 立即报错 |
| 状态管理 | step() 含奖励计算 | 仅记录 (state, action) 对 |

**同一个类无法高效地同时满足这两组需求。**

### 10.2 正确的架构应该是

```
                    ┌──────────────────┐
                    │  GameCapture     │  ← 只负责截屏，返回 numpy 数组
                    │  - screencap()   │
                    └──────┬───────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼──────┐ ┌──▼──────────┐ ┌▼──────────────┐
     │ TrainingEnv   │ │ CollectRec  │ │ HumanPlay     │
     │ - step()      │ │ - record()  │ │ - visualize() │
     │ - reward      │ │ - save()    │ │ - interactive │
     │ - death_detect│ │             │ │               │
     └───────────────┘ └─────────────┘ └───────────────┘
```

### 10.3 架构教训

- **不要用一个类服务两个不同的调用方**：当发现类的方法需要加 boolean 参数来区分行为时（`check_death=True/False`），说明类该拆分了。
- **组合优于继承，更优于"全能类"**：`TrainingEnv` 和 `CollectRec` 都应该组合 `GameCapture`，而不是继承同一个 `GameEnv`。
- **先明确使用场景再设计 API**：如果一开始做了"采集模式的需求分析"，就不会设计出耦合的 GameEnv。

---

## BUG 11: "异步截屏"实际未异步

### 11.1 阻塞点分析

```python
# collect_data.py 中的 capture_thread
def capture_thread():
    while _alive:
        state, raw, _ = env.observe(save_ss=False, check_death=False)
        # ★ observe() 内部调用了 get_screen_numpy()
        # → _adb_screencap() → subprocess.run(adb exec-out screencap -p)
        # → 阻塞 400-2600ms（取决于 ADB 速度）
        with _lock:
            _latest['state'] = state
            _latest['raw'] = raw
            _latest['ts'] = time.time()

# 主线程
def wait_new_frame():
    while time.time() < deadline:
        with _lock:
            if _latest['ts'] > last_ts:  # ★ 等后台线程产出新帧
                return _latest['state'], _latest['raw']
        time.sleep(0.01)  # ★ 阻塞在这里，不能读键盘！
```

**关键发现**：虽然截屏在后台线程（不阻塞主线程），但主线程的 `wait_new_frame()` 是一个**忙等循环**。在等待新帧的 400-2600ms 内，主线程完全不能读键盘。用户按键的响应延迟 = 截屏延迟，异步架构的优势完全丧失。

### 11.2 为什么停在这里没有继续修

在这个问题上花费了大量时间后，意识到根本矛盾：**截屏本身是瓶颈，异步解决不了截屏慢的问题**。最终选择彻底放弃异步，回归最简单的同步模型：每一步 = 读键 → 发动作 → 截屏 → 存数据 → 循环。虽然每步 ~400ms，但代码只有 150 行，逻辑清晰，永远不会出现竞态。

### 11.3 架构教训

- **异步不能解决"生产者太慢"的问题**：异步的价值在于解耦速率不匹配的生产者和消费者。当消费者需要等生产者时，异步只是把阻塞换了个地方。
- **先优化瓶颈再考虑架构**：截屏从 2+ 秒优化到 400ms 之后，异步就已经不必要了。如果没有先做性能优化，任何架构都是白费。
- **忙等（busy-wait）不是真正的异步**：`while not ready: sleep(0.01)` 仍然是同步语义，只是换成了轮询。真正的异步应该用回调或 `asyncio`。但在 Python 中，`subprocess.run()` 无法被 `asyncio` 中断（除非改用 `asyncio.create_subprocess_exec`）。

---

## 总结：从 11 个 BUG 提炼的设计原则

| # | 原则 | 违反此原则的 BUG |
|---|------|-----------------|
| 1 | **Crash-only software**: 假设每一步都可能崩溃，数据即时持久化 | BUG2 |
| 2 | **显式资源计算**: 容量/内存/磁盘必须基于测量，不能硬编码 | BUG1 |
| 3 | **单一职责**: 一个类/方法只做一件事 | BUG3, BUG10 |
| 4 | **Boolean 参数是坏味道**: 行为分裂意味着类该拆分 | BUG3, BUG10 |
| 5 | **Fast-fail > Slow-retry**: 共享根因的回退链只是延迟惩罚 | BUG5 |
| 6 | **正确性 > 性能**: 不能为加速牺牲数据质量 | BUG4 |
| 7 | **显式契约**: 截屏分辨率应作为 API 契约，违反时报错 | BUG6 |
| 8 | **信号处理应幂等**: 多次 Ctrl+C 不应产生副作用 | BUG7 |
| 9 | **I/O 抽象值得跨平台成本**: 原生 API 的便利不值得绑定单一平台 | BUG8 |
| 10 | **异步不解决慢生产者**: 先优化瓶颈再考虑架构 | BUG9, BUG11 |
| 11 | **采集与训练解耦**: 两个不同用例不应共享同一个类 | BUG3, BUG10 |
| 12 | **测试边界条件**: 帧栈初始化、memmap 容量等边界应在开发阶段测试 | BUG1, BUG4 |
