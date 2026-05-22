<p align="center">
  <h1 align="center">🐻 熊大快跑 — 强化学习智能体</h1>
  <p align="center">
    <strong>Behavior Cloning + PPO Reinforcement Learning for Temple Run-style Mobile Games</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/PyTorch-2.0+-ee4c2c?logo=pytorch" alt="PyTorch">
    <img src="https://img.shields.io/badge/Platform-Android%20Emulator-3DDC84?logo=android" alt="Android">
    <img src="https://img.shields.io/badge/Capture-Minicap-4285F4" alt="Minicap">
    <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  </p>
</p>

---

## 📖 简介

一个基于 **行为克隆（Behavior Cloning）+ PPO 强化学习** 的 Android 游戏 AI 智能体。通过 ADB 控制雷电模拟器，使用 minicap 高速截屏，训练 CNN 模型自动玩 Temple Run 类跑酷游戏。

**两阶段训练**：
1. **预训练**：人工标注游戏画面 → CNN 行为克隆（参考 [Subway-Surfers-AI](https://github.com/vinaymancha/Subway-Surfers-AI)）
2. **PPO 微调**：教师蒸馏 + 环境奖励，在线强化学习提升表现

> 项目为一门强化学习课程的期末课设。完整技术文档见 [`doc/s4/`](doc/s4/)。

---

## 🎮 演示

```
# 使用预训练 CNN 模型游玩
python play_ai.py --pretrain

# 使用 PPO 训练后的模型游玩
python play_ai.py

# 录制游玩视频
python play_ai.py --pretrain --record
```

| 操作键 | 功能 |
|--------|------|
| `Q` | 退出 |
| `S` | 切换慢动作 |
| `R` | 开始/停止录制 |
| `Space` | 手动重启游戏 |

---

## 🧠 工作原理

```
┌──────────────┐    ADB exec-out     ┌──────────────┐
│  雷电模拟器    │ ◄── minicap JPEG ── │   Python 端   │
│  (LDPlayer)   │ ── input text ──► │  (训练/推理)   │
│  熊大快跑.apk  │                    │  PyTorch CNN  │
└──────────────┘                    └──────┬───────┘
       ▲                                   │
       │        128×128 RGB 单帧            │
       │     ┌─────────────────────┐       │
       └─────│  SimpleCNN /        │───────┘
             │  ActorCriticCNN     │
             │  4 ConvBlock → 4096 │
             │  → Actor + Critic   │
             └─────────────────────┘
```

- **截屏**：minicap 单帧 JPEG（~80-150ms），支持模拟器后台运行
- **触控**：`adb shell input text w/a/s/d`（LDPlayer 键盘映射） + `input swipe` 长按
- **死亡检测**：HSV 红心区域像素比 + 多帧投票 + 广告弹窗识别
- **6 类动作**：跳跃 / 滑铲 / 左移 / 右移 / 不动 / 长按

---

## 📁 项目结构

```
├── config.py              # 全局配置（超参/路径/奖励/动作空间）
├── model.py               # SimpleCNN + ActorCriticCNN 模型定义
│
├── label_game.py          # 数据标注工具（键盘操控 + 自动存图）
├── pretrain_cnn.py        # 行为克隆预训练（类别平衡 + 数据增强）
├── train_ppo.py           # PPO 强化学习训练（教师蒸馏 + GAE + 死亡回溯）
├── play_ai.py             # AI 游玩可视化（实时画面 + HUD）
├── predict_image.py       # 单张图片预测工具
│
├── game_env.py            # 游戏环境（截屏 + 触控 + 死亡检测 + 重启）
├── fast_io.py             # 高性能 IO（FastTouch + MinicapCapture）
│
├── visualize_pipeline.py  # 训练曲线可视化
├── generate_report.py     # 实验报告生成
│
├── labeled_data/          # 标注数据（up/down/left/right/noop/long_press）
├── models/                # 训练好的模型权重
├── logs/                  # 训练日志（CSV）
├── doc/s4/                # 完整技术文档（开发流程 + 设计决策）
├── test/                  # ADB 基础测试
└── bin/minicap/           # minicap 二进制文件
```

---

## 🚀 快速开始

### 环境要求

- Windows 10/11 + Python 3.8+
- 雷电模拟器（LDPlayer） + 熊大快跑.apk
- ADB 已添加到 PATH

### 安装

```bash
# 克隆仓库
git clone https://github.com/Blankname-12/bear-run-rl.git
cd bear-run-rl

# 安装依赖
pip install torch numpy opencv-python pillow matplotlib
```

### 步骤 1：连接模拟器

```bash
# 启动雷电模拟器，开启 ADB 调试，然后连接
adb connect 127.0.0.1:5555

# 测试连接
python test/test.py

# 推送 minicap 到设备
python -c "from fast_io import get_minicap; get_minicap()"
```

### 步骤 2：采集标注数据

```bash
python label_game.py
```

| 键盘 | 动作 |
|------|------|
| `W` | 跳跃 |
| `S` | 滑铲 |
| `A` | 左移 |
| `D` | 右移 |
| `Space` | 不动（自动间隔采集） |
| `L` | 长按 |
| `Space` | 开始/停止录制 |
| `U` | 撤销上一张 |

### 步骤 3：预训练 CNN

```bash
python pretrain_cnn.py               # 自动读 labeled_data/
python pretrain_cnn.py --epochs 50   # 自定义轮数
```

输出：`models/pretrain_cnn.pth`

### 步骤 4：PPO 强化学习微调

```bash
python train_ppo.py                  # 从预训练权重启动
python train_ppo.py --no-pretrain    # 从头训练
python train_ppo.py --resume         # 从检查点恢复
```

输出：`models/checkpoint_ppo.pth` + `models/best_ppo.pth`

### 步骤 5：观看 AI 游玩

```bash
python play_ai.py --pretrain         # 纯 CNN 模型
python play_ai.py                    # PPO 模型
```

---

## 🏗️ 模型架构

### SimpleCNN（行为克隆）

```
Input: (3, 128, 128) 单帧 RGB
  ↓
Conv2d(3→32, 3×3) → ReLU → MaxPool(/2)   ─┐
Conv2d(32→64, 3×3) → ReLU → MaxPool(/4)    │ 4 个
Conv2d(128→128, 3×3) → ReLU → MaxPool(/8)  │ ConvBlock
Conv2d(128→256, 3×3) → ReLU → MaxPool(/16)─┘
  ↓
AdaptiveAvgPool(4×4) → Flatten → 4096d
  ↓
Linear(4096→256) → ReLU → Dropout(0.3) → Linear(256→6)
  ↓
Output: 6-class softmax (跳/滑/左/右/不动/长按)
```

### ActorCriticCNN（PPO 强化学习）

```
共享骨干 ← 与 SimpleCNN 100% 兼容，预训练权重 1:1 加载
  ├── Actor Head:  Linear(4096→256) → ReLU → Linear(256→6)
  └── Critic Head: Linear(4096→256) → ReLU → Linear(256→1)
```

---

## 🎯 关键技术决策

| 决策 | 理由 |
|------|------|
| **输入统一为 3ch×128×128 单帧** | PPO 与预训练模型输入格式完全一致，权重 100% 复用 |
| **教师蒸馏 + 轻惩罚** | 教师模型提供逐帧动作参考（匹配 +1.0），不惩罚偏离（mismatch = 0） |
| **noop 轻微惩罚 (-0.1)** | 避免策略坍缩到完全不动作 |
| **死亡轻罚 (-1.0)** | 不会扼杀探索，约 10 次正确动作可抵一次死亡 |
| **ENTROPY_COEF = 0.15** | 维持策略多样性，打破"左左停右右停"定式 |
| **数据增强仅颜色/模糊** | 游戏画面不会空间变换（翻转/旋转），仅做亮度/对比度/饱和/模糊 |
| **类别加权损失** | 滑铲、长按等少数类自动获得更高权重 |
| **minicap 单帧截图** | 比 ADB screencap 快 5-8 倍，支持模拟器后台运行 |

---

## 📊 训练监控

训练日志自动保存到 `logs/ppo_YYYYMMDD_HHMMSS.csv`：

| episode | steps | reward | avg_reward_100 | avg_reward_20 | total_steps | elapsed_sec |
|---------|-------|--------|----------------|---------------|-------------|-------------|
| 1 | 42 | -5.30 | -5.30 | -5.30 | 42 | 65.2 |
| 30 | 78 | 12.40 | 3.20 | 8.70 | 1890 | 1800.5 |
| ... | ... | ... | ... | ... | ... | ... |

模型自动保存：
- **每 30 局** → `models/checkpoint_ppo.pth`（含 optimizer 状态，支持 `--resume` 恢复）
- **打破最佳 avg20** → `models/best_ppo.pth`

---

## 🙏 致谢

- [Subway-Surfers-AI](https://github.com/vinaymancha/Subway-Surfers-AI) — 纯 CNN 行为克隆方案的灵感来源
- [minicap](https://github.com/DeviceFarmer/minicap) — 高性能 Android 截屏
- 雷电模拟器 (LDPlayer) — 稳定的 Android 模拟环境

---

## 📄 License

MIT License

---

<p align="center">
  <sub>Made with ❤️ for Reinforcement Learning</sub>
</p>
