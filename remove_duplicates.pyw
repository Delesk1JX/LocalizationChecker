#!/usr/bin/env python3
"""
Лаунчер для запуска двойным кликом в Windows (.pyw не открывает консоль).
Вся логика живёт в remove_duplicates.py — здесь только запуск, чтобы не держать
вторую копию кода, которая быстро устареет.
"""
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from remove_duplicates import main  # noqa: E402

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # При запуске двойным кликом окна консоли нет, и ошибка просто исчезла бы.
        # Пишем её в лог утилиты и показываем короткое окно.
        error = traceback.format_exc()
        try:
            with (SCRIPT_DIR / "remove_duplicates.log").open("a", encoding="utf-8") as log:
                log.write("\nОшибка запуска:\n" + error)
        except OSError:
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Ошибка запуска",
                "Не удалось запустить утилиту.\n\n"
                + error.strip().splitlines()[-1]
                + "\n\nПодробности — в файле remove_duplicates.log рядом со скриптом.",
            )
        except Exception:
            pass
        sys.exit(1)
