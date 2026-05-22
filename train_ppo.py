"""
PPO 强化学习训练 — 从预训练 CNN 权重 100% 加载启动

用法:
  python train_ppo.py                        # 正常训练（从 pretrain_cnn.pth 加载权重）
  python train_ppo.py --no-pretrain          # 从头训练
  python train_ppo.py --resume <dir>         # 从检查点恢复

核心设计:
  - 输入: 单帧 128×128 RGB（与预训练模型完全一致，卷积层 1:1 映射）
  - 奖励: 纯环境奖励（noop=0, 动作=存活, 死亡/心跳扣分）
  - 权重: 预训练 CNN 所有层完整加载（conv + actor head）
"""

import csv
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

import config
from model import ActorCriticCNN, load_pretrained_backbone
from game_env import (get_screen_numpy, send_action,
                      ensure_adb_connected, _capture_scale,
                      _detect_heart_pixels, tap_screen, _crop_game_area)

interrupted = False
_interrupt_count = 0


def signal_handler(sig, frame):
    global interrupted, _interrupt_count
    _interrupt_count += 1
    if _interrupt_count == 1:
        print("\n[Train] 收到中断信号，安全退出中... (再按一次强制)")
        interrupted = True
    else:
        print("\n[Train] 强制退出！")
        os._exit(1)


signal.signal(signal.SIGINT, signal_handler)


# ═══════════════════════════════════════════════════════════════════
# RL 训练专用 GameEnv（含死亡检测 + 重启 + 帧堆叠）
# ═══════════════════════════════════════════════════════════════════

