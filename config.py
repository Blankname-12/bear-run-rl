import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ ADB / 设备配置 ============
DEVICE_SERIAL = "127.0.0.1:5555"
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920
SWIPE_DURATION_MS = 150

# ============ 截屏方式 ============
# "window"  = Windows DXGI 桌面捕获（~17ms，需模拟器窗口可见）
# "scrcpy"  = scrcpy H.264 硬编码 socket 流（~30-50ms）
# "minicap" = minicap 原生 JPEG 流（~20ms）
# "adb"     = ADB screencap（~680ms，兜底方案）
CAPTURE_MODE = "minicap"

# 模拟器窗口标题关键词（自动检测用）
EMULATOR_KEYWORDS = ["雷电", "LDPlayer", "MEmu", "夜神", "Nox", "BlueStacks", "MuMu", "Android", "Emulator"]

# ============ 动作空间 ============
# 0: swipe_up    (跳跃)
# 1: swipe_down  (滑铲)
# 2: swipe_left  (左移)
# 3: swipe_right (右移)
# 4: no_op       (不动)
# 5: long_press  (长按)
ACTION_SPACE = 6
ACTION_NAMES = ["swipe_up", "swipe_down", "swipe_left", "swipe_right", "no_op", "long_press"]
ACTION_LABEL_CN = ["跳跃", "滑铲", "左移", "右移", "不动", "长按"]
LONG_PRESS_CONFIDENCE = 0.90
LONG_PRESS_DURATION_MS = 1000  # 单次长按
LONG_PRESS_REPEAT = 3          # 连续长按次数

# ============ 游戏区域裁剪 ============
GAME_CROP_LEFT = 60
GAME_CROP_TOP = 200
GAME_CROP_RIGHT = 1020
GAME_CROP_BOTTOM = 1700

# ============ CNN 输入配置 ============
IMG_WIDTH = 128
IMG_HEIGHT = 128
FRAME_WIDTH = 128
FRAME_HEIGHT = 128
FRAME_STACK = 1               # PPO 用单帧（与预训练模型输入一致，100% 权重复用）
USE_GRAYSCALE = False

# ============ 预训练配置 ============
PRETRAIN_EPOCHS = 25
PRETRAIN_BATCH = 64
PRETRAIN_LR = 1e-3
PRETRAIN_WEIGHT_DECAY = 1e-4
PRETRAIN_EARLY_STOP = 8       # 验证准确率多少轮不提升就早停
PRETRAIN_VAL_SPLIT = 0.2
DATA_AUGMENT = True           # 水平翻转 + 亮度抖动增强

# ============ 数据路径 ============
LABELED_DATA_DIR = os.path.join(BASE_DIR, "labeled_data")
DATA_DIR = os.path.join(BASE_DIR, "data")

# ============ 模型路径 ============
MODEL_DIR = os.path.join(BASE_DIR, "models")
PRETRAIN_PATH = os.path.join(MODEL_DIR, "pretrain_cnn.pth")
CHECKPOINT_PATH = os.path.join(MODEL_DIR, "checkpoint_ppo.pth")
BEST_MODEL_PATH = os.path.join(MODEL_DIR, "best_ppo.pth")

# ============ PPO 超参数 ============
LEARNING_RATE = 1e-4          # 从预训练启动，用更小的 LR
GAMMA = 0.99
GAE_LAMBDA = 0.95
CLIP_EPSILON = 0.2
ENTROPY_COEF = 0.15
CRITIC_COEF = 0.5
MAX_GRAD_NORM = 0.5

ROLLOUT_STEPS = 256
PPO_EPOCHS = 4
MINI_BATCH_SIZE = 32

# ============ 训练步调 ============
STEP_INTERVAL = 0.5           # 训练时两次动作间隔（秒），比旧版 1.2s 快很多
MIN_ACTION_INTERVAL = 0.25    # 最小动作间隔
FREEZE_BACKBONE_EPOCHS = 0    # PPO 前 N 局冻结骨干，0=不冻结

# ============ 奖励设计（教师蒸馏 + 环境反馈）============
REWARD_TEACHER_MATCH = 1.0     # agent 动作与教师一致 → 强正信号
REWARD_TEACHER_MISMATCH = 0.0  # 不一致不惩罚（鼓励探索，教师只给糖不给鞭子）
REWARD_ALIVE = 0.1             # 每步存活小奖励
REWARD_DEATH = -1.0            # 死亡（不宜太重，否则会扼杀探索）
REWARD_HEART_LOST = -1.0       # 红心消失每帧
REWARD_BOUNDARY = -2.0         # 撞墙

# ============ 死亡检测（红心）============
GAME_OFFSET_X = 0
GAME_OFFSET_Y = 0
HEART_REGION_LEFT = 50
HEART_REGION_TOP = 100
HEART_REGION_RIGHT = 250
HEART_REGION_BOTTOM = 175
HEART_DEBUG = False

HEART_H_MIN1 = 0
HEART_H_MAX1 = 10
HEART_H_MIN2 = 170
HEART_H_MAX2 = 180
HEART_S_MIN = 80
HEART_V_MIN = 80

HEART_PIXEL_RATIO = 0.010
HEART_DEATH_FRAMES = 2        # 连续丢失帧数阈值

# 按钮坐标
GIVE_UP_BUTTON_X = 134
GIVE_UP_BUTTON_Y = 1850
RESTART_BUTTON_X = 544
RESTART_BUTTON_Y = 1800
START_BUTTON_X = 698
START_BUTTON_Y = 1491

# ============ AI 游玩配置 ============
MIN_ACTION_CONFIDENCE = 0.60   # softmax 置信度低于此值不动作（subwAI 风格）
PLAY_FPS_TARGET = 5          # AI 游玩目标帧率

# ============ 保存间隔 ============
SAVE_INTERVAL = 30
BEST_SAVE_WINDOW = 20

# ============ 日志 ============
LOG_DIR = os.path.join(BASE_DIR, "logs")
RUNS_DIR = os.path.join(BASE_DIR, "runs")

# ============ 赛道（RL 用）============
NUM_LANES = 3
LANE_INIT = 2
