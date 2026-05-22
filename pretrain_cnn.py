"""
行为克隆预训练 — 纯 CNN 图像分类（参考 subwAI 方案）

用法:
  python pretrain_cnn.py                          # 自动使用 labeled_data/ 五文件夹
  python pretrain_cnn.py --data <dir>             # 指定数据目录
  python pretrain_cnn.py --epochs 30 --lr 0.001   # 自定义参数
  python pretrain_cnn.py --no-augment              # 关闭数据增强

输出: models/pretrain_cnn.pth
"""

import os
import sys
import time
import random
from collections import Counter

import numpy as np
import cv2  # flip/resize 等内存操作（imread/imwrite 禁用，改用 PIL）
import torch
from PIL import Image
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

import config
from model import SimpleCNN


# ═══════════════════════════════════════════════════════════════════
# 数据集
# ═══════════════════════════════════════════════════════════════════

class LabeledGameDataset(Dataset):
    """
    从 labeled_data/{up,down,left,right,noop}/ 五文件夹加载标注数据（subwAI 格式）。

    数据增强（训练时）：
      - 水平翻转 (p=0.5)：left ↔ right 互转
      - 亮度抖动 (p=0.5)：factor ∈ [0.7, 1.3]
    """

    # 文件夹名 → action_idx
    FOLDER2IDX = {"up": 0, "down": 1, "left": 2, "right": 3, "noop": 4, "long_press": 5}

    def __init__(self, data_dir, augment=False, balance_noop=True):
        self.augment = augment
        self.samples = []

        for folder, action_idx in self.FOLDER2IDX.items():
            folder_path = os.path.join(data_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            for fname in os.listdir(folder_path):
                if fname.lower().endswith(".png"):
                    img_path = os.path.join(folder_path, fname)
                    self.samples.append((img_path, action_idx))

        if len(self.samples) == 0:
            raise RuntimeError(
                f"[Dataset] {data_dir}/ 中没有找到图片！\n"
                f"  请先运行: python label_game.py\n"
                f"  确保存在: up/ down/ left/ right/ noop/ 五个子文件夹")

        # 类别平衡：noop 降采样到其他 4 类的中位数
        if balance_noop:
            self._balance()

        # 统计
        counts = Counter(a for _, a in self.samples)
        print(f"[Dataset] 加载 {len(self.samples)} 条数据 (from {data_dir}):")
        for a in range(config.ACTION_SPACE):
            folder = ["up", "down", "left", "right", "noop", "long_press"][a]
            print(f"  {folder:8s}: {counts.get(a, 0):5d}")
        noop_ratio = counts.get(4, 0) / len(self.samples) * 100
        print(f"  noop 占比: {noop_ratio:.1f}%")

    def _balance(self):
        """noop (action=4) 降采样到其他类别中位数"""
        by_action = {a: [] for a in range(config.ACTION_SPACE)}
        for img_path, action in self.samples:
            by_action[action].append((img_path, action))

        non_noop = [a for a in range(config.ACTION_SPACE) if a != 4]
        other_counts = [len(by_action[a]) for a in non_noop]
        max_non_noop = int(np.median(other_counts)) if other_counts else 0

        if max_non_noop > 0 and len(by_action[4]) > max_non_noop:
            kept = random.sample(by_action[4], max_non_noop)
            balanced = []
            for a in non_noop:
                balanced.extend(by_action[a])
            balanced.extend(kept)
            print(f"[Dataset] noop 平衡: {len(by_action[4])} -> {max_non_noop}")
            self.samples = balanced

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, action = self.samples[idx]

        try:
            img = Image.open(img_path).convert("RGB")
        except Exception:
            return self.__getitem__(random.randint(0, len(self) - 1))

        # 数据增强（仅训练时）
        if self.augment:
            img, action = self._augment(img, action)

        # 缩放到模型输入尺寸（PIL 操作，避免 cv2 中文路径问题）
        img = img.resize((config.IMG_WIDTH, config.IMG_HEIGHT), Image.BILINEAR)

        # HWC → CHW, uint8 → float32 [0,1]
        img = torch.from_numpy(np.array(img)).permute(2, 0, 1).float() / 255.0

        return img, action

    def _augment(self, img, action):
        # img 是 PIL.Image，action 是 int
        # 仅做颜色/模糊增强，不做空间变换（游戏画面不会翻转/旋转）
        import PIL.ImageEnhance, PIL.ImageFilter

        # 亮度抖动 (p=0.5)
        if random.random() < 0.5:
            factor = random.uniform(0.6, 1.4)
            img = PIL.ImageEnhance.Brightness(img).enhance(factor)

        # 对比度抖动 (p=0.3)
        if random.random() < 0.3:
            factor = random.uniform(0.7, 1.3)
            img = PIL.ImageEnhance.Contrast(img).enhance(factor)

        # 饱和度抖动 (p=0.3)
        if random.random() < 0.3:
            factor = random.uniform(0.5, 1.5)
            img = PIL.ImageEnhance.Color(img).enhance(factor)

        # 高斯模糊 — 模拟运动模糊/不同屏幕质量 (p=0.15)
        if random.random() < 0.15:
            img = img.filter(PIL.ImageFilter.GaussianBlur(radius=random.uniform(0.3, 0.8)))

        return img, action


# ═══════════════════════════════════════════════════════════════════
# 查找数据
# ═══════════════════════════════════════════════════════════════════

def find_data_dir():
    """查找 labeled_data/ 目录（含 up/down/left/right/noop 子文件夹）"""
    data_arg = None
    for arg in sys.argv:
        if arg.startswith("--data="):
            data_arg = arg.split("=", 1)[1]

    if data_arg:
        if os.path.isdir(data_arg):
            return data_arg
        print(f"[Pretrain] 目录不存在: {data_arg}")
        return None

    if os.path.isdir(config.LABELED_DATA_DIR):
        return config.LABELED_DATA_DIR

    print(f"[Pretrain] 没有标注数据目录: {config.LABELED_DATA_DIR}")
    print(f"  请先运行: python label_game.py")
    return None


# ═══════════════════════════════════════════════════════════════════
# 训练主函数
# ═══════════════════════════════════════════════════════════════════

def pretrain():
    augment = "--no-augment" not in sys.argv
    epochs_arg = [a for a in sys.argv if a.startswith("--epochs=")]
    epochs = int(epochs_arg[0].split("=")[1]) if epochs_arg else config.PRETRAIN_EPOCHS
    lr_arg = [a for a in sys.argv if a.startswith("--lr=")]
    lr = float(lr_arg[0].split("=")[1]) if lr_arg else config.PRETRAIN_LR

    # 查找数据
    data_dir = find_data_dir()
    if not data_dir:
        print("[Pretrain] 没有找到标注数据！请先运行 label_game.py 收集数据。")
        return
    print(f"[Pretrain] 数据来源: {data_dir}/")

    # 加载数据
    full_dataset = LabeledGameDataset(data_dir, augment=False, balance_noop=True)
    if len(full_dataset) == 0:
        print("[Pretrain] 数据为空！")
        return

    # 80/20 划分
    n_total = len(full_dataset)
    n_train = int(n_total * (1 - config.PRETRAIN_VAL_SPLIT))
    n_val = n_total - n_train
    train_idx, val_idx = torch.utils.data.random_split(
        range(n_total), [n_train, n_val],
        generator=torch.Generator().manual_seed(42),
    )

    # 训练集用增强（重新创建数据集后取子集）
    train_full = LabeledGameDataset(data_dir, augment=augment, balance_noop=True)
    val_full = LabeledGameDataset(data_dir, augment=False, balance_noop=True)

    # 用 Subset 包装
    from torch.utils.data import Subset
    train_dataset = Subset(train_full, train_idx.indices)
    val_dataset = Subset(val_full, val_idx.indices)

    train_loader = DataLoader(train_dataset, batch_size=config.PRETRAIN_BATCH,
                              shuffle=True, drop_last=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=config.PRETRAIN_BATCH * 2,
                            shuffle=False, num_workers=0)

    print(f"[Pretrain] 训练集: {len(train_dataset)}, 验证集: {len(val_dataset)}")

    # 设备 & 模型
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[Pretrain] 设备: {device}")

    input_channels = 1 if config.USE_GRAYSCALE else 3
    model = SimpleCNN(input_channels, config.ACTION_SPACE).to(device)
    total_params = sum(p.numel() for p in model.parameters())
    print(f"[Pretrain] 模型: SimpleCNN, {total_params/1e6:.1f}M 参数")
    print(f"[Pretrain] 输入: {input_channels}x{config.IMG_WIDTH}x{config.IMG_HEIGHT}")

    # 类别权重（反比频率，给小类更大权重）
    train_counts = Counter(train_full.samples[i][1] for i in train_idx.indices)
    max_count = max(train_counts.values())
    class_weights = torch.zeros(config.ACTION_SPACE)
    for a in range(config.ACTION_SPACE):
        class_weights[a] = max_count / max(train_counts.get(a, 1), 1)
    class_weights = class_weights.to(device)
    print(f"[Pretrain] 类别权重: {[f'{config.ACTION_LABEL_CN[a]}:{class_weights[a]:.1f}' for a in range(config.ACTION_SPACE)]}")

    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr,
                                  weight_decay=config.PRETRAIN_WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-6,
    )

    # 早停
    best_val_acc = 0.0
    best_epoch = 0
    patience_counter = 0
    early_stop_patience = config.PRETRAIN_EARLY_STOP

    print(f"\n[Pretrain] 开始训练: {epochs} epochs, lr={lr:.0e}")
    print(f"[Pretrain] 早停耐心值: {early_stop_patience}")
    start_time = time.time()

    history = {"train_loss": [], "val_loss": [], "val_acc": []}

    for epoch in range(1, epochs + 1):
        # ── 训练 ──
        model.train()
        train_loss = 0.0
        train_correct = 0
        train_total = 0

        for batch_imgs, batch_actions in train_loader:
            batch_imgs = batch_imgs.to(device)
            batch_actions = batch_actions.to(device)

            optimizer.zero_grad()
            logits = model(batch_imgs)
            loss = criterion(logits, batch_actions)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item() * batch_imgs.size(0)
            pred = logits.argmax(dim=1)
            train_correct += (pred == batch_actions).sum().item()
            train_total += batch_imgs.size(0)

        train_loss_avg = train_loss / train_total
        train_acc = train_correct / train_total * 100

        # ── 验证 ──
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        per_class_correct = {a: 0 for a in range(config.ACTION_SPACE)}
        per_class_total = {a: 0 for a in range(config.ACTION_SPACE)}

        with torch.no_grad():
            for batch_imgs, batch_actions in val_loader:
                batch_imgs = batch_imgs.to(device)
                batch_actions = batch_actions.to(device)

                logits = model(batch_imgs)
                loss = criterion(logits, batch_actions)

                val_loss += loss.item() * batch_imgs.size(0)
                pred = logits.argmax(dim=1)
                val_correct += (pred == batch_actions).sum().item()
                val_total += batch_imgs.size(0)

                for a in range(config.ACTION_SPACE):
                    mask = batch_actions == a
                    per_class_correct[a] += (pred[mask] == a).sum().item()
                    per_class_total[a] += mask.sum().item()

        val_loss_avg = val_loss / val_total
        val_acc = val_correct / val_total * 100

        scheduler.step(val_loss_avg)
        current_lr = optimizer.param_groups[0]["lr"]

        # 日志
        elapsed = time.time() - start_time
        class_strs = []
        for a in range(config.ACTION_SPACE):
            if per_class_total[a] > 0:
                acc = per_class_correct[a] / per_class_total[a] * 100
                class_strs.append(f"{config.ACTION_LABEL_CN[a]}:{acc:.0f}%")
            else:
                class_strs.append(f"{config.ACTION_LABEL_CN[a]}:--")

        print(f"  Epoch {epoch:3d}/{epochs} | "
              f"Train Loss: {train_loss_avg:.4f} Acc: {train_acc:5.1f}% | "
              f"Val Loss: {val_loss_avg:.4f} Acc: {val_acc:5.1f}% | "
              f"LR: {current_lr:.1e} | {elapsed:.0f}s")
        print(f"          每类 → {' '.join(class_strs)}")

        history["train_loss"].append(train_loss_avg)
        history["val_loss"].append(val_loss_avg)
        history["val_acc"].append(val_acc)

        # 最佳模型 & 早停
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch
            patience_counter = 0
            os.makedirs(config.MODEL_DIR, exist_ok=True)
            torch.save({
                "model_state": model.state_dict(),
                "val_acc": val_acc,
                "epoch": epoch,
                "config": {
                    "img_width": config.IMG_WIDTH,
                    "img_height": config.IMG_HEIGHT,
                    "input_channels": input_channels,
                    "action_space": config.ACTION_SPACE,
                },
            }, config.PRETRAIN_PATH)
            print(f"  ★ 新最佳模型 (Val Acc: {val_acc:.1f}%)")
        else:
            patience_counter += 1

        if patience_counter >= early_stop_patience:
            print(f"\n[Pretrain] 早停: {early_stop_patience} 轮未提升 "
                  f"(最佳: {best_val_acc:.1f}% @ epoch {best_epoch})")
            break

    # ── 完成 ──
    elapsed = time.time() - start_time
    print(f"\n[Pretrain] 训练完成，耗时: {elapsed/60:.1f} 分钟")
    print(f"[Pretrain] 最佳验证准确率: {best_val_acc:.1f}% (Epoch {best_epoch})")
    print(f"[Pretrain] 模型已保存: {config.PRETRAIN_PATH}")

    # 绘制训练曲线
    _plot_history(history)


def _plot_history(history):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
        fig.suptitle("Behavior Cloning — Pretraining", fontsize=14, fontweight="bold")

        epochs = range(1, len(history["train_loss"]) + 1)
        ax1.plot(epochs, history["train_loss"], label="Train", color="steelblue")
        ax1.plot(epochs, history["val_loss"], label="Val", color="darkorange")
        ax1.set_xlabel("Epoch")
        ax1.set_ylabel("Loss")
        ax1.set_title("Loss")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        ax2.plot(epochs, history["val_acc"], color="seagreen", linewidth=2)
        ax2.set_xlabel("Epoch")
        ax2.set_ylabel("Accuracy (%)")
        ax2.set_title("Validation Accuracy")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = os.path.join(config.MODEL_DIR, "pretrain_history.png")
        plt.savefig(plot_path, dpi=150)
        print(f"[Pretrain] 训练曲线: {plot_path}")
        plt.close()
    except Exception as e:
        print(f"[Pretrain] 无法绘图: {e}")


if __name__ == "__main__":
    pretrain()
