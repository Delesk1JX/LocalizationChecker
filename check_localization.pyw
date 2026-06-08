#!/usr/bin/env python3
"""
Программа для проверки русской локализации в модах Minecraft.
Работает напрямую с .jar файлами, проверяет наличие ru_ru.json и .lang файлов,
сравнивает ключи с en_us.json или en_US.lang.
Также поддерживает проверку переводов в папке TranslatedMods.

Поддерживаемые форматы:
- JSON (Minecraft 1.13+): en_us.json, ru_ru.json
- .lang (Minecraft 1.12.2 и ниже): en_US.lang, ru_RU.lang

Категории:
- Полный перевод: 100% совпадение ключей с английским файлом (все ключи из en есть в ru)
- Неполный перевод: есть русский файл, но не все ключи из английского присутствуют
- Отсутствует: есть английский файл, но нет русского

Важно: Моды без файла en_us.json/en_US.lang не учитываются при проверке.

Лишние ключи в русском файле (которых нет в английском) сохраняются в отчете,
так как могут использоваться для обратной совместимости.
"""

import os
import json
import zipfile
import argparse
import threading
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# GUI импортируется только при запуске в режиме GUI
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


# Глобальные переменные
TRANSLATED_MODS_PATH: Optional[Path] = None
CONFIG: Dict[str, Any] = {}


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    """
    Загружает конфигурацию из JSON файла.
    
    Args:
        config_file: Путь к файлу конфига
        
    Returns:
        Словарь с конфигурацией или значения по умолчанию
    """
    global CONFIG
    try:
        config_path = Path(config_file)
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                CONFIG = json.load(f)
                # Устанавливаем TRANSLATED_MODS_PATH из конфига, если указан
                if CONFIG.get("translated_mods_path"):
                    translated_mods_path = Path(CONFIG["translated_mods_path"])
                    # Проверяем несколько возможных расположений
                    possible_paths = [
                        translated_mods_path,
                        Path.cwd() / translated_mods_path,
                        Path.cwd().parent / translated_mods_path,
                    ]
                    for path in possible_paths:
                        if path.exists() and path.is_dir():
                            set_translated_mods_path(path)
                            break
                # Гарантируем наличие секции row_colors
                CONFIG.setdefault("row_colors", {
                    "jar": "#d4f4dd",
                    "translated_mods": "#d1e7ff",
                    "missing": "#ffe4e1"
                })
                return CONFIG
    except Exception as e:
        print(f"⚠️  Ошибка загрузки конфига: {e}")
    
    # Значения по умолчанию
    CONFIG = {
        "translated_mods_path": "TranslatedMods",
        "supported_languages": ["ru_ru"],
        "max_workers": 4,
        "show_statistics": True,
        "default_export_file": "localization_results.json",
        "row_colors": {
            "jar": "#d4f4dd",
            "translated_mods": "#d1e7ff",
            "missing": "#ffe4e1"
        }
    }
    return CONFIG


def set_translated_mods_path(path: Optional[Path]):
    """Устанавливает путь к папке TranslatedMods."""
    global TRANSLATED_MODS_PATH
    TRANSLATED_MODS_PATH = path


def get_translated_mods_path() -> Optional[Path]:
    """Возвращает текущий путь к папке TranslatedMods."""
    return TRANSLATED_MODS_PATH


def find_translated_mods_directory(base_path: Path) -> Optional[Path]:
    """
    Ищет папку TranslatedMods рядом с указанной директорией, в ней или внутри HelperTranslatorRU.
    
    Args:
        base_path: Базовая директория для поиска
        
    Returns:
        Путь к папке TranslatedMods или None если не найдена
    """
    # Проверяем несколько возможных мест расположения TranslatedMods
    possible_paths = [
        base_path / "TranslatedMods",
        base_path.parent / "TranslatedMods",
        Path.cwd() / "TranslatedMods",
        base_path / "HelperTranslatorRU" / "TranslatedMods",
        base_path.parent / "HelperTranslatorRU" / "TranslatedMods",
        Path.cwd() / "HelperTranslatorRU" / "TranslatedMods",
    ]
    
    for path in possible_paths:
        if path.exists() and path.is_dir():
            return path
    
    return None


def extract_json_from_file(file_path: Path) -> Optional[Dict[str, str]]:
    """
    Извлекает JSON файл локализации из файловой системы.
    
    Args:
        file_path: Путь к JSON файлу
        
    Returns:
        Словарь с ключами локализации или None, если файл не найден/ошибка
    """
    try:
        if not file_path.exists():
            return None
        
        # Используем utf-8-sig для корректной обработки BOM (Byte Order Mark)
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
            data = json.loads(content)
            return data
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        print(f"⚠️  Ошибка чтения {file_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {file_path}: {e}")
        return None


def parse_lang_file(file_path: Path) -> Optional[Dict[str, str]]:
    """
    Парсит файл локализации .lang (формат для Minecraft 1.12.2 и ниже).
    
    Формат файла:
    # Комментарии начинаются с решетки
    key=value
    another.key=another value
    
    Args:
        file_path: Путь к файлу .lang
        
    Returns:
        Словарь с ключами локализации или None, если файл не найден/ошибка
    """
    try:
        if not file_path.exists():
            return None
        
        result = {}
        # Используем utf-8-sig для корректной обработки BOM (Byte Order Mark)
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Пропускаем пустые строки и комментарии
                if not line or line.startswith('#'):
                    continue
                
                # Ищем первое вхождение '=' для разделения ключа и значения
                if '=' in line:
                    eq_index = line.index('=')
                    key = line[:eq_index].strip()
                    value = line[eq_index + 1:].strip()
                    
                    if key:  # Ключ не должен быть пустым
                        result[key] = value
                elif line:
                    # Строка без '=' - возможно malformed, пропускаем с предупреждением
                    print(f"⚠️  Пропущена строка {line_num} в {file_path}: нет разделителя '='")
        
        return result
    except (UnicodeDecodeError, IOError) as e:
        print(f"⚠️  Ошибка чтения {file_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {file_path}: {e}")
        return None


