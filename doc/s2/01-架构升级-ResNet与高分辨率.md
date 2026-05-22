# S2-01 架构升级：ResNet-18 与 320×320 高分辨率视觉输入

> 所属项目：熊大快跑 RL 训练  
> 实验阶段：S2（第一新对话）  
> 日期：2026-05-20

---

## 1. 原始架构的缺陷

### 1.1 输入分辨率严重不足：128×128

在 S1 阶段，我们使用了 `128×128` 的输入分辨率。

- **游戏画面裁切尺寸**：从 minicap 截取的游戏画面原始裁切区域约为 `960×1500` 像素（宽度约 960px，高度约 1500px，涵盖了跑道区域的竖屏裁切）。
- **障碍物原始尺寸**：游戏中的障碍物（岩羊、石头、木桩等）在裁切后的原始画面上大约占据 50~80 像素的宽度/高度。
- **缩放后的灾难**：当把 `960×1500` 的画面缩放到 `128×128` 时，缩放比约为 1/7.5 ~ 1/11.7。原本 50~80 像素的障碍物被压缩到 **仅 7~10 像素**左右。
- **CNN 的"视觉盲区"**：在 128×128 的输入上，一个 7~10 像素的物体经过典型 CNN 的 stride 下采样（例如 stride=4 的 stem 层之后只剩 ~2 像素），几乎完全消失在特征图中。CNN 根本无法从像素级别"看见"并学习障碍物的语义模式。

### 1.2 特征提取网络过浅：Nature CNN

S1 阶段使用的网络结构是典型的 **Nature CNN**（类似 DQN 原论文的卷积架构）：

| 层 | 卷积核 | 步长 | 通道数 | 激活 |
|---|---|---|---|---|
| Conv1 | 8×8 | 4 | 32 | ReLU |
| Conv2 | 4×4 | 2 | 64 | ReLU |
| Conv3 | 3×3 | 1 | 64 | ReLU |
| FC | - | - | 512 | ReLU |

**问题分析**：

- **仅 3 层卷积**：感受野有限，无法建立从低级纹理到高级语义的多层抽象。
- **通道数不足**：最大仅 64 通道，表征容量不足以编码复杂的障碍物形状、纹理和位置信息。
- **缺乏残差连接**：训练深层网络时容易梯度消失，而浅层网络又没有足够的表达能力。
- **结论**：如此浅的网络从原始像素中学习障碍物语义，本质上是不可行的，模型几乎只能在纯随机策略附近徘徊，很难收到有意义的梯度信号。

### 1.3 奖励函数粗糙且噪声大

S1 的奖励设计：

| 事件 | 奖励值 |
|---|---|
| 存活（每步） | +0.5 |
| 死亡 | -10.0 |
| 处于"危险区域" | 负奖励（基于帧间差分） |

**危险区域检测机制**：

- 在画面底部固定一个矩形"危险区域"（`DANGER_ZONE_X1/X2/Y1/Y2`），大约覆盖跑道前 1/3。
- 对该区域做帧间差分（`cv2.absdiff`），计算差分图的全局均值。
- 如果均值超过阈值 `DANGER_FRAME_DIFF_THRESH = 8.0`，判定为"有障碍物接近"，给予负奖励。

**问题**：

1. **无法定位具体障碍物**：全局均值只关心"有没有变化"，无法区分障碍物出现在左/中/右哪条跑道，也无法区分一个接近的大障碍物和远处的多个小噪点。
2. **噪声极大**：游戏中的光影变化、UI 元素的闪烁、主角自身的动画都会引起帧间差分，导致大量误报。
3. **奖励信号稀疏且不可靠**：模型收到"危险"信号时，不知道危险来自哪里、应该往哪个方向躲避，无法形成有效的策略梯度。

### 1.4 帧率瓶颈：Minicap 上限 15fps

- Minicap 是 Android 端的截屏守护进程，在高分辨率下实测帧率上限约 **12~15fps**。
- 这意味着 512 步 rollout 需要约 **34~43 秒**（512 ÷ 15 = 34.1s），加上推理和训练开销，每个 rollout 需要约 40~50 秒。
- 低帧率导致两个问题：
  - **时间分辨率不足**：高速接近的障碍物可能在两帧之间移动数十像素，模型无法捕捉平滑的运动轨迹。
  - **训练速度慢**：整个训练周期的时间成本大幅增加。

---

## 2. 方案一改造计划总览

