"""
实验全流程可视化 — 用于实验报告和答辩 PPT

用法:
  python visualize_pipeline.py                 # 生成全部图表
  python visualize_pipeline.py --phase 1       # 仅数据采集阶段
  python visualize_pipeline.py --phase 2       # 仅预训练阶段
  python visualize_pipeline.py --phase 3       # 仅 PPO 训练阶段

输出: reports/ 文件夹
"""

import os, sys, glob
from collections import Counter

import numpy as np
import cv2
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import matplotlib.gridspec as gridspec

import config

REPORT_DIR = os.path.join(config.BASE_DIR, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)



# 中文字体设置
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ACTION_NAMES_CN = ["上滑(跳)", "下滑(铲)", "左移", "右移", "不动"]
ACTION_COLORS = ["#2ecc71", "#e74c3c", "#3498db", "#f39c12", "#95a5a6"]


# ═══════════════════════════════════════════════════════════
# Phase 1: 数据采集可视化
# ═══════════════════════════════════════════════════════════

def phase1_data_overview():
    """标注数据总览：各类别数量 + 样本缩略图 + 分布图"""
    print("[Phase 1] 标注数据可视化...")

    folders = ["up", "down", "left", "right", "noop"]
    data = {}
    for i, f in enumerate(folders):
        path = os.path.join(config.LABELED_DATA_DIR, f)
        if os.path.isdir(path):
            pngs = sorted([x for x in os.listdir(path) if x.endswith(".png")],
                          key=lambda x: int(x[:-4]) if x[:-4].isdigit() else 0)
            data[f] = {"count": len(pngs), "files": pngs, "path": path}
        else:
            data[f] = {"count": 0, "files": [], "path": path}

    counts = [data[f]["count"] for f in folders]
    total = sum(counts)
    if total == 0:
        print("  无标注数据，跳过")
        return

    fig = plt.figure(figsize=(16, 10))
    gs = gridspec.GridSpec(3, 5, figure=fig, hspace=0.35, wspace=0.25)

    # Row 1: 每类样本缩略图 (各取 5 张均匀分布)
    for i, f in enumerate(folders):
        ax = fig.add_subplot(gs[0, i])
        files = data[f]["files"]
        if files:
            # 均匀采样 5 张
            idxs = np.linspace(0, len(files)-1, min(5, len(files)), dtype=int)
            samples = []
            for idx in idxs:
                img = cv2.imread(os.path.join(data[f]["path"], files[idx]))
                if img is not None:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    img = cv2.resize(img, (128, 128))
                    samples.append(img)
            if samples:
                # 水平拼接 5 张
                combined = np.hstack(samples)
                ax.imshow(combined)
        ax.set_title(f"{f}\n({data[f]['count']} 张)", fontsize=11, fontweight="bold",
                     color=ACTION_COLORS[i])
        ax.axis("off")

    # Row 2 left: 饼图
    ax_pie = fig.add_subplot(gs[1, :2])
    wedges, texts, autotexts = ax_pie.pie(
        counts, labels=folders, autopct="%1.1f%%",
        colors=ACTION_COLORS, startangle=90, explode=(0, 0, 0, 0, 0.05),
        textprops={"fontsize": 10})
    for at in autotexts:
        at.set_fontweight("bold")
    ax_pie.set_title("Action Distribution", fontsize=13, fontweight="bold")

    # Row 2 right: 柱状图
    ax_bar = fig.add_subplot(gs[1, 2:])
    bars = ax_bar.bar(folders, counts, color=ACTION_COLORS, edgecolor="white", linewidth=1.5)
    for bar, c in zip(bars, counts):
        ax_bar.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(counts)*0.02,
                    str(c), ha="center", fontsize=12, fontweight="bold")
    ax_bar.set_ylabel("Frames", fontsize=11)
    ax_bar.set_title("Frames per Action", fontsize=13, fontweight="bold")
    ax_bar.set_ylim(0, max(counts) * 1.15)

    # Row 3: 统计面板
    ax_info = fig.add_subplot(gs[2, :])
    ax_info.axis("off")
    noop_ratio = data["noop"]["count"] / total * 100 if total else 0
    info_lines = [
        f"Total Frames: {total}",
        f"No-op Ratio: {noop_ratio:.1f}%",
        f"Action Frames (up+down+left+right): {total - data['noop']['count']}",
        f"Most Common: {folders[np.argmax(counts)]} ({max(counts)} frames)",
        f"Data Directory: {config.LABELED_DATA_DIR}",
    ]
    for i, line in enumerate(info_lines):
        ax_info.text(0.05, 0.8 - i * 0.15, line, transform=ax_info.transAxes,
                     fontsize=13, fontfamily="monospace")

    fig.suptitle("Phase 1: Data Collection Overview", fontsize=16, fontweight="bold", y=0.98)
    path = os.path.join(REPORT_DIR, "phase1_data_overview.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")


