#!/usr/bin/env python3
"""
Финальный тест всех изменений
"""

import ast
import json
from pathlib import Path

# Проверяем синтаксис
with open('/workspaces/LocalizationChecker/check_localization.py', 'r', encoding='utf-8') as f:
    try:
        ast.parse(f.read())
        print("✓ Синтаксис корректен")
    except SyntaxError as e:
        print(f"✗ Синтаксическая ошибка: {e}")
        exit(1)

# Проверяем наличие исправлений
with open('/workspaces/LocalizationChecker/check_localization.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = [
    ("utf-8-sig в extract_json_from_file", "encoding='utf-8-sig'" in content),
    ("utf-8-sig в extract_json_from_jar", "decode('utf-8-sig')" in content),
    ("Кнопка темы в top_frame", "self.theme_btn = ttk.Button(top_frame" in content),
    ("theme_btn.pack RIGHT", 'self.theme_btn.pack(side=tk.RIGHT' in content),
    ("progress_frame использует tk.Frame", "self.progress_frame = tk.Frame(self.root)" in content),
    ("status_frame использует tk.Frame", "self.status_frame = tk.Frame(self.root)" in content),
    ("progress_label использует tk.Label", "self.progress_label = tk.Label(self.progress_frame" in content),
    ("status_label использует tk.Label", "self.status_label = tk.Label(self.status_frame" in content),
    ("Функция toggle_theme существует", "def toggle_theme(self):" in content),
    ("Кнопка закрытия в top_frame", 'ttk.Button(top_frame, text="❌ Закрыть"' in content),
]

print("\n✓ Проверка основных изменений:")
all_ok = True
for check_name, check_result in checks:
    status = "✓" if check_result else "✗"
    print(f"  {status} {check_name}")
    if not check_result:
        all_ok = False

# Проверяем что нет дублирования кнопки в search_frame
if 'self.theme_btn.pack(side=tk.RIGHT, padx=5)\n        \n        # Основная область с результатами' in content:
    # Это значит что нет никаких кнопок после theme_btn в search_frame
    print("  ✓ Нет дублирования кнопки в search_frame")
else:
    # Проверим более мягко
    if content.count('self.theme_btn') == 4:  # инициализация + pack + 2 раза в toggle_theme
        print("  ✓ Кнопка не дублирована")
    else:
        print(f"  ✗ Возможное дублирование (найдено {content.count('self.theme_btn')} упоминаний theme_btn)")
        all_ok = False

if all_ok:
    print("\n✅ Все проверки пройдены успешно!")
else:
    print("\n❌ Некоторые проверки не пройдены")
    exit(1)