针对上述四大缺陷，S2 阶段进行了系统性改造。涉及三个核心文件的修改：

| 文件 | 改造内容 |
|---|---|
| `config.py` | 提升输入分辨率、调整训练超参、重构奖励函数 |
| `agent.py` | 从 Nature CNN 替换为 ResNet-18 + Spatial Attention |
| `game_env.py` | 用全分辨率帧间差分 + 轮廓检测替代固定区域全局阈值 |

---

## 3. config.py 变更详解

### 3.1 输入分辨率提升

```python
# S1 → S2
FRAME_WIDTH  = 128  → 320
FRAME_HEIGHT = 128  → 320
```

**选择 320×320 的理由**：

- 从原始游戏裁切（~960×1500）缩放到 320×320，缩放比约为 1/3 ~ 1/4.7。
- 原本 50~80 像素的障碍物在 320×320 下为 **~13~17 像素**，虽然仍然很小，但经过 ResNet 的 stem（÷4）后仍有 ~3~4 像素，在 layer3（÷16）后仍有 ~0.8~1 像素——至少在前几层保留了一些可分辨的空间信息。
- 320×320 是 32 的倍数（320 = 32 × 10），适配 ResNet 的 32 倍总下采样率。
- 相比于 256（障碍物 ~10~13px）提供了更多余量，相比于 448/512 则平衡了显存消耗。

### 3.2 训练超参调整（应对显存压力）

320×320 的单帧状态占用：3 通道 × 320 × 320 × 1 byte（uint8）= **307,200 字节 ≈ 300KB**。  
float32 格式下为 **1.2MB/帧**。  
以 4 帧堆叠（frame stack）计算，每个状态约为 **4.8MB（float32）或 1.2MB（uint8）**。

| 超参 | S1 值 | S2 值 | 原因 |
|---|---|---|---|
| `ROLLOUT_STEPS` | 512 | 256 | 512 个 states × 4.8MB ≈ 2.4GB（float32）；512 个 states × 1.2MB ≈ 614MB（uint8 优化后）。降为 256 进一步节省显存并加速 rollout |
| `MINI_BATCH_SIZE` | 64 | 32 | 320×320 的 feature map 更大，bottleneck 在 GPU 吞吐而非 batch 并行度 |
| `PRETRAIN_BATCH` | 128 | 32 | 预训练阶段同样受显存限制 |

### 3.3 删除的危险区域配置

```python
# 以下配置全部删除
DANGER_ZONE_X1 = ...  # 固定危险区域边界
DANGER_ZONE_X2 = ...
DANGER_ZONE_Y1 = ...
DANGER_ZONE_Y2 = ...
DANGER_FRAME_DIFF_THRESH = 8.0  # 全局均值阈值
REWARD_DANGER = ...  # 旧版危险奖励
```

### 3.4 新增的障碍物检测配置

```python
# 帧间差分阈值：absdiff 灰度值 > 25 视为运动像素
OBSTACLE_FRAME_DIFF_THRESH = 25

# 轮廓最小面积：过滤噪点，小于 300px² 的连通域忽略
OBSTACLE_MIN_AREA = 300

# 障碍物出现在屏幕纵轴位置
# y_ratio < 0.65 → 进入"危险区"（在跑道前方 65% 高度以上）
# y_ratio < 0.92 → 进入"临界区"（即将碰撞，非常接近底部）
OBSTACLE_DANGER_Y_RATIO = 0.65
OBSTACLE_CRITICAL_Y_RATIO = 0.92
```

各阈值的设计依据：
- **DIFF_THRESH=25**：在 320×320 灰度图上，帧间差分值 25 能有效区分真实物体移动（通常 30~80）和照明/噪声伪影（通常 <15）。
- **MIN_AREA=300**：320×320 下 300px² ≈ 面积占比 0.29%，对应约 17×17 像素的区域，恰好覆盖缩放后的最小可识别障碍物。
- **DANGER_Y_RATIO=0.65**：当障碍物的 y 坐标中心位于画面 65% 高度以上时（y=0 为顶部），判定为进入危险区域，给予躲避奖励或惩罚。
- **CRITICAL_Y_RATIO=0.92**：非常接近底部，此时障碍物几乎与主角接触，用于死亡检测辅助。

### 3.5 重构的奖励函数

