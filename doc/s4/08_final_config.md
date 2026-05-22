# S4-08 最终配置参数

## 数据/模型路径
```
DEVICE_SERIAL = "127.0.0.1:5555"
CAPTURE_MODE = "minicap"
LABELED_DATA_DIR = ./labeled_data
PRETRAIN_PATH = ./models/pretrain_cnn.pth
CHECKPOINT_PATH = ./models/checkpoint_ppo.pth
BEST_MODEL_PATH = ./models/best_ppo.pth
```

## 模型输入
```
IMG_WIDTH = 128
IMG_HEIGHT = 128
FRAME_WIDTH = 128
FRAME_HEIGHT = 128
FRAME_STACK = 1          # 单帧，与预训练一致
USE_GRAYSCALE = False    # RGB
ACTION_SPACE = 6         # 跳/滑/左/右/不动/长按
NUM_LANES = 3            # 仅用于赛道跟踪，不参与模型输入
```

## 预训练
```
PRETRAIN_EPOCHS = 25
PRETRAIN_BATCH = 64
PRETRAIN_LR = 1e-3
PRETRAIN_WEIGHT_DECAY = 1e-4
PRETRAIN_EARLY_STOP = 8
PRETRAIN_VAL_SPLIT = 0.2
DATA_AUGMENT = True      # 颜色+模糊增强
```

## PPO超参
```
LEARNING_RATE = 1e-4
GAMMA = 0.99
GAE_LAMBDA = 0.95
CLIP_EPSILON = 0.2
ENTROPY_COEF = 0.15       # ★ 从0.02提到0.15
CRITIC_COEF = 0.5
MAX_GRAD_NORM = 0.5
ROLLOUT_STEPS = 256
PPO_EPOCHS = 4
MINI_BATCH_SIZE = 32
```

## 奖励（最终版）
```
REWARD_TEACHER_MATCH = 1.0     # 匹配教师
REWARD_TEACHER_MISMATCH = 0.0  # 不匹配不罚
REWARD_ALIVE = 0.1             # 存活 (无教师时用)
REWARD_DEATH = -1.0            # 死亡 (原-10)
REWARD_HEART_LOST = -1.0       # 红心丢失 (当前已不生效)
# noop硬编码 -0.1
```

## 步调
```
STEP_INTERVAL = 0.5            # 两次动作间隔
MIN_ACTION_INTERVAL = 0.25     # 最小间隔 (play_ai不再使用)
PLAY_FPS_TARGET = 5            # AI游玩帧率
SWIPE_DURATION_MS = 150        # 滑动持续时间
LONG_PRESS_DURATION_MS = 1000  # 长按持续时间
LONG_PRESS_CONFIDENCE = 0.90   # 长按置信度门槛
MIN_ACTION_CONFIDENCE = 0.30   # 低置信度过滤
```

## 模型保存
```
SAVE_INTERVAL = 30             # 每30局保存checkpoint
BEST_SAVE_WINDOW = 20          # avg20用于判断最佳模型
```

## 死亡检测
```
HEART_REGION = (50,100) → (250,175)
HEART_H_MIN1=0, H_MAX1=10
HEART_H_MIN2=170, H_MAX2=180
HEART_S_MIN=80, HEART_V_MIN=80
HEART_PIXEL_RATIO = 0.010
HEART_DEATH_FRAMES = 2
```

## 游戏按钮坐标 (1080×1920)
```
GIVE_UP_BUTTON = (134, 1850)
RESTART_BUTTON = (544, 1800)
START_BUTTON = (698, 1491)
```
