#!/usr/bin/env python3
"""
Тест инициализации GUI класса
"""

import tkinter as tk
from check_localization import LocalizationCheckerGUI, load_config

# Загружаем конфиг
load_config()

# Создаём root окно
root = tk.Tk()

try:
    # Инициализируем GUI
    app = LocalizationCheckerGUI(root)
    
    # Проверяем что всё создано
    assert hasattr(app, 'dark_mode'), "Missing dark_mode attribute"
    assert app.dark_mode == False, "dark_mode should be False initially"
    
    assert hasattr(app, 'theme_btn'), "Missing theme_btn attribute"
    assert hasattr(app, 'toggle_theme'), "Missing toggle_theme method"
    
    assert hasattr(app, 'full_tree'), "Missing full_tree"
    assert hasattr(app, 'partial_tree'), "Missing partial_tree"
    assert hasattr(app, 'missing_tree'), "Missing missing_tree"
    
    print("✓ GUI класс инициализирован успешно")
    print(f"✓ dark_mode = {app.dark_mode}")
    print(f"✓ theme_btn текст: {app.theme_btn.cget('text')}")
    print("✓ Все необходимые компоненты на месте")
    
    # Проверяем что toggle_theme работает
    app.toggle_theme()
    print(f"✓ После toggle_theme: dark_mode = {app.dark_mode}")
    print(f"✓ theme_btn текст изменился на: {app.theme_btn.cget('text')}")
    
    print("\n✓ Все тесты пройдены успешно!")
    
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()

finally:
    # Закрываем root
    try:
        root.destroy()
    except:
        pass