```python
REWARD_ALIVE  = 0.2   # 每步存活奖励（降低了门槛，增加探索）
REWARD_AVOID  = 1.0   # 成功躲开障碍物（从所在跑道切换到安全跑道）
REWARD_MISS   = -2.0  # 障碍物在所在跑道但没有躲避
REWARD_CLEAR  = 0.05  # 前方没有障碍物，继续直行
```

相比 S1 的 `ALIVE=0.5`，S2 将存活奖励降到 0.2，使模型不再依赖"苟活"就能获得可观奖励。新的奖励结构鼓励主动避障：
- 成功躲避 → 高正向激励（`+1.0`）
- 忽略障碍物 → 强惩罚（`-2.0`）
- 前方安全 → 小额正向激励 `+0.05`（鼓励保持前进，但不会让模型满足于静止）

### 3.6 截屏方式尝试

```python
# 尝试顺序
CAPTURE_MODE = "minicap"  # 初始：15fps，延迟低但帧率不足
              → "window"  # 尝试直接截取窗口（Windows+scrcpy）：需 window title 匹配，不稳定
              → "adb"     # ADB screencap：稳定但延迟更高（~50-80ms/帧）
```

最终在 S2 中采用 `adb` 模式，虽然单帧延迟略高，但稳定性和兼容性最好。

---

## 4. agent.py — ResNet-18 架构详解

### 4.1 自实现 BasicBlock

出于教学和可控性考虑，我们没有使用 torchvision 的预训练 ResNet，而是手动实现了 BasicBlock。

```python
class BasicBlock(nn.Module):
    """
    ResNet BasicBlock: 3×3 conv → BN → ReLU → 3×3 conv → BN → + shortcut

    当 in_channels != out_channels 或 stride != 1 时，
    shortcut 使用 1×1 conv + BN 进行维度匹配。
    """
    expansion = 1

    def __init__(self, in_channels, out_channels, stride=1):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=3,
                               stride=stride, padding=1, bias=False)
        self.bn1   = nn.BatchNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3,
                               stride=1, padding=1, bias=False)
        self.bn2   = nn.BatchNorm2d(out_channels)

        self.shortcut = nn.Sequential()
        if stride != 1 or in_channels != out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1,
                          stride=stride, bias=False),
                nn.BatchNorm2d(out_channels)
            )

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = F.relu(out)
        return out
```

设计要点：
- **bias=False**：因为 BN 层自带可学习偏移量，卷积层的 bias 冗余。
- **shortcut 1×1 卷积**：仅在维度不匹配时插入，用于通道对齐和下采样。
- **expansion=1**：与 Bottleneck(expansion=4) 区分，保持通道数稳定。

### 4.2 完整网络结构

#### Stem（输入预处理）

```
Input: (batch, 4, 320, 320)  # 4 帧灰度图堆叠（frame stack）

Conv2d(4→64, kernel=7, stride=2, padding=3, bias=False)
BatchNorm2d(64)
ReLU
MaxPool2d(kernel=3, stride=2, padding=1)

Output: (batch, 64, 80, 80)
# 320 → 160 (conv stride=2) → 80 (pool stride=2)
```

#### 主干网络（layer1 ~ layer4 + Spatial Attention）

```
layer1: BasicBlock(64 → 64,  stride=1) × 2   → (batch, 64,  80, 80)
layer2: BasicBlock(64 → 128, stride=2) × 2   → (batch, 128, 40, 40)
layer3: BasicBlock(128→ 256, stride=2) × 2   → (batch, 256, 20, 20)

===== Spatial Attention Module =====
  Conv2d(256→1, kernel=7, padding=3) → Sigmoid → multiply back
  # 生成空间注意力图，突出前景障碍物区域，抑制背景

layer4: BasicBlock(256→ 512, stride=2) × 2   → (batch, 512, 10, 10)

Global Average Pooling → (batch, 512)
```

#### 双头输出（Actor-Critic）

```
共享特征: (batch, 512)

Actor Head:
  Linear(512 → 256) → ReLU → Linear(256 → 5)
  输出 5 个动作的 logits: [不动, 左移, 右移, 上滑(跳), 下滑(滑铲)]

Critic Head:
  Linear(512 → 256) → ReLU → Linear(256 → 1)
  输出状态价值估计 V(s)
```

### 4.3 整体维度流转表

