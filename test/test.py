import io
import os
import struct
import subprocess
import time

DEVICE_SERIAL = "127.0.0.1:5555"
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920
SWIPE_DURATION_MS = 80

_TEMP_DIR = os.environ.get("TEMP", os.path.dirname(os.path.abspath(__file__)))


def _robust_screencap():
    serial = DEVICE_SERIAL
    base = ["adb", "-s", serial]

    # -p 方案
    for args in [["exec-out", "screencap", "-p"], ["shell", "screencap", "-p"]]:
        try:
            r = subprocess.run(base + args, capture_output=True, timeout=10)
            if r.returncode == 0 and len(r.stdout) > 200:
                return r.stdout.replace(b'\r\n', b'\n')
        except Exception:
            pass

    for png_path in ["/data/local/tmp/screen_rl.png", "/sdcard/screen_rl.png"]:
        try:
            subprocess.run(base + ["shell", "screencap", "-p", png_path],
                           capture_output=True, timeout=10, check=True)
            local = os.path.join(_TEMP_DIR, "adb_screen.png")
            subprocess.run(base + ["pull", png_path, local],
                           capture_output=True, timeout=10, check=True)
            with open(local, "rb") as f:
                data = f.read()
            if len(data) > 200:
                return data
        except Exception:
            pass

    # 原始格式
    for args in [["exec-out", "screencap"], ["shell", "screencap"]]:
        try:
            r = subprocess.run(base + args, capture_output=True, timeout=10)
            if r.returncode == 0 and len(r.stdout) > 100:
                data = r.stdout
                if data[:4] not in (b'\x89PNG', b'\xff\xd8'):
                    w, h, fmt = struct.unpack_from("<III", data, 0)
                    if 0 < w <= 4096 and 0 < h <= 4096 and fmt in (1,4):
                        pixels = data[12:12+w*h*4]
                        from PIL import Image as PILImage
                        img = PILImage.frombytes("RGBA", (w,h), pixels, "raw", "RGBA" if fmt==1 else "RGBX")
                        buf = io.BytesIO()
                        img.convert("RGB").save(buf, format="PNG")
                        return buf.getvalue()
        except Exception:
            pass

    for raw_path in ["/data/local/tmp/screen.raw", "/sdcard/screen.raw", "/tmp/screen.raw"]:
        try:
            subprocess.run(base + ["shell", "screencap", raw_path],
                           capture_output=True, timeout=10, check=True)
            local = os.path.join(_TEMP_DIR, "screen.raw")
            subprocess.run(base + ["pull", raw_path, local],
                           capture_output=True, timeout=10, check=True)
            with open(local, "rb") as f:
                data = f.read()
            if len(data) > 100 and data[:4] not in (b'\x89PNG', b'\xff\xd8'):
                w, h, fmt = struct.unpack_from("<III", data, 0)
                if 0 < w <= 4096 and 0 < h <= 4096 and fmt in (1,4):
                    pixels = data[12:12+w*h*4]
                    from PIL import Image as PILImage
                    img = PILImage.frombytes("RGBA", (w,h), pixels, "raw", "RGBA" if fmt==1 else "RGBX")
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, format="PNG")
                    return buf.getvalue()
        except Exception:
            pass

    raise RuntimeError("screencap: all methods exhausted")


def capture_screenshot(output_path="screenshot.png"):
    data = _robust_screencap()
    with open(output_path, "wb") as f:
        f.write(data)
    print(f"Screenshot saved to {output_path}")


def tap(x, y):
    subprocess.run(
        ["adb", "-s", DEVICE_SERIAL, "shell", "input", "tap", str(x), str(y)],
        timeout=5,
        check=True
    )
    # print(f"Tap at ({x}, {y})")


def swipe(x1, y1, x2, y2, duration=SWIPE_DURATION_MS):
    subprocess.run(
        ["adb", "-s", DEVICE_SERIAL, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration)],
        timeout=5,
        check=True
    )
    # print(f"Swipe from ({x1}, {y1}) to ({x2}, {y2}) in {duration}ms")


def swipe_up():
    cx = SCREEN_WIDTH // 2
    y1 = int(SCREEN_HEIGHT * 0.7)
    y2 = int(SCREEN_HEIGHT * 0.3)
    swipe(cx, y1, cx, y2)
    # print("Swipe up")


def swipe_down():
    cx = SCREEN_WIDTH // 2
    y1 = int(SCREEN_HEIGHT * 0.3)
    y2 = int(SCREEN_HEIGHT * 0.7)
    swipe(cx, y1, cx, y2)
    # print("Swipe down")


def swipe_left():
    cy = SCREEN_HEIGHT // 2
    x1 = int(SCREEN_WIDTH * 0.65)
    x2 = int(SCREEN_WIDTH * 0.35)
    swipe(x1, cy, x2, cy)
    # print("Swipe left")


def swipe_right():
    cy = SCREEN_HEIGHT // 2
    x1 = int(SCREEN_WIDTH * 0.35)
    x2 = int(SCREEN_WIDTH * 0.65)
    swipe(x1, cy, x2, cy)
    # print("Swipe right")


def get_screen_size():
    """获取模拟器实际屏幕分辨率"""
    result = subprocess.run(
        ["adb", "-s", DEVICE_SERIAL, "shell", "wm", "size"],
        capture_output=True, text=True, check=True
    )
    print("Screen size:", result.stdout.strip())


if __name__ == "__main__":
    # 查看屏幕分辨率
    get_screen_size()

    # 截图
    capture_screenshot()

    # 测试点击
    tap(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

    # 测试四个方向的滑动
    time.sleep(1)
    swipe_up()

    time.sleep(1)
    swipe_down()

    time.sleep(1)
    swipe_left()

    time.sleep(1)
    swipe_right()