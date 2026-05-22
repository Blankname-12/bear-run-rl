"""
CNN 模型定义 — 参考 subwAI 纯 CNN 方案，PyTorch 实现。

SimpleCNN: 行为克隆预训练用，单帧 RGB → 5 类分类
ActorCriticCNN: PPO 强化学习用，帧堆叠 → Actor/Critic 双头
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

import config
import os

# ═══════════════════════════════════════════════════════════════════
# 基础卷积块
# ═══════════════════════════════════════════════════════════════════

class ConvBlock(nn.Module):
    """Conv2d → ReLU → MaxPool2d"""
    def __init__(self, in_ch, out_ch, kernel_size=3, stride=1, padding=1, pool_size=2):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size, stride, padding)
        self.pool = nn.MaxPool2d(pool_size)

    def forward(self, x):
        return self.pool(F.relu(self.conv(x)))


# ═══════════════════════════════════════════════════════════════════
# SimpleCNN — 行为克隆预训练（单帧分类）
# ═══════════════════════════════════════════════════════════════════

class SimpleCNN(nn.Module):
    """
    纯 CNN 图像分类器，参考 subwAI 架构。

    输入: (B, 3, IMG_HEIGHT, IMG_WIDTH) 单帧 RGB，值域 [0,1]
    输出: (B, 5) logits

    架构:
      Conv(32,3x3) → ReLU → MaxPool(2x2)    <- 128→64
      Conv(64,3x3) → ReLU → MaxPool(2x2)    <- 64→32
      Conv(128,3x3) → ReLU → MaxPool(2x2)   <- 32→16
      Conv(256,3x3) → ReLU → MaxPool(2x2)   <- 16→8
      AdaptiveAvgPool2d(4x4) → 4096
      FC(256) → ReLU → Dropout(0.3)
      FC(5)
    """

    def __init__(self, input_channels=3, n_actions=config.ACTION_SPACE):
        super().__init__()
        self.input_channels = input_channels

        self.conv1 = ConvBlock(input_channels, 32, 3, padding=1)   # /2
        self.conv2 = ConvBlock(32, 64, 3, padding=1)               # /4
        self.conv3 = ConvBlock(64, 128, 3, padding=1)              # /8
        self.conv4 = ConvBlock(128, 256, 3, padding=1)             # /16

        self.avgpool = nn.AdaptiveAvgPool2d((4, 4))  # → 256*4*4 = 4096

        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(256 * 4 * 4, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(256, n_actions),
        )

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, mean=0, std=0.01)
                nn.init.constant_(m.bias, 0)

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.conv4(x)
        x = self.avgpool(x)
        return self.classifier(x)

    def predict(self, x):
        """返回 action_idx 和置信度"""
        logits = self.forward(x)
        probs = F.softmax(logits, dim=-1)
        conf, action = probs.max(dim=-1)
        return action, conf


# ═══════════════════════════════════════════════════════════════════
# ActorCriticCNN — PPO 强化学习（Actor-Critic 双头）
# ═══════════════════════════════════════════════════════════════════

class ActorCriticCNN(nn.Module):
    """
    Actor-Critic 网络，共享 CNN 骨干。

    输入: (B, 3, IMG_HEIGHT, IMG_WIDTH) 单帧 RGB，与预训练模型一致

    骨干与 SimpleCNN 结构相同（去掉分类头），替换为 Actor/Critic 双头。
    """

    def __init__(self, input_channels=3, n_actions=config.ACTION_SPACE):
        super().__init__()
        self.input_channels = input_channels

        # 卷积骨干（与 SimpleCNN 同结构）
        self.conv1 = ConvBlock(input_channels, 32, 3, padding=1)
        self.conv2 = ConvBlock(32, 64, 3, padding=1)
        self.conv3 = ConvBlock(64, 128, 3, padding=1)
        self.conv4 = ConvBlock(128, 256, 3, padding=1)
        self.avgpool = nn.AdaptiveAvgPool2d((4, 4))
        self.flatten = nn.Flatten()

        feature_dim = 256 * 4 * 4  # 4096

        # Actor 头（策略）
        self.actor = nn.Sequential(
            nn.Linear(feature_dim, 256),
            nn.ReLU(inplace=True),
            nn.Linear(256, n_actions),
        )

        # Critic 头（价值）
        self.critic = nn.Sequential(
            nn.Linear(feature_dim, 256),
            nn.ReLU(inplace=True),
            nn.Linear(256, 1),
        )

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=0.01)
                nn.init.constant_(m.bias, 0)

    def forward(self, x):
        """返回 (logits, value)"""
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.conv4(x)
        x = self.avgpool(x)
        x = self.flatten(x)
        logits = self.actor(x)
        value = self.critic(x)
        return logits, value

    def get_action(self, state, deterministic=False):
        """给定 state，返回 (action, log_prob, entropy, value)"""
        logits, value = self.forward(state)
        probs = F.softmax(logits, dim=-1)
        if deterministic:
            action = logits.argmax(dim=-1)
        else:
            dist = torch.distributions.Categorical(probs)
            action = dist.sample()
        log_prob = F.log_softmax(logits, dim=-1).gather(1, action.unsqueeze(-1)).squeeze(-1)
        entropy = -(probs * F.log_softmax(logits, dim=-1)).sum(dim=-1)
        return action, log_prob, entropy, value.squeeze(-1)

    def evaluate(self, states, actions):
        """批量评估：给定 states 和 actions，返回 (log_probs, entropy, values)"""
        logits, values = self.forward(states)
        probs = F.softmax(logits, dim=-1)
        dist = torch.distributions.Categorical(probs)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()
        return log_probs, entropy, values.squeeze(-1)


# ═══════════════════════════════════════════════════════════════════
# 预训练权重加载
# ═══════════════════════════════════════════════════════════════════

def load_pretrained_backbone(ac_model, pretrained_path, freeze=False):
    """
    将 SimpleCNN 预训练权重完整加载到 ActorCriticCNN 中。

    输入通道已统一为 3（单帧 RGB 128×128），所有卷积层 1:1 映射。
    Classifier → Actor 头也完整复制。
    """
    if not pretrained_path or not os.path.exists(pretrained_path):
        print(f"[Model] 预训练权重不存在: {pretrained_path}")
        return False

    pretrained = torch.load(pretrained_path, map_location="cpu")

    if "model_state" in pretrained:
        pretrained = pretrained["model_state"]

    # 卷积骨干：1:1 映射（通道数已统一）
    key_map = {
        "conv1.conv.weight": "conv1.conv.weight",
        "conv1.conv.bias": "conv1.conv.bias",
        "conv2.conv.weight": "conv2.conv.weight",
        "conv2.conv.bias": "conv2.conv.bias",
        "conv3.conv.weight": "conv3.conv.weight",
        "conv3.conv.bias": "conv3.conv.bias",
        "conv4.conv.weight": "conv4.conv.weight",
        "conv4.conv.bias": "conv4.conv.bias",
    }

    loaded = 0
    for ac_name, pt_name in key_map.items():
        if pt_name in pretrained and hasattr(ac_model, ac_name.split(".")[0]):
            ac_param = ac_model.state_dict()[ac_name]
            pt_param = pretrained[pt_name]
            if ac_param.shape == pt_param.shape:
                ac_param.copy_(pt_param)
                loaded += 1
            else:
                print(f"[Model] 形状不匹配 {ac_name}: {ac_param.shape} vs {pt_param.shape}")

    print(f"[Model] 骨干: {loaded}/{len(key_map)} 层匹配")

    # 同时加载 Actor 头权重（从预训练分类器复制）
    actor_head_map = {
        "actor.0.weight": "classifier.1.weight",   # Linear 4096->256
        "actor.0.bias": "classifier.1.bias",
        "actor.2.weight": "classifier.4.weight",   # Linear 256->n_actions
        "actor.2.bias": "classifier.4.bias",
    }
    actor_loaded = 0
    for ac_name, pt_name in actor_head_map.items():
        if pt_name in pretrained and ac_name in ac_model.state_dict():
            ac_param = ac_model.state_dict()[ac_name]
            pt_param = pretrained[pt_name]
            if ac_param.shape == pt_param.shape:
                ac_param.copy_(pt_param)
                actor_loaded += 1
            elif ".2." in ac_name and ac_param.dim() == 2:
                # 输出层形状不匹配（如 5→6）：复制前 min 个输出
                out_old = min(pt_param.shape[0], ac_param.shape[0])
                ac_param[:out_old] = pt_param[:out_old]
                actor_loaded += 1
                print(f"[Model] {ac_name}: 部分复制 ({out_old}/{ac_param.shape[0]} 输出)")
    if actor_loaded > 0:
        print(f"[Model] Actor 头加载: {actor_loaded}/{len(actor_head_map)} 层")
    else:
        print("[Model] Actor 头权重不匹配，使用随机初始化")

    if freeze:
        for name, param in ac_model.named_parameters():
            if "actor" not in name and "critic" not in name:
                param.requires_grad = False
        print("[Model] 骨干已冻结（仅训练 Actor/Critic 头）")

    return True