| 层 | 输入尺寸 | 输出尺寸 | 通道数 |
|---|---|---|---|
| Input | 320×320 | - | 4 |
| Stem Conv(k7,s2) | 320×320 | 160×160 | 64 |
| MaxPool(k3,s2) | 160×160 | 80×80 | 64 |
| layer1 (stride=1) | 80×80 | 80×80 | 64 |
| layer2 (stride=2) | 80×80 | 40×40 | 128 |
| layer3 (stride=2) | 40×40 | 20×20 | 256 |
| Spatial Attention | 20×20 | 20×20 | 256 |
| layer4 (stride=2) | 20×20 | 10×10 | 512 |
| GAP | 10×10 | 1×1 | 512 |
| Actor | 512 | (5,) | - |
| Critic | 512 | (1,) | - |

### 4.4 参数量估算

| 模块 | 参数量（约） |
|---|---|
| Stem | 4 × 64 × 7 × 7 = 12,544 |
| layer1 (2×BasicBlock) | ~73,728 |
| layer2 (2×BasicBlock) | ~379,904 |
| layer3 (2×BasicBlock) | ~1,507,328 |
| Spatial Attention | 256 × 1 × 7 × 7 = 12,544 |
| layer4 (2×BasicBlock) | ~5,996,544 |
| Actor Head | 512×256 + 256×5 = 132,352 |
| Critic Head | 512×256 + 256×1 = 131,328 |
| **总计** | **~11,246,272 ≈ 11M** |

约 1100 万参数，在 RTX 4060 8GB 上完全可以容纳，且 batch size 32 时显存占用约 2~3GB。

### 4.5 RolloutBuffer 的 uint8 存储优化

```python
class RolloutBuffer:
    """
    PPO rollout 缓冲区。

    关键优化：状态以 uint8 存储而非 float32。
    - float32 [0, 1] → uint8 [0, 255] 量化
    - 存储空间从 4 bytes/pixel 降到 1 byte/pixel
    - 节省 75% 内存
    - 使用时解量化为 float32 / 255.0
    """
```

对于 320×320×4 帧的单个状态：
- float32: 320 × 320 × 4 × 4 = 1,638,400 bytes ≈ 1.56MB
- uint8: 320 × 320 × 4 × 1 = 409,600 bytes ≈ 0.39MB
- 256 步 rollout: 256 × (1.56 - 0.39) = 299.5MB 节省

### 4.6 旧模型兼容性处理

```python
def load_checkpoint(self, path):
    """
    加载检查点时自动检测旧架构（Nature CNN）。
    如果 checkpoint 中的 state_dict 键与当前模型不匹配，
    打印警告并跳过加载，使用随机初始化的权重开始训练。
    """
    checkpoint = torch.load(path, map_location=self.device)
    model_state = checkpoint.get('model_state_dict', checkpoint)

    # 尝试加载，如果 key 不匹配则说明是旧架构
    try:
        self.policy.load_state_dict(model_state, strict=True)
        print("[Agent] Checkpoint 加载成功")
    except RuntimeError as e:
        print(f"[Agent] 架构不兼容，跳过旧 checkpoint\n  {str(e)[:200]}")
        print("[Agent] 将使用随机初始化权重开始训练")
```

---

## 5. game_env.py — 全分辨率帧间差分障碍物检测

### 5.1 旧方案回顾

S1 的做法（已全部删除）：

| 函数 | 功能 | 缺陷 |
|---|---|---|
| `_extract_danger_zone_gray()` | 从画面底部裁切固定矩形，转灰度 | 固定范围无法覆盖远处障碍物 |
| `_compute_danger_level()` | 帧间差分全局均值与阈值比较 | 无法定位障碍物、无法区分跑道 |
| `_check_danger()` | 综合判断是否处于危险状态 | 只有二元输出（危险/安全） |

### 5.2 新方案：`_detect_obstacles()` 详细流程

```python
def _detect_obstacles(self, frame, prev_frame):
    """
    全分辨率帧间差分 + 轮廓检测 障碍物定位。

    输入：
        frame:      当前帧 RGB (H, W, 3)，原始尺寸
        prev_frame: 前一帧 RGB (H, W, 3)

    返回：
        obstacles: List[(lane_idx, y_ratio, area)]
          - lane_idx: 0=左, 1=中, 2=右
          - y_ratio:  障碍物中心 y 坐标 / 画面高度 (0=顶部, 1=底部)
          - area:     轮廓面积 (像素²)
    """
```

**步骤详解**：

