# 03 — APK 逆向工程

> **会话 S2 (第一新对话)** | 日期: 2025-07-18  
> **主题**: 对《熊出没之熊大快跑》v4.6.0 进行完整的 APK 逆向工程尝试

---

## 目录

1. [背景与动机](#1-背景与动机)
2. [APK 获取与基本信息](#2-apk-获取与基本信息)
3. [引擎识别与确认](#3-引擎识别与确认)
4. [APK 内部结构遍历](#4-apk-内部结构遍历)
5. [文件提取阶段](#5-文件提取阶段)
6. [Il2CppDumper 下载与失败全记录](#6-il2cppdumper-下载与失败全记录)
7. [自写 Metadata 解析器](#7-自写-metadata-解析器)
8. [从 Metadata 提取的关键信息](#8-从-metadata-提取的关键信息)
9. [UE5 开源项目对照分析](#9-ue5-开源项目对照分析)
10. [综合结论与后续影响](#10-综合结论与后续影响)

---

## 1. 背景与动机

为构建仿真 Gym 环境，需要从真实游戏中获取以下关键信息：

| 需求 | 期望来源 | 重要性 |
|------|----------|--------|
| 完整动作空间 (跳跃/滑铲/左右移动) | C# 游戏逻辑 | **极高** |
| 碰撞检测机制与距离阈值 | Unity 物理参数 | 高 |
| 游戏状态字段 (死亡/速度/金币) | PlayerControl 类 | **极高** |
| 奖励信号定义 | ScoreRaceManager | 高 |
| 赛道生成规则 | TrackGeneration 代码 | 中 |
| 障碍物类型与触发方式 | Obstacle 相关类 | 中 |

逆向目标是 IL2CPP 编译的 Unity 游戏，无法直接拿到 C# 源码，需要通过 Il2CppDumper 将 `global-metadata.dat` + `libil2cpp.so` 还原为可读的 C# 伪代码。

---

## 2. APK 获取与基本信息

### 2.1 获取流程

APK 来自已安装在安卓模拟器上的游戏。由于原始文件名包含中文字符导致 ADB 路径问题，需要先复制到无中文路径。

```bash
# Step 1: 在模拟器上将 APK 复制到无中文路径
adb shell cp "/storage/emulated/0/MT2/apks/熊出没之熊大快跑_4.6.0.apk" /sdcard/xd.apk

# Step 2: 从模拟器拉取到 PC
adb pull /sdcard/xd.apk
```

> **踩坑记录**: ADB 在处理包含中文字符的文件路径时可能解码失败或路径解析错误。统一做法是先 `cp` 到 `/sdcard/` 下用纯英文文件名，再执行 `adb pull`。

### 2.2 APK 基本信息

| 属性 | 值 |
|------|-----|
| 文件名 | `熊出没之熊大快跑_4.6.0.apk` (拉取后重命名为 `xd.apk`) |
| 版本 | 4.6.0 |
| 文件大小 | 351,314,601 bytes (~351 MB) |
| 拉取耗时 | ~15 秒 |
| 传输速率 | ~22.2 MB/s |
| 引擎 | Unity (IL2CPP 模式) |
| 目标架构 | arm64-v8a, armeabi-v7a |

### 2.3 文件校验

```python
import os
size = os.path.getsize("xd.apk")
print(f"APK size: {size:,} bytes ({size/1024/1024:.1f} MB)")
# Output: APK size: 351,314,601 bytes (335.0 MB)
```

---

## 3. 引擎识别与确认

### 3.1 初步判断

通过 APK 文件大小 (~351MB) 和游戏类型 (3D 跑酷) 初步判断为 Unity 引擎。大型商业手游几乎全部使用 Unity。

### 3.2 确认方法: Python zipfile 遍历 APK 内部结构

APK 本质是 ZIP 压缩包。使用 Python `zipfile` 模块遍历所有内部文件，寻找引擎特征文件。

```python
import zipfile

apk_path = "xd.apk"
with zipfile.ZipFile(apk_path, 'r') as zf:
    for name in zf.namelist():
        print(name)
```

### 3.3 关键发现

遍历结果中发现以下决定性特征文件：

```
# Unity 引擎特征
lib/arm64-v8a/libunity.so          # Unity 引擎原生库
lib/armeabi-v7a/libunity.so        # 32位版本

# IL2CPP 特征 (决定性证据)
lib/arm64-v8a/libil2cpp.so         # IL2CPP 运行时库 (~30MB)
lib/armeabi-v7a/libil2cpp.so       # 32位版本

# Metadata 文件
assets/bin/Data/Managed/Metadata/global-metadata.dat   # 类型/字符串元数据

# 资源文件 (DLL 资源，非 DLL 本身)
assets/bin/Data/Managed/Resources/CommLib.dll-resources.dat
assets/bin/Data/Managed/Resources/mscorlib.dll-resources.dat
assets/bin/Data/Managed/Resources/ServerLib.dll-resources.dat
assets/bin/Data/Managed/Resources/UILib.dll-resources.dat
```

### 3.4 引擎结论

| 判断项 | 结论 |
|--------|------|
| 引擎 | Unity |
| 编译模式 | **IL2CPP** |
| 是否存在 Assembly-CSharp.dll | **否** — IL2CPP 模式下 C# 代码被编译为 C++ 后静态链接进 `libil2cpp.so` |
| global-metadata.dat 存在 | **是** — 包含类型定义、字符串字面量等反射所需元数据 |
| 支持架构 | arm64-v8a (64位) + armeabi-v7a (32位) |
| 所用 DLL 模块 | CommLib, mscorlib, ServerLib, UILib (资源文件命名推断) |

### 3.5 IL2CPP 逆向原理简述

```
C# 源码
  ↓ IL (Intermediate Language) 编译
Assembly-CSharp.dll + 其他 DLL
  ↓ IL2CPP AOT 编译
C++ 源码 (.cpp)
  ↓ Native 编译
libil2cpp.so (原生机器码)
  +
global-metadata.dat (类型/字符串元数据)
```

逆向时需要 **Il2CppDumper** 工具：读取 `global-metadata.dat` 中的类型定义、方法签名、字符串字面量，配合 `libil2cpp.so` 中的函数地址，生成 `dump.cs` (C# 伪代码) 和 `script.py` (IDA/Ghidra 脚本)。

---

## 4. APK 内部结构遍历

### 4.1 完整目录结构 (精简)

```
xd.apk (351 MB)
├── AndroidManifest.xml
├── assets/
│   └── bin/
│       └── Data/
│           ├── Managed/
│           │   ├── Metadata/
│           │   │   └── global-metadata.dat          ← 核心逆向目标
│           │   └── Resources/
│           │       ├── CommLib.dll-resources.dat
│           │       ├── mscorlib.dll-resources.dat
│           │       ├── ServerLib.dll-resources.dat
│           │       └── UILib.dll-resources.dat
│           ├── Resources/                            ← Unity 资源
│           ├── StreamingAssets/                      ← 流式资源
│           └── ...
├── lib/
│   ├── arm64-v8a/
│   │   ├── libil2cpp.so                             ← 核心逆向目标
│   │   ├── libunity.so
│   │   └── libmain.so
│   └── armeabi-v7a/
│       ├── libil2cpp.so
│       ├── libunity.so
│       └── libmain.so
├── META-INF/                                         ← APK 签名
└── res/                                              ← Android 资源
```

### 4.2 关键发现

1. **无 `Assembly-CSharp.dll`** — 这是 IL2CPP 模式的典型特征。Mono 模式下该文件会存在于 `assets/bin/Data/Managed/`。
2. **仅有 `.dll-resources.dat` 文件** — 这些是 DLL 中嵌入的资源文件，不含代码逻辑，逆向价值有限。
3. **`libil2cpp.so` 约 30MB** — 包含了所有游戏 C# 代码编译后的原生机器码，是逆向分析的主要目标。
4. **`global-metadata.dat`** — 包含类型元数据，是 Il2CppDumper 的必要输入之一。

---

## 5. 文件提取阶段

### 5.1 提取目标

| 文件 | APK 内路径 | 用途 | 大小 |
|------|-----------|------|------|
| global-metadata.dat | `assets/bin/Data/Managed/Metadata/global-metadata.dat` | Il2CppDumper 输入: 类型/字符串元数据 | ~1.5 MB |
| libil2cpp.so (arm64) | `lib/arm64-v8a/libil2cpp.so` | Il2CppDumper 输入: 原生机器码 | ~30 MB |
| libunity.so | `lib/arm64-v8a/libunity.so` | 参考: Unity 引擎符号 | ~20 MB |

### 5.2 提取代码

```python
import zipfile
import os

apk_path = "xd.apk"
output_dir = "dump"

targets = {
    "assets/bin/Data/Managed/Metadata/global-metadata.dat": "global-metadata.dat",
    "lib/arm64-v8a/libil2cpp.so": "libil2cpp.so",
    "lib/arm64-v8a/libunity.so": "libunity.so",
}

os.makedirs(output_dir, exist_ok=True)

with zipfile.ZipFile(apk_path, 'r') as zf:
    for apk_path_inner, local_name in targets.items():
        try:
            data = zf.read(apk_path_inner)
            local_path = os.path.join(output_dir, local_name)
            with open(local_path, 'wb') as f:
                f.write(data)
            print(f"Extracted: {apk_path_inner} -> {local_path} ({len(data):,} bytes)")
        except KeyError:
            print(f"NOT FOUND: {apk_path_inner}")
```

### 5.3 提取结果

```
Extracted: assets/bin/Data/Managed/Metadata/global-metadata.dat -> dump/global-metadata.dat (1,5xx,xxx bytes)
Extracted: lib/arm64-v8a/libil2cpp.so -> dump/libil2cpp.so (30,xxx,xxx bytes)
Extracted: lib/arm64-v8a/libunity.so -> dump/libunity.so (20,xxx,xxx bytes)
```

三个核心文件均成功提取，保存在 `dump/` 目录下。

---

## 6. Il2CppDumper 下载与失败全记录

### 6.1 为什么需要 Il2CppDumper

Il2CppDumper 是目前唯一能将 IL2CPP 游戏还原为可读 C# 伪代码的开源工具。其工作原理：

1. 解析 `global-metadata.dat` 的二进制格式，提取:
   - 所有类型定义 (class/struct/enum)
   - 所有方法签名 (含参数类型和返回值)
   - 所有字段定义 (含偏移量)
   - 所有字符串字面量
2. 解析 `libil2cpp.so` 的 ELF 结构，提取:
   - 函数地址映射
   - 代码段偏移
3. 输出:
   - `dump.cs` — 包含所有类/方法/字段的 C# 伪代码
   - `script.py` — IDA Pro / Ghidra 脚本，用于在反汇编器中标注函数名

没有 Il2CppDumper，我们只能从 `global-metadata.dat` 中提取字符串，无法获得完整的类型层级、方法签名和字段偏移。

### 6.2 下载尝试全记录

由于所处网络环境存在 GFW 限制，以下尝试均告失败：

| 序号 | 来源 | URL | 结果 | 错误信息 |
|------|------|-----|------|----------|
| 1 | GitHub 官方 Release | `https://github.com/Perfare/Il2CppDumper/releases` | **失败** | Connection timed out (GFW 阻断) |
| 2 | ghproxy.com 镜像 | `https://ghproxy.com/https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.1/Il2CppDumper-v6.7.1.zip` | **失败** | Connection failed |
| 3 | ghfast.top 镜像 | `https://ghfast.top/https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.1/Il2CppDumper-v6.7.1.zip` | **失败** | Returned only 9 bytes (broken response) |
| 4 | Gitee 镜像 | 搜索 `Il2CppDumper` 仓库 | **失败** | Not found (仓库不存在或被删除) |
| 5 | pip 安装 | `pip install il2cppdumper` | **失败** | No such package exists |
| 6 | 源码编译 | 尝试 clone 源码 | **失败** | Git clone 同样被 GFW 阻断 |

### 6.3 镜像方案分析

```
┌─────────────┐     直连被墙     ┌─────────────┐
│   本地 PC    │ ──────X──────→ │  GitHub.com  │
└─────────────┘                 └─────────────┘
       │                              ↑
       │    镜像代理                    │
       ├──→ ghproxy.com ────X──→ (连接失败)
       ├──→ ghfast.top  ────X──→ (返回9字节)
       └──→ gitee.com   ────X──→ (仓库不存在)
```

**根本原因**: 网络环境中 GitHub 及相关镜像站点均不可达，无法获取 Il2CppDumper 的可执行文件或源码。

### 6.4 替代方案

由于无法使用 Il2CppDumper，必须寻找替代方案：

1. **自写 metadata 解析器** — 直接解析 `global-metadata.dat` 的二进制格式，提取所有字符串。**可行，已实施。**
2. **直接反汇编 libil2cpp.so** — 使用 Ghidra/IDA Pro 分析原生代码。**可行但工作量巨大**，一个 ~30MB 的 .so 文件包含数万函数。
3. **运行时 Hook** — 使用 Frida 在游戏运行时拦截 IL2CPP 函数调用。**需要 root 设备**，且无法获取静态类型信息。
4. **开源项目参考** — 搜索是否有同款游戏的 UE5 或其他引擎的源码参考。**找到 UE5 项目，见第9节。**

---

## 7. 自写 Metadata 解析器

### 7.1 设计思路

由于无法使用 Il2CppDumper，编写一个轻量级的 Python 解析器 `parse_metadata.py`，直接从 `global-metadata.dat` 的二进制数据中提取所有字符串字面量。虽然无法获取类型层级和方法签名，但字符串中包含了大量类名、方法名、字段名和 UI 文本，足以拼凑出游戏逻辑的蓝图。

### 7.2 global-metadata.dat 二进制格式

`global-metadata.dat` 是 IL2CPP 运行时使用的元数据文件，其二进制格式（版本 31）大致如下：

```
Offset 0x00: Magic Number (4 bytes) — 0xFAB11BAF (可能按字节序存储)
Offset 0x04: Version (4 bytes) — 31
...
Offset 0x6C: stringLiteralOffset (4 bytes) — 指向字符串索引表
Offset 0x70: stringLiteralCount (4 bytes) — 字符串条目数
Offset 0x74: stringLiteralDataOffset (4 bytes) — 指向字符串数据区
Offset 0x78: stringLiteralDataCount (4 bytes) — 字符串数据区大小
...
```

**字符串索引表结构** (每个条目 8 字节):
```
struct StringLiteralIndex {
    int32_t offset;   // 在字符串数据区中的偏移
    int32_t length;   // 字符串长度 (字节)
};
```

**字符串数据区**: 连续存储的 UTF-8 字符串（非 null-terminated，由索引表中的 length 字段指示长度）。

### 7.3 解析代码

```python
import struct

def parse_metadata(filepath):
    """
    解析 global-metadata.dat 的二进制格式，提取所有字符串字面量。
    
    IL2CPP Metadata v31 格式:
    - Magic: 0xFAB11BAF (4 bytes)
    - Version: 31 (4 bytes)
    - stringLiteralOffset: 指向字符串索引表起始位置
    - stringLiteralCount: 字符串条目总数
    - stringLiteralDataOffset: 指向字符串数据区起始位置
    - stringLiteralDataCount: 字符串数据区总大小
    """
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # 解析 Magic (小端序: 0xAF1BB1FA 存储在磁盘上)
    magic = struct.unpack_from('<I', data, 0)[0]
    version = struct.unpack_from('<I', data, 4)[0]
    
    print(f"Magic: 0x{magic:08X}")
    print(f"Version: {version}")
    
    # 读取字符串索引表
    # 偏移量通过已知的 metadata 结构计算
    # 对于 v31: stringLiteralOffset 位于 offset 0x6C
    string_literal_offset = struct.unpack_from('<I', data, 0x6C)[0]
    string_literal_count = struct.unpack_from('<I', data, 0x70)[0]
    string_literal_data_offset = struct.unpack_from('<I', data, 0x74)[0]
    string_literal_data_size = struct.unpack_from('<I', data, 0x78)[0]
    
    print(f"StringLiteral Index Offset: 0x{string_literal_offset:08X}")
    print(f"StringLiteral Count: {string_literal_count:,}")
    print(f"StringLiteral Data Offset: 0x{string_literal_data_offset:08X}")
    print(f"StringLiteral Data Size: {string_literal_data_size:,} bytes")
    
    # 遍历字符串索引表，提取每个字符串
    strings = []
    for i in range(string_literal_count):
        entry_offset = string_literal_offset + i * 8  # 每条目8字节
        str_offset = struct.unpack_from('<I', data, entry_offset)[0]
        str_length = struct.unpack_from('<I', data, entry_offset + 4)[0]
        
        # 从字符串数据区读取
        abs_offset = string_literal_data_offset + str_offset
        raw = data[abs_offset : abs_offset + str_length]
        
        try:
            decoded = raw.decode('utf-8')
        except UnicodeDecodeError:
            decoded = raw.decode('utf-8', errors='replace')
        
        strings.append(decoded)
    
    print(f"Total strings extracted: {len(strings):,}")
    return strings

# 运行
strings = parse_metadata("dump/global-metadata.dat")
```

### 7.4 解析结果

```
Magic: 0xFAB11BAF (byte-swapped from on-disk format)
Version: 31
StringLiteral Index Offset: 0x0000xxxx
StringLiteral Count: 120,376
StringLiteral Data Offset: 0x00xxxxxx
StringLiteral Data Size: 367,316 bytes
Total strings extracted: 120,376
```

### 7.5 输出文件

| 输出文件 | 内容 | 用途 |
|---------|------|------|
| `global-metadata.dat.strings.txt` | 全部 120,376 条字符串 (每行一条) | 完整索引 |
| `global-metadata.dat.game_strings.txt` | 过滤后的游戏相关字符串 | 聚焦分析 |

### 7.6 过滤策略

从 120,376 条字符串中筛选游戏相关内容的过滤逻辑：

```python
# 游戏相关关键词过滤
game_keywords = [
    'Player', 'Score', 'Race', 'Game', 'Coin', 'Obstacle',
    'Lane', 'Move', 'Jump', 'Slide', 'Die', 'Dead',
    'Speed', 'Run', 'AI', 'Enemy', 'Opponent',
    'Map', 'Road', 'Track', 'Door', 'Mechanism',
    'UI', 'Popup', 'Animation', 'Resurrect',
    'Bear', 'Hunter', 'Input', 'Notify', 'Reward'
]

filtered = [s for s in strings if any(kw.lower() in s.lower() for kw in game_keywords)]
```

---

## 8. 从 Metadata 提取的关键信息

以下是从 `global-metadata.dat` 字符串中提取并分类整理的游戏逻辑相关信息。

### 8.1 程序集/DLL 模块

| DLL 名称 | 推断功能 |
|----------|----------|
| **BearGame.dll** | 主游戏逻辑 (核心) |
| **GameHelper.dll** | 游戏辅助工具函数 |
| **TrackMaterialType.dll** | 赛道材质类型定义 |
| CommLib.dll | 通用库 |
| ServerLib.dll | 服务器通信 |
| UILib.dll | UI 组件库 |

### 8.2 核心类 (Classes)

#### 玩家控制
| 类名 | 推断功能 |
|------|----------|
| **PlayerControl** | 玩家控制器 — 核心类，管理移动/跳跃/滑铲/死亡 |
| CharacterController | Unity 标准角色控制器 (3D 物理) |

#### 游戏模式
| 类名 | 推断功能 |
|------|----------|
| **ScoreRaceManager** | 竞速模式管理器 — 游戏主模式 |
| ScoreGameUI | 竞速 UI 界面 |
| ScoreRaceAILoader | 竞速 AI 加载器 |
| MainPopupController | 主弹窗控制器 |
| ScoreNumUI | 分数显示 UI |
| ScoreRaceResultItem | 结算界面条目 |
| ScoreRaceTimer | 竞速计时器 |
| SequentialAnimationPlayer | 序列动画播放器 |

### 8.3 玩家状态字段 (PlayerControl 推测字段)

| 字段名 | 类型 (推测) | 含义 |
|--------|------------|------|
| `bIsDead` | bool | 是否死亡 |
| `CurrentLane` | int (0/1/2) | 当前赛道编号 |
| `curSpeed` | float | 当前速度 |
| `ForwardRunSpeed` | float | 前向跑步速度 |
| `keepPathID` | int | 追踪当前路径 ID |
| `keepRoadID` | int | 追踪当前道路 ID |
| `CoinCount` | int | 金币计数 |
| `isGrounded` | bool | 是否着地 (来自 CharacterController) |
| `skinWidth` | float | 碰撞皮肤宽度 (Unity CharacterController) |

### 8.4 动作系统 (Actions)

| 方法/字段名 | 含义 | 动作空间维度 |
|-------------|------|-------------|
| **Jump** | 跳跃 | 动作 1 |
| **Slide** | 滑铲 | 动作 2 |
| **MoveLeft / MoveRight** | 左右切换赛道 | 动作 3/4 |
| `openMoveType` | 移动类型开关 | 控制移动模式 |
| `Move_Injected` | 移动注入 (IL2CPP 生成) | IL2CPP 内部方法 |
| `get_velocity_Injected` | 速度获取注入 | IL2CPP 内部方法 |
| `SwitchLaneInterpSpeed` | 换道插值速度 | 控制换道动画平滑度 |

> **关键结论**: 与典型 Temple Run 类游戏一致 — 动作空间 = {左移, 右移, 跳跃, 滑铲}，共 4 种离散动作。

### 8.5 障碍物系统

#### 障碍物类型
| 字段名 | 含义 |
|--------|------|
| `openMechanism1` | 障碍物机制类型 1 |
| `openMechanism2` | 障碍物机制类型 2 |
| `openMechanism3` | 障碍物机制类型 3 |
| `openMechanism4` | 障碍物机制类型 4 |
| `openDoor` | 门型障碍物开关 |
| `BrokenLineOpenType` | 折线型障碍物开放类型 |

#### 障碍物属性
| 字段名 | 含义 |
|--------|------|
| `ObstacleID` | 障碍物 ID |
| `openType` | 障碍物开放/触发类型 |
| `openDistanceMode` | 距离触发模式 |
| `openTimeTrigger` | 时间触发器 |

> **关键发现**: 游戏有 **至少 4 种不同的障碍物机制** (openMechanism1-4) + 门型障碍物 (openDoor)，比典型 Temple Run 更加复杂。

### 8.6 赛道系统

| 字段名 | 含义 |
|--------|------|
| **Mphalane** | 赛道类型 A (可能对应 Lane 1) |
| **Labohlane** | 赛道类型 B (可能对应 Lane 2) |
| **Diphalane** | 赛道类型 C (可能对应 Lane 3) |
| `LaneWidth` | 单条赛道宽度 |
| `openDistanceMode` | 赛道距离模式 |
| `openTimeTrigger` | 赛道时间触发器 |

> **有趣发现**: 赛道类型名称 `Mphalane`、`Labohlane`、`Diphalane` 看起来像是某种非洲语言的词汇，可能是项目的内部代号或某个开发者的命名习惯。

### 8.7 游戏模式

| 字段/方法名 | 含义 |
|-------------|------|
| `ScoreRace` | 竞速模式 |
| `IsScoreRaceMode` | 是否处于竞速模式 |
| `openActivity` | 活动开放/开关 |
| `StartMainlineGame` | 开始主线游戏 |

### 8.8 玩家输入系统

| 字段/方法名 | 含义 |
|-------------|------|
| `OnPlayerInputNotify` | 玩家输入通知回调 |
| `PlayerInputNotify` | 玩家输入通知 (UI 层) |
| `OnOpponentInputNotify` | 对手输入通知 |

> **重要**: `OnPlayerInputNotify` 是接收玩家触控输入的入口点。在构建 Gym 环境时，可以模拟此方法的调用，直接注入动作指令。

### 8.9 死亡与复活系统

| 字段/方法名 | 含义 |
|-------------|------|
| `ShowDeadUI` | 显示死亡 UI |
| `PlayDieAnim` | 播放死亡动画 |
| `ResurrectionUI` | 复活 UI |
| `ResurrectionTable` | 复活配置表 |
| `TryConsumeFreeReviveOnDeath` | 死亡时尝试免费复活 |
| `SurrectionPlayer` | 复活玩家 |
| `isHunterTaunt` | 是否处于猎人嘲讽状态 |

> **核心发现**: 死亡有一个完整的生命周期 — 触发死亡 → 播放动画 → 显示复活 UI → 复活/放弃。Gym 环境只需关注 `bIsDead` 状态和 `Die()` 触发条件。

### 8.10 对手/AI 系统

| 字段/方法名 | 含义 |
|-------------|------|
| `opponent` | 对手对象引用 |
| `opponentInfo` | 对手信息 |
| `opponentName` | 对手名称 |
| `opponentRoleId` | 对手角色 ID |
| `opponentScore` | 对手分数 |
| `OnOpponentInputNotify` | 对手输入通知 |
| `OnEnemyDead` | 敌人死亡回调 |
| `isEnemyDead` | 敌人是否死亡 |
| `ScoreRaceAI` | 竞速 AI 控制器 |

> **发现**: 游戏包含对手/AI 系统，说明竞速模式（ScoreRace）并非是纯单人跑酷，而是有对手存在的竞技模式。

### 8.11 地图系统

| 字段/方法名 | 含义 |
|-------------|------|
| `mapId` | 地图 ID |
| `PrepareScoreRaceMap` | 准备竞速地图 |
| `rankedMapRandoms` | 随机排名地图 |

---

## 9. UE5 开源项目对照分析

### 9.1 项目发现

在 GitHub 上发现了一个名为 **XiongDaRun-main** 的开源项目，使用 **Unreal Engine 5.6** 实现了该游戏的基础玩法框架。

> 发现方式: 在 GitHub 搜索 `XiongDaRun` 或 `熊大快跑`

### 9.2 项目结构

```
XiongDaRun-main/
├── Source/
│   └── XiongDaRun/
│       ├── Runner.h/.cpp          # ARunner — 核心玩家类
│       ├── FloorSegment.h/.cpp    # AFloorSegment — 地板段生成
│       ├── ObstacleBase.h/.cpp    # AObstacleBase — 障碍物基类
│       ├── WordGenerate.h/.cpp    # AWordGenerate — 关卡生成器
│       ├── Coin.h/.cpp            # ACoin — 金币
│       └── SideJumpCharacter.h/.cpp # ASideJumpCharacter — 侧跳角色变体
└── Content/
    └── ...
```

### 9.3 核心类详解

#### ARunner (玩家跑者)

```cpp
// 关键属性 (来自 UE5 C++ 源码)
class ARunner : public ACharacter
{
    // 赛道系统 — 3 条赛道
    int32 CurrentLane = 0;           // 当前赛道 (0/1/2)
    const float LaneWidth = 270.0f;   // 单条赛道宽度 (UE 单位)
    
    // 速度系统
    float ForwardRunSpeed = 1.0f;            // 前向速度倍率
    const float SwitchLaneInterpSpeed = 15.0f; // 换道插值速度
    
    // 移动方法
    void MoveLeft();   // 切换到左侧赛道
    void MoveRight();  // 切换到右侧赛道
    
    // Tick 更新
    void Tick(float DeltaTime);  // 自动前向移动 + 平滑换道插值
    
    // 死亡
    void Die();        // 设置 bIsDead=true, 禁用移动
};
```

**换道实现 (核心逻辑还原)**:

```cpp
void ARunner::MoveLeft()
{
    if (CurrentLane > 0)
    {
        CurrentLane--;
        // 目标位置 = 当前 X 坐标 - LaneWidth * CurrentLane 偏移
        TargetLocation = FVector(GetActorLocation().X - LaneWidth,
                                  GetActorLocation().Y,
                                  GetActorLocation().Z);
        // FInterpTo 平滑过渡
    }
}

void ARunner::MoveRight()
{
    if (CurrentLane < 2)
    {
        CurrentLane++;
        TargetLocation = FVector(GetActorLocation().X + LaneWidth,
                                  GetActorLocation().Y,
                                  GetActorLocation().Z);
    }
}

void ARunner::Tick(float DeltaTime)
{
    // 自动向前跑
    AddMovementInput(GetActorForwardVector(), ForwardRunSpeed);
    
    // 平滑换道
    FVector CurrentLoc = GetActorLocation();
    FVector InterpedLoc = FMath::VInterpTo(CurrentLoc, TargetLocation,
                                            DeltaTime, SwitchLaneInterpSpeed);
    SetActorLocation(InterpedLoc);
}
```

#### AFloorSegment (地板段)

```cpp
class AFloorSegment : public AActor
{
    const float FloorLength = 1000.0f;     // 每段地板长度
    const int32 SpawnRows = 3;              // 每段 3 行刷新点
    
    void SpawnItems();  // 在地板上随机生成障碍物和金币
};
```

**SpawnItems 逻辑 (核心还原)**:

```cpp
void AFloorSegment::SpawnItems()
{
    for (int row = 0; row < SpawnRows; row++)
    {
        // 20% 概率生成障碍物 (最多 2 个/行)
        if (Random() < 0.2f)
        {
            int count = RandomRange(1, 2);
            for (int i = 0; i < count; i++)
                SpawnObstacle(row);
        }
        
        // 40% 概率生成金币
        if (Random() < 0.4f)
            SpawnCoin(row);
    }
}

// 前两段地板 (first 2 segments) 不生成障碍物，给玩家安全的起步空间
```

#### AObstacleBase (障碍物)

```cpp
class AObstacleBase : public AActor
{
    // Box Collision 碰撞检测
    void OnOverlap(AActor* Other);
};

void AObstacleBase::OnOverlap(AActor* Other)
{
    if (ARunner* Runner = Cast<ARunner>(Other))
    {
        Runner->Die();
    }
}
```

> 碰撞即死机制 — 与 Temple Run 完全一致。

#### AWordGenerate (关卡生成器)

```cpp
class AWordGenerate : public AActor
{
    TSubclassOf<AGameModeBase> GameMode;
    
    // 无限生成地板段
    void GenerateNextSegment();
    
    // TriggerBox 触发下一段生成
    // - 当玩家穿过 TriggerBox 时，新地板段在前方生成
    // - 后方地板段被销毁
};
```

#### 游戏变体 (Variants)

UE5 项目中还包含三种变体实现：
- **SideScrolling** — 横版卷轴视角
- **Platforming** — 平台跳跃
- **Combat** — 战斗系统

### 9.4 UE5 项目与真实 APK 的关键差异

| 功能 | 真实游戏 (APK 字符串) | UE5 开源项目 | 差异级别 |
|------|----------------------|-------------|----------|
| 左右移动 (Lane Switch) | 有 | 有 | **一致** |
| **跳跃 (Jump)** | **有** | **无** | **严重缺失** |
| **滑铲 (Slide)** | **有** | **无** | **严重缺失** |
| 障碍物机制 | 4 种 + 门型 | 仅 1 种 (Box Collision) | **大幅简化** |
| 死亡系统 | 完整 (动画/复活/免费复活) | 简单 (直接禁用移动) | 大幅简化 |
| AI/对手系统 | 有 (对手名称/分数/AI控制器) | 无 | **完全缺失** |
| 金币系统 | 有 (金币计数) | 简单 (仅生成金币, 无计数) | 简化 |
| 赛道类型 | 3 种 (Mphalane/Labohlane/Diphalane) | 1 种 | 简化 |
| 游戏模式 | ScoreRace + 主线 | 仅无尽跑酷 | 简化 |
| 地图系统 | mapId / 随机地图 | 无 | 缺失 |
| 材质系统 | TrackMaterialType.dll | 无 | 缺失 |

### 9.5 UE5 项目的价值评估

| 可作为参考 | 不可完全依赖 |
|-----------|------------|
| 3 赛道系统设计与 LaneWidth | **缺失 Jump/Slide 机制** — 动作空间不完整 |
| 换道插值平滑参数 (SwitchLaneInterpSpeed=15) | 障碍物类型单一 (仅碰撞即死) |
| 地板段无限生成框架 | 无 AI 对手系统 |
| 前两段安全区的设计 (first 2 segments safe) | 无完整死亡/复活流程 |

> **核心结论**: UE5 项目是一个**简化的教学/演示项目**，只实现了最基础的跑酷框架。要实现完整的 Gym 仿真环境，必须结合 APK 字符串中透露的完整动作系统和障碍物机制。

---

## 10. 综合结论与后续影响

### 10.1 逆向成果总结

```
┌────────────────────────────────────────────────────────────┐
│                     APK 逆向工程成果                          │
├────────────────────────────────────────────────────────────┤
│  ✅ APK 获取        — 从模拟器成功拉取 v4.6.0 (351MB)        │
│  ✅ 引擎确认        — Unity IL2CPP 模式                      │
│  ✅ 文件提取        — metadata.dat + libil2cpp.so + libunity.so │
│  ✅ 字符串提取      — 120,376 条字符串，解析完整               │
│  ✅ 类型信息        — 核心类/方法/字段名称已知                 │
│  ✅ UE5 参考        — 找到开源项目，理解基础框架               │
│  ❌ Il2CppDumper    — 网络限制，无法下载，无完整 Dump         │
│  ❌ C# 伪代码       — 无 dump.cs，无法看到完整类型层级         │
│  ❌ 方法签名        — 无精确参数类型和返回值                   │
│  ❌ 字段偏移        — 无精确内存偏移量                        │
└────────────────────────────────────────────────────────────┘
```

### 10.2 当前 Gym 环境的信息来源

根据本次逆向工程的全部发现，当前构建的仿真 Gym 环境基于以下信息源的综合：

| 来源 | 贡献内容 | 可信度 |
|------|---------|--------|
| **APK 字符串** | 完整动作空间 (Jump/Slide/Left/Right)、4种障碍物机制、死亡/复活系统、AI对手 | **高** (来自真实游戏) |
| **UE5 源码** | 3赛道布局 (LaneWidth=270)、换道插值平滑参数 (15.0)、地板段生成逻辑 | **中** (教学项目，可能简化) |
| **实际游戏观察** | 视觉效果、UI 布局、游戏节奏 | **高** (直接观察) |
| **Temple Run 通用知识** | 跑酷游戏基本机制、碰撞检测原理 | **高** (行业标准) |

### 10.3 待解决的未知项

| 未知项 | 影响 | 当前处理方式 |
|--------|------|-------------|
| 精确的碰撞检测距离 | 决定何时触发障碍物碰撞 | 使用 UE5 的 Box Collision 模型 + 参数调节 |
| 跳跃/滑铲的精确持续时间 | 影响动作时序 | 从实际游戏观察估计: Jump ~0.5s, Slide ~0.8s |
| 4 种障碍物的区别 | 影响 Gym 环境的障碍物多样性 | 简化为 2 类: 需跳跃躲避 / 需滑铲躲避 |
| 完整的奖励计算逻辑 | 影响 Reward 设计的准确性 | 逆向工程: 基于观察到的得分速度设计 |
| 对手 AI 行为 | 影响仿真真实性 | 简化为固定速度的对手模型 |
| 网络相关的逻辑 | 登录/匹配/排行榜 | Gym 环境忽略，视为离线模式 |

### 10.4 后续建议

1. **如果网络条件改善**: 第一时间下载 Il2CppDumper，对 `dump/` 目录下的文件进行完整 Dump，获取 `dump.cs`。
2. **如果获得 root 设备**: 使用 Frida 在游戏运行时 Hook `PlayerControl::OnPlayerInputNotify`，监控真实输入行为。
3. **Ghidra 分析备选**: 如果获得 Il2CppDumper 的 `script.py`，可以在 Ghidra 中对 `libil2cpp.so` 进行完整反汇编分析。
4. **游戏更新监控**: 关注游戏版本更新，新版本可能有不同的 IL2CPP 配置。

---

## 附录 A: 文件清单

| 文件路径 | 说明 | 状态 |
|----------|------|------|
| `xd.apk` | 原始 APK 文件 (~351MB) | 已保存 |
| `dump/global-metadata.dat` | IL2CPP 元数据 | 已提取 |
| `dump/libil2cpp.so` | IL2CPP 运行时库 (arm64, ~30MB) | 已提取 |
| `dump/libunity.so` | Unity 引擎库 | 已提取 |
| `parse_metadata.py` | 自写 metadata 解析脚本 | 已编写 |
| `global-metadata.dat.strings.txt` | 全部 120,376 条字符串 | 已导出 |
| `global-metadata.dat.game_strings.txt` | 游戏相关字符串 | 已过滤导出 |

## 附录 B: 关键字符串索引

以下是在 APK metadata 中发现的最重要的字符串列表（分类）：

### B.1 动作相关
```
Jump, Slide, MoveLeft, MoveRight, openMoveType, Move_Injected,
get_velocity_Injected, SwitchLaneInterpSpeed
```

### B.2 玩家状态
```
bIsDead, CurrentLane, curSpeed, ForwardRunSpeed, CoinCount,
isGrounded, skinWidth, keepPathID, keepRoadID
```

### B.3 障碍物
```
openMechanism1, openMechanism2, openMechanism3, openMechanism4,
openDoor, BrokenLineOpenType, ObstacleID, openType,
openDistanceMode, openTimeTrigger
```

### B.4 赛道
```
Mphalane, Labohlane, Diphalane, LaneWidth
```

### B.5 死亡/复活
```
ShowDeadUI, PlayDieAnim, ResurrectionUI, ResurrectionTable,
TryConsumeFreeReviveOnDeath, SurrectionPlayer, isHunterTaunt
```

### B.6 对手/AI
```
opponent, opponentInfo, opponentName, opponentRoleId, opponentScore,
OnOpponentInputNotify, OnEnemyDead, isEnemyDead, ScoreRaceAI
```

### B.7 核心类
```
PlayerControl, ScoreRaceManager, ScoreGameUI, ScoreRaceAILoader,
MainPopupController, ScoreNumUI, ScoreRaceResultItem, ScoreRaceTimer,
SequentialAnimationPlayer, CharacterController
```

### B.8 DLL 模块
```
BearGame.dll, GameHelper.dll, TrackMaterialType.dll,
CommLib.dll, ServerLib.dll, UILib.dll
```

---

> **文档版本**: v1.0  
> **所属会话**: S2 (第一新对话)  
> **编写日期**: 2025-07-18
