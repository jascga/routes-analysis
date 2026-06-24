# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec 文件 - 用于 windows 打包
# 用法: pyinstaller routesanalysis.spec
# 或:   pyinstaller --clean routesanalysis.spec
#
# 需从项目根目录运行: pyinstaller scripts\routesanalysis.spec

import sys
from pathlib import Path

# 项目根目录（spec 文件的父目录的父目录）
ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH 由 pyinstaller 注入)
sys.path.insert(0, str(ROOT))

block_cipher = None

# 资源文件
datas = [
    (str(ROOT / 'README.md'), '.'),
]

hiddenimports = [
    'openpyxl.cell._writer',
]

a = Analysis(
    [str(ROOT / 'routesanalysis' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports + [
        'routesanalysis',
        'routesanalysis.analyzer',
        'routesanalysis.exporter',
        'routesanalysis.parser',
        'routesanalysis.models',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='routesanalysis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