1. **帧间差分**
   ```python
   diff = cv2.absdiff(frame, prev_frame)
   gray = cv2.cvtColor(diff, cv2.COLOR_RGB2GRAY)
   ```
   直接在全分辨率（未缩放）的帧间差分灰度图上操作，保留原始像素精度。

2. **二值化**
   ```python
   _, thresh = cv2.threshold(gray, OBSTACLE_FRAME_DIFF_THRESH, 255,
                              cv2.THRESH_BINARY)
   ```
   阈值 25：低于 25 的像素（静止背景、微小光照变化）被过滤。

3. **形态学开运算**
   ```python
   kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
   cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
   ```
   开运算（先腐蚀后膨胀）去除孤立噪点，同时保护障碍物的连通域完整性。

4. **轮廓检测**
   ```python
   contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
   ```

5. **过滤与分类**
   ```python
   for cnt in contours:
       area = cv2.contourArea(cnt)
       if area < OBSTACLE_MIN_AREA:
           continue  # 过滤过小噪声

       M = cv2.moments(cnt)
       if M["m00"] == 0:
           continue
       cx = int(M["m10"] / M["m00"])  # 轮廓质心 x
       cy = int(M["m01"] / M["m00"])  # 轮廓质心 y

       y_ratio = cy / frame_height

       # 跑道划分（左中右三跑道）
       lane_w = frame_width / 3
       lane_idx = int(cx / lane_w)
       lane_idx = min(lane_idx, 2)  # clamp to [0, 2]

       obstacles.append((lane_idx, y_ratio, area))
   ```

6. **返回值排序**：按 `y_ratio` 降序排列（距离主角最近的在前）。

### 5.3 `_compute_obstacle_reward()` 奖励计算逻辑

```python
def _compute_obstacle_reward(self, action, obstacles, current_lane):
    """
    基于障碍物检测结果和动作，计算奖励。

    action:        0=不动, 1=左, 2=右, 3=跳, 4=滑
    obstacles:     _detect_obstacles() 的返回值
    current_lane:  主角当前所在跑道 (0, 1, 2)

    返回: reward (float)
    """
```

**打分逻辑**：

```
遍历所有检测到的障碍物 obstacles:
    if obstacle.y_ratio < OBSTACLE_DANGER_Y_RATIO:    # 障碍物在远方（画面上部）
        continue  # 忽略，还未进入关注范围

    if obstacle.lane_idx == current_lane:               # 障碍物在我的跑道上！
        if action in [MOVE_LEFT, MOVE_RIGHT]:           # 模型执行了变道
            return REWARD_AVOID (+1.0)                  # 成功躲避！
        else:
            return REWARD_MISS (-2.0)                   # 没躲！严重惩罚

    # 障碍物在其他跑道 → 不影响我

# 循环结束：没有障碍物在我的跑道上
if action == NOOP:
    return REWARD_CLEAR (+0.05)  # 前方安全，保持直行
else:
    return REWARD_ALIVE (+0.2)   # 前方安全但做了多余动作，给基础存活奖励
```

这个设计的关键思想是 **只在确实存在威胁时给予强信号**，避免无差别惩罚导致的策略退化。

### 5.4 差分冷却（Diff Cooldown）机制

```python
self._diff_cooldown = 0  # 冷却计数器

def _step(self, action):
    ...
    if action in [MOVE_LEFT, MOVE_RIGHT]:
        self._diff_cooldown = 2  # 变道后冷却 2 帧
    ...
```

**为什么需要冷却**：

左右变道时，整个画面会发生显著的横向位移，帧间差分会检测到大量"变化"，但这些变化并非障碍物，而是整个背景的平移。冷却 2 帧可以让画面稳定后再恢复差分检测，避免因主角位移导致的虚假障碍物检测。

### 5.5 observe() 的 check_death 参数

```python
def observe(self, check_death=True):
    """
    获取当前状态。

    check_death: bool
        True  → 在 rollout 中使用，需要检测死亡并终止 episode
        False → 在数据收集/预训练中使用，仅收集帧不做死亡判断
    """
```

这个分离是为了在数据收集阶段（如人类演示录制）不触发死亡重置。

### 5.6 初始化帧堆栈的改进

S1 的做法：
```python
# 启动时只捕获 1 帧，复制 4 次填充 frame stack
frames = [first_frame] * 4
```

S2 的改进：
```python
# 启动时连续捕获 4 帧，每帧间隔 50ms，构建真实的帧堆栈
frames = []
for i in range(4):
    frames.append(self._capture_frame())
    if i < 3:
        time.sleep(0.05)  # 50ms 间隔
```

