# -*- mode: python ; coding: utf-8 -*-
"""
LocalizationChecker.spec

Сборка: pyinstaller LocalizationChecker.spec

Что делает этот spec-файл:
- Собирает check_localization.pyw в один .exe без консольного окна (windowed=True)
- Если в окружении установлен tkinterdnd2, автоматически встраивает его данные
  (папку с бинарниками tkdnd) внутрь .exe, чтобы drag & drop работал у конечного
  пользователя без отдельной установки библиотеки. Если tkinterdnd2 не установлен,
  сборка всё равно пройдёт успешно — просто без поддержки drag & drop.
"""

import os

block_cipher = None

# ── Поиск tkinterdnd2 (опционально) ─────────────────────────────────────────
datas = []
hiddenimports = []

try:
    import tkinterdnd2
    tkdnd_pkg_dir = os.path.dirname(tkinterdnd2.__file__)
    # Встраиваем всю папку пакета (включая tkdnd/<платформа>/*.tcl и бинарники)
    datas.append((tkdnd_pkg_dir, "tkinterdnd2"))
    hiddenimports.append("tkinterdnd2")
    print(f"[spec] tkinterdnd2 найден: {tkdnd_pkg_dir} — будет встроен в сборку")
except ImportError:
    print("[spec] tkinterdnd2 не установлен — сборка будет без поддержки drag & drop")

a = Analysis(
    ['check_localization.pyw'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports + [
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
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
    name='LocalizationChecker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # windowed=True — без консольного окна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,           # укажите путь к .ico файлу, если нужен свой значок
)
