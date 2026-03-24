# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['master_uninstaller.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['webview', 'webview.platforms.edgechromium', 'clr_loader', 'pythonnet'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MasterUninstaller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    uac_admin=True,
    icon=None,
)
