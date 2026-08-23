#!/usr/bin/env python3
"""
Лаунчер для запуска двойным кликом в Windows (.pyw не открывает консоль).
Вся логика живёт в check_localization.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_localization import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