class RLGameEnv:
    """RL 训练环境：截屏 / 死亡检测 / 奖励计算（单帧 128×128 RGB + 教师引导）"""

    def __init__(self, teacher_model=None, teacher_device=None):
        self._heart_loss_frames = 0
        self._current_frame = None  # (3, 128, 128) float32 [0,1]
        self._episode_steps = 0
        self._current_lane = config.LANE_INIT
        self._heart_seen = False
        self._teacher = teacher_model
        self._teacher_device = teacher_device or torch.device("cpu")

    def _teacher_predict(self, raw_frame):
        """用预训练 CNN 预测当前帧的最优动作（输入与 agent 完全一致）"""
        import cv2
        cropped = _crop_game_area(raw_frame)
        img = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                         interpolation=cv2.INTER_AREA)
        t = (torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
             ).unsqueeze_(0).to(self._teacher_device)
        with torch.no_grad():
            logits = self._teacher(t)
            return int(logits.argmax(dim=-1).item())

    def _init_frame(self):
        raw = get_screen_numpy()
        cropped = _crop_game_area(raw)
        import cv2
        resized = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                            interpolation=cv2.INTER_AREA)
        self._current_frame = resized.astype(np.float32).transpose(2, 0, 1) / 255.0

    def _update_frame(self, raw):
        import cv2
        cropped = _crop_game_area(raw)
        resized = cv2.resize(cropped, (config.IMG_WIDTH, config.IMG_HEIGHT),
                            interpolation=cv2.INTER_AREA)
        self._current_frame = resized.astype(np.float32).transpose(2, 0, 1) / 255.0

    def get_state(self):
        """返回单帧 RGB (3, 128, 128)，值与 [0,1]"""
        return self._current_frame.copy()

    def _heart_lost(self, raw_frame):
        """检测红心是否消失（3 帧多数投票）"""
        sx, sy = _capture_scale or (1.0, 1.0)
        region_area = ((config.HEART_REGION_BOTTOM - config.HEART_REGION_TOP) * sy *
                       (config.HEART_REGION_RIGHT - config.HEART_REGION_LEFT) * sx)

        def _no_heart(frame):
            return _detect_heart_pixels(frame) / max(region_area, 1) < config.HEART_PIXEL_RATIO

        lost_count = 1 if _no_heart(raw_frame) else 0
        for _ in range(2):
            time.sleep(0.03)
            lost_count += 1 if _no_heart(get_screen_numpy()) else 0

        if lost_count >= 2:
            self._heart_loss_frames += 1
        else:
            self._heart_loss_frames = max(0, self._heart_loss_frames - 1)

        return self._heart_loss_frames >= config.HEART_DEATH_FRAMES

    def _verify_death(self):
        """红心丢失后验证是死亡还是广告"""
        time.sleep(0.8)
        print("[Env] 红心丢失，点「取消」...")
        tap_screen(config.GIVE_UP_BUTTON_X, config.GIVE_UP_BUTTON_Y)
        time.sleep(1.0)

        for _ in range(5):
            test_raw = get_screen_numpy()
            heart_px = _detect_heart_pixels(test_raw)
            sx, sy = _capture_scale or (1.0, 1.0)
            region_area = ((config.HEART_REGION_BOTTOM - config.HEART_REGION_TOP) * sy *
                           (config.HEART_REGION_RIGHT - config.HEART_REGION_LEFT) * sx)
            if heart_px / max(region_area, 1) >= config.HEART_PIXEL_RATIO:
                print("[Env] 红心恢复 → 广告弹窗，继续游戏")
                self._heart_loss_frames = 0
                return False
            time.sleep(0.08)

        print("[Env] 红心未恢复 → 确认死亡")
        self._heart_loss_frames = 0
        return True

    def restart_game(self):
        """死亡后重启游戏"""
        print("\n" + "=" * 50)
        print("  游戏结束 — 正在重启...")
        print("=" * 50)

        time.sleep(1.5)
        print(f"  点击「重新开始」({config.RESTART_BUTTON_X},{config.RESTART_BUTTON_Y})")
        tap_screen(config.RESTART_BUTTON_X, config.RESTART_BUTTON_Y)
        time.sleep(0.4)
        time.sleep(1.0)

        print(f"  点击「开始跑酷」({config.START_BUTTON_X},{config.START_BUTTON_Y})")
        tap_screen(config.START_BUTTON_X, config.START_BUTTON_Y)
        time.sleep(0.4)
        time.sleep(1.5)

        print("  重启完成")
        print("=" * 50 + "\n")

    def reset(self):
        self._heart_loss_frames = 0
        self._episode_steps = 0
        self._current_lane = config.LANE_INIT
        self._heart_seen = False
        self._init_frame()
        return self.get_state()

    def step(self, action_idx):
        step_start = time.perf_counter()

        raw = get_screen_numpy()
        self._update_frame(raw)
        next_state = self.get_state()

        # 暖机：等红心出现
        if not self._heart_seen:
            sx, sy = _capture_scale or (1.0, 1.0)
            region_area = ((config.HEART_REGION_BOTTOM - config.HEART_REGION_TOP) * sy *
                           (config.HEART_REGION_RIGHT - config.HEART_REGION_LEFT) * sx)
            heart_px = _detect_heart_pixels(raw)
            if heart_px / max(region_area, 1) >= config.HEART_PIXEL_RATIO:
                self._heart_seen = True
                print(f"[Step {self._episode_steps:4d}] 红心出现，暖机结束")
            else:
                self._episode_steps += 1
                elapsed = time.perf_counter() - step_start
                remaining = config.STEP_INTERVAL - elapsed
                if remaining > 0:
                    time.sleep(remaining)
                return next_state, 0.0, False

        # 死亡检测
        if self._heart_lost(raw):
            if self._verify_death():
                self._episode_steps += 1
                print(f"[Step {self._episode_steps:4d}] 动作={'no_op':11s} "
                      f"奖励={config.REWARD_DEATH:+.2f} ★ 死亡")
                return next_state, config.REWARD_DEATH, True

        # 红心丢失中 → 强制 noop
        if self._heart_loss_frames > 0:
            action_idx = 4

        # 执行动作（边界拦截）
        blocked = False
        if action_idx == 2 and self._current_lane == 1:
            blocked = True
        elif action_idx == 3 and self._current_lane == config.NUM_LANES:
            blocked = True

        if not blocked:
            send_action(action_idx)

        if action_idx == 2:
            self._current_lane = max(1, self._current_lane - 1)
        elif action_idx == 3:
            self._current_lane = min(config.NUM_LANES, self._current_lane + 1)

        # 奖励：教师匹配=+1.0，不匹配=0，noop=-0.1（小惩防坍缩）
        if action_idx == 4:
            step_reward = -0.1
            match_str = "(noop)"
        elif self._teacher is not None:
            teacher_action = self._teacher_predict(raw)
            if action_idx == teacher_action:
                step_reward = config.REWARD_TEACHER_MATCH
                match_str = "=教师"
            else:
                step_reward = 0.0
                match_str = f"≠教师({config.ACTION_LABEL_CN[teacher_action]})"
        else:
            step_reward = config.REWARD_ALIVE
            match_str = ""

        reward = step_reward

        self._episode_steps += 1

        print(f"[Step {self._episode_steps:4d}] "
              f"动作={config.ACTION_LABEL_CN[action_idx]} "
              f"赛道={['左','中','右'][self._current_lane-1]} "
              f"奖励={reward:+.2f} {match_str}")

        elapsed = time.perf_counter() - step_start
        remaining = config.STEP_INTERVAL - elapsed
        if remaining > 0:
            time.sleep(remaining)

        return next_state, reward, False


