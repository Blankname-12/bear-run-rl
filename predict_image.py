"""
单张图片预测 — 全 PIL 实现（不依赖 cv2 中文乱码和路径问题）

用法:
  python predict_image.py <图片路径>                    # CNN 预训练模型
  python predict_image.py <图片路径> --ppo              # PPO 模型
  python predict_image.py <图片路径> --save <输出路径>   # 保存（不弹窗）
"""

import os, sys

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFont

import config
from model import SimpleCNN, ActorCriticCNN

SYMBOLS = ["^", "v", "<-", "->", "o", "#"]
COLORS = ["#2ecc71", "#e74c3c", "#3498db", "#f39c12", "#95a5a6", "#e040fb"]

# 字体
FONT_S, FONT_M, FONT_L = None, None, None
for fp in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
           "C:/Windows/Fonts/simsun.ttc"]:
    if os.path.exists(fp):
        try:
            FONT_S = ImageFont.truetype(fp, 22)
            FONT_M = ImageFont.truetype(fp, 38)
            FONT_L = ImageFont.truetype(fp, 70)
            break
        except Exception:
            pass


def draw_prediction(pil_img, act, conf, probs):
    """在 PIL 图片底部叠加超大预测面板，返回 PIL 图片"""
    w, h = pil_img.size
    ph = max(h // 3, 220)
    pil_img = pil_img.convert("RGBA")

    # 底部半透明深色面板
    overlay = Image.new("RGBA", (w, ph), (15, 15, 15, 200))
    pil_img.paste(overlay, (0, h - ph), overlay)

    draw = ImageDraw.Draw(pil_img)
    color = COLORS[act]

    # 标题
    draw.text((30, h - ph + 10), "模型预测", fill="#ffffff", font=FONT_M)
    # 动作名 — 超大
    draw.text((30, h - ph + 65), f"{SYMBOLS[act]}  {config.ACTION_LABEL_CN[act]}",
              fill=color, font=FONT_L)

    # 置信度条
    bx, by, bw, bh = 30, h - ph + 155, w - 60, 36
    draw.rectangle([(bx, by), (bx + bw, by + bh)], fill=(50, 50, 50))
    draw.rectangle([(bx, by), (bx + int(bw * conf), by + bh)], fill=color)
    draw.text((bx + 10, by + bh + 10), f"置信度  {conf:.1%}", fill=color, font=FONT_M)

    # 五类概率竖排
    py0 = h - ph + 230
    row_h = 44
    for i in range(len(SYMBOLS)):
        y = py0 + row_h * i
        p = probs[i]
        marker = " ★" if i == act else "   ·"
        c = COLORS[i]
        bar_w = int((bw - 280) * p)
        draw.text((bx, y), f"{marker} {SYMBOLS[i]} {config.ACTION_LABEL_CN[i]}", fill=c, font=FONT_M)
        draw.rectangle([(bx + 260, y + 14), (bx + 260 + bar_w, y + 30)], fill=c)
        draw.text((bx + 260 + bar_w + 10, y), f"{p:.1%}", fill=c, font=FONT_M)

    return pil_img.convert("RGB")


def load_model(path, use_ppo):
    d = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if use_ppo:
        ch = config.FRAME_STACK * 3 + config.NUM_LANES
        m = ActorCriticCNN(ch, config.ACTION_SPACE).to(d)
        ck = torch.load(path, map_location=d, weights_only=False)
        m.load_state_dict(ck.get("network", ck))
    else:
        m = SimpleCNN(3, config.ACTION_SPACE).to(d)
        ck = torch.load(path, map_location=d, weights_only=False)
        m.load_state_dict(ck.get("model_state", ck))
    m.eval()
    return m, d


def predict(pil_img, model, device, use_ppo):
    """对 PIL 图片做预测"""
    w, h = pil_img.size
    if w > 400 and h > 400:
        crop = pil_img.crop((config.GAME_CROP_LEFT, config.GAME_CROP_TOP,
                             config.GAME_CROP_RIGHT, config.GAME_CROP_BOTTOM))
    else:
        crop = pil_img
    img = np.array(crop.resize((config.IMG_WIDTH, config.IMG_HEIGHT), Image.BILINEAR))
    t = torch.from_numpy(img).permute(2, 0, 1).float().div_(255.0).unsqueeze_(0).to(device)

    with torch.no_grad():
        if use_ppo:
            s = t.repeat(1, config.FRAME_STACK, 1, 1)
            lane = torch.zeros(1, config.NUM_LANES, config.IMG_HEIGHT, config.IMG_WIDTH, device=device)
            lane[:, config.LANE_INIT - 1] = 1.0
            s = torch.cat([s, lane], dim=1)
            logits, _ = model(s)
        else:
            logits = model(t)
        probs = F.softmax(logits, dim=-1).squeeze_(0).cpu().numpy()
    return int(probs.argmax()), float(probs.max()), probs


def main():
    use_ppo = "--ppo" in sys.argv
    model_path = save_path = img_path = None
    for i, a in enumerate(sys.argv):
        if a == "--model" and i + 1 < len(sys.argv):
            model_path = sys.argv[i + 1]
        if a == "--save" and i + 1 < len(sys.argv):
            save_path = sys.argv[i + 1]
    for a in sys.argv[1:]:
        if os.path.isfile(a):
            img_path = a
            break

    if img_path is None:
        print("用法: python predict_image.py <图片> [--ppo] [--save <路径>]")
        sys.exit(1)

    model_path = model_path or (config.CHECKPOINT_PATH if use_ppo else config.PRETRAIN_PATH)
    if not os.path.exists(model_path):
        print(f"模型不存在: {model_path}")
        sys.exit(1)

    model, device = load_model(model_path, use_ppo)

    pil_img = Image.open(img_path).convert("RGB")
    print(f"原图: {pil_img.size[0]}x{pil_img.size[1]}")

    act, conf, probs = predict(pil_img, model, device, use_ppo)
    print(f"预测: {config.ACTION_LABEL_CN[act]}  置信度: {conf:.1%}")
    for i in range(len(SYMBOLS)):
        print(f"  {SYMBOLS[i]} {config.ACTION_LABEL_CN[i]:5s}: {probs[i]:.1%}")

    result = draw_prediction(pil_img, act, conf, probs)
    print(f"输出: {result.size[0]}x{result.size[1]}")

    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        result.save(save_path)
        print(f"已保存: {save_path}")
    else:
        result.show()


if __name__ == "__main__":
    main()