这确保了 frame stack 从第一步起就包含真实的运动信息，而非 4 张完全相同的图片。对于依赖帧间运动检测的障碍物识别尤其重要。

---

## 6. 为什么纯视觉方案仍然困难

即使升级到 ResNet-18 + 320×320，纯视觉方案仍然面临严峻挑战：

### 6.1 障碍物在特征图中的"消失"问题

以 320×320 输入、障碍物原始尺寸约 50px 为例：

| 阶段 | 空间尺寸 | 障碍物尺寸（约） | 可辨识度 |
|---|---|---|---|
| 缩放前（原始裁切） | ~960×1500 | ~50px | 清晰 |
| 缩放后（320×320） | 320×320 | ~13.3px | 勉强 |
| Stem（Conv s2 + Pool s2） | 80×80 | ~3.3px | 模糊轮廓 |
| layer2（s2 累计 ÷8） | 40×40 | ~1.7px | 几乎只剩一个点 |
| layer3（s2 累计 ÷16） | 20×20 | ~0.8px | **小于 1 像素，完全消失** |
| layer4（s2 累计 ÷32） | 10×10 | ~0.4px | **不存在** |

这意味着，在 ResNet 的深层特征图中，障碍物几乎不可见。CNN 只能依赖浅层的外观模式（边缘、纹理）来推断障碍物的存在，无法在深层获得明确的"障碍物在此"的语义特征。

### 6.2 从零学习的困难

- **稀疏奖励**：即使在 S2 改进后，明确的奖励信号（`+1.0`/`-2.0`）仍然只在障碍物接近危险区时才出现，大部分时间只有微弱的 `+0.2` 存活奖励。
- **探索困难**：模型需要在巨大的像素空间中，通过随机的动作尝试，碰巧发现"看到某图案 → 左右移动 → 获得 +1.0"的规律。这在没有引导的情况下极难收敛。
- **像素模式的多样性**：同一类障碍物（如岩羊）在不同光照、不同距离、不同角度下的像素外观变化很大，CNN 需要大量样本才能学会不变性。

### 6.3 时序信息的必要性

即使有 4 帧堆叠，从静止帧中推断物体的运动速度、加速度和碰撞时间（TTC）也非常困难。人类玩家依赖连续的视觉流来判断"这个障碍物会什么时候撞到我"，而 CNN 只有 4 帧的快照。

---

## 7. 训练时间估算

### 7.1 纯 RL 训练（从零开始）

| 参数 | 值 |
|---|---|
| 目标步数 | 500,000 steps |
| Rollout 步数/次 | 256 steps |
| 需要 rollout 次数 | 500,000 / 256 ≈ 1,953 次 |
| 单次 rollout 耗时 | ~70 秒（minicap 15fps: 256/15=17s + 训练约 50s + 开销 3s） |
| GPU | RTX 4060 8GB |
| **预计总耗时** | 1,953 × 70s ≈ **38 小时** |

考虑到 minicap 偶尔掉帧和系统开销，实际估算约为 **40~50 小时**。

### 7.2 含行为克隆预训练的训练

| 阶段 | 耗时 |
|---|---|
| 人类演示录制 | ~15 分钟（手动玩几局，收集约 2000~3000 帧） |
| BC 预训练 | ~5 分钟（在 RTX 4060 上做几轮监督学习，模仿人类动作） |
| PPO 微调 | ~3~6 小时（模型已有基本避障能力，收敛更快） |
| **总计** | **~3~7 小时** |

行为克隆预训练的核心价值：
- 为 CNN 提供了良好的初始特征表示，使浅层的边缘/纹理检测器能够收敛到有意义的方向。
- 为策略提供了"遇到障碍物就变道"的基本行为先验，PPO 微调只需要在这个基础上优化时机和精度。
- BC 阶段直接使用交叉熵损失监督动作分类，梯度信号远比 RL 的稀疏奖励密集。

---

## 8. 文件变更清单

| 文件 | 变更类型 | 主要改动 |
|---|---|---|
| `config.py` | 修改 | 分辨率 128→320、超参调整、重构奖励配置、删除危险区域配置 |
| `agent.py` | 重写 | Nature CNN → ResNet-18 + Spatial Attention + uint8 buffer |
| `game_env.py` | 重写 | 固定区域差分 → 全分辨率轮廓检测、帧堆栈初始化改进、冷却机制 |
