# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from pathlib import Path

block_cipher = None

# 获取streamlit的安装路径
import streamlit
streamlit_path = Path(streamlit.__file__).parent

a = Analysis(
    ['container_system.py'],  # 你的主程序文件名
    pathex=[],
    binaries=[],
    datas=[
        (str(streamlit_path), 'streamlit'),
        ('container_system.db', '.'),  # 数据库文件
    ],
    hiddenimports=[
        'streamlit.runtime',
        'streamlit.runtime.scriptrunner',
        'streamlit.runtime.scriptrunner.script_run_context',
        'xlrd',
        'openpyxl',
        'plotly',
        'reportlab',
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
    name='集装箱配箱系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 改为False可隐藏控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico'  # 可选：添加软件图标
)
