"""
AI 游玩可视化 — 加载模型，观看 AI 玩熊大快跑

用法:
  python play_ai.py                # PPO 模型
  python play_ai.py --pretrain     # 纯 CNN 预训练模型
  python play_ai.py --record       # 录制视频

操作: Q=退出  S=慢动作  R=录制  Space=重启
"""

import os, sys, time
from collections import deque
from datetime import datetime

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFont

import config
from model import SimpleCNN, ActorCriticCNN
from game_env import (get_screen_numpy, send_action,
                      ensure_adb_connected, _capture_scale, tap_screen,
                      _crop_game_area)
from game_env import _detect_heart_pixels
DISPLAY_W = 480
DISPLAY_H = 854
SYMBOLS = ["^", "v", "<-", "->", "o", "#"]
COLORS_BGR = [(0,255,0), (255,0,0), (255,165,0), (0,255,255), (128,128,128), (255,0,255)]
COLORS_HEX = ["#2ecc71", "#e74c3c", "#3498db", "#f39c12", "#95a5a6", "#e040fb"]

# 尝试加载中文字体
_FONT = None
_FONT_SMALL = None
for fp in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
           "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/arial.ttf"]:
    if os.path.exists(fp):
        try:
            _FONT = ImageFont.truetype(fp, 18)
            _FONT_SMALL = ImageFont.truetype(fp, 14)
            break
        except Exception:
            pass


def pil_text(img_bgr, text, xy, color_hex="#fff", font=None):
    """用 PIL 在 BGR numpy 图片上画文字（支持中文），返回 BGR 图片"""
    h, w = img_bgr.shape[:2]
    pil_img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = font or _FONT or ImageFont.load_default()
    draw.text(xy, text, fill=color_hex, font=font)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def draw_hud(frame, action, confidence, fps, action_history, info_cn):
    """全中文 HUD，用 PIL 渲染"""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 70), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.5, frame, 0.5, 0)

    # 帧率
    frame = pil_text(frame, f"帧率:{fps:.0f}", (8, 4), "#c8c8c8", _FONT_SMALL)

    # 当前动作
    if action is not None:
        color = COLORS_HEX[action]
        text = f"{SYMBOLS[action]} {config.ACTION_LABEL_CN[action]}"
        frame = pil_text(frame, text, (8, 22), color, _FONT)

    # 置信度
    frame = pil_text(frame, f"置信度:{confidence:.0%}", (8, 48), "#c8c8c8", _FONT_SMALL)

    # 动作历史条
    if action_history:
        bar_y = h - 30
        bar_h = 16
        bar_w = max(w // len(action_history), 2)
        for i, a in enumerate(action_history):
            cv2.rectangle(frame, (i*bar_w, bar_y), (i*bar_w+bar_w-1, bar_y+bar_h),
                          COLORS_BGR[a], -1)

    # 底部状态
    cv2.rectangle(frame, (0, h-18), (w, h), (0, 0, 0), -1)
    frame = pil_text(frame, info_cn, (4, h-20), "#b0b0b0", _FONT_SMALL)

    return frame


# ═══════════════════════════════════════════
# AI 玩家
# ═══════════════════════════════════════════

class AIPlayer:
    def __init__(self, model_type="ppo", model_path=None):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model_type = model_type
        self.frame_stack = []
        self._current_lane = config.LANE_INIT
        self._last_action = None
        self._last_action_time = 0
        self._same_count = 0  # 连续相同动作计数

        if model_type == "cnn":
            self._init_cnn(model_path)
        else:
            self._init_ppo(model_path)

    def _init_cnn(self, model_path):
        path = model_path or config.PRETRAIN_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(f"Model not found: {path}")
        self.model = SimpleCNN(3, config.ACTION_SPACE).to(self.device)
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ckpt.get("model_state", ckpt))
        self.model.eval()
        print(f"[AI] CNN模型已加载: {path}  验证准确率={ckpt.get('val_acc','?')}")

    def _init_ppo(self, model_path):
        path = model_path or config.CHECKPOINT_PATH
        if not os.path.exists(path):
            path = config.BEST_MODEL_PATH
        if not os.path.exists(path):
            raise FileNotFoundError("No PPO model found")
        self.model = ActorCriticCNN(3, config.ACTION_SPACE).to(self.device)
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ckpt.get("network", ckpt))
        self.model.eval()
        print(f"[AI] PPO模型已加载: {path}  训练局数={ckpt.get('episodes','?')}")

    def _init_frame_stack(self):
        raw = get_screen_numpy()
        from game_env import _crop_game_area
        cropped = _crop_game_area(raw)
        resized = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                            interpolation=cv2.INTER_AREA)
        self.frame_stack = [resized.astype(np.float32).transpose(2, 0, 1) / 255.0]

    def reset(self):
        self._current_lane = config.LANE_INIT
        self._last_action = None
        self._last_action_time = 0
        self._same_count = 0
        if self.model_type == "ppo":
            self._init_frame_stack()

    def predict(self, raw_frame):
        """返回 (action, confidence, display_frame) — display_frame 为高清裁剪图"""
        cropped = _crop_game_area(raw_frame)

        if self.model_type == "cnn":
            img_small = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                                   interpolation=cv2.INTER_AREA)
            img_t = torch.from_numpy(img_small).permute(2,0,1).float().div_(255.0).unsqueeze_(0).to(self.device)
            with torch.no_grad():
                logits = self.model(img_t)
                probs = F.softmax(logits, dim=-1)
                conf, act = probs.max(dim=-1)
            return act.item(), conf.item(), cropped

        else:  # ppo — 单帧 128×128 RGB，与预训练模型输入一致
            small = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                              interpolation=cv2.INTER_AREA)
            state = small.astype(np.float32).transpose(2, 0, 1) / 255.0
            state_t = torch.FloatTensor(state).unsqueeze_(0).to(self.device)
            with torch.no_grad():
                logits, _ = self.model(state_t)
                probs = F.softmax(logits, dim=-1)
                conf, act = probs.max(dim=-1)
            ai = act.item()
            if ai == 2: self._current_lane = max(1, self._current_lane - 1)
            elif ai == 3: self._current_lane = min(config.NUM_LANES, self._current_lane + 1)
            return ai, conf.item(), cropped

    def should_act(self, action, confidence):
        # 长按仅在置信度 >= 90% 时执行
        if action == 5:
            return confidence >= config.LONG_PRESS_CONFIDENCE
        if confidence < config.MIN_ACTION_CONFIDENCE:
            return False
        # 防抖：连续第2次相同动作跳过，第3次及以后放行
        if action == self._last_action:
            self._same_count += 1
            if self._same_count == 2:
                return False
        else:
            self._same_count = 1
        return True

    def execute(self, action, confidence):
        # 边界拦截
        if action == 2 and self._current_lane == 1:
            return
        if action == 3 and self._current_lane == config.NUM_LANES:
            return
        send_action(action)
        self._last_action = action
        self._last_action_time = time.time()
        if self.model_type == "ppo":
            if action == 2: self._current_lane = max(1, self._current_lane - 1)
            elif action == 3: self._current_lane = min(config.NUM_LANES, self._current_lane + 1)


