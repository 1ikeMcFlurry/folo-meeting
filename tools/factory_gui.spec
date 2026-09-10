# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置:把 8 工位产测治具 factory_gui 打成单个 exe。
# 在 Windows 上构建:  py -m PyInstaller --clean tools/factory_gui.spec
# 产物:  dist/factory_gui.exe
block_cipher = None

a = Analysis(
    ["factory_gui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["factory_test", "serial", "serial.tools", "serial.tools.list_ports"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="factory_gui",
    debug=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,           # GUI 应用,不弹控制台
)
