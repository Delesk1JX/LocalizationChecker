#!/usr/bin/env python3
"""
Тест для проверки импортов и синтаксиса
"""

# Подавляем попытку открытия GUI
import os
os.environ['DISPLAY'] = ''

try:
    import tkinter as tk
    from tkinter import ttk
    
    # Импортируем функции из модуля
    from check_localization import load_config, CONFIG
    
    # Загружаем конфигурацию
    config = load_config()
    
    print("✓ Импорты успешны")
    print(f"✓ Конфигурация загружена: {config}")
    print(f"✓ Row colors: {config.get('row_colors', {})}")
    
    # Проверяем что есть нужные переменные в config
    assert 'row_colors' in config, "Missing row_colors in config"
    assert 'jar' in config['row_colors'], "Missing jar color"
    assert 'translated_mods' in config['row_colors'], "Missing translated_mods color"
    assert 'missing' in config['row_colors'], "Missing missing color"
    
    print("✓ Все проверки пройдены успешно!")
    
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()