# ═══════════════════════════════════════════════════════════
# Phase 2: 预训练可视化
# ═══════════════════════════════════════════════════════════

def phase2_pretraining():
    """预训练结果：训练曲线 + 模型结构 + 验证准确率"""
    print("[Phase 2] 预训练可视化...")

    history_path = os.path.join(config.MODEL_DIR, "pretrain_history.png")

    fig = plt.figure(figsize=(16, 8))
    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.3, wspace=0.3)

    # 训练曲线（如果存在则嵌入）
    ax_curve = fig.add_subplot(gs[0, :])
    if os.path.exists(history_path):
        img = plt.imread(history_path)
        ax_curve.imshow(img)
        ax_curve.axis("off")
        ax_curve.set_title("Training Curves (Loss & Accuracy)", fontsize=12, fontweight="bold")
    else:
        ax_curve.text(0.5, 0.5, "No training history yet.\nRun pretrain_cnn.py first.",
                      transform=ax_curve.transAxes, ha="center", va="center",
                      fontsize=14, color="gray")
        ax_curve.axis("off")

    # 模型结构
    ax_arch = fig.add_subplot(gs[1, 0])
    ax_arch.axis("off")
    layers = [
        "Input: 128x128x3",
        "Conv2D(32,3x3)+ReLU+MaxPool",
        "Conv2D(64,3x3)+ReLU+MaxPool",
        "Conv2D(128,3x3)+ReLU+MaxPool",
        "Conv2D(256,3x3)+ReLU+MaxPool",
        "AdaptiveAvgPool(4x4)",
        "Flatten -> 4096",
        "FC(256)+ReLU+Dropout(0.3)",
        "FC(5) -> 5 actions",
    ]
    for i, layer in enumerate(layers):
        y = 0.9 - i * 0.1
        is_conv = "Conv" in layer
        is_fc = "FC" in layer
        color = "#3498db" if is_conv else ("#e74c3c" if is_fc else "#2c3e50")
        ax_arch.text(0.05, y, layer, transform=ax_arch.transAxes,
                     fontsize=9, fontfamily="monospace", color=color,
                     bbox=dict(boxstyle="round,pad=0.3", facecolor="#f8f9fa",
                               edgecolor=color, alpha=0.8))
    ax_arch.set_title("SimpleCNN Architecture", fontsize=12, fontweight="bold")

    # 参数统计
    ax_params = fig.add_subplot(gs[1, 1])
    ax_params.axis("off")
    try:
        import torch
        from model import SimpleCNN
        model = SimpleCNN(3, 5)
        total_params = sum(p.numel() for p in model.parameters())
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)

        # 各层参数
        param_data = [
            ("Conv1", 3*32*3*3 + 32),
            ("Conv2", 32*64*3*3 + 64),
            ("Conv3", 64*128*3*3 + 128),
            ("Conv4", 128*256*3*3 + 256),
            ("FC(256)", 4096*256 + 256),
            ("FC(5)", 256*5 + 5),
        ]
        names, vals = zip(*param_data)
        ax_params.barh(names, vals, color=ACTION_COLORS[:6])
        ax_params.set_xlabel("Parameters")
        ax_params.set_title(f"Total: {total_params/1e6:.1f}M params", fontsize=11, fontweight="bold")
    except ImportError:
        ax_params.text(0.5, 0.5, "torch not available", ha="center", va="center")

    # 预训练配置
    ax_cfg = fig.add_subplot(gs[1, 2])
    ax_cfg.axis("off")
    configs = [
        f"Epochs: {config.PRETRAIN_EPOCHS}",
        f"Batch Size: {config.PRETRAIN_BATCH}",
        f"Learning Rate: {config.PRETRAIN_LR}",
        f"Input Size: {config.IMG_WIDTH}x{config.IMG_HEIGHT}",
        f"Augmentation: {config.DATA_AUGMENT}",
        f"Early Stop: {config.PRETRAIN_EARLY_STOP}",
    ]
    for i, cfg in enumerate(configs):
        ax_cfg.text(0.05, 0.9 - i * 0.14, cfg, transform=ax_cfg.transAxes,
                    fontsize=11, fontfamily="monospace")
    ax_cfg.set_title("Pretrain Config", fontsize=12, fontweight="bold")

    fig.suptitle("Phase 2: Behavioral Cloning Pretraining", fontsize=16, fontweight="bold", y=0.98)
    path = os.path.join(REPORT_DIR, "phase2_pretraining.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")