# ═══════════════════════════════════════════
# 死亡检测 & 重启
# ═══════════════════════════════════════════

def check_heart_lost(frame):
    
    sx, sy = _capture_scale or (1.0, 1.0)
    region = ((config.HEART_REGION_BOTTOM - config.HEART_REGION_TOP) * sy *
              (config.HEART_REGION_RIGHT - config.HEART_REGION_LEFT) * sx)
    return _detect_heart_pixels(frame) / max(region, 1) < config.HEART_PIXEL_RATIO


def restart_game():
    print("[AI] 正在重启游戏...")
    tap_screen(config.GIVE_UP_BUTTON_X, config.GIVE_UP_BUTTON_Y)
    time.sleep(1.0)
    tap_screen(config.RESTART_BUTTON_X, config.RESTART_BUTTON_Y)
    time.sleep(1.4)
    tap_screen(config.START_BUTTON_X, config.START_BUTTON_Y)
    time.sleep(3)
    print("[AI] 等待游戏加载...")
    time.sleep(3)
    print("[AI] 等待游戏加载...")
    time.sleep(3)
    print("[AI] 等待游戏加载...")
    time.sleep(3)
    print("[AI] 重启完成")


# ═══════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════

def play():
    model_type = "cnn" if "--pretrain" in sys.argv else "ppo"
    model_path = None
    for i, a in enumerate(sys.argv):
        if a == "--model" and i+1 < len(sys.argv):
            model_path = sys.argv[i+1]
    record = "--record" in sys.argv
    slow = "--slow" in sys.argv

    if not ensure_adb_connected():
        print("[AI] ADB连接失败！")
        return

    ai = AIPlayer(model_type, model_path)
    ai.reset()

    cv2.namedWindow("AI Play", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("AI Play", DISPLAY_W, DISPLAY_H)

    vw = None
    if record:
        os.makedirs("recordings", exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        vw = cv2.VideoWriter(f"recordings/ai_{datetime.now():%Y%m%d_%H%M%S}.avi",
                             fourcc, 10, (DISPLAY_W, DISPLAY_H))

    action, confidence = None, 0.0
    action_history = deque(maxlen=30)
    fps_hist = deque(maxlen=20)
    last_t = time.time()
    death_cnt = 0
    step = 0
    last_print_action = None

    print("\n  Q=退出  S=慢动作  R=录制  Space=手动重启\n")

    try:
        while True:
            step += 1
            t0 = time.time()

            # 截屏
            try:
                frame = get_screen_numpy()
            except Exception as e:
                print(f"[AI] 截屏失败: {e}")
                time.sleep(0.5)
                continue

            # AI 预测
            action, confidence, cropped = ai.predict(frame)

            # 死亡检测
            if check_heart_lost(frame):
                death_cnt += 1
            else:
                death_cnt = 0

            if death_cnt >= 3:
                print("[AI] 检测到死亡，正在重启...")
                restart_game()
                ai.reset()
                death_cnt = 0
                continue

            # 执行动作
            if action is not None and ai.should_act(action, confidence):
                ai.execute(action, confidence)
                action_history.append(action)
                name = config.ACTION_LABEL_CN[action]
                lane = ['左','中','右'][ai._current_lane-1]
                print(f"[Step {step:5d}] {name}  置信度={confidence:.1%}  跑道={lane}")
            elif action is not None:
                action_history.append(4)
                if action != 4 and action != last_print_action:
                    name = config.ACTION_LABEL_CN[action]
                    print(f"[Step {step:5d}] {name}(跳过:置信度低 {confidence:.1%})")
            last_print_action = action

            # FPS
            fps_hist.append(1.0 / max(time.time() - last_t, 0.001))
            avg_fps = np.mean(fps_hist) if fps_hist else 0
            last_t = time.time()

            # 渲染：裁剪原图缩放（不是 128x128 模型输入！）
            h_crop, w_crop = cropped.shape[:2]
            scale = min(DISPLAY_W / w_crop, DISPLAY_H / h_crop)
            dw, dh = int(w_crop * scale), int(h_crop * scale)
            display = cv2.resize(cropped, (dw, dh), interpolation=cv2.INTER_LANCZOS4)
            # 填充到目标尺寸
            canvas = np.zeros((DISPLAY_H, DISPLAY_W, 3), dtype=np.uint8)
            y_off = (DISPLAY_H - dh) // 2
            x_off = (DISPLAY_W - dw) // 2
            canvas[y_off:y_off+dh, x_off:x_off+dw] = cv2.cvtColor(display, cv2.COLOR_RGB2BGR)

            info = (f"跑道:{['左','中','右'][ai._current_lane-1]} | "
                    f"置信度阈值:{config.MIN_ACTION_CONFIDENCE:.0%} | "
                    f"{'慢动作' if slow else '正常'}")
            canvas = draw_hud(canvas, action, confidence, avg_fps, action_history, info)
            cv2.imshow("AI Play", canvas)

            if vw:
                vw.write(canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), ord('Q'), 27):
                break
            elif key in (ord('s'), ord('S')):
                slow = not slow
            elif key in (ord('r'), ord('R')):
                record = not record
                if record and vw is None:
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    vw = cv2.VideoWriter(f"recordings/ai_{datetime.now():%Y%m%d_%H%M%S}.avi",
                                         fourcc, 10, (DISPLAY_W, DISPLAY_H))
                elif not record and vw:
                    vw.release()
                    vw = None
            elif key == ord(' '):
                restart_game()
                ai.reset()
                death_cnt = 0

            # 帧率控制
            target = 1.0 / config.PLAY_FPS_TARGET * (2 if slow else 1)
            elapsed = time.time() - t0
            if elapsed < target:
                time.sleep(target - elapsed)

    except KeyboardInterrupt:
        print("\n[AI] 用户中断")
    finally:
        cv2.destroyAllWindows()
        if vw:
            vw.release()
        print("[AI] 游玩结束")


if __name__ == "__main__":
    play()
