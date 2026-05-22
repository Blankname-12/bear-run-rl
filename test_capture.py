"""截屏测试：minicap 模式"""
import time, sys
import cv2
import numpy as np
import config
from game_env import get_screen_numpy, ensure_adb_connected

print(f"Capture mode: {config.CAPTURE_MODE}")

# 先杀掉可能残留的 minicap
import subprocess
subprocess.run(["adb", "-s", config.DEVICE_SERIAL, "shell", "killall -9 minicap 2>/dev/null"], capture_output=True, timeout=3)
time.sleep(0.5)

print("Connecting...")
if not ensure_adb_connected():
    print("ADB failed!")
    sys.exit(1)

print("Warmup (first frame)...")
try:
    t0 = time.time()
    frame = get_screen_numpy()
    dt = time.time() - t0
    print(f"  OK: {dt*1000:.0f}ms, shape={frame.shape}")
except Exception as e:
    print(f"  FAIL: {e}")
    print("\nTrying ADB fallback...")
    config.CAPTURE_MODE = "adb"
    try:
        t0 = time.time()
        frame = get_screen_numpy()
        print(f"  ADB OK: {time.time()-t0:.3f}s")
    except Exception as e2:
        print(f"  ADB also failed: {e2}")
        sys.exit(1)

# 连续 5 帧
print("\n5 frames:")
times = []
for i in range(5):
    try:
        t0 = time.time()
        frame = get_screen_numpy()
        dt = (time.time() - t0) * 1000
        times.append(dt)
        print(f"  Frame {i+1}: {dt:.0f}ms")
    except Exception as e:
        print(f"  Frame {i+1}: ERROR - {e}")

if times:
    print(f"\n  avg={np.mean(times):.0f}ms  min={np.min(times):.0f}ms  max={np.max(times):.0f}ms  FPS={1000/np.mean(times):.0f}")

# 显示
h, w = frame.shape[:2]
scale = min(480/w, 854/h)
dw, dh = int(w*scale), int(h*scale)
display = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), (dw, dh))
cv2.imshow("Capture Test - Press any key", display)
cv2.waitKey(0)
cv2.destroyAllWindows()