# ═══════════════════════════════════════════════════════════
# Phase 3: PPO 训练可视化
# ═══════════════════════════════════════════════════════════

def phase3_ppo_training():
    """PPO 训练曲线：奖励 + 存活步数 + 训练时间"""
    print("[Phase 3] PPO 训练可视化...")

    # 找最新的训练日志
    log_files = glob.glob(os.path.join(config.LOG_DIR, "ppo_*.csv"))
    if not log_files:
        print("  无 PPO 训练日志，跳过")
        # 画个占位图
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.text(0.5, 0.5, "No PPO training logs yet.\nRun train_ppo.py first.",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=16, color="gray")
        ax.axis("off")
        fig.suptitle("Phase 3: PPO Training (No Data)", fontsize=16, fontweight="bold")
        path = os.path.join(REPORT_DIR, "phase3_ppo_training.png")
        plt.savefig(path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  -> {path}")
        return

    latest_log = max(log_files, key=os.path.getmtime)

    # 读取 CSV
    import csv
    episodes, steps, rewards, avg100, avg20, total_steps, elapsed = [], [], [], [], [], [], []
    with open(latest_log, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) >= 8:
                episodes.append(int(row[0]))
                steps.append(int(row[1]))
                rewards.append(float(row[2]))
                avg100.append(float(row[3]))
                avg20.append(float(row[4]))
                total_steps.append(int(row[6]))
                elapsed.append(float(row[7]))

    if not episodes:
        print("  日志为空")
        return

    fig = plt.figure(figsize=(16, 10))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.3, wspace=0.25)

    # Subplot 1: 每局奖励 + 平滑曲线
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(episodes, rewards, alpha=0.25, color="steelblue", linewidth=0.5, label="Episode Reward")
    ax1.plot(episodes, avg100, color="darkorange", linewidth=2, label="Avg100")
    if any(a != b for a, b in zip(avg100, avg20)):
        ax1.plot(episodes, avg20, color="red", linewidth=2, label="Avg20")
    ax1.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax1.set_xlabel("Episode")
    ax1.set_ylabel("Reward")
    ax1.set_title("Episode Reward", fontsize=12, fontweight="bold")
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Subplot 2: 存活步数
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.fill_between(episodes, steps, alpha=0.3, color="seagreen")
    ax2.plot(episodes, steps, color="seagreen", linewidth=1, alpha=0.7)

    # 滑动平均
    if len(steps) > 20:
        window = min(20, len(steps))
        steps_ma = np.convolve(steps, np.ones(window)/window, mode="valid")
        ax2.plot(episodes[window-1:], steps_ma, color="darkgreen", linewidth=2, label=f"MA{window}")
        ax2.legend(fontsize=9)
    ax2.set_xlabel("Episode")
    ax2.set_ylabel("Survival Steps")
    ax2.set_title("Survival Steps per Episode", fontsize=12, fontweight="bold")
    ax2.grid(True, alpha=0.3)

    # Subplot 3: 累积训练时间
    ax3 = fig.add_subplot(gs[1, 0])
    minutes = [e / 60.0 for e in elapsed]
    ax3.plot(episodes, minutes, color="purple", linewidth=1.5)
    ax3.fill_between(episodes, 0, minutes, alpha=0.2, color="purple")
    ax3.set_xlabel("Episode")
    ax3.set_ylabel("Elapsed (min)")
    ax3.set_title("Cumulative Training Time", fontsize=12, fontweight="bold")
    ax3.grid(True, alpha=0.3)

    # Subplot 4: 训练总结
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.axis("off")
    total_min = elapsed[-1] / 60 if elapsed else 0
    summary = [
        f"Total Episodes: {len(episodes)}",
        f"Total Training Time: {total_min:.1f} min ({total_min/60:.2f} h)",
        f"Max Reward: {max(rewards):.2f}",
        f"Final Avg100 Reward: {avg100[-1]:.2f}" if avg100 else "",
        f"Max Avg20: {max(avg20):.2f}" if avg20 else "",
        f"Max Survival Steps: {max(steps)}",
        f"Total Steps (all episodes): {total_steps[-1]}" if total_steps else "",
        f"",
        f"Reward Design:",
        f"  Alive: {config.REWARD_ALIVE}",
        f"  Death: {config.REWARD_DEATH}",
        f"  Heart Lost: {config.REWARD_HEART_LOST}",
        f"  Boundary: {config.REWARD_BOUNDARY}",
        f"",
        f"PPO Hyperparams:",
        f"  LR: {config.LEARNING_RATE}, Gamma: {config.GAMMA}",
        f"  Clip: {config.CLIP_EPSILON}, Entropy: {config.ENTROPY_COEF}",
        f"  Rollout: {config.ROLLOUT_STEPS}, Epochs: {config.PPO_EPOCHS}",
    ]
    for i, line in enumerate(summary):
        if line:
            ax4.text(0.05, 0.95 - i * 0.045, line, transform=ax4.transAxes,
                     fontsize=10, fontfamily="monospace")
    ax4.set_title("Training Summary", fontsize=12, fontweight="bold")

    fig.suptitle("Phase 3: PPO Reinforcement Learning Training",
                 fontsize=16, fontweight="bold", y=0.98)
    path = os.path.join(REPORT_DIR, "phase3_ppo_training.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")