def parse_lang_from_jar(jar_path: Path, lang_path: str) -> Optional[Dict[str, str]]:
    """
    Парсит файл локализации .lang из .jar архива.
    
    Args:
        jar_path: Путь к .jar файлу
        lang_path: Путь внутри архива (например, 'assets/modname/lang/en_US.lang')
    
    Returns:
        Словарь с ключами локализации или None, если файл не найден/ошибка
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            # Ищем файл внутри архива (регистронезависимый поиск пути)
            actual_path = None
            for name in jar_file.namelist():
                if name.lower() == lang_path.lower():
                    actual_path = name
                    break
            
            if actual_path is None:
                return None
            
            with jar_file.open(actual_path) as f:
                # Используем utf-8-sig для корректной обработки BOM (Byte Order Mark)
                content = f.read().decode('utf-8-sig')
                
                result = {}
                for line_num, line in enumerate(content.splitlines(), 1):
                    line = line.strip()
                    
                    # Пропускаем пустые строки и комментарии
                    if not line or line.startswith('#'):
                        continue
                    
                    # Ищем первое вхождение '=' для разделения ключа и значения
                    if '=' in line:
                        eq_index = line.index('=')
                        key = line[:eq_index].strip()
                        value = line[eq_index + 1:].strip()
                        
                        if key:  # Ключ не должен быть пустым
                            result[key] = value
                
                return result
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as e:
        print(f"⚠️  Ошибка чтения {lang_path} из {jar_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {jar_path}: {e}")
        return None


def find_ru_ru_in_translated_mods(mod_name: str) -> Optional[Path]:
    """
    Ищет файл ru_ru.json или ru_RU.lang для мода в папке TranslatedMods.
    
    Структура TranslatedMods:
    TranslatedMods/
    ├── <название_мода>/
    │   └── lang/
    │       ├── ru_ru.json (для новых версий Minecraft 1.13+)
    │       └── ru_RU.lang (для старых версий Minecraft 1.12.2 и ниже)
    
    Args:
        mod_name: Название мода (извлеченное из assets внутри .jar файла)
        
    Returns:
        Путь к файлу локализации (.json или .lang) или None если не найден
    """
    if TRANSLATED_MODS_PATH is None:
        return None
    
    # mod_name уже является чистым именем мода из assets
    possible_names = [mod_name]
    
    # Пробуем найти папку мода в TranslatedMods
    for name in possible_names:
        mod_dir = TRANSLATED_MODS_PATH / name
        if mod_dir.exists() and mod_dir.is_dir():
            # Сначала ищем ru_ru.json (новый формат)
            ru_ru_path = mod_dir / "lang" / "ru_ru.json"
            if ru_ru_path.exists():
                return ru_ru_path
            # Затем ищем ru_RU.lang (старый формат для 1.12.2 и ниже)
            ru_lang_path = mod_dir / "lang" / "ru_RU.lang"
            if ru_lang_path.exists():
                return ru_lang_path
    
    # Если точное совпадение не найдено, ищем по более строгим правилам.
    # Избегаем ложных совпадений для очень коротких имен, например 'aq'.
    if TRANSLATED_MODS_PATH.exists():
        for item in TRANSLATED_MODS_PATH.iterdir():
            if item.is_dir():
                # Проверяем, содержит ли название папки название мода или наоборот.
                # Применяем правило только для длинных имен, чтобы не сопоставлять 'aq' с 'aquaculture'.
                if len(mod_name) > 3 and len(item.name) > 3:
                    if mod_name.lower() in item.name.lower() or item.name.lower() in mod_name.lower():
                        # Сначала ищем JSON
                        ru_ru_path = item / "lang" / "ru_ru.json"
                        if ru_ru_path.exists():
                            return ru_ru_path
                        # Затем ищем .lang
                        ru_lang_path = item / "lang" / "ru_RU.lang"
                        if ru_lang_path.exists():
                            return ru_lang_path
        
        # Дополнительная проверка: если имя папки является аббревиатурой или префиксом
        # Например, ali -> advancedlootinfo (a-l-i первые буквы слов)
        import re
        # Разбиваем mod_name на слова (по подчеркиваниям или дефисам)
        words = re.split(r'[_-]', mod_name.lower())
        if len(words) == 1:
            # Если одно слово, пробуем разбить по буквам
            words = re.findall(r'[a-z]+', mod_name.lower())
        
        # Создаем аббревиатуру из первых букв слов
        if len(words) > 1:
            abbrev = ''.join([w[0] for w in words if w])
            for item in TRANSLATED_MODS_PATH.iterdir():
                if item.is_dir() and item.name.lower() == abbrev:
                    # Сначала ищем JSON
                    ru_ru_path = item / "lang" / "ru_ru.json"
                    if ru_ru_path.exists():
                        return ru_ru_path
                    # Затем ищем .lang
                    ru_lang_path = item / "lang" / "ru_RU.lang"
                    if ru_lang_path.exists():
                        return ru_lang_path
        
        # Также проверяем, начинается ли mod_name с названия папки.
        # Но только для более длинных имен, чтобы избежать неправильных совпадений.
        for item in TRANSLATED_MODS_PATH.iterdir():
            if item.is_dir() and len(item.name) > 3 and len(mod_name) > 4:
                if mod_name.lower().startswith(item.name.lower()):
                    # Сначала ищем JSON
                    ru_ru_path = item / "lang" / "ru_ru.json"
                    if ru_ru_path.exists():
                        return ru_ru_path
                    # Затем ищем .lang
                    ru_lang_path = item / "lang" / "ru_RU.lang"
                    if ru_lang_path.exists():
                        return ru_lang_path
    
    return None


def extract_mod_name_from_assets(jar_path: Path) -> Optional[str]:
    """
    Извлекает имя мода из структуры папок assets внутри .jar файла.
    
    Например, если внутри .jar есть путь 'assets/advancedlootinfo/lang/en_us.json',
    то имя мода будет 'advancedlootinfo'.
    
    Args:
        jar_path: Путь к .jar файлу
        
    Returns:
        Имя мода или None если не удалось извлечь
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                # Ищем файлы в папке assets/<modname>/lang/
                if '/lang/' in name or '\\\\lang\\\\' in name:
                    # Извлекаем путь до lang
                    parts = name.replace('\\\\', '/').split('/')
                    # Находим индекс 'assets' и берем следующий элемент
                    for i, part in enumerate(parts):
                        if part == 'assets' and i + 1 < len(parts):
                            mod_name = parts[i + 1]
                            if mod_name and mod_name != 'lang':
                                return mod_name
    except zipfile.BadZipFile:
        return None
    
    return None


def check_translated_mods_localization(jar_path: Path, en_data: Dict[str, str], en_us_path: str) -> Dict[str, Any]:
    """
    Проверяет наличие перевода для мода в папке TranslatedMods.
    Поддерживает как .json (Minecraft 1.13+), так и .lang (Minecraft 1.12.2 и ниже) файлы.
    
    Args:
        jar_path: Путь к .jar файлу мода
        en_data: Данные из en_us.json внутри .jar файла
        en_us_path: Путь к en_us.json внутри архива
        
    Returns:
        Словарь с результатами проверки или None если TranslatedMods не настроен
    """
    result = {
        "found": False,
        "status": "not_found",  # full, partial, not_found
        "source": None,  # "translated_mods" или None
        "ru_keys": 0,
        "en_keys": len(en_data),
        "percentage": 0.0,
        "missing_keys": [],
        "extra_keys": [],
        "error": None
    }
    
    if TRANSLATED_MODS_PATH is None:
        return result
    
    # Извлекаем имя мода из assets внутри .jar файла
    mod_name = extract_mod_name_from_assets(jar_path)
    
    if mod_name is None:
        result["error"] = "Не удалось извлечь имя мода из assets"
        return result
    
    # Ищем ru_ru.json или ru_RU.lang в TranslatedMods используя имя мода из assets
    ru_path = find_ru_ru_in_translated_mods(mod_name)
    
    if ru_path is None:
        return result
    
    # Определяем тип файла и извлекаем данные соответствующим парсером
    if ru_path.suffix.lower() == '.json':
        ru_data = extract_json_from_file(ru_path)
    elif ru_path.suffix.lower() == '.lang':
        ru_data = parse_lang_file(ru_path)
    else:
        result["error"] = f"Неподдерживаемый формат файла: {ru_path}"
        return result
    
    if ru_data is None:
        result["error"] = f"Не удалось прочитать {ru_path}"
        return result
    
    result["found"] = True
    result["source"] = "translated_mods"
    result["ru_keys"] = len(ru_data)
    
    en_keys_set = set(en_data.keys())
    ru_keys_set = set(ru_data.keys())
    
    # Находим недостающие ключи
    missing_keys = en_keys_set - ru_keys_set
    extra_keys = ru_keys_set - en_keys_set
    
    result["missing_keys"] = sorted(list(missing_keys))
    result["extra_keys"] = sorted(list(extra_keys))
    
    # Вычисляем процент
    if result["en_keys"] == 0:
        result["percentage"] = 0.0
        result["status"] = "not_found"
    else:
        present_keys = en_keys_set & ru_keys_set
        result["percentage"] = round((len(present_keys) / result["en_keys"]) * 100, 2)
        
        if result["percentage"] == 100.0:
            result["status"] = "full"
        else:
            result["status"] = "partial"
    
    return result