# ═══════════════════════════════════════════════════════════════════
# RolloutBuffer — PPO 经验缓冲区
# ═══════════════════════════════════════════════════════════════════

class RolloutBuffer:
    def __init__(self):
        self.states = []
        self.actions = []
        self.log_probs = []
        self.rewards = []
        self.values = []
        self.dones = []

    def push(self, state, action, log_prob, reward, value, done):
        self.states.append((state * 255).astype(np.uint8))
        self.actions.append(action)
        self.log_probs.append(log_prob)
        self.rewards.append(reward)
        self.values.append(value)
        self.dones.append(done)

    def clear(self):
        self.states.clear()
        self.actions.clear()
        self.log_probs.clear()
        self.rewards.clear()
        self.values.clear()
        self.dones.clear()

    def __len__(self):
        return len(self.states)

    def get_all(self):
        states = np.array([s.astype(np.float32) / 255.0 for s in self.states],
                          dtype=np.float32)
        return (
            states,
            np.array(self.actions, dtype=np.int64),
            np.array(self.log_probs, dtype=np.float32),
            np.array(self.rewards, dtype=np.float32),
            np.array(self.values, dtype=np.float32),
            np.array(self.dones, dtype=np.float32),
        )


# ═══════════════════════════════════════════════════════════════════
# PPO Agent
# ═══════════════════════════════════════════════════════════════════

