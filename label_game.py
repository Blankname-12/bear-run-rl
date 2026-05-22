"""
熊大快跑 — 游戏数据标注工具（六文件夹：up/down/left/right/noop/long_press）

用法:
  python label_game.py                  # 实时模式
  python label_game.py --offline <dir>  # 离线模式

操作: W=跳 S=滑 A=左 D=右 Space=不动 L=长按  R=跳过 U=撤销 Q=退出
"""

import os, sys, time
import cv2
import numpy as np
from PIL import Image
import config
from game_env import get_screen_numpy, ensure_adb_connected

DISPLAY_SIZE = (480, 854)
NOOP_AUTO_INTERVAL = 0.4
FRAME_BUF_SIZE = 1
ACTION_TAIL_DURATION = 0.2

ACTION_FOLDER = ["up", "down", "left", "right", "noop", "long_press"]
ACTION_COLOR = [
    (0, 255, 0), (255, 0, 0), (0, 165, 255),
    (0, 255, 255), (128, 128, 128), (255, 0, 255),
]
ACTION_SYMBOL = ["▲", "▼", "◄", "►", "●", "■"]

KEY_MAP = {
    ord('w'): 0, ord('W'): 0,
    ord('s'): 1, ord('S'): 1,
    ord('a'): 2, ord('A'): 2,
    ord('d'): 3, ord('D'): 3,
    ord('l'): 5, ord('L'): 5,
}


def init_folders():
    folders = {}
    for i, name in enumerate(ACTION_FOLDER):
        path = os.path.join(config.LABELED_DATA_DIR, name)
        os.makedirs(path, exist_ok=True)
        existing = [f for f in os.listdir(path) if f.endswith('.png') and f[:-4].isdigit()]
        folders[i] = {"path": path, "count": len(existing)}
    return folders