def extract_json_from_jar(jar_path: Path, lang_path: str) -> Optional[Dict[str, str]]:
    """
    Извлекает JSON файл локализации из .jar архива.
    
    Args:
        jar_path: Путь к .jar файлу
        lang_path: Путь внутри архива (например, 'assets/modname/lang/en_us.json')
    
    Returns:
        Словарь с ключами локализации или None, если файл не найден/ошибка
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            # Ищем файл внутри архива (регистронезависимый поиск пути)
            actual_path = None
            for name in jar_file.namelist():
                if name.lower() == lang_path.lower():
                    actual_path = name
                    break
            
            if actual_path is None:
                return None
            
            with jar_file.open(actual_path) as f:
                # Используем utf-8-sig для корректной обработки BOM (Byte Order Mark)
                content = f.read().decode('utf-8-sig')
                data = json.loads(content)
                return data
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        print(f"⚠️  Ошибка чтения {lang_path} из {jar_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {jar_path}: {e}")
        return None


def _is_preferred_asset_lang_path(path: str) -> bool:
    """Возвращает True для пути вида assets/<modid>/lang/<file>."""
    normalized = path.replace('\\', '/').lower()
    parts = normalized.split('/')
    return len(parts) >= 4 and parts[0] == 'assets' and parts[2] == 'lang'


def _select_best_lang_path(candidates: List[str]) -> Optional[str]:
    """Выбирает наиболее подходящий путь среди кандидатов."""
    if not candidates:
        return None

    preferred = [path for path in candidates if _is_preferred_asset_lang_path(path)]
    if preferred:
        return min(preferred, key=lambda p: len(p.replace('\\', '/').split('/')))

    return min(candidates, key=lambda p: len(p.replace('\\', '/').split('/')))


def find_lang_files_in_jar(jar_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str], bool, bool]:
    """
    Ищет файлы en_us.json, ru_ru.json и .lang файлы внутри .jar файла.
    Проходит по всем файлам архива для обнаружения всех языковых файлов.
    
    Returns:
        Tuple[путь_к_en_us, путь_к_ru_ru, путь_к_en_lang, есть ли папка lang, есть ли .lang файлы] внутри архива
    """
    en_us_candidates: List[str] = []
    ru_ru_candidates: List[str] = []
    en_lang_candidates: List[str] = []  # Для старых модов с en_US.lang
    has_lang_dir = False
    has_lang_files = False
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                normalized = name.replace('\\', '/').lower()
                
                # Ранний выход: пропускаем файлы без /lang/ в пути
                if '/lang/' not in normalized:
                    continue
                
                has_lang_dir = True
                
                # Ищем файлы локализации JSON (Minecraft 1.13+)
                if normalized.endswith('/lang/en_us.json'):
                    en_us_candidates.append(name)
                elif normalized.endswith('/lang/ru_ru.json'):
                    ru_ru_candidates.append(name)
                # Ищем файлы локализации .lang (Minecraft 1.12.2 и ниже)
                elif normalized.endswith('/lang/en_us.lang'):
                    en_lang_candidates.append(name)
                    has_lang_files = True
                elif normalized.endswith('.lang'):
                    has_lang_files = True

                # Продолжаем поиск, даже если нашли один из файлов,
                # чтобы обнаружить все форматы
    except zipfile.BadZipFile:
        return (None, None, None, False, False)
    
    en_us_path = _select_best_lang_path(en_us_candidates)
    ru_ru_path = _select_best_lang_path(ru_ru_candidates)
    en_lang_path = _select_best_lang_path(en_lang_candidates)
    
    return (en_us_path, ru_ru_path, en_lang_path, has_lang_dir, has_lang_files)


def check_jar_localization(jar_path: Path) -> Dict[str, Any]:
    """
    Проверяет локализацию в одном .jar файле.
    Поддерживает как JSON (Minecraft 1.13+), так и .lang (Minecraft 1.12.2 и ниже) форматы.
    Сначала проверяет наличие перевода в папке TranslatedMods.
    Если не найден, проверяет встроенные файлы локализации в .jar.
    
    Returns:
        Словарь с результатами проверки
    """
    result = {
        "mod_name": jar_path.name,
        "status": "missing",  # full, partial, missing
        "source": "none",  # "jar", "translated_mods", "none"
        "ru_keys": 0,
        "en_keys": 0,
        "percentage": 0.0,
        "missing_keys": [],  # Ключи из en_us, которых нет в ru_ru
        "extra_keys": [],    # Ключи из ru_ru, которых нет в en_us (для обратной совместимости)
        "error": None
    }
    
    # Находим пути к файлам внутри архива
    en_us_path, ru_ru_path, en_lang_path, has_lang_dir, has_lang_files = find_lang_files_in_jar(jar_path)
    
    if not has_lang_dir:
        # Если в архиве нет папки lang, мод пропускаем и не включаем в отсутствующие
        result["status"] = "skipped"
        result["error"] = "Папка lang не найдена в архиве (мод пропущен)"
        return result
    
    # Определяем, какой файл использовать для английского текста
    # Приоритет: en_us.json > en_US.lang
    en_data = None
    en_source = None
    
    if en_us_path is not None:
        en_data = extract_json_from_jar(jar_path, en_us_path)
        en_source = en_us_path
    
    # Если en_us.json не найден или не прочитан, пробуем .lang файл
    if en_data is None and en_lang_path is not None:
        en_data = parse_lang_from_jar(jar_path, en_lang_path)
        en_source = en_lang_path
    
    if en_data is None:
        # Если нет файла en_us.json или en_US.lang, мод не учитывается
        result["status"] = "skipped"
        result["error"] = "Файл en_us.json/en_US.lang не найден в архиве (мод пропущен)"
        return result
    
    result["en_keys"] = len(en_data)
    
    if result["en_keys"] == 0:
        # Если файл есть, но не содержит ключей, мод не учитывается
        result["status"] = "skipped"
        result["error"] = "Файл en_us.json/en_US.lang пустой (мод пропущен)"
        return result
    
    # Сначала проверяем перевод в папке TranslatedMods (если доступна)
    # Это позволяет использовать актуальные переводы вместо устаревших из .jar
    if TRANSLATED_MODS_PATH is not None:
        translated_mods_result = check_translated_mods_localization(jar_path, en_data, en_us_path or en_lang_path)
        
        if translated_mods_result["found"]:
            result["source"] = "translated_mods"
            result["ru_keys"] = translated_mods_result["ru_keys"]
            result["percentage"] = translated_mods_result["percentage"]
            result["missing_keys"] = translated_mods_result["missing_keys"]
            result["extra_keys"] = translated_mods_result["extra_keys"]
            
            if translated_mods_result["status"] == "full":
                result["status"] = "full"
            elif translated_mods_result["status"] == "partial":
                result["status"] = "partial"
            else:
                result["status"] = "missing"
            
            return result
    
    # Если перевода нет в TranslatedMods, проверяем встроенные файлы в .jar
    # Сначала пробуем ru_ru.json
    if ru_ru_path is not None:
        ru_data = extract_json_from_jar(jar_path, ru_ru_path)
        
        if ru_data is not None:
            # Есть встроенный перевод JSON
            result["source"] = "jar"
            en_keys_set = set(en_data.keys())
            ru_keys_set = set(ru_data.keys())
            
            result["ru_keys"] = len(ru_keys_set)
            
            # Находим недостающие ключи
            missing_keys = en_keys_set - ru_keys_set
            extra_keys = ru_keys_set - en_keys_set
            
            result["missing_keys"] = sorted(list(missing_keys))
            result["extra_keys"] = sorted(list(extra_keys))
            
            # Вычисляем процент
            if result["en_keys"] == 0:
                result["percentage"] = 0.0
                result["status"] = "missing"
            else:
                present_keys = en_keys_set & ru_keys_set
                result["percentage"] = round((len(present_keys) / result["en_keys"]) * 100, 2)
                
                if result["percentage"] == 100.0:
                    result["status"] = "full"
                else:
                    result["status"] = "partial"
            
            return result
    
    # Если нет ru_ru.json, ищем ru_RU.lang внутри .jar
    ru_lang_path = None
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                normalized = name.replace('\\', '/').lower()
                if '/lang/' in normalized and normalized.endswith('.lang'):
                    # Ищем русские варианты: ru_ru.lang, ru_RU.lang, ru-ru.lang
                    if 'ru' in normalized:
                        ru_lang_path = name
                        break
    except zipfile.BadZipFile:
        pass
    
    if ru_lang_path is not None:
        ru_data = parse_lang_from_jar(jar_path, ru_lang_path)
        
        if ru_data is not None:
            # Есть встроенный перевод .lang
            result["source"] = "jar"
            en_keys_set = set(en_data.keys())
            ru_keys_set = set(ru_data.keys())
            
            result["ru_keys"] = len(ru_keys_set)
            
            # Находим недостающие ключи
            missing_keys = en_keys_set - ru_keys_set
            extra_keys = ru_keys_set - en_keys_set
            
            result["missing_keys"] = sorted(list(missing_keys))
            result["extra_keys"] = sorted(list(extra_keys))
            
            # Вычисляем процент
            if result["en_keys"] == 0:
                result["percentage"] = 0.0
                result["status"] = "missing"
            else:
                present_keys = en_keys_set & ru_keys_set
                result["percentage"] = round((len(present_keys) / result["en_keys"]) * 100, 2)
                
                if result["percentage"] == 100.0:
                    result["status"] = "full"
                else:
                    result["status"] = "partial"
            
            return result
    
    # Перевода нет ни в .jar, ни в TranslatedMods
    result["status"] = "missing"
    result["error"] = "Нет ru_ru.json/ru_RU.lang"
    return result


def scan_jars_directory(base_path: Path, progress_callback=None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Сканирует директорию с .jar файлами модов и проверяет локализацию.
    
    Args:
        base_path: Директория для сканирования
        progress_callback: Функция обратного вызова для обновления прогресса (current, total)
    
    Returns:
        Словарь с результатами по категориям
    """
    results = {
        "full": [],
        "partial": [],
        "missing": []
    }
    
    # Находим все .jar файлы в директории (не рекурсивно)
    jar_files = list(base_path.glob("*.jar"))
    
    if not jar_files:
        return results
    
    total = len(jar_files)
    
    # Используем многопоточность для ускорения обработки
    processed_count = 0
    max_workers = CONFIG.get("max_workers", 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_jar = {executor.submit(check_jar_localization, jar): jar for jar in jar_files}
        
        for i, future in enumerate(as_completed(future_to_jar)):
            jar_result = future.result()
            
            # Пропускаем моды без файлов локализации (статус skipped)
            if jar_result["status"] == "skipped":
                if progress_callback:
                    progress_callback(i + 1, total)
                continue
            
            if jar_result["status"] == "full":
                results["full"].append(jar_result)
            elif jar_result["status"] == "partial":
                results["partial"].append(jar_result)
            elif jar_result["status"] == "missing":
                results["missing"].append(jar_result)
            
            processed_count += 1
            if progress_callback:
                progress_callback(i + 1, total)
    
    # Сортируем результаты по имени мода
    for category in results:
        results[category].sort(key=lambda x: x["mod_name"])
    
    return results


class LocalizationCheckerGUI:
    """Графический интерфейс для проверки локализации."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Localization Checker")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        self.current_path = None
        self.results = None
        self.status_message = ""
        self.status_color = "gray"
        self.dark_mode = False
        
        # Отслеживание сортировки для каждой таблицы
        self.sort_state = {
            "full": {"column": None, "reverse": False},
            "partial": {"column": None, "reverse": False},
            "missing": {"column": None, "reverse": False}
        }
        
        self.setup_ui()
    
    def setup_ui(self):
        """Настройка пользовательского интерфейса."""
        # Верхняя панель с кнопками
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.select_btn = ttk.Button(self.top_frame, text="📁 Выбрать папку с модами", command=self.select_directory, style="Custom.TButton")
        self.select_btn.pack(side=tk.LEFT, padx=5)
        
        self.check_btn = ttk.Button(self.top_frame, text="▶️ Проверить", command=self.start_check, state=tk.DISABLED, style="Custom.TButton")
        self.check_btn.pack(side=tk.LEFT, padx=5)
        
        self.export_btn = ttk.Button(self.top_frame, text="💾 Экспорт в JSON", command=self.export_results, state=tk.DISABLED, style="Custom.TButton")
        self.export_btn.pack(side=tk.LEFT, padx=5)
        
        # Кнопка переключения темы
        self.theme_btn = ttk.Button(self.top_frame, text="🌙 Тёмная тема", command=self.toggle_theme, style="Custom.TButton")
        self.theme_btn.pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(self.top_frame, text="❌ Закрыть", command=self.root.quit, style="Custom.TButton").pack(side=tk.RIGHT, padx=5)
        
        # Стиль для виджетов ttk — будем менять для тёмной темы
        self.style = ttk.Style(self.root)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.style.configure("Custom.Treeview", background="white", fieldbackground="white", foreground="black", borderwidth=0)
        self.style.configure("Custom.Treeview.Heading", background="#e0e0e0", foreground="black", borderwidth=0)
        self.style.map("Custom.Treeview.Heading",
            background=[('active', '#d0d0d0'), ('!active', '#e0e0e0')],
            foreground=[('active', 'black'), ('!active', 'black')]
        )
        self.style.configure("Custom.TButton", background="#e0e0e0", foreground="black", padding=5)
        self.style.map("Custom.TButton",
            background=[('active', '#d0d0d0'), ('!disabled', '#e0e0e0')],
            foreground=[('active', 'black'), ('!disabled', 'black')]
        )
        self.style.configure("Custom.Horizontal.TProgressbar", troughcolor="#e8e8e8", background="#28a028", bordercolor="#e8e8e8", lightcolor="#4cbf4c", darkcolor="#1c7c1c")
        # Notebook/tab: выровнять фон вкладок и области клиента, убрать поля, чтобы не было "серой полосы"
        self.style.configure("Custom.TNotebook", background="#f0f0f0", borderwidth=0, tabmargins=[2, 5, 2, 0])
        self.style.configure("Custom.TNotebook.Tab", background="#f0f0f0", foreground="black", padding=10, borderwidth=0)
        self.style.map("Custom.TNotebook.Tab",
            background=[('selected', '#f0f0f0'), ('!selected', '#f0f0f0')],
            foreground=[('selected', 'black'), ('!selected', '#999999')],
            borderwidth=[('selected', 0), ('!selected', 0)]
        )
        self.style.configure("Custom.TEntry", fieldbackground="white", foreground="black", background="white")
        self.style.configure("Custom.TCombobox", fieldbackground="white", foreground="black", background="#e0e0e0")

        # Панель прогресса - используем tk.Frame для поддержки смены цветов фона
        self.progress_frame = tk.Frame(self.root)
        self.progress_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.progress_label = tk.Label(self.progress_frame, text="Готов к работе")
        self.progress_label.pack(side=tk.LEFT, padx=5)
        
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='determinate', length=400, style='Custom.Horizontal.TProgressbar')
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # Панель поиска и фильтрации (используем tk.Frame чтобы можно было менять bg)
        self.search_frame = tk.Frame(self.root)
        self.search_frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(self.search_frame, text="🔍 Поиск по имени:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.on_search_change)
        search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var, width=30, style="Custom.TEntry")
        search_entry.pack(side=tk.LEFT, padx=5)
        
        # Основная область с результатами
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Вкладки для категорий
        self.notebook = ttk.Notebook(self.main_frame, style="Custom.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка "Полный перевод"
        self.full_frame = tk.Frame(self.notebook)
        self.notebook.add(self.full_frame, text="[100%] Полный")
        self.full_tree = self.create_treeview(self.full_frame, ["Мод", "Ключи RU", "Ключи EN", "%"])
        
        # Вкладка "Неполный перевод"
        self.partial_frame = tk.Frame(self.notebook)
        self.notebook.add(self.partial_frame, text="[Частично] Неполный")
        self.partial_tree = self.create_treeview(self.partial_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Не хватает"])
        
        # Вкладка "Отсутствует"
        self.missing_frame = tk.Frame(self.notebook)
        self.notebook.add(self.missing_frame, text="[Нет] Отсутствует")
        self.missing_tree = self.create_treeview(self.missing_frame, ["Мод", "Ключи EN", "Причина"])

        # Глобальная привязка Ctrl+C, чтобы копирование работало независимо от фокуса и раскладки
        self.root.bind_all("<Control-KeyPress>", self.on_copy_shortcut)
        
        # Статус бар - используем tk.Frame для поддержки смены цветов фона
        self.status_frame = tk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Начальный цвет статуса серый — но при смене темы будем учитывать self.status_color
        self.status_label = tk.Label(self.status_frame, text="Выберите папку с .jar файлами модов", fg="gray")
        self.status_label.pack(side=tk.LEFT)
    
    def create_treeview(self, parent, columns):
        """Создает Treeview для отображения результатов."""
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse", style="Custom.Treeview")
        
        for col in columns:
            tree.heading(col, text=col, command=lambda c=col, t=tree: self.on_column_click(c, t))
            tree.column(col, width=100 if col != "Мод" else 300)
        
        # Добавляем полосу прокрутки
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.setup_tree_tags(tree)
        tree.bind("<Double-1>", lambda e: self.show_details(tree))

        return tree
    
    def get_tree_category(self, tree):
        """Определяет категорию дерева по объекту."""
        if tree == self.full_tree:
            return "full"
        elif tree == self.partial_tree:
            return "partial"
        elif tree == self.missing_tree:
            return "missing"
        return None

    def get_source_tag(self, source: str) -> str:
        """Возвращает тег строки для указанного источника перевода."""
        if source == "jar":
            return "source_jar"
        if source == "translated_mods":
            return "source_translated_mods"
        return "source_missing"

    def set_status_message(self, message: str, color: str = "black", persist: bool = True):
        """Обновляет текст статуса и сохраняет его, если это нужно."""
        self.status_label.config(text=message, foreground=color)
        if persist:
            self.status_message = message
            self.status_color = color

    def show_temporary_status(self, message: str, color: str = "green", timeout: int = 2000):
        """Показывает временное сообщение в статусной строке."""
        self.status_label.config(text=message, foreground=color)
        self.root.after(timeout, lambda: self.set_status_message(self.status_message, self.status_color, True))

    def on_copy_shortcut(self, event, tree):
        """Обрабатывает Ctrl+C на разных раскладках клавиатуры."""
        key = (event.keysym or "").lower()
        char = (event.char or "").lower()
        if key in ("c", "с", "cyrillic_es") or char in ("c", "с"):
            self.copy_selected_mod_name(tree)
            return "break"
        return None

    def toggle_theme(self):
        """Переключает между светлой и тёмной темой."""
        self.dark_mode = not self.dark_mode
        
        if self.dark_mode:
            # Тёмная тема
            dark_bg = "#1e1e1e"
            dark_fg = "white"

            self.root.config(bg=dark_bg)
            self.theme_btn.config(text="☀️ Светлая тема")

            # Обновляем цвета фреймов (tk.Frame поддерживает bg)
            self.progress_frame.config(bg=dark_bg)
            self.status_frame.config(bg=dark_bg)
            # Верхние фреймы и область результатов
            try:
                self.top_frame.config(bg=dark_bg)
            except Exception:
                pass
            try:
                self.search_frame.config(bg=dark_bg)
            except Exception:
                pass
            try:
                self.main_frame.config(bg=dark_bg)
            except Exception:
                pass
            try:
                self.full_frame.config(bg=dark_bg)
                self.partial_frame.config(bg=dark_bg)
                self.missing_frame.config(bg=dark_bg)
            except Exception:
                pass

            # Обновляем цвета лейблов
            self.progress_label.config(bg=dark_bg, fg=dark_fg)
            # Сохраняем и применяем текущий цвет статуса, если он есть
            status_fg = self.status_color if getattr(self, 'status_color', None) else dark_fg
            self.status_label.config(bg=dark_bg, fg=status_fg)

            # Настраиваем стиль ttk виджетов для тёмной темы
            try:
                self.style.configure("Custom.Treeview", background="#121212", fieldbackground="#121212", foreground=dark_fg, borderwidth=0)
                self.style.configure("Custom.Treeview.Heading", background="#121212", foreground=dark_fg, borderwidth=0)
                self.style.map("Custom.Treeview.Heading",
                    background=[('active', '#1a1a1a'), ('!active', '#121212')],
                    foreground=[('active', dark_fg), ('!active', dark_fg)]
                )
                # Notebook/tab: убрать разделительный светлый фон под вкладками
                self.style.configure("Custom.TNotebook", background=dark_bg, tabmargins=[2, 5, 2, 0])
                self.style.configure("Custom.TNotebook.Tab", background=dark_bg, foreground=dark_fg, padding=10, borderwidth=0)
                self.style.map("Custom.TNotebook.Tab",
                    background=[('selected', dark_bg), ('!selected', dark_bg)],
                    foreground=[('selected', dark_fg), ('!selected', dark_fg)],
                    borderwidth=[('selected', 0), ('!selected', 0)]
                )
                self.style.configure("Custom.TButton", background="#2f2f2f", foreground=dark_fg, padding=5)
                self.style.map("Custom.TButton",
                    background=[('active', '#393939'), ('!disabled', '#2f2f2f')],
                    foreground=[('active', dark_fg), ('!disabled', dark_fg)]
                )
                self.style.configure("Custom.TEntry", fieldbackground="#1e1e1e", foreground=dark_fg, background="#1e1e1e")
                self.style.configure("Custom.TCombobox", fieldbackground="#1e1e1e", foreground=dark_fg, background="#2f2f2f")
                self.style.configure("Custom.Horizontal.TProgressbar", troughcolor="#2b2b2b", background="#28a028", bordercolor="#2b2b2b", lightcolor="#4cbf4c", darkcolor="#1c7c1c")
            except Exception:
                pass
            
        else:
            # Светлая тема
            default_bg = "#f0f0f0"
            default_fg = "black"

            self.root.config(bg=default_bg)
            self.theme_btn.config(text="🌙 Тёмная тема")

            # Восстанавливаем стандартные цвета фреймов
            self.progress_frame.config(bg=default_bg)
            self.status_frame.config(bg=default_bg)
            # Верхние фреймы и область результатов
            try:
                self.top_frame.config(bg=default_bg)
            except Exception:
                pass
            try:
                self.search_frame.config(bg=default_bg)
            except Exception:
                pass
            try:
                self.main_frame.config(bg=default_bg)
            except Exception:
                pass
            try:
                self.full_frame.config(bg=default_bg)
                self.partial_frame.config(bg=default_bg)
                self.missing_frame.config(bg=default_bg)
            except Exception:
                pass

            # Восстанавливаем цвета лейблов
            self.progress_label.config(bg=default_bg, fg=default_fg)
            # В light режиме показываем статус тем цветом, который хранится в status_color (по умолчанию черный/green)
            status_fg = self.status_color if getattr(self, 'status_color', None) else default_fg
            self.status_label.config(bg=default_bg, fg=status_fg)

            # Сброс стилей ttk к светлым значениям
            try:
                self.style.configure("Custom.Treeview", background="white", fieldbackground="white", foreground="black", borderwidth=0)
                self.style.configure("Custom.Treeview.Heading", background="#e0e0e0", foreground="#121212", borderwidth=0)
                self.style.map("Custom.Treeview.Heading",
                    background=[('active', '#d0d0d0'), ('!active', '#e0e0e0')],
                    foreground=[('active', 'black'), ('!active', 'black')]
                )
                # Notebook/tab: выровнять фон вкладок и области клиента в светлой теме
                self.style.configure("Custom.TNotebook", background=default_bg, tabmargins=[2, 5, 2, 0])
                self.style.configure("Custom.TNotebook.Tab", background=default_bg, foreground="black", padding=10, borderwidth=0)
                self.style.map("Custom.TNotebook.Tab",
                    background=[('selected', default_bg), ('!selected', default_bg)],
                    foreground=[('selected', 'black'), ('!selected', 'black')],
                    borderwidth=[('selected', 0), ('!selected', 0)]
                )
                self.style.configure("Custom.TButton", background="#e0e0e0", foreground="#121212", padding=5)
                self.style.map("Custom.TButton",
                    background=[('active', '#d0d0d0'), ('!disabled', '#e0e0e0')],
                    foreground=[('active', 'black'), ('!disabled', 'black')]
                )
                self.style.configure("Custom.TEntry", fieldbackground="white", foreground="black", background="white")
                self.style.configure("Custom.TCombobox", fieldbackground="#ffffff", foreground="#121212", background="#e0e0e0")
                self.style.configure("Custom.Horizontal.TProgressbar", troughcolor="#e8e8e8", background="#28a028", bordercolor="#e8e8e8", lightcolor="#4cbf4c", darkcolor="#1c7c1c")
            except Exception:
                pass
        
        # Пересоздаём теги для всех деревьев при смене темы
        if self.results:
            self.setup_tree_tags(self.full_tree)
            self.setup_tree_tags(self.partial_tree)
            self.setup_tree_tags(self.missing_tree)
            # Пересоздаём результаты чтобы применить новые теги
            self.apply_filter()

    def setup_tree_tags(self, tree):
        """Создает теги для раскрашивания строк по источнику перевода."""
        colors = CONFIG.get("row_colors", {})
        
        # Использование светлых цветов (одинаковые для обеих тем)
        jar_bg = colors.get("jar", "#d4f4dd")
        translated_bg = colors.get("translated_mods", "#d1e7ff")
        missing_bg = colors.get("missing", "#ffe4e1")
        
        # Фон поля дерева: тёмный в тёмной теме, белый в светлой
        field_bg = "#1e1e1e" if self.dark_mode else "white"

        # Применяем фон для области Treeview (чтобы убрать серые полосы в тёмной теме)
        try:
            tree.configure(background=field_bg, fieldbackground=field_bg)
        except Exception:
            pass

        # Теги для строк всегда имеют чёрный текст (так как сами фоновые теги светлые)
        tree.tag_configure("source_jar", background=jar_bg, foreground="black")
        tree.tag_configure("source_translated_mods", background=translated_bg, foreground="black")
        tree.tag_configure("source_missing", background=missing_bg, foreground="black")

        # Заголовки колонок должны соответствовать текущей теме
        heading_bg = "#121212" if self.dark_mode else "#e0e0e0"
        heading_fg = "white" if self.dark_mode else "black"
        try:
            self.style.configure("Treeview.Heading", background=heading_bg, foreground=heading_fg)
        except Exception:
            pass

    def copy_selected_mod_name(self, tree):
        """Копирует название мода из выделенной строки в буфер обмена."""
        selection = tree.selection()
        if not selection:
            return "break"

        item = tree.item(selection[0])
        values = item.get("values", [])
        if not values:
            return "break"

        mod_name = str(values[0])
        self.root.clipboard_clear()
        self.root.clipboard_append(mod_name)
        self.show_temporary_status(f"Скопировано: {mod_name}")
        return "break"

    def get_tree_with_selection(self):
        """Возвращает первое дерево, в котором есть текущий выбор."""
        for tree in (self.full_tree, self.partial_tree, self.missing_tree):
            if tree.selection():
                return tree
        return None

    def is_copy_shortcut(self, event):
        """Проверяет, что нажат Ctrl+C на любой раскладке клавиатуры."""
        key = (event.keysym or "").lower()
        char = (event.char or "").lower()
        keysym_num = getattr(event, "keysym_num", None)
        keycode = getattr(event, "keycode", None)

        if key in ("c", "с", "cyrillic_es"):
            return True
        if char in ("c", "с"):
            return True
        if keysym_num in (ord("c"), ord("C"), ord("с"), ord("С"), 0x0441, 0x0421):
            return True
        if keycode in (54, 67, 99):
            return True
        return False

    def on_copy_shortcut(self, event, tree=None):
        """Обрабатывает Ctrl+C на разных раскладках клавиатуры."""
        if not (event.state & 0x4):
            return None

        if self.is_copy_shortcut(event):
            target_tree = tree if tree is not None else self.get_tree_with_selection()
            if target_tree:
                self.copy_selected_mod_name(target_tree)
                return "break"
        return None
    
    def on_column_click(self, column, tree):
        """Обработчик клика на заголовок колонки для сортировки."""
        category = self.get_tree_category(tree)
        if category is None or not self.results:
            return
        
        # Определяем, нужно ли переворачивать порядок
        if self.sort_state[category]["column"] == column:
            # Если кликнули на ту же колонку, переворачиваем порядок
            self.sort_state[category]["reverse"] = not self.sort_state[category]["reverse"]
        else:
            # Если кликнули на новую колонку, выбираем подходящее направление сортировки
            self.sort_state[category]["column"] = column
            # Числовые колонки сортируются по убыванию (большие значения первыми)
            numeric_columns = ["%", "Ключи RU", "Ключи EN", "Не хватает"]
            self.sort_state[category]["reverse"] = column in numeric_columns
        
        # Пересортируем и отобразим результаты
        self.apply_filter()
    
    def sort_results(self, mods_list, column_name, reverse=False):
        """Сортирует список модов по указанной колонке."""
        if not mods_list:
            return mods_list
        
        # Функции для определения значения сортировки
        if column_name == "Мод":
            # Сортировка по имени мода
            return sorted(mods_list, key=lambda x: x["mod_name"].lower(), reverse=reverse)
        
        elif column_name == "Ключи RU":
            # Сортировка по количеству RU ключей
            return sorted(mods_list, key=lambda x: x["ru_keys"], reverse=reverse)
        
        elif column_name == "Ключи EN":
            # Сортировка по количеству EN ключей
            return sorted(mods_list, key=lambda x: x["en_keys"], reverse=reverse)
        
        elif column_name == "%":
            # Сортировка по проценту перевода
            return sorted(mods_list, key=lambda x: x["percentage"], reverse=reverse)
        
        elif column_name == "Не хватает":
            # Сортировка по количеству недостающих ключей
            return sorted(mods_list, key=lambda x: len(x.get("missing_keys", [])), reverse=reverse)
        
        elif column_name == "Причина":
            # Сортировка по причине (для вкладки "Отсутствует")
            return sorted(mods_list, key=lambda x: x.get("error", ""), reverse=reverse)
        
        return mods_list
    
    def select_directory(self):
        """Открывает диалог выбора директории."""
        directory = filedialog.askdirectory(title="Выберите папку с .jar файлами модов")
        if directory:
            self.current_path = Path(directory)
            self.select_btn.config(text=f"📁 {self.current_path.name}")
            self.check_btn.config(state=tk.NORMAL)
            self.set_status_message(f"Папка выбрана: {self.current_path}", color="black")
            
            # Автоматически ищем папку TranslatedMods
            translated_mods_path = find_translated_mods_directory(self.current_path)
            if translated_mods_path:
                set_translated_mods_path(translated_mods_path)
                self.set_status_message(
                    f"Папка выбрана: {self.current_path} | TranslatedMods найден: {translated_mods_path}", 
                    color="green"
                )
            else:
                self.set_status_message(
                    f"Папка выбрана: {self.current_path} | TranslatedMods не найден", 
                    color="green"
                )
    
    def update_progress(self, current, total):
        """Обновляет индикатор прогресса."""
        progress = (current / total) * 100
        self.progress_bar['value'] = progress
        self.progress_label.config(text=f"Обработано: {current}/{total}")
        self.root.update_idletasks()
    
    def start_check(self):
        """Запускает проверку локализации."""
        if not self.current_path:
            messagebox.showwarning("Предупреждение", "Сначала выберите папку с модами")
            return
        
        self.check_btn.config(state=tk.DISABLED)
        self.select_btn.config(state=tk.DISABLED)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Начало проверки...")
        
        # Очищаем предыдущие результаты
        for tree in [self.full_tree, self.partial_tree, self.missing_tree]:
            for item in tree.get_children():
                tree.delete(item)
        
        # Запускаем проверку в отдельном потоке
        def check_thread():
            try:
                self.results = scan_jars_directory(self.current_path, self.update_progress)
                
                # Обновляем GUI в главном потоке
                self.root.after(0, self.display_results)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Ошибка", f"Произошла ошибка: {e}"))
            finally:
                self.root.after(0, self.check_complete)
        
        thread = threading.Thread(target=check_thread, daemon=True)
        thread.start()
    
    def display_results(self):
        """Отображает результаты проверки."""
        if not self.results:
            return
        
        # Применяем фильтр для отображения результатов
        self.apply_filter()
        
        # Обновляем статус
        total = len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"])
        self.set_status_message(
            f"Всего: {total} | [100%]: {len(self.results['full'])} | [Частично]: {len(self.results['partial'])} | [Нет]: {len(self.results['missing'])}",
            color="green"
        )
        
        # Показываем сообщение о завершении
        self.show_info_dialog(
            "Готово",
            f"Проверено {total} модов (только с файлами локализации).\nРезультаты отображены во вкладках.\n\n💡 Подсказка: Нажимайте на заголовки столбцов для сортировки!"
        )
    
    def show_info_dialog(self, title: str, message: str):
        """Показывает информационный диалог с поддержкой тёмной темы."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        bg_color = "#121212" if self.dark_mode else "#ffffff"
        fg_color = "white" if self.dark_mode else "black"
        btn_bg = "#2f2f2f" if self.dark_mode else "#e0e0e0"
        btn_fg = "white" if self.dark_mode else "black"

        try:
            dialog.config(bg=bg_color)
        except Exception:
            pass

        label = tk.Label(
            dialog,
            text=message,
            bg=bg_color,
            fg=fg_color,
            justify=tk.LEFT,
            wraplength=560,
            padx=20,
            pady=20
        )
        label.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(dialog, bg=bg_color)
        btn_frame.pack(fill=tk.X, pady=(0, 15))

        ok_btn = tk.Button(
            btn_frame,
            text="OK",
            command=dialog.destroy,
            bg=btn_bg,
            fg=btn_fg,
            activebackground=btn_bg,
            activeforeground=btn_fg,
            relief=tk.FLAT,
            padx=15,
            pady=5
        )
        ok_btn.pack(side=tk.RIGHT, padx=20)

        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        dialog.wait_window()

    def check_complete(self):
        """Завершение проверки."""
        self.check_btn.config(state=tk.NORMAL)
        self.select_btn.config(state=tk.NORMAL)
        self.export_btn.config(state=tk.NORMAL)
        self.progress_label.config(text="Проверка завершена")
    
    def on_search_change(self, *args):
        """Обработчик изменения текста поиска."""
        self.apply_filter()
    
    def apply_filter(self):
        """Применяет фильтр поиска и сортировку к таблицам."""
        search_text = self.search_var.get().lower()
        
        # Очищаем все таблицы
        for item in self.full_tree.get_children():
            self.full_tree.delete(item)
        for item in self.partial_tree.get_children():
            self.partial_tree.delete(item)
        for item in self.missing_tree.get_children():
            self.missing_tree.delete(item)
        
        if not self.results:
            return
        
        # Показываем результаты - Полный перевод
        full_filtered = [mod for mod in self.results["full"] 
                       if search_text in mod["mod_name"].lower()]
        # Применяем сортировку
        sort_col = self.sort_state["full"]["column"] or "Мод"
        full_sorted = self.sort_results(full_filtered, sort_col, self.sort_state["full"]["reverse"])
        
        for mod in full_sorted:
            self.full_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%"
            ), tags=(self.get_source_tag(mod.get("source", "none")),))
        
        # Показываем результаты - Неполный перевод
        partial_filtered = [mod for mod in self.results["partial"]
                          if search_text in mod["mod_name"].lower()]
        # Применяем сортировку
        sort_col = self.sort_state["partial"]["column"] or "Мод"
        partial_sorted = self.sort_results(partial_filtered, sort_col, self.sort_state["partial"]["reverse"])
        
        for mod in partial_sorted:
            missing_count = len(mod["missing_keys"])
            self.partial_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                f"{missing_count} ключей"
            ), tags=(self.get_source_tag(mod.get("source", "none")),))
        
        # Показываем результаты - Отсутствует
        missing_filtered = [mod for mod in self.results["missing"]
                          if search_text in mod["mod_name"].lower()]
        # Применяем сортировку
        sort_col = self.sort_state["missing"]["column"] or "Мод"
        missing_sorted = self.sort_results(missing_filtered, sort_col, self.sort_state["missing"]["reverse"])
        
        for mod in missing_sorted:
            reason = mod.get("error", "Нет ru_ru.json")
            self.missing_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["en_keys"],
                reason
            ), tags=(self.get_source_tag(mod.get("source", "none")),))
    
    def show_details(self, tree):
        """Показывает детали выбранного мода."""
        selection = tree.selection()
        if not selection:
            return
        
        item = tree.item(selection[0])
        mod_name = item["values"][0]
        
        # Находим полную информацию о моде
        mod_info = None
        for category in ["full", "partial", "missing"]:
            for mod in self.results[category]:
                if mod["mod_name"] == mod_name:
                    mod_info = mod
                    break
            if mod_info:
                break
        
        if not mod_info:
            return
        
        # Создаем окно с деталями
        detail_window = tk.Toplevel(self.root)
        detail_window.title(f"Детали: {mod_name}")
        detail_window.geometry("600x400")
        
        text_widget = tk.Text(detail_window, wrap=tk.WORD, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Применим тему к окну деталей
        if self.dark_mode:
            dark_bg = "#1e1e1e"
            dark_fg = "white"
            try:
                detail_window.config(bg=dark_bg)
                text_widget.config(bg=dark_bg, fg=dark_fg, insertbackground=dark_fg)
            except Exception:
                pass
        else:
            try:
                detail_window.config(bg="#ffffff")
                text_widget.config(bg="white", fg="black", insertbackground="black")
            except Exception:
                pass
        
        details = f"Мод: {mod_info['mod_name']}\n"
        details += f"Статус: {mod_info['status']}\n"
        details += f"Источник: {mod_info.get('source', 'none')}\n"
        details += f"Ключей в RU: {mod_info['ru_keys']}\n"
        details += f"Ключей в EN: {mod_info['en_keys']}\n"
        details += f"Процент: {mod_info['percentage']}%\n\n"
        
        if mod_info.get("missing_keys"):
            details += f"❌ Недостающие ключи ({len(mod_info['missing_keys'])}):\n"
            for key in mod_info["missing_keys"][:20]:  # Показываем первые 20
                details += f"   - {key}\n"
            if len(mod_info["missing_keys"]) > 20:
                details += f"   ... и еще {len(mod_info['missing_keys']) - 20}\n"
        
        if mod_info.get("extra_keys"):
            details += f"\n✅ Лишние ключи (сохранены для совместимости) ({len(mod_info['extra_keys'])}):\n"
            for key in mod_info["extra_keys"][:20]:  # Показываем первые 20
                details += f"   - {key}\n"
            if len(mod_info["extra_keys"]) > 20:
                details += f"   ... и еще {len(mod_info['extra_keys']) - 20}\n"
        
        if mod_info.get("error"):
            details += f"\n⚠️ Ошибка: {mod_info['error']}\n"
        
        text_widget.insert(tk.END, details)
        text_widget.config(state=tk.DISABLED)

        # Разрешаем копирование выделенного текста через Ctrl+C в окне деталей
        text_widget.bind("<Control-KeyPress>", lambda e, w=text_widget: self.on_copy_text_shortcut(e, w))

    def on_copy_text_shortcut(self, event, text_widget):
        """Обрабатывает Ctrl+C в окне деталей."""
        if not (event.state & 0x4):
            return None

        if self.is_copy_shortcut(event):
            try:
                selection = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                return None

            if selection:
                self.root.clipboard_clear()
                self.root.clipboard_append(selection)
                self.show_temporary_status("Скопировано")
                return "break"
        return None

    def export_results(self):
        """Экспортирует результаты в JSON файл."""
        if not self.results:
            messagebox.showwarning("Предупреждение", "Сначала выполните проверку")
            return
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="localization_results.json",
            title="Сохранить результаты"
        )
        
        if not file_path:
            return
        
        output_data = {
            "scan_directory": str(self.current_path),
            "total_mods": len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"]),
            "summary": {
                "full_translation": len(self.results["full"]),
                "partial_translation": len(self.results["partial"]),
                "missing_translation": len(self.results["missing"])
            },
            "mods": self.results
        }
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Успех", f"Результаты сохранены в:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{e}")


