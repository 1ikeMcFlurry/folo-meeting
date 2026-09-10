# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置:把 trae_studio 及其所有库模块打成单个 exe。
# 在 Windows 上构建:  py -m PyInstaller --clean tools/trae_studio.spec
# 产物:  dist/trae_studio.exe
#
# 惰性 import(在函数内)的模块 PyInstaller 扫不到,须在 hiddenimports 里显式列出。

block_cipher = None

# 各面板惰性导入的库模块 + 运行时依赖
HIDDEN = [
    "make_cardid", "factory_test", "token_broadcast_gui",
    "mp3_to_rtttl", "wav_to_adpcm", "adpcm_codec",
    "gen_pixel_text", "gen_reward_art", "gen_cn_font",
    "ble_card_client", "avatar_export_gui", "ble_advertiser_gui",
    "serial", "serial.tools", "serial.tools.list_ports",
]
# 音频/BLE 为重依赖,装了才打进去;没装则跳过(对应面板运行时才报缺依赖)。
for opt in ("librosa", "soundfile", "numpy", "miniaudio", "PIL", "bleak"):
    try:
        __import__(opt)
        HIDDEN.append(opt)
    except Exception:
        pass

a = Analysis(
    ["trae_studio.py"],
    pathex=["."],            # tools/ 目录,库模块都在这
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="trae_studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI 应用,不弹控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