class PPOAgent:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[PPO] 设备: {self.device}")

        self.n_actions = config.ACTION_SPACE
        input_channels = 3  # 单帧 RGB，与预训练模型输入完全一致
        print(f"[PPO] 输入: {input_channels}ch × {config.IMG_WIDTH}×{config.IMG_HEIGHT}")

        self.network = ActorCriticCNN(input_channels, self.n_actions).to(self.device)
        total_params = sum(p.numel() for p in self.network.parameters())
        print(f"[PPO] ActorCriticCNN: {total_params/1e6:.1f}M 参数")

        self.optimizer = optim.Adam(self.network.parameters(), lr=config.LEARNING_RATE,
                                    eps=1e-5)
        self.buffer = RolloutBuffer()
        self.total_steps = 0
        self.episodes = 0
        self.best_avg_reward = -float("inf")

    def load_pretrained(self):
        return load_pretrained_backbone(self.network, config.PRETRAIN_PATH,
                                        freeze=False)

    def freeze_backbone(self, freeze=True):
        for name, param in self.network.named_parameters():
            if "actor" not in name and "critic" not in name:
                param.requires_grad = not freeze
        if freeze:
            print("[PPO] 骨干已冻结")

    def unfreeze_backbone(self):
        self.freeze_backbone(False)

    def select_action(self, state, deterministic=False):
        state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
        with torch.no_grad():
            action, log_prob, entropy, value = self.network.get_action(
                state_t, deterministic=deterministic)
        return action.item(), log_prob.item(), entropy.item(), value.item()

    def store_transition(self, state, action, log_prob, reward, value, done):
        self.buffer.push(state, action, log_prob, reward, value, done)
        self.total_steps += 1

    def _compute_gae(self, rewards, values, dones, last_value):
        advantages = np.zeros(len(rewards), dtype=np.float32)
        gae = 0.0
        for t in reversed(range(len(rewards))):
            next_value = last_value if t == len(rewards) - 1 else values[t + 1]
            delta = (rewards[t] + config.GAMMA * next_value * (1 - dones[t]) -
                     values[t])
            gae = delta + config.GAMMA * config.GAE_LAMBDA * (1 - dones[t]) * gae
            advantages[t] = gae
        returns = advantages + values
        return advantages, returns

    def update(self):
        if len(self.buffer) < config.ROLLOUT_STEPS:
            return None

        states, actions, old_log_probs, rewards, values, dones = self.buffer.get_all()

        # 最后一个状态的价值
        with torch.no_grad():
            last_state = torch.FloatTensor(states[-1]).unsqueeze(0).to(self.device)
            _, last_value = self.network.forward(last_state)
            last_value = last_value.item()

        advantages, returns = self._compute_gae(rewards, values, dones, last_value)
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        states_t = torch.FloatTensor(states).to(self.device)
        actions_t = torch.LongTensor(actions).to(self.device)
        old_log_probs_t = torch.FloatTensor(old_log_probs).to(self.device)
        returns_t = torch.FloatTensor(returns).to(self.device)
        advantages_t = torch.FloatTensor(advantages).to(self.device)

        total_samples = len(states_t)
        indices = np.arange(total_samples)
        epoch_losses = []

        for _ in range(config.PPO_EPOCHS):
            np.random.shuffle(indices)
            for start in range(0, total_samples, config.MINI_BATCH_SIZE):
                end = start + config.MINI_BATCH_SIZE
                mb_idx = indices[start:end]

                new_log_probs, entropy, new_values = self.network.evaluate(
                    states_t[mb_idx], actions_t[mb_idx])

                ratio = torch.exp(new_log_probs - old_log_probs_t[mb_idx])
                surr1 = ratio * advantages_t[mb_idx]
                surr2 = (torch.clamp(ratio, 1.0 - config.CLIP_EPSILON,
                                     1.0 + config.CLIP_EPSILON) *
                         advantages_t[mb_idx])
                actor_loss = -torch.min(surr1, surr2).mean()
                critic_loss = F.mse_loss(new_values, returns_t[mb_idx])
                entropy_loss = -entropy.mean()

                total_loss = (actor_loss + config.CRITIC_COEF * critic_loss +
                              config.ENTROPY_COEF * entropy_loss)

                self.optimizer.zero_grad()
                total_loss.backward()
                nn.utils.clip_grad_norm_(self.network.parameters(),
                                         config.MAX_GRAD_NORM)
                self.optimizer.step()

                epoch_losses.append(total_loss.item())

        self.buffer.clear()
        avg_loss = np.mean(epoch_losses) if epoch_losses else 0.0
        print(f"[PPO] 更新 | Steps={self.total_steps} | Loss={avg_loss:.4f} | "
              f"Episodes={self.episodes}")
        return avg_loss

    def save_checkpoint(self):
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        torch.save({
            "network": self.network.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "total_steps": self.total_steps,
            "episodes": self.episodes,
            "best_avg_reward": self.best_avg_reward,
        }, config.CHECKPOINT_PATH)
        print(f"[PPO] 检查点已保存 (Ep{self.episodes}, Step{self.total_steps})")

    def save_best(self):
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        torch.save({
            "network": self.network.state_dict(),
            "total_steps": self.total_steps,
            "episodes": self.episodes,
            "best_avg_reward": self.best_avg_reward,
        }, config.BEST_MODEL_PATH)
        print(f"[PPO] 最佳模型已保存 (avg20={self.best_avg_reward:.2f})")

    def load_checkpoint(self):
        if not os.path.exists(config.CHECKPOINT_PATH):
            return False
        try:
            ckpt = torch.load(config.CHECKPOINT_PATH, map_location=self.device,
                              weights_only=False)
        except Exception as e:
            print(f"[PPO] 加载检查点失败: {e}")
            return False

        self.network.load_state_dict(ckpt["network"])
        if "optimizer" in ckpt:
            self.optimizer.load_state_dict(ckpt["optimizer"])
            self.total_steps = ckpt.get("total_steps", 0)
            self.episodes = ckpt.get("episodes", 0)
            self.best_avg_reward = ckpt.get("best_avg_reward", -float("inf"))
            print(f"[PPO] 检查点已加载: Ep{self.episodes}, Step{self.total_steps}, "
                  f"best_avg={self.best_avg_reward:.2f}")
        else:
            print("[PPO] 预训练权重已加载（无 optimizer 状态）")
            self.total_steps = 0
            self.episodes = 0
        return True


