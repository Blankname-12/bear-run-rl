"""
游戏交互模块 — 截屏 + 触控 + ADB 工具。
去掉所有 RL/死亡检测/赛道逻辑，只保留核心 IO。
"""

import io
import os
import struct
import subprocess
import time
import tempfile

import numpy as np
from PIL import Image

import config
from test.test import DEVICE_SERIAL, SCREEN_WIDTH, SCREEN_HEIGHT
from test.test import swipe_up, swipe_down, swipe_left, swipe_right, tap

import cv2

_TEMP_DIR = tempfile.gettempdir()
_screencap_count = 0

# ── Windows DXGI 桌面捕获 ──
_WINDOW_RECT = None
_MSS_CTX = None
_HAS_MSS = False
_HAS_WIN32 = False
try:
    import mss as _mss_lib
    _HAS_MSS = True
except ImportError:
    pass
try:
    import win32gui
    import win32con
    _HAS_WIN32 = True
except ImportError:
    pass


def _init_capture():
    global _MSS_CTX
    if not _HAS_MSS:
        return False
    if _MSS_CTX is None:
        _MSS_CTX = _mss_lib.mss()
        rect = _get_window_rect()
        if rect:
            _MSS_CTX.grab(rect)
    return True


def _detect_emulator_window():
    if not _HAS_WIN32:
        return None
    result = []

    def _callback(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            return True
        if title and any(kw in title for kw in config.EMULATOR_KEYWORDS):
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            right, bottom = win32gui.ClientToScreen(
                hwnd, win32gui.GetClientRect(hwnd)[2:])
            w, h = right - left, bottom - top
            if w > 200 and h > 400:
                result.append((left, top, w, h, title))
        return True
    try:
        win32gui.EnumWindows(_callback, None)
    except Exception:
        pass
    if result:
        return result[0]
    return None


def _get_window_rect():
    global _WINDOW_RECT
    if _WINDOW_RECT is None:
        det = _detect_emulator_window()
        if det:
            left, top, w, h, title = det
            _WINDOW_RECT = {"left": left, "top": top, "width": w, "height": h}
            print(f"[Capture] 检测到模拟器窗口: '{title}' ({w}x{h} @ {left},{top})")
        else:
            print("[Capture] 未检测到模拟器窗口，请确认模拟器已启动且窗口可见")
            return None
    return _WINDOW_RECT


def _get_screen_window():
    rect = _get_window_rect()
    if rect is None:
        return None
    if not _init_capture():
        return None
    img = _MSS_CTX.grab(rect)
    arr = np.array(img, dtype=np.uint8)
    return arr[:, :, [2, 1, 0]]  # BGRA → RGB


def _restart_adbd():
    global _screencap_count
    _screencap_count = 0
    for _ in range(2):
        try:
            _run_adb(["shell", "setprop", "ctl.restart", "adbd"], timeout=5)
        except Exception:
            pass
        time.sleep(2.0)
        try:
            r = subprocess.run(["adb", "connect", DEVICE_SERIAL],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                break
        except Exception:
            pass
        time.sleep(0.5)
    time.sleep(0.3)
    try:
        subprocess.run(["adb", "-s", DEVICE_SERIAL, "wait-for-device"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def ensure_adb_connected():
    for attempt in range(3):
        try:
            subprocess.run(["adb", "start-server"], capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            r = subprocess.run(["adb", "connect", DEVICE_SERIAL],
                               capture_output=True, timeout=5)
        except Exception:
            time.sleep(1.0)
            continue
        stdout = r.stdout.decode(errors="ignore").lower()
        if "connected" in stdout or "already" in stdout:
            try:
                subprocess.run(["adb", "-s", DEVICE_SERIAL, "wait-for-device"],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            return True
        if attempt < 2:
            time.sleep(1.5)
    return False


def _run_adb(args, check=False, timeout=10):
    return subprocess.run(
        ["adb", "-s", DEVICE_SERIAL] + args,
        capture_output=True, timeout=timeout, check=check
    )


def _adb_screencap_raw():
    global _screencap_count
    if _screencap_count >= 200:
        _restart_adbd()

    raw_data = None
    for _ in range(2):
        try:
            r = _run_adb(["exec-out", "screencap"], timeout=8)
        except Exception:
            continue
        if r.returncode == 0 and len(r.stdout) > 12:
            raw_data = r.stdout
            break

    if raw_data is None:
        raise RuntimeError("ADB screencap raw 失败")

    _screencap_count += 1
    w, h, fmt = struct.unpack_from("<III", raw_data, 0)
    pixels = raw_data[12:12 + w * h * 4]
    img = np.frombuffer(pixels, dtype=np.uint8).reshape((h, w, 4))
    return np.ascontiguousarray(img[:, :, :3])


_capture_scale = None


def get_screen_numpy():
    """获取当前游戏画面，返回 RGB numpy 数组"""
    global _capture_scale

    # 桌面捕获
    if config.CAPTURE_MODE == "window":
        frame = _get_screen_window()
        if frame is not None:
            if _capture_scale is None:
                h, w = frame.shape[:2]
                _capture_scale = (w / config.SCREEN_WIDTH, h / config.SCREEN_HEIGHT)
            return frame

    # scrcpy / minicap
    try:
        from fast_io import capture_frame as _fast_capture
        frame = _fast_capture()
        if _capture_scale is None:
            h, w = frame.shape[:2]
            _capture_scale = (w / config.SCREEN_WIDTH, h / config.SCREEN_HEIGHT)
        return frame
    except ImportError:
        pass

    # ADB 兜底
    frame = _adb_screencap_raw()
    if _capture_scale is None:
        h, w = frame.shape[:2]
        _capture_scale = (w / config.SCREEN_WIDTH, h / config.SCREEN_HEIGHT)
    return frame


def _crop_game_area(frame):
    sx, sy = _capture_scale or (1.0, 1.0)
    top = int(config.GAME_CROP_TOP * sy)
    bottom = int(config.GAME_CROP_BOTTOM * sy)
    left = int(config.GAME_CROP_LEFT * sx)
    right = int(config.GAME_CROP_RIGHT * sx)
    return frame[top:bottom, left:right]


def preprocess_frame(frame):
    """裁剪 + 缩放到模型输入尺寸"""
    cropped = _crop_game_area(frame)
    try:
        if config.USE_GRAYSCALE:
            gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
            resized = cv2.resize(gray, (config.FRAME_WIDTH, config.FRAME_HEIGHT),
                                 interpolation=cv2.INTER_LINEAR)
            return resized.astype(np.float32) / 255.0
        else:
            resized = cv2.resize(cropped, (config.FRAME_WIDTH, config.FRAME_HEIGHT),
                                 interpolation=cv2.INTER_LINEAR)
            arr = resized.astype(np.float32) / 255.0
            return arr.transpose(2, 0, 1)  # HWC → CHW
    except ImportError:
        pass
    if config.USE_GRAYSCALE:
        img = Image.fromarray(cropped).convert("L")
        img = img.resize((config.FRAME_WIDTH, config.FRAME_HEIGHT), Image.BILINEAR)
        arr = np.array(img, dtype=np.float32) / 255.0
        return arr
    else:
        img = Image.fromarray(cropped).resize(
            (config.FRAME_WIDTH, config.FRAME_HEIGHT), Image.BILINEAR)
        arr = np.array(img, dtype=np.float32) / 255.0
        return arr.transpose(2, 0, 1)


# ── 触控 ──

_last_action_time = 0


def send_action(action_idx):
    """发送滑动指令。仅保留最小间隔防抖，不限制同向连续动作。"""
    global _last_action_time
    now = time.time()
    if now - _last_action_time < config.MIN_ACTION_INTERVAL:
        return False
    _last_action_time = now

    ft = _get_fast_touch()
    cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2
    dur = config.SWIPE_DURATION_MS

    if action_idx == 0:        # 上滑（跳）
        ft.swipe(cx, int(SCREEN_HEIGHT * 0.7), cx, int(SCREEN_HEIGHT * 0.3), dur)
    elif action_idx == 1:      # 下滑（铲）
        ft.swipe(cx, int(SCREEN_HEIGHT * 0.3), cx, int(SCREEN_HEIGHT * 0.7), dur)
    elif action_idx == 2:      # 左移
        ft.swipe(int(SCREEN_WIDTH * 0.65), cy, int(SCREEN_WIDTH * 0.35), cy, dur)
    elif action_idx == 3:      # 右移
        ft.swipe(int(SCREEN_WIDTH * 0.35), cy, int(SCREEN_WIDTH * 0.65), cy, dur)
    elif action_idx == 4:      # noop
        pass
    elif action_idx == 5:      # 长按
        ft.long_press(cx, cy, config.LONG_PRESS_DURATION_MS)
    return True


def _get_fast_touch():
    try:
        from fast_io import get_touch
        return get_touch()
    except ImportError:
        return None


def _tap_via_adb(x, y):
    subprocess.run(
        ["adb", "-s", DEVICE_SERIAL, "exec-out", "input", "tap", str(x), str(y)],
        capture_output=True, timeout=5,
    )


def tap_screen(x, y):
    """点击屏幕坐标"""
    ft = _get_fast_touch()
    if ft:
        ft.tap_sync(x, y)
    else:
        _tap_via_adb(x, y)


# ── 红心检测（死亡检测用）──

def _count_red_pixels(rgb_patch):
    """统计红色像素数量（HSV 阈值）"""
    hsv = cv2.cvtColor(rgb_patch, cv2.COLOR_RGB2HSV)
    h = hsv[..., 0]
    s = hsv[..., 1]
    v = hsv[..., 2]
    mask1 = ((h >= config.HEART_H_MIN1) & (h <= config.HEART_H_MAX1) &
             (s >= config.HEART_S_MIN) & (v >= config.HEART_V_MIN))
    mask2 = ((h >= config.HEART_H_MIN2) & (h <= config.HEART_H_MAX2) &
             (s >= config.HEART_S_MIN) & (v >= config.HEART_V_MIN))
    return int(np.sum(mask1) + np.sum(mask2))


def _detect_heart_pixels(rgb_frame):
    """检测红心区域内的红色像素数"""
    sx, sy = _capture_scale or (1.0, 1.0)
    ox = int(config.GAME_OFFSET_X * sx) if config.GAME_OFFSET_X else 0
    oy = int(config.GAME_OFFSET_Y * sy) if config.GAME_OFFSET_Y else 0
    top = int(config.HEART_REGION_TOP * sy) + oy
    bottom = int(config.HEART_REGION_BOTTOM * sy) + oy
    left = int(config.HEART_REGION_LEFT * sx) + ox
    right = int(config.HEART_REGION_RIGHT * sx) + ox
    h, w = rgb_frame.shape[:2]
    top, bottom = max(0, top), min(h, bottom)
    left, right = max(0, left), min(w, right)
    h_crop = rgb_frame[top:bottom, left:right]
    return _count_red_pixels(h_crop)
