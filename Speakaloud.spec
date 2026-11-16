# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

block_cipher = None

# App and assets data
datas = [
    ('assets', 'assets'),
]

binaries = []
hiddenimports = [
    'app',
    'app.audio_dsp',
    'app.config',
    'app.local_tts',
    'app.speech_rules',
    'app.state_manager',
    'app.tts_engine',
    'kokoro_onnx',
    'piper',
    'piper.voice',
    'piper.config',
    'espeakng_loader',
    'phonemizer',
    'phonemizer.backend',
    'phonemizer.backend.espeak',
    'phonemizer.backend.espeak.wrapper',
]

packages_to_collect = [
    'customtkinter',
    'pymupdf',
    'soundfile',
    'sounddevice',
    'onnxruntime',
    'kokoro_onnx',
    'piper_tts',
    'espeakng_loader',
    'edge_tts',
]

for pkg in packages_to_collect:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[Spec] Note: collect_all for {pkg}: {e}")

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'torch', 'torchvision', 'jupyter', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Speakaloud',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Speakaloud',
)