# ═══════════════════════════════════════════════════════════════════
# 训练日志
# ═══════════════════════════════════════════════════════════════════

class TrainingLogger:
    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(config.LOG_DIR, f"ppo_{timestamp}.csv")
        self.f = open(self.path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.f)
        self.writer.writerow([
            "episode", "steps", "reward", "avg_reward_100", "avg_reward_20",
            "loss", "total_steps", "elapsed_sec"
        ])
        self.f.flush()

    def log(self, episode, ep_steps, reward, avg100, avg20, loss, total_steps, elapsed):
        self.writer.writerow([
            episode, ep_steps, f"{reward:.2f}", f"{avg100:.2f}", f"{avg20:.2f}",
            f"{loss:.4f}" if loss else "", total_steps, f"{elapsed:.1f}"
        ])
        self.f.flush()

    def close(self):
        self.f.close()


# ═══════════════════════════════════════════════════════════════════
# 训练主循环
# ═══════════════════════════════════════════════════════════════════

def train():
    # 连接模拟器
    print("[Train] 连接模拟器...")
    for attempt in range(3):
        if ensure_adb_connected():
            print("[Train] 模拟器连接成功")
            break
        print(f"[Train] 连接尝试 {attempt+1}/3 失败")
        if attempt == 2:
            print("[Train] 请确认模拟器已启动且 ADB 调试已开启")
            return

    # 加载教师模型（冻结的预训练 CNN，提供逐帧动作参考）
    teacher = None
    teacher_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if os.path.exists(config.PRETRAIN_PATH) and "--no-pretrain" not in sys.argv:
        from model import SimpleCNN
        teacher = SimpleCNN(3, config.ACTION_SPACE).to(teacher_device)
        ck = torch.load(config.PRETRAIN_PATH, map_location=teacher_device, weights_only=False)
        teacher.load_state_dict(ck.get("model_state", ck))
        teacher.eval()
        print(f"[Train] 教师模型就绪 (val_acc={ck.get('val_acc','?')})")

    env = RLGameEnv(teacher_model=teacher, teacher_device=teacher_device)
    agent = PPOAgent()

    # 加载预训练
    if "--no-pretrain" not in sys.argv:
        if agent.load_pretrained():
            if "--freeze" in sys.argv:
                idx = sys.argv.index("--freeze")
                n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 5
                agent.freeze_backbone(True)
                agent._freeze_episodes = n
                print(f"[PPO] 前 {n} 局将冻结骨干")
        else:
            print("[Train] 预训练加载失败，从头训练")
    else:
        print("[Train] 跳过预训练，从头训练")

    # 尝试恢复
    if "--resume" in sys.argv:
        agent.load_checkpoint()

    logger = TrainingLogger()
    episode_rewards = deque(maxlen=100)
    episode_steps_list = deque(maxlen=100)
    start_time = time.time()

    print(f"\n[Train] PPO 训练开始 (从 Ep{agent.episodes + 1})")
    print(f"[Train] 步间隔: {config.STEP_INTERVAL}s | "
          f"Rollout: {config.ROLLOUT_STEPS}步 | "
          f"LR: {config.LEARNING_RATE:.0e}")
    print(f"[Train] 存活奖励: {config.REWARD_ALIVE} | "
          f"死亡: {config.REWARD_DEATH} | "
          f"心跳失: {config.REWARD_HEART_LOST}")
    print()

    try:
        while not interrupted:
            agent.episodes += 1

            # 解冻骨干
            if hasattr(agent, '_freeze_episodes') and agent.episodes > agent._freeze_episodes:
                agent.unfreeze_backbone()
                del agent._freeze_episodes

            state = env.reset()
            episode_reward = 0.0
            done = False

            while not done and not interrupted:
                action, log_prob, entropy, value = agent.select_action(state)
                next_state, reward, done = env.step(action)
                agent.store_transition(state, action, log_prob, reward, value, done)

                # 死亡回溯：前11帧无辜，第12~15帧重罚（罪魁祸首）
                if done and len(agent.buffer.rewards) >= 16:
                    for i in range(13, 17):
                        if i <= len(agent.buffer.rewards):
                            agent.buffer.rewards[-i] += config.REWARD_DEATH
                    print(f"[Death] 第12-15帧前重罚 (每帧{config.REWARD_DEATH:+.1f})")

                state = next_state
                episode_reward += reward
                agent.update()

            if interrupted:
                break

            episode_rewards.append(episode_reward)
            episode_steps_list.append(env._episode_steps)

            avg100 = np.mean(episode_rewards) if episode_rewards else 0
            avg20 = (np.mean(list(episode_rewards)[-config.BEST_SAVE_WINDOW:])
                     if len(episode_rewards) >= config.BEST_SAVE_WINDOW else avg100)
            avg_steps = np.mean(episode_steps_list) if episode_steps_list else 0
            elapsed = time.time() - start_time

            print(f"\n{'#' * 56}")
            print(f"#  第 {agent.episodes:5d} 局 — 存活 {env._episode_steps:4d} 步")
            print(f"#  奖励: {episode_reward:+.2f} | "
                  f"Avg100: {avg100:+.2f} | Avg20: {avg20:+.2f} | "
                  f"AvgSteps: {avg_steps:5.0f}")
            print(f"#  总步数: {agent.total_steps} | 用时: {elapsed/60:.1f}min")
            print(f"{'#' * 56}\n")

            logger.log(agent.episodes, env._episode_steps, episode_reward,
                       avg100, avg20, None, agent.total_steps, elapsed)

            # 最佳模型
            if avg20 > agent.best_avg_reward and len(episode_rewards) >= config.BEST_SAVE_WINDOW:
                agent.best_avg_reward = avg20
                agent.save_best()

            # 定期保存
            if agent.episodes % config.SAVE_INTERVAL == 0:
                agent.save_checkpoint()

            # 重启游戏
            if done and not interrupted:
                env.restart_game()

    except Exception as e:
        print(f"[Train] 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[Train] 保存最终模型...")
        agent.save_checkpoint()
        logger.close()
        print(f"[Train] 训练结束。总耗时: {(time.time() - start_time)/60:.1f} 分钟")
        print(f"[Train] 日志: {logger.path}")


if __name__ == "__main__":
    train()
