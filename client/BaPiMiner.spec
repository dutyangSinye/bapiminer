# -*- mode: python ; coding: utf-8 -*-


import os

from PyInstaller.utils.win32 import versioninfo as vi

_BRAND_ICO = os.path.join(SPECPATH, '..', 'brand', 'export', 'bapiminer.ico')

# exe 属性（资源管理器右键 → 属性 → 详细信息）统一品牌信息
_APP_VERSION = (1, 13, 0, 9)
_VERSION_INFO = vi.VSVersionInfo(
    ffi=vi.FixedFileInfo(
        filevers=_APP_VERSION, prodvers=_APP_VERSION,
        mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        vi.StringFileInfo([
            vi.StringTable('080404b0', [
                vi.StringStruct('CompanyName', '芯烨 Sinye'),
                vi.StringStruct('FileDescription', '扒皮矿工·矿机端'),
                vi.StringStruct('FileVersion', '1.13.0.9'),
                vi.StringStruct('InternalName', 'BaPiMiner'),
                vi.StringStruct('LegalCopyright', 'Copyright (C) 2026 芯烨 Sinye'),
                vi.StringStruct('OriginalFilename', 'BaPiMiner.exe'),
                vi.StringStruct('ProductName', '扒皮矿工 BaPiMiner'),
                vi.StringStruct('ProductVersion', '1.13.0.9'),
            ])]),
        vi.VarFileInfo([vi.VarStruct('Translation', [2052, 1200])]),
    ])

a = Analysis(
    ['miner_client.py'],
    pathex=[],
    binaries=[],
    datas=[('bapiminer-64.png', '.'), ('bapiminer-256.png', '.'),
           ('web', 'web')],
    hiddenimports=['pystray._win32', 'pystray._util', 'PIL.Image', 'PIL.ImageDraw'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BaPiMiner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_BRAND_ICO,
    version=_VERSION_INFO,
)
