"""scrcpy 诊断：逐步测试每个环节，定位卡死原因"""
import subprocess
import time

SERIAL = "127.0.0.1:5555"
JAR = "/data/local/tmp/scrcpy-server.jar"

def adb(args, timeout=5):
    cmd = ["adb", "-s", SERIAL] + args
    print(f"  $ adb {' '.join(args)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        if out:
            print(f"    -> {out[:200]}")
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        print("    -> TIMEOUT!")
        return False, ""

print("=== Step 1: ADB connection ===")
ok, _ = adb(["shell", "echo", "hello"])
if not ok:
    print("FAIL: ADB not connected!")
    exit(1)

print("\n=== Step 2: Check device ===")
ok, out = adb(["shell", "getprop", "ro.build.version.sdk"])
print(f"    SDK: {out}")

ok, out = adb(["shell", "getprop", "ro.product.cpu.abi"])
print(f"    ABI: {out}")

print("\n=== Step 3: Check jar ===")
ok, out = adb(["shell", f"ls -l {JAR}"])
if not ok:
    print("    Jar not on device, will need push")

print("\n=== Step 4: Kill old server ===")
adb(["shell", "killall -9 app_process 2>/dev/null"], timeout=3)
time.sleep(1)
ok, out = adb(["shell", "pidof app_process"])
if out:
    print(f"    Old server still alive! PID: {out}")
else:
    print("    Old server gone")

print("\n=== Step 5: Forward port ===")
adb(["forward", "tcp:29999", "localabstract:scrcpy"], timeout=5)
ok, out = adb(["forward", "--list"])
print(f"    Forwards: {out}")

print("\n=== Step 6: Start scrcpy server ===")
server_args = (
    f"CLASSPATH={JAR} "
    "app_process / com.genymobile.scrcpy.Server 2.4 "
    "tunnel_forward=true audio=false control=false "
    "send_device_meta=false send_frame_meta=false "
    "send_dummy_byte=false max_size=1080 bit_rate=8000000"
)
print(f"    Args: {server_args[:100]}...")
proc = subprocess.Popen(
    ["adb", "-s", SERIAL, "shell", server_args],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(3)

print("\n=== Step 7: Check server running ===")
ok, out = adb(["shell", "pidof app_process"])
if out:
    print(f"    Server PID: {out}")
else:
    print("    Server NOT running!")
    print("    -> LDPlayer probably doesn't support app_process")
    print("    -> Use CAPTURE_MODE = 'window' instead")

print("\n=== Step 8: Cleanup ===")
adb(["forward", "--remove", "tcp:29999"], timeout=3)
adb(["shell", "killall -9 app_process 2>/dev/null"], timeout=3)
print("    Done")
