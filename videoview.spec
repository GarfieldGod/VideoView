# -*- mode: python ; coding: utf-8 -*-
"""videoview - PyInstaller 打包配置
跨平台（Windows / Linux）通用 spec 文件。

使用方法：
    # Windows
    pyinstaller videoview.spec

    # Linux
    pyinstaller videoview.spec

产物：
    Windows -> dist/videoview/  目录（含 videoview.exe + vlc/ 运行库）
    Linux   -> dist/videoview/  目录（含 videoview 可执行文件 + 系统依赖）
"""
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# -------- 路径 --------
# spec 执行时的基准目录（spec 所在目录即项目根目录）
PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))

# 入口脚本
ENTRY = os.path.join(PROJECT_ROOT, "main.py")

# 额外 hidden imports —— PyQt5 / OpenCV / python-vlc 的子模块与插件
hiddenimports = []
hiddenimports += collect_submodules("PyQt5")
hiddenimports += collect_submodules("cv2")
hiddenimports += ["vlc"]  # python-vlc

# 额外数据文件（保留相对路径）
datas = []

# 把项目内的 README 打进去（可选，便于用户查看）
if os.path.exists(os.path.join(PROJECT_ROOT, "README.md")):
    datas.append((os.path.join(PROJECT_ROOT, "README.md"), "."))

# 把 requirements.txt 打进去（可选）
if os.path.exists(os.path.join(PROJECT_ROOT, "requirements.txt")):
    datas.append((os.path.join(PROJECT_ROOT, "requirements.txt"), "."))


# -------- 二进制文件 / 共享库 --------
binaries = []

# 把项目根目录内的 vlc/ 整体作为 binaries + datas（同时应对 Windows 和 Linux）
# Windows: libvlc.dll / libvlccore.dll / *.dll 在 vlc/ 目录，插件在 vlc/plugins/
# Linux:   libvlc.so / libvlccore.so 在 vlc/lib/，插件在 vlc/lib/vlc/plugins/
VLC_SRC = os.path.join(PROJECT_ROOT, "vlc")
if os.path.isdir(VLC_SRC):
    # 1) 直接把整个 vlc/ 作为 "data" 复制到 dist/videoview/vlc/
    #    PyInstaller 的 data 规则是 (源路径, 目标相对目录)
    for root, dirs, files in os.walk(VLC_SRC):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, VLC_SRC)
            dest_dir = os.path.join("vlc", os.path.dirname(rel))
            binaries.append((src, dest_dir))

    # 2) 额外显式把 plugins 目录也作为 data 兜底（某些发行版插件在不同位置）
    #    对 Windows：plugins/ 下会有很多 .dll
    #    对 Linux：  lib/vlc/plugins/ 下会有很多 .so / .dat
    plugins_dir = os.path.join(VLC_SRC, "plugins")
    if os.path.isdir(plugins_dir):
        for root, dirs, files in os.walk(plugins_dir):
            for f in files:
                src = os.path.join(root, f)
                rel = os.path.relpath(src, VLC_SRC)
                dest_dir = os.path.join("vlc", os.path.dirname(rel))
                # 避免在上面 walk 中已添加的重复项
                if not any(b[0] == src for b in binaries):
                    binaries.append((src, dest_dir))


# -------- PyInstaller 主配置 --------
a = Analysis(
    [ENTRY],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 以下模块不需要，减小体积
        "tkinter",
        "test",
        "unittest",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtQuick",
        "PyQt5.QtQml",
        "PyQt5.Qt3DCore",
        "PyQt5.QtNetwork",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="videoview",        # 可执行文件名：videoview.exe / videoview
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # 启用 UPX 压缩（如系统可用）
    console=False,           # Windows 下不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="videoview",
)