def main_gui():
    """Запускает графический интерфейс."""
    load_config()  # Загружаем конфигурацию при запуске
    root = tk.Tk()
    app = LocalizationCheckerGUI(root)
    root.mainloop()


def main_cli():
    """Запускает консольную версию."""
    load_config()  # Загружаем конфигурацию при запуске
    parser = argparse.ArgumentParser(
        description="Проверка русской локализации в .jar файлах модов Minecraft",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s                          # Проверить текущую директорию
  %(prog)s .                        # Проверить текущую директорию
  %(prog)s C:\\Games\\Minecraft\\mods  # Проверить конкретную папку
  %(prog)s --gui                    # Запустить графический интерфейс
        """
    )
    
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Директория для сканирования (по умолчанию текущая папка)"
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="localization_results.json",
        help="Имя выходного JSON файла (по умолчанию: localization_results.json)"
    )
    
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить графический интерфейс"
    )
    
    parser.add_argument(
        "--translated_mods",
        "--rtfe",
        dest="translated_mods",
        default=None,
        help="Путь к папке TranslatedMods с переводами (необязательно)"
    )
    
    args = parser.parse_args()
    
    if args.gui:
        main_gui()
        return 0
    
    base_path = Path(args.directory).resolve()
    
    if not base_path.exists():
        print(f"❌ Ошибка: Директория '{base_path}' не существует")
        return 1
    
    # Ищем и устанавливаем путь к TranslatedMods
    translated_mods_path = None
    if args.translated_mods:
        translated_mods_path = Path(args.translated_mods)
        if not translated_mods_path.exists():
            print(f"⚠️  Предупреждение: Указанная папка TranslatedMods не существует: {translated_mods_path}")
            translated_mods_path = None
    else:
        translated_mods_path = find_translated_mods_directory(base_path)
    
    if translated_mods_path:
        set_translated_mods_path(translated_mods_path)
        print(f"📦 TranslatedMods найден: {translated_mods_path}")
    else:
        print("📦 TranslatedMods не найден (проверка только встроенных переводов)")
    
    print(f"🔍 Сканирование директории: {base_path}")
    print("-" * 60)
    
    results = scan_jars_directory(base_path)
    
    # Считаем только моды, которые действительно проверены (не пропущены)
    total_mods = len(results["full"]) + len(results["partial"]) + len(results["missing"])
    
    if total_mods == 0:
        print("⚠️  .jar файлы с файлами локализации не найдены или все пропущены!")
        return 1
    
    # Вывод статистики
    print("\n" + "=" * 60)
    print("СТАТИСТИКА (только моды с файлами локализации):")
    print(f"   Всего модов проверено: {total_mods}")
    print(f"   [100%] С полным переводом: {len(results['full'])}")
    print(f"   [Частично] С неполным переводом: {len(results['partial'])}")
    print(f"   [Нет] Без русского языка: {len(results['missing'])}")
    print("=" * 60)
    
    # Сохранение результатов в JSON
    output_file = Path(args.output)
    output_data = {
        "scan_directory": str(base_path),
        "total_mods": total_mods,
        "summary": {
            "full_translation": len(results["full"]),
            "partial_translation": len(results["partial"]),
            "missing_translation": len(results["missing"])
        },
        "mods": results
    }
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Результаты сохранены в файл: {output_file}")
    except Exception as e:
        print(f"\n❌ Ошибка сохранения файла: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    import sys
    # Если аргументов нет или есть --gui, запускаем GUI
    if len(sys.argv) == 1 or "--gui" in sys.argv:
        main_gui()
    else:
        sys.exit(main_cli())