# ═══════════════════════════════════════════════════════════
# Phase 4: 综合对比
# ═══════════════════════════════════════════════════════════

def phase4_summary():
    """全流程总结：三阶段对比 + Pipeline 示意图"""
    print("[Phase 4] 全流程总结...")

    fig = plt.figure(figsize=(18, 10))
    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.3, wspace=0.25)

    # Pipeline 流程
    ax_flow = fig.add_subplot(gs[0, :])
    ax_flow.set_xlim(0, 10)
    ax_flow.set_ylim(0, 4)
    ax_flow.axis("off")

    stages = [
        (1, 2, "1. Data Collection", "label_game.py\nW/A/S/D play\n+ Auto-noop",
         "#2ecc71", "#e8f8f5"),
        (3.5, 2, "2. Pretraining", "pretrain_cnn.py\nBehavior Cloning\nSimpleCNN 1.4M",
         "#3498db", "#ebf5fb"),
        (6, 2, "3. PPO Training", "train_ppo.py\nPPO Fine-tuning\nActorCriticCNN 2.5M",
         "#e74c3c", "#fdedec"),
        (8.5, 2, "4. AI Playing", "play_ai.py\nReal-time Inference\nMinicap Capture",
         "#f39c12", "#fef5e7"),
    ]
    for x, y, title, desc, color, bg in stages:
        # Box
        rect = FancyBboxPatch((x-0.8, y-0.7), 1.8, 1.4,
                              boxstyle="round,pad=0.05", facecolor=bg,
                              edgecolor=color, linewidth=2)
        ax_flow.add_patch(rect)
        ax_flow.text(x+0.1, y+0.35, title, fontsize=12, fontweight="bold", color=color)
        ax_flow.text(x+0.1, y-0.2, desc, fontsize=9, fontfamily="monospace", color="#2c3e50")

    # 箭头
    for i in range(3):
        ax_flow.annotate("", xy=(stages[i+1][0]-0.9, stages[i+1][1]),
                         xytext=(stages[i][0]+1.0, stages[i][1]),
                         arrowprops=dict(arrowstyle="->", color="#7f8c8d",
                                        lw=2, connectionstyle="arc3,rad=0"))

    ax_flow.set_title("Pipeline Overview", fontsize=15, fontweight="bold", y=1.02)

    # 三个阶段的缩略图
    phase_imgs = [
        os.path.join(REPORT_DIR, "phase1_data_overview.png"),
        os.path.join(REPORT_DIR, "phase2_pretraining.png"),
        os.path.join(REPORT_DIR, "phase3_ppo_training.png"),
    ]
    titles = ["Data Collection", "Pretraining", "PPO Training"]

    for i, (img_path, title) in enumerate(zip(phase_imgs, titles)):
        ax = fig.add_subplot(gs[1, i])
        if os.path.exists(img_path):
            img = plt.imread(img_path)
            ax.imshow(img)
        else:
            ax.text(0.5, 0.5, f"Run phase {i+1} first", ha="center", va="center",
                    fontsize=12, color="gray")
        ax.axis("off")
        ax.set_title(title, fontsize=12, fontweight="bold")

    fig.suptitle("XiongDa Run RL — Full Pipeline Summary", fontsize=17, fontweight="bold", y=0.99)
    path = os.path.join(REPORT_DIR, "phase4_summary.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    phase = 0
    if "--phase" in sys.argv:
        idx = sys.argv.index("--phase")
        if idx + 1 < len(sys.argv):
            phase = int(sys.argv[idx + 1])

    print("=" * 50)
    print("  熊大快跑 RL — 全流程可视化")
    print("=" * 50)

    if phase == 0 or phase == 1:
        phase1_data_overview()
    if phase == 0 or phase == 2:
        phase2_pretraining()
    if phase == 0 or phase == 3:
        phase3_ppo_training()
    if phase == 0:
        phase4_summary()

    print(f"\n所有图表已保存至: {REPORT_DIR}/")
    print("可直接用于实验报告和答辩 PPT。")


if __name__ == "__main__":
    main()
