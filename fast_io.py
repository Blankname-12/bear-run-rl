"""
高性能 IO 模块 — scrcpy / minicap 截屏 + 持久化 ADB 触控

目标: 15+ Hz 端到端帧率

截屏方案（按优先级）:
  1. scrcpy — H.264 硬件编码 + socket 流, ~30-50ms/帧 ★ 推荐
  2. minicap — 原生 socket JPEG 流, ~15-30ms/帧（实验性）
  3. Windows DXGI — mss 桌面捕获, ~17ms/帧（需窗口可见）
  4. ADB screencap — 兜底方案, ~200-400ms/帧

触控方案:
  - 持久化 ADB shell 管道 — ~5ms/指令（vs subprocess.run 的 182ms）
"""

import os
import io
import struct
import socket
import subprocess
import time
import atexit
import random
import tempfile

import numpy as np
from PIL import Image

import config

_TEMP_DIR = tempfile.gettempdir()

# ═══════════════════════════════════════════════════════════════════
# ADB exec-out 触控（fire-and-forget，最快）
# ═══════════════════════════════════════════════════════════════════

class FastTouch:
    """adb shell input text — 向 Android 发送文本字符，LDPlayer 映射为触控"""

    def __init__(self, device_serial):
        self.serial = device_serial

    def start(self):
        pass

    def _text(self, char):
        subprocess.Popen(
            ["adb", "-s", self.serial, "shell", "input", "text", char],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def swipe(self, x1, y1, x2, y2, duration_ms):
        dx = x2 - x1
        dy = y2 - y1
        if abs(dy) > abs(dx):
            self._text('w' if dy < 0 else 's')
        else:
            self._text('a' if dx < 0 else 'd')

    def long_press(self, x, y, duration_ms):
        subprocess.run(
            ["adb", "-s", self.serial, "shell", "input", "swipe",
             str(x), str(y), str(x), str(y), str(duration_ms)],
            capture_output=True, timeout=duration_ms / 1000 + 3,
        )

    def tap(self, x, y):
        subprocess.Popen(
            ["adb", "-s", self.serial, "shell", "input", "tap", str(x), str(y)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def tap_sync(self, x, y):
        subprocess.run(
            ["adb", "-s", self.serial, "shell", "input", "tap", str(x), str(y)],
            capture_output=True, timeout=5,
        )

    def close(self):
        pass


# ═══════════════════════════════════════
# minicap 单帧截图
# ═══════════════════════════════════════

_MINICAP_PATH = "/data/local/tmp/minicap"
_MINICAP_SO = "/data/local/tmp/minicap.so"


class MinicapCapture:
    """minicap 单帧截图 — adb exec-out 每次抓一张 JPEG"""

    def __init__(self, device_serial, display_width=1080, display_height=1920):
        self.serial = device_serial
        self.width = display_width
        self.height = display_height
        self._connected = False

    def _get_abi(self):
        r = subprocess.run(["adb", "-s", self.serial, "shell", "getprop", "ro.product.cpu.abi"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()

    def _get_sdk(self):
        r = subprocess.run(["adb", "-s", self.serial, "shell", "getprop", "ro.build.version.sdk"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()

    def _find_local_binaries(self):
        abi = self._get_abi()
        sdk = self._get_sdk()
        base = os.path.join(config.BASE_DIR, "bin", "minicap")
        minicap_bin = None
        for c in [abi, abi.replace("_64", ""), "x86", "x86_64"]:
            p = os.path.join(base, c, "minicap")
            if os.path.isfile(p):
                minicap_bin = p
                break
        minicap_so = None
        for sd in [f"android-{sdk}"]:
            for ab in [abi, abi.replace("_64", ""), "x86", "x86_64"]:
                p = os.path.join(base, sd, ab, "minicap.so")
                if os.path.isfile(p):
                    minicap_so = p
                    break
            if minicap_so:
                break
        if minicap_so is None and os.path.isdir(base):
            for sd in sorted(os.listdir(base), reverse=True):
                sp = os.path.join(base, sd)
                if not os.path.isdir(sp):
                    continue
                for ab in [abi, abi.replace("_64", ""), "x86", "x86_64"]:
                    p = os.path.join(sp, ab, "minicap.so")
                    if os.path.isfile(p):
                        minicap_so = p
                        break
                if minicap_so:
                    break
        if not minicap_bin or not minicap_so:
            raise FileNotFoundError(f"[Minicap] No binaries for ABI={abi} SDK={sdk}")
        return minicap_bin, minicap_so, abi, sdk

    def _push_and_setup(self, bin_path, so_path):
        subprocess.run(["adb", "-s", self.serial, "push", bin_path, _MINICAP_PATH], capture_output=True, timeout=10)
        subprocess.run(["adb", "-s", self.serial, "push", so_path, _MINICAP_SO], capture_output=True, timeout=10)
        subprocess.run(["adb", "-s", self.serial, "shell", "chmod", "755", _MINICAP_PATH], capture_output=True, timeout=5)

    def connect(self):
        if self._connected:
            return
        bin_path, so_path, abi, sdk = self._find_local_binaries()
        print(f"[Minicap] ABI={abi} SDK={sdk} -> {os.path.basename(bin_path)}")
        self._push_and_setup(bin_path, so_path)
        r = subprocess.run(
            ["adb", "-s", self.serial, "exec-out",
             f"LD_LIBRARY_PATH=/data/local/tmp {_MINICAP_PATH} "
             f"-P {self.width}x{self.height}@{self.width}x{self.height}/0 -s"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0 or len(r.stdout) < 100:
            raise RuntimeError(f"[Minicap] Screenshot test failed. ret={r.returncode}")
        data = r.stdout
        jpeg_start = data.find(bytes([0xff, 0xd8]))
        if jpeg_start < 0:
            raise RuntimeError(f"[Minicap] No JPEG in output. First 200 bytes: {r.stdout[:200]}")
        data = data[jpeg_start:]
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        self._connected = True
        print(f"[Minicap] Ready: {w}x{h} JPEG")

    def get_frame(self):
        spec = f"{self.width}x{self.height}@{self.width}x{self.height}/0"
        for attempt in range(3):
            try:
                r = subprocess.run(
                    ["adb", "-s", self.serial, "exec-out",
                     f"LD_LIBRARY_PATH=/data/local/tmp {_MINICAP_PATH} -P {spec} -s"],
                    capture_output=True, timeout=8,
                )
                if r.returncode == 0 and len(r.stdout) > 100:
                    data = r.stdout
                    jpeg_start = data.find(bytes([0xff, 0xd8]))
                    if jpeg_start >= 0:
                        data = data[jpeg_start:]
                    return np.array(Image.open(io.BytesIO(data)).convert("RGB"))
            except Exception:
                if attempt == 2:
                    raise RuntimeError("[Minicap] get_frame failed")
                time.sleep(0.3)

    def close(self):
        self._connected = False


# ═══════════════════════════════════════
# 全局实例管理
# ═══════════════════════════════════════

_fast_touch = None
_minicap = None
_scrcpy = None


def get_touch():
    global _fast_touch
    if _fast_touch is None:
        _fast_touch = FastTouch(config.DEVICE_SERIAL)
        _fast_touch.start()
    return _fast_touch


def get_minicap():
    global _minicap
    if _minicap is None:
        _minicap = MinicapCapture(config.DEVICE_SERIAL, config.SCREEN_WIDTH, config.SCREEN_HEIGHT)
        _minicap.connect()
    return _minicap


def capture_frame():
    """截一帧，返回 numpy RGB 数组"""
    if config.CAPTURE_MODE == "minicap":
        try:
            return get_minicap().get_frame()
        except Exception as e:
            print(f"[FastIO] minicap: {e}")

    if config.CAPTURE_MODE in ("window", "scrcpy", "minicap"):
        from game_env import _get_screen_window
        frame = _get_screen_window()
        if frame is not None:
            return frame

    from game_env import _adb_screencap_raw
    return _adb_screencap_raw()


def shutdown_io():
    global _fast_touch, _minicap
    if _fast_touch:
        _fast_touch.close()
        _fast_touch = None
    if _minicap:
        _minicap.close()
        _minicap = None


atexit.register(shutdown_io)
