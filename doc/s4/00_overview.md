# Session 4 总览 — PPO 训练全流程调优

**日期**: 2026-05-20
**状态**: 预训练能跑 1 分半（躲 5+ 障碍物），PPO 在调优中

---

## 本会话涉及的文件

| 文件 | 改动内容 |
|---|---|
| `config.py` | FRAME_STACK=1, FRAME_W/H=128, ENTROPY_COEF=0.15, REWARD_DEATH=-1.0, REWARD_MISMATCH=0, 删重复FRAME_STACK |
| `model.py` | ActorCriticCNN input_channels=3, 简化 load_pretrained_backbone |
| `train_ppo.py` | 统一输入格式, 去掉帧堆叠/跑道编码, 教师蒸馏+环境奖励, 死亡回溯 |
| `play_ai.py` | PPO路径改单帧128x128, 智能防抖(skip2nd/allow3rd), 去时间间隔 |
| `pretrain_cnn.py` | PIL弱增强(颜色+模糊), 类别加权损失 |
| `fast_io.py` | long_press改 subprocess.run 同步调用 |
| `game_env.py` | send_action(5)去重循环 |

## 核心改动路线图

```
输入格式对齐 (3ch 128x128)
    ↓
奖励设计五轮迭代 (教师蒸馏+轻惩罚)
    ↓
探索度调优 (ENTROPY 0.02→0.15)
    ↓
数据增强精简 (去翻转, 保留颜色/模糊)
    ↓
防抖策略 (skip 2nd, allow 3rd+)
    ↓
长按修复 (Popen→subprocess.run)
```