def draw_hud(img, action_hint=None, counts=None, info=""):
    h, w = img.shape[:2]
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, 55), (0, 0, 0), -1)
    img = cv2.addWeighted(overlay, 0.5, img, 0.5, 0)
    hints = [("W:UP", (w-320, 25), ACTION_COLOR[0]), ("S:DOWN", (w-250, 25), ACTION_COLOR[1]),
             ("A:LEFT", (w-180, 25), ACTION_COLOR[2]), ("D:RIGHT", (w-110, 25), ACTION_COLOR[3]),
             ("L:LONG", (w-60, 25), ACTION_COLOR[5])]
    for text, pos, color in hints:
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    if counts:
        cv2.putText(img, f"Total: {sum(counts.values())}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    if action_hint is not None:
        idx, name = action_hint
        text = f"{ACTION_SYMBOL[idx]} {name}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
        cx, cy = w // 2, h // 2
        cv2.rectangle(img, (cx - tw // 2 - 15, cy - th // 2 - 15),
                      (cx + tw // 2 + 15, cy + th // 2 + 15), (0, 0, 0), -1)
        cv2.putText(img, text, (cx - tw // 2, cy + th // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, ACTION_COLOR[idx], 3)
    cv2.rectangle(img, (0, h - 20), (w, h), (0, 0, 0), -1)
    cv2.putText(img, info, (10, h - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (180, 180, 180), 1)
    return img


def label_live():
    print("=" * 60)
    print("  熊大快跑 — 实时标注")
    print("=" * 60)

    if not ensure_adb_connected():
        print("[Label] 无法连接模拟器！")
        return

    folders = init_folders()
    for i, name in enumerate(ACTION_FOLDER):
        print(f"  {name}/ : {folders[i]['count']} 张已有")

    history = []

    print("\n  操作: W=跳 S=滑 A=左 D=右 Space=不动 L=长按  R=跳过 U=撤销 Q=退出")
    print("  打开游戏，回到此窗口，按任意键开始...")

    cv2.namedWindow("Label Game", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Label Game", DISPLAY_SIZE[0], DISPLAY_SIZE[1])

    frame = get_screen_numpy()
    display = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), DISPLAY_SIZE)
    cv2.imshow("Label Game", draw_hud(display, info="Press any key..."))
    cv2.waitKey(0)

    action_hint = None
    frame_buf = []
    tail_action = None
    tail_deadline = 0
    last_action_time = 0
    last_noop_time = time.time()

    print(f"\n  空格=开始/停止录制   不按键=自动存noop   按键=前{FRAME_BUF_SIZE}帧+后{ACTION_TAIL_DURATION}s\n")

    recording = False

    try:
        while True:
            try:
                frame = get_screen_numpy()
            except Exception as e:
                print(f"[Label] 截屏失败: {e}")
                time.sleep(0.3)
                continue

            frame_buf.append(frame)
            if len(frame_buf) > FRAME_BUF_SIZE + 1:
                frame_buf.pop(0)

            # 显示
            counts = {i: folders[i]["count"] for i in range(len(ACTION_FOLDER))}
            total = sum(counts.values())
            status = "● 录制中" if recording else "○ 已暂停"
            display = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), DISPLAY_SIZE)
            cv2.imshow("Label Game", draw_hud(display, action_hint, counts,
                                              f"Space=开关录制 Q退出 U撤销 | {status} Total:{total}"))

            key = cv2.waitKey(1) & 0xFF

            if key in (ord('q'), ord('Q'), 27):
                print("\n[Label] 退出")
                break

            # 空格切换录制
            if key == ord(' '):
                recording = not recording
                if recording:
                    print("[Label] ● 开始记录")
                    last_noop_time = time.time()
                    last_action_time = 0
                else:
                    print("[Label] ○ 停止记录")
                continue

            if key in (ord('u'), ord('U')):
                if history:
                    fidx, fpath = history.pop()
                    if os.path.exists(fpath):
                        os.remove(fpath)
                        folders[fidx]["count"] -= 1
                        print(f"[Label] 撤销: {ACTION_FOLDER[fidx]}/{os.path.basename(fpath)}")
                    action_hint = None
                continue
            if key in (ord('r'), ord('R')):
                print(f"[Label] 跳过 (总{total}张)")
                action_hint = None
                continue

            if not recording:
                continue

            now = time.time()
            action_idx = KEY_MAP.get(key)

            if action_idx is not None and action_idx != 4:
                # 回存缓冲帧
                for buf_frame in frame_buf[:-1]:
                    folders[action_idx]["count"] += 1
                    num = folders[action_idx]["count"]
                    fpath = os.path.join(folders[action_idx]["path"], f"{num}.png")
                    Image.fromarray(buf_frame).save(fpath)
                    history.append((action_idx, fpath))
                # 当前帧
                folders[action_idx]["count"] += 1
                num = folders[action_idx]["count"]
                fpath = os.path.join(folders[action_idx]["path"], f"{num}.png")
                Image.fromarray(frame).save(fpath)
                history.append((action_idx, fpath))
                # tail 模式
                tail_action = action_idx
                tail_deadline = now + ACTION_TAIL_DURATION
                action_hint = (action_idx, config.ACTION_LABEL_CN[action_idx])
                last_action_time = now
                last_noop_time = now
                frame_buf.clear()
                print(f"[Label] {config.ACTION_LABEL_CN[action_idx]} -> "
                      f"{ACTION_FOLDER[action_idx]}/{num}.png  (总{sum(folders[i]['count'] for i in range(len(ACTION_FOLDER)))}张)")

            elif tail_action is not None and now < tail_deadline:
                folders[tail_action]["count"] += 1
                num = folders[tail_action]["count"]
                fpath = os.path.join(folders[tail_action]["path"], f"{num}.png")
                Image.fromarray(frame).save(fpath)
                history.append((tail_action, fpath))
                last_noop_time = now

            elif tail_action is not None and now >= tail_deadline:
                tail_action = None

            elif (now - last_action_time > config.MIN_ACTION_INTERVAL and
                  now - last_noop_time > NOOP_AUTO_INTERVAL and
                  tail_action is None):
                folders[4]["count"] += 1
                num = folders[4]["count"]
                fpath = os.path.join(folders[4]["path"], f"{num}.png")
                Image.fromarray(frame).save(fpath)
                history.append((4, fpath))
                last_noop_time = now

    except KeyboardInterrupt:
        print("\n[Label] 用户中断")
    finally:
        cv2.destroyAllWindows()

    final = {i: folders[i]["count"] for i in range(len(ACTION_FOLDER))}
    total = sum(final.values())
    print(f"\n  标注完成! 共 {total} 张")
    for i, name in enumerate(ACTION_FOLDER):
        print(f"    {name:12s}: {final[i]:5d}")
    print(f"  noop 占比: {final[4] / max(total, 1) * 100:.1f}%")


def label_offline(image_dir):
    if not os.path.isdir(image_dir):
        print(f"[Label] 目录不存在: {image_dir}")
        return
    exts = ('.png', '.jpg', '.jpeg', '.bmp')
    images = sorted([f for f in os.listdir(image_dir) if f.lower().endswith(exts)])
    if not images:
        print("[Label] 无图片")
        return

    print(f"[Label] 离线: {len(images)} 张")
    folders = init_folders()
    history = []

    cv2.namedWindow("Label Offline", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Label Offline", 480, 854)

    try:
        for i, img_name in enumerate(images):
            img_path = os.path.join(image_dir, img_name)
            frame_rgb = np.array(Image.open(img_path).convert("RGB"))
            frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            while True:
                h, w = frame.shape[:2]
                s = min(480 / w, 854 / h)
                display = cv2.resize(frame, (int(w * s), int(h * s)))
                cv2.imshow("Label Offline", display)
                key = cv2.waitKey(0) & 0xFF

                if key in (ord('q'), ord('Q'), 27):
                    cv2.destroyAllWindows()
                    return
                if key in (ord('r'), ord('R')):
                    break
                if key in (ord('u'), ord('U')):
                    if history:
                        fidx, fpath = history.pop()
                        if os.path.exists(fpath):
                            os.remove(fpath)
                            folders[fidx]["count"] -= 1
                    continue

                action_idx = KEY_MAP.get(key)
                if action_idx is None:
                    continue

                folders[action_idx]["count"] += 1
                num = folders[action_idx]["count"]
                fpath = os.path.join(folders[action_idx]["path"], f"{num}.png")
                Image.fromarray(frame_rgb).save(fpath)
                history.append((action_idx, fpath))
                print(f"[Label] {img_name} -> {ACTION_FOLDER[action_idx]}/{num}.png")
                break

    except KeyboardInterrupt:
        print("\n[Label] 用户中断")
    finally:
        cv2.destroyAllWindows()

    final = {i: folders[i]["count"] for i in range(len(ACTION_FOLDER))}
    print(f"\n  离线标注完成! 共 {sum(final.values())} 张")


def main():
    if "--offline" in sys.argv:
        idx = sys.argv.index("--offline")
        label_offline(sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ".")
    elif "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    else:
        label_live()


if __name__ == "__main__":
    main()
