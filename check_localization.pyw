#!/usr/bin/env python3
"""
Программа для проверки русской локализации в модах Minecraft.
Работает напрямую с .jar и .zip файлами, проверяет наличие ru_ru.json и .lang файлов,
сравнивает ключи с en_us.json или en_US.lang.
Также поддерживает проверку переводов в папке TranslatedMods.

Поддерживаемые форматы:
- JSON (Minecraft 1.13+): en_us.json, ru_ru.json
- .lang (Minecraft 1.12.2 и ниже): en_US.lang, ru_RU.lang

Поддерживаемые типы архивов:
- .jar файлы (стандартные моды)
- .zip файлы (моды в архивах)

Поддерживаемые пути локализации:
- assets/<modname>/lang/ (стандартный путь для новых модов)
- assets/<modname>/language/ (путь для старых модов)
- assets/<modname>/lang_nei/ (путь для старых модов)

Категории:
- Полный перевод: 100% совпадение ключей с английским файлом (все ключи из en есть в ru)
- Неполный перевод: есть русский файл, но не все ключи из английского присутствуют
- Отсутствует: есть английский файл, но нет русского

Важно: Моды без файла en_us.json/en_US.lang не учитываются при проверке.

Лишние ключи в русском файле (которых нет в английском) сохраняются в отчете,
так как могут использоваться для обратной совместимости.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import argparse
import threading
import platform
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

# GUI импортируется только при запуске в режиме GUI
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────────
# Логирование: пишем и в файл (для разбора проблем), и в консоль (как раньше)
# ──────────────────────────────────────────────────────────────────────────

LOG_FILE_NAME = "localization_checker.log"

logger = logging.getLogger("localization_checker")
logger.setLevel(logging.DEBUG)
logger.propagate = False

if not logger.handlers:
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setLevel(logging.WARNING)
    _console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_console_handler)

    try:
        _file_handler = logging.FileHandler(LOG_FILE_NAME, encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(_file_handler)
    except Exception:
        # Если файл лога недоступен (например, папка только для чтения) —
        # продолжаем работу только с консольным выводом.
        pass

# Собранные во время работы ошибки/предупреждения — используется GUI (вкладка "Ошибки").
# Доступ из нескольких потоков (сканирование идёт в ThreadPoolExecutor), поэтому под замком.
ERROR_LOG: List[Dict[str, str]] = []
_error_log_lock = threading.Lock()


def log_warning(context: str, message: str) -> None:
    """Логирует предупреждение: пишет в лог-файл/консоль и сохраняет для GUI."""
    import datetime
    logger.warning(f"⚠️  {context}: {message}")
    with _error_log_lock:
        ERROR_LOG.append({
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "context": context,
            "message": message,
        })


def get_error_log() -> List[Dict[str, str]]:
    """Возвращает копию накопленных ошибок/предупреждений."""
    with _error_log_lock:
        return list(ERROR_LOG)


def clear_error_log() -> None:
    """Очищает накопленные ошибки/предупреждения (например, перед новым сканированием)."""
    with _error_log_lock:
        ERROR_LOG.clear()


# Глобальные переменные
TRANSLATED_MODS_PATH: Optional[Path] = None
CONFIG: Dict[str, Any] = {}

# ──────────────────────────────────────────────────────────────────────────
# Целевой язык перевода (по умолчанию русский). Меняется через config.json
# ("target_language") или через выпадающий список в GUI.
# ──────────────────────────────────────────────────────────────────────────

TARGET_LANG_CODE: str = "ru_ru"

# Часто используемые языки для выпадающего списка в GUI. Можно указать и любой
# другой код вручную — программа не ограничена этим списком.
AVAILABLE_LANGUAGES: List[Tuple[str, str]] = [
    ("ru_ru", "Русский (ru_ru)"),
    ("uk_ua", "Українська (uk_ua)"),
    ("de_de", "Deutsch (de_de)"),
    ("fr_fr", "Français (fr_fr)"),
    ("es_es", "Español (es_es)"),
    ("pl_pl", "Polski (pl_pl)"),
    ("pt_br", "Português (pt_br)"),
    ("it_it", "Italiano (it_it)"),
    ("tr_tr", "Türkçe (tr_tr)"),
    ("zh_cn", "中文 (zh_cn)"),
    ("ja_jp", "日本語 (ja_jp)"),
    ("ko_kr", "한국어 (ko_kr)"),
]


CATEGORY_LABELS: Dict[str, str] = {
    "full": "[100%] Полный",
    "partial": "[Частично] Неполный",
    "missing": "[Нет] Отсутствует",
    "translated": "[Переведён]",
    "outdated": "[Устарел]",
}


def normalize_lang_code(code: str) -> str:
    """Приводит код языка к виду xx_yy (например, 'RU' -> 'ru_ru', 'de-DE' -> 'de_de')."""
    code = (code or "ru_ru").strip().lower().replace("-", "_")
    if not code:
        return "ru_ru"
    if "_" not in code:
        code = f"{code}_{code}"
    return code


def set_target_language(code: str) -> None:
    """Устанавливает целевой язык перевода для всей проверки/извлечения."""
    global TARGET_LANG_CODE
    TARGET_LANG_CODE = normalize_lang_code(code)


def get_target_language() -> str:
    """Возвращает текущий выбранный код целевого языка (например, 'ru_ru')."""
    return TARGET_LANG_CODE


def target_json_filename() -> str:
    """Имя JSON-файла локализации для текущего целевого языка (Minecraft 1.13+)."""
    return f"{TARGET_LANG_CODE}.json"


def target_lang_filename() -> str:
    """Имя .lang-файла локализации для текущего целевого языка (Minecraft 1.12.2 и ниже)."""
    parts = TARGET_LANG_CODE.split("_")
    if len(parts) == 2:
        return f"{parts[0]}_{parts[1].upper()}.lang"
    return f"{TARGET_LANG_CODE}.lang"


def target_lang_display() -> str:
    """Короткое отображаемое имя языка для UI, например 'RU', 'DE'."""
    return TARGET_LANG_CODE.split("_")[0].upper()


def _is_target_lang_file(filename: str) -> bool:
    """
    Проверяет, что ИМЯ файла (без пути) — это .lang файл текущего целевого языка.
    Сравнение по префиксу языка (например 'ru' у 'ru_ru'/'ru_RU'), но обязательно
    по всему имени файла целиком — а не подстрокой где угодно в пути, чтобы не
    словить случайное совпадение вроде "structure.lang".
    """
    match = re.match(r'^([a-z]{2,3})_([a-z]{2,3})\.lang$', filename.lower())
    if not match:
        return False
    target_prefix = TARGET_LANG_CODE.split('_')[0]
    return match.group(1) == target_prefix


def get_system_theme() -> str:
    """Определяет системную тему: 'light' или 'dark'."""
    try:
        system = platform.system()
        if system == "Windows":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                    return "light" if value == 1 else "dark"
            except Exception:
                return "light"

        if system == "Darwin":
            try:
                result = subprocess.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0 and result.stdout.strip().lower() == "dark":
                    return "dark"
            except Exception:
                pass
            return "light"

        if system == "Linux":
            gtk_theme = os.environ.get("GTK_THEME", "").lower()
            if "dark" in gtk_theme:
                return "dark"
            try:
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0 and "dark" in result.stdout.lower():
                    return "dark"
            except Exception:
                pass
            return "light"
    except Exception:
        pass
    return "light"


def load_config(config_file: str = "config.json") -> None:
    """
    Загружает конфигурацию из JSON файла и сохраняет в глобальную переменную CONFIG.
    Возвращаемого значения нет — используйте CONFIG напрямую после вызова.

    Args:
        config_file: Путь к файлу конфига
    """
    global CONFIG

    default_row_colors = {
        "light": {
            "jar": "#d4f4dd",
            "translated_mods": "#9dcafa",
            "missing": "#ffe4e1"
        },
        "dark": {
            "jar": "#7d8a7d",
            "translated_mods": "#5f738c",
            "missing": "#b18a82"
        }
    }

    # Значения по умолчанию — используются как база и дополняются тем, что найдено в файле
    CONFIG = {
        "translated_mods_path": "TranslatedMods",
        "supported_languages": ["ru_ru"],
        "target_language": "ru_ru",
        "max_workers": 4,
        "show_statistics": True,
        "default_export_file": "localization_results.json",
        "last_directory": "",
        "last_extract_source": "",
        "last_extract_output": "",
        "row_colors": default_row_colors
    }

    try:
        config_path = Path(config_file)
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # Загруженные значения дополняют/перекрывают дефолтные, но не стирают их полностью
                CONFIG.update(loaded)

                # Применяем целевой язык перевода из конфига
                set_target_language(CONFIG.get("target_language", "ru_ru"))

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

                # Нормализуем секцию row_colors для поддержки светлой и тёмной темы
                row_colors = loaded.get("row_colors", {})
                if isinstance(row_colors, dict) and any(key in row_colors for key in ("light", "dark")):
                    CONFIG["row_colors"] = {
                        "light": {
                            "jar": row_colors.get("light", {}).get("jar", default_row_colors["light"]["jar"]),
                            "translated_mods": row_colors.get("light", {}).get("translated_mods", default_row_colors["light"]["translated_mods"]),
                            "missing": row_colors.get("light", {}).get("missing", default_row_colors["light"]["missing"])
                        },
                        "dark": {
                            "jar": row_colors.get("dark", {}).get("jar", default_row_colors["dark"]["jar"]),
                            "translated_mods": row_colors.get("dark", {}).get("translated_mods", default_row_colors["dark"]["translated_mods"]),
                            "missing": row_colors.get("dark", {}).get("missing", default_row_colors["dark"]["missing"])
                        }
                    }
                elif row_colors:
                    CONFIG["row_colors"] = {
                        "light": {
                            "jar": row_colors.get("jar", default_row_colors["light"]["jar"]),
                            "translated_mods": row_colors.get("translated_mods", default_row_colors["light"]["translated_mods"]),
                            "missing": row_colors.get("missing", default_row_colors["light"]["missing"])
                        },
                        "dark": default_row_colors["dark"]
                    }
                # конец нормализации row_colors
    except Exception as e:
        log_warning("Загрузка конфига", str(e))


def save_config(config_file: str = "config.json") -> None:
    """Сохраняет текущее содержимое CONFIG обратно в JSON файл."""
    try:
        config_path = Path(config_file)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_warning("Сохранение конфига", str(e))


def set_translated_mods_path(path: Optional[Path]):
    """Устанавливает путь к папке TranslatedMods."""
    global TRANSLATED_MODS_PATH
    TRANSLATED_MODS_PATH = path


def get_row_colors(theme: str = "light") -> Dict[str, str]:
    """Возвращает цвета строк для указанной темы."""
    default_row_colors = {
        "light": {
            "jar": "#d4f4dd",
            "translated_mods": "#9dcafa",
            "missing": "#ffe4e1"
        },
        "dark": {
            "jar": "#a2bca2",
            "translated_mods": "#7c96b7",
            "missing": "#d7b7b1"
        }
    }
    row_colors = CONFIG.get("row_colors", {})
    if not isinstance(row_colors, dict):
        return default_row_colors.get(theme, default_row_colors["light"])

    if "light" in row_colors or "dark" in row_colors:
        theme_colors = row_colors.get(theme, {})
        defaults = default_row_colors.get(theme, {})
        return {
            "jar": theme_colors.get("jar", defaults["jar"]),
            "translated_mods": theme_colors.get("translated_mods", defaults["translated_mods"]),
            "missing": theme_colors.get("missing", defaults["missing"])
        }

    # backward compatibility with top-level row_colors keys
    if theme == "light":
        return {
            "jar": row_colors.get("jar", default_row_colors["light"]["jar"]),
            "translated_mods": row_colors.get("translated_mods", default_row_colors["light"]["translated_mods"]),
            "missing": row_colors.get("missing", default_row_colors["light"]["missing"])
        }

    return default_row_colors["dark"]


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


def clean_json_with_comments(content: str) -> str:
    """
    Удаляет комментарии из JSON содержимого.
    Поддерживает следующие типы комментариев:
    - // однострочные комментарии
    - /* многострочные комментарии */
    
    Также удаляет запятые перед закрывающей скобкой (trailing commas).
    
    Args:
        content: Содержимое JSON файла со строками, потенциально содержащими комментарии
        
    Returns:
        JSON содержимое с удаленными комментариями
    """
    # Удаляем многострочные комментарии /* ... */ с учётом строк JSON.
    # Простая regex вида re.sub(r'/\*.*?\*/', ...) ломает валидный JSON,
    # если подстрока /* ... */ встречается внутри строкового значения.
    # Используем посимвольный обход, чтобы не трогать содержимое строк.
    result_chars: List[str] = []
    i = 0
    in_string = False
    escaped = False
    n = len(content)
    while i < n:
        ch = content[i]
        if escaped:
            result_chars.append(ch)
            escaped = False
            i += 1
            continue
        if ch == '\\' and in_string:
            result_chars.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result_chars.append(ch)
            i += 1
            continue
        if not in_string and ch == '/' and i + 1 < n and content[i + 1] == '*':
            # Ищем закрывающий */
            end = content.find('*/', i + 2)
            if end == -1:
                # Незакрытый комментарий — удаляем до конца
                break
            i = end + 2
            continue
        result_chars.append(ch)
        i += 1
    content = ''.join(result_chars)
    
    # Удаляем однострочные комментарии //
    # Но должны быть осторожны не удалить // внутри строк
    lines = content.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # Удаляем комментарии, которые начинаются с // вне строк
        # Просто ищем // и удаляем всё после них, если это не внутри кавычек
        in_string = False
        escaped = False
        cleaned = []
        i = 0
        
        while i < len(line):
            char = line[i]
            
            # Обработка экранирования
            if escaped:
                cleaned.append(char)
                escaped = False
                i += 1
                continue
            
            # Проверка на экранированный символ
            if char == '\\' and in_string:
                cleaned.append(char)
                escaped = True
                i += 1
                continue
            
            # Переключение режима строки
            if char == '"':
                in_string = not in_string
                cleaned.append(char)
                i += 1
                continue
            
            # Проверка на начало комментария (только вне строк)
            if not in_string and i + 1 < len(line) and line[i:i+2] == '//':
                break  # Удаляем всё от // до конца строки
            
            cleaned.append(char)
            i += 1
        
        cleaned_lines.append(''.join(cleaned))
    
    content = '\n'.join(cleaned_lines)
    
    # Удаляем trailing commas перед закрывающей скобкой или квадратной скобкой
    content = re.sub(r',(\s*[}\]])', r'\1', content)
    
    return content


def extract_json_from_file(file_path: Path) -> Optional[Dict[str, str]]:
    """
    Извлекает JSON файл локализации из файловой системы.
    Поддерживает JSON с комментариями (// и /* */).
    
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
            # Удаляем комментарии перед парсингом
            content = clean_json_with_comments(content)
            data = json.loads(content)
            return data
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        log_warning("Чтение JSON", f"{file_path}: {e}")
        return None
    except Exception as e:
        log_warning("Чтение JSON", f"{file_path} (неожиданная ошибка): {e}")
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
                    value = line[eq_index + 1:]  # не strip() — пробелы в значении значимы
                    
                    if key:  # Ключ не должен быть пустым
                        result[key] = value
                elif line:
                    # Строка без '=' - возможно malformed, пропускаем с предупреждением
                    log_warning("Чтение .lang", f"строка {line_num} в {file_path} пропущена: нет разделителя '='")
        
        return result
    except (UnicodeDecodeError, IOError) as e:
        log_warning("Чтение .lang", f"{file_path}: {e}")
        return None
    except Exception as e:
        log_warning("Чтение .lang", f"{file_path} (неожиданная ошибка): {e}")
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
                        value = line[eq_index + 1:]  # не strip() — пробелы в значении значимы
                        
                        if key:  # Ключ не должен быть пустым
                            result[key] = value
                
                return result
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as e:
        log_warning("Чтение .lang из архива", f"{lang_path} из {jar_path}: {e}")
        return None
    except Exception as e:
        log_warning("Чтение .lang из архива", f"{jar_path} (неожиданная ошибка): {e}")
        return None


def find_ru_ru_in_translated_mods(mod_name: str) -> Optional[Path]:
    """
    Ищет файл локализации выбранного целевого языка (по умолчанию ru_ru.json/ru_RU.lang,
    но зависит от TARGET_LANG_CODE / CONFIG["target_language"]) для мода в папке TranslatedMods.
    
    Структура TranslatedMods:
    TranslatedMods/
    ├── <название_мода>/
    │   └── lang/
    │       ├── <lang>.json (для новых версий Minecraft 1.13+)
    │       └── <Lang>.lang (для старых версий Minecraft 1.12.2 и ниже)
    
    Args:
        mod_name: Название мода (извлеченное из assets внутри .jar файла)
        
    Returns:
        Путь к файлу локализации (.json или .lang) или None если не найден
    """
    if TRANSLATED_MODS_PATH is None or not TRANSLATED_MODS_PATH.exists():
        return None

    # Только точное совпадение: папка в TranslatedMods должна называться ровно так же,
    # как assets-имя мода. Нечёткий поиск давал ложные срабатывания.
    mod_dir = TRANSLATED_MODS_PATH / mod_name
    if mod_dir.exists() and mod_dir.is_dir():
        json_path = mod_dir / "lang" / target_json_filename()
        if json_path.exists():
            return json_path
        lang_path = mod_dir / "lang" / target_lang_filename()
        if lang_path.exists():
            return lang_path

    return None


def extract_mod_name_from_assets(jar_path: Path) -> Optional[str]:
    """
    Извлекает имя мода из структуры папок assets внутри .jar/.zip файла.
    
    Поддерживает следующие структуры:
    - assets/advancedlootinfo/lang/en_us.json -> 'advancedlootinfo'
    - assets/advancedlootinfo/language/en_us.json -> 'advancedlootinfo'
    - assets/advancedlootinfo/lang_nei/en_us.json -> 'advancedlootinfo'
    
    Args:
        jar_path: Путь к .jar/.zip файлу
        
    Returns:
        Имя мода или None если не удалось извлечь
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                # Ищем файлы в одной из папок локализации (lang, language, или lang_nei)
                normalized = name.replace('\\', '/').lower()
                if any(pattern in normalized for pattern in ['/lang/', '/language/', '/lang_nei/']):
                    # Извлекаем путь до папки локализации
                    parts = normalized.split('/')
                    # Находим индекс 'assets' и берем следующий элемент
                    for i, part in enumerate(parts):
                        if part == 'assets' and i + 1 < len(parts):
                            mod_name = parts[i + 1]
                            if mod_name and mod_name not in ('lang', 'language', 'lang_nei'):
                                return mod_name
    except zipfile.BadZipFile:
        return None
    
    return None


def _has_letters(value: str) -> bool:
    """True, если строка содержит хотя бы одну букву (любого алфавита)."""
    return any(ch.isalpha() for ch in value)


def _find_identical_value_keys(en_data: Dict[str, str], ru_data: Dict[str, str],
                                shared_keys: set) -> List[str]:
    """
    Ищет ключи, у которых значение перевода совпадает со значением на английском —
    частый признак того, что строку забыли перевести (или скопировали as-is).

    Чтобы не заваливать результат ложными срабатываниями, пропускаем значения без
    единой буквы (числа, "%s", цветовые коды §c и т.п. — там совпадение нормально).
    """
    identical = []
    for key in shared_keys:
        en_value = str(en_data.get(key, "")).strip()
        ru_value = str(ru_data.get(key, "")).strip()
        if not en_value or en_value != ru_value:
            continue
        if not _has_letters(en_value):
            continue
        identical.append(key)
    return sorted(identical)


def _compare_keys(en_data: Dict[str, str], ru_data: Dict[str, str]) -> Dict[str, Any]:
    """
    Сравнивает ключи английской и русской локализаций.

    Returns:
        Словарь с полями: ru_keys, missing_keys, extra_keys, percentage, status,
        identical_keys, identical_count
    """
    en_keys_set = set(en_data.keys())
    ru_keys_set = set(ru_data.keys())
    en_count = len(en_keys_set)

    missing_keys = sorted(en_keys_set - ru_keys_set)
    extra_keys = sorted(ru_keys_set - en_keys_set)
    shared_keys = en_keys_set & ru_keys_set

    identical_keys = _find_identical_value_keys(en_data, ru_data, shared_keys)

    if en_count == 0:
        percentage = 0.0
        status = "missing"
    else:
        percentage = round((len(shared_keys) / en_count) * 100, 2)
        status = "full" if percentage == 100.0 else "partial"

    return {
        "ru_keys": len(ru_data),
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "percentage": percentage,
        "status": status,
        "identical_keys": identical_keys,
        "identical_count": len(identical_keys),
    }


def check_translated_mods_localization(jar_path: Path, mod_name: str, en_data: Dict[str, str], en_us_path: str) -> Dict[str, Any]:
    """
    Проверяет наличие перевода для мода в папке TranslatedMods.
    Поддерживает как .json (Minecraft 1.13+), так и .lang (Minecraft 1.12.2 и ниже) файлы.
    
    Args:
        jar_path: Путь к .jar файлу мода
        mod_name: Имя мода, извлеченное из assets
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
        "identical_keys": [],
        "identical_count": 0,
        "error": None
    }
    
    if TRANSLATED_MODS_PATH is None:
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

    cmp = _compare_keys(en_data, ru_data)
    result.update(cmp)
    # _compare_keys возвращает статус "missing" при 0% — нормализуем до "not_found" здесь
    if cmp["status"] == "missing":
        result["status"] = "not_found"

    return result


def extract_json_from_jar(jar_path: Path, lang_path: str) -> Optional[Dict[str, str]]:
    """
    Извлекает JSON файл локализации из .jar архива.
    Поддерживает JSON с комментариями (// и /* */).
    
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
                # Удаляем комментарии перед парсингом
                content = clean_json_with_comments(content)
                data = json.loads(content)
                return data
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        log_warning("Чтение JSON из архива", f"{lang_path} из {jar_path}: {e}")
        return None
    except Exception as e:
        log_warning("Чтение JSON из архива", f"{jar_path} (неожиданная ошибка): {e}")
        return None


def check_patchouli_books(jar_path: Path) -> List[Dict[str, Any]]:
    """
    Проверяет наличие русской локализации для Patchouli гайдбуков внутри .jar архива.

    Patchouli хранит страницы гайдбука как отдельные .json файлы по пути:
      assets/<modname>/patchouli_books/<bookname>/<lang>/entries/...
      assets/<modname>/patchouli_books/<bookname>/<lang>/categories/...

    Логика простая (вариант 1): сравниваем наличие файлов в en_us/ vs ru_ru/.

    Returns:
        Список словарей — по одному на каждый найденный гайдбук.
    """
    results = []
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            all_names = jar_file.namelist()
    except zipfile.BadZipFile:
        return results

    # Собираем все пути внутри patchouli_books
    patchouli_files = [
        name.replace('\\', '/') for name in all_names
        if 'patchouli_books' in name.replace('\\', '/').lower()
    ]

    if not patchouli_files:
        return results

    # Группируем по (mod_name, book_name)
    books: Dict[str, Dict[str, set]] = {}
    for raw_path in patchouli_files:
        path = raw_path.replace('\\', '/')
        parts = path.split('/')
        # Ожидаем: assets/<mod>/patchouli_books/<book>/<lang>/...
        try:
            assets_idx = next(i for i, p in enumerate(parts) if p.lower() == 'assets')
            pb_idx     = next(i for i, p in enumerate(parts) if p.lower() == 'patchouli_books')
        except StopIteration:
            continue

        if pb_idx - assets_idx != 2:
            continue
        if len(parts) <= pb_idx + 2:
            continue

        mod_name  = parts[assets_idx + 1]
        book_name = parts[pb_idx + 1]
        lang_raw  = parts[pb_idx + 2]
        lang      = lang_raw.lower()
        # Остаток пути — сам файл
        rest = '/'.join(parts[pb_idx + 3:])

        if not rest or rest.endswith('/'):
            continue  # папка, не файл

        key = f"{mod_name}/{book_name}"
        if key not in books:
            books[key] = {'mod_name': mod_name, 'book_name': book_name,
                          'en_files': set(), 'ru_files': set()}

        # Сравниваем без учёта регистра: en_us == en_US, а целевой язык — по TARGET_LANG_CODE
        if lang in ('en_us',):
            books[key]['en_files'].add(rest)
        elif lang == TARGET_LANG_CODE:
            books[key]['ru_files'].add(rest)

    for key, book in books.items():
        en_files = book['en_files']
        ru_files = book['ru_files']

        if not en_files:
            continue  # нет английской версии — нечего проверять

        missing = sorted(en_files - ru_files)
        extra   = sorted(ru_files - en_files)
        translated_count = len(en_files & ru_files)
        total   = len(en_files)
        percentage = round((translated_count / total) * 100, 2) if total > 0 else 0.0

        if percentage == 100.0:
            status = 'full'
        elif percentage > 0:
            status = 'partial'
        else:
            status = 'missing'

        # Проверяем TranslatedMods/<mod>/patchouli_books/<book>/<target_lang>/
        # и дополняем ru_files файлами оттуда
        final_ru_count = len(ru_files)
        if TRANSLATED_MODS_PATH is not None and TRANSLATED_MODS_PATH.exists():
            tm_ru_dir = TRANSLATED_MODS_PATH / book['mod_name'] / "patchouli_books" / book['book_name'] / TARGET_LANG_CODE
            if tm_ru_dir.exists():
                tm_ru_files = set()
                for f in tm_ru_dir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(tm_ru_dir).as_posix()
                        tm_ru_files.add(rel)
                combined_ru = ru_files | tm_ru_files
                final_ru_count = len(combined_ru)
                missing  = sorted(en_files - combined_ru)
                extra    = sorted(combined_ru - en_files)
                translated_count = len(en_files & combined_ru)
                percentage = round((translated_count / total) * 100, 2) if total > 0 else 0.0
                if percentage == 100.0:
                    status = 'full'
                elif percentage > 0:
                    status = 'partial'
                else:
                    status = 'missing'

        results.append({
            'mod_name':      book['mod_name'],
            'book_name':     book['book_name'],
            'status':        status,
            'en_files':      total,
            'ru_files':      final_ru_count,
            'percentage':    percentage,
            'missing_files': missing,
            'extra_files':   extra,
        })

    return results



def _is_preferred_asset_lang_path(path: str) -> bool:
    """Возвращает True для пути вида assets/<modid>/lang/<file>, assets/<modid>/language/<file> или assets/<modid>/lang_nei/<file>."""
    normalized = path.replace('\\', '/').lower()
    parts = normalized.split('/')
    if len(parts) < 4 or parts[0] != 'assets':
        return False
    # Проверяем, что третий элемент (parts[2]) - это одна из поддерживаемых папок локализации
    return parts[2] in ('lang', 'language', 'lang_nei')


def _select_best_lang_path(candidates: List[str]) -> Optional[str]:
    """Выбирает наиболее подходящий путь среди кандидатов."""
    if not candidates:
        return None

    preferred = [path for path in candidates if _is_preferred_asset_lang_path(path)]
    if preferred:
        return min(preferred, key=lambda p: len(p.replace('\\', '/').split('/')))

    return min(candidates, key=lambda p: len(p.replace('\\', '/').split('/')))


def find_mod_lang_files_in_archive(jar_path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Ищет языковые файлы в каждом моде внутри .jar/.zip архива.

    Возвращает словарь модов с кандидатами на пути локализации.
    """
    lang_folder_patterns = ['/lang/', '/language/', '/lang_nei/']
    mods: Dict[str, Dict[str, Any]] = {}

    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                normalized = name.replace('\\', '/').lower()
                if not any(pattern in normalized for pattern in lang_folder_patterns):
                    continue

                parts = normalized.split('/')
                if len(parts) < 4 or parts[0] != 'assets':
                    continue

                mod_name = parts[1]
                if not mod_name:
                    continue

                # Поля называются 'ru_*' исторически, но хранят кандидатов для ТЕКУЩЕГО
                # целевого языка перевода (TARGET_LANG_CODE / CONFIG["target_language"]),
                # а не обязательно русского.
                info = mods.setdefault(mod_name, {
                    'en_us_candidates': [],
                    'ru_ru_candidates': [],
                    'en_lang_candidates': [],
                    'ru_lang_candidates': [],
                    'has_lang_dir': False,
                    'has_lang_files': False
                })

                info['has_lang_dir'] = True

                if normalized.endswith('/en_us.json'):
                    info['en_us_candidates'].append(name)
                elif normalized.endswith('/' + target_json_filename()):
                    info['ru_ru_candidates'].append(name)
                elif normalized.endswith('/en_us.lang'):
                    info['en_lang_candidates'].append(name)
                    info['has_lang_files'] = True
                elif normalized.endswith('.lang'):
                    # Сравниваем по имени файла (без пути) и точному соответствию
                    # xx_yy.lang выбранному целевому языку — например ru_ru.lang/ru_RU.lang для "ru".
                    filename = normalized.rsplit('/', 1)[-1]
                    if _is_target_lang_file(filename):
                        info['ru_lang_candidates'].append(name)
                        info['has_lang_files'] = True
    except zipfile.BadZipFile:
        return {}

    return mods


def _select_mod_lang_paths(mod_info: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], bool, bool]:
    """
    Выбирает лучшие пути локализации для одного мода.
    """
    en_us_path = _select_best_lang_path(mod_info['en_us_candidates'])
    ru_ru_path = _select_best_lang_path(mod_info['ru_ru_candidates'])
    en_lang_path = _select_best_lang_path(mod_info['en_lang_candidates'])
    ru_lang_path = _select_best_lang_path(mod_info['ru_lang_candidates'])
    return en_us_path, ru_ru_path, en_lang_path, ru_lang_path, mod_info['has_lang_dir'], mod_info['has_lang_files']


# ──────────────────────────────────────────────────────────────────────────
# Извлечение заготовок переводов (en_us + ru_ru) в отдельную папку для перевода
# ──────────────────────────────────────────────────────────────────────────

EXCLUDED_MOD_DIRS = {".connector", "mcef-cache"}


def find_all_mod_files(base_path: Path) -> List[Path]:
    """Рекурсивно находит все .jar/.zip файлы, пропуская служебные папки."""
    mod_files: List[Path] = []
    for item in base_path.rglob("*"):
        if any(part in EXCLUDED_MOD_DIRS for part in item.parts):
            continue
        if item.is_file() and item.suffix.lower() in (".jar", ".zip"):
            mod_files.append(item)
    return mod_files


def _merge_json_candidates(jar_path: Path, candidates: List[str]) -> Dict[str, str]:
    """Объединяет ключи из нескольких JSON-кандидатов локализации в один словарь."""
    merged: Dict[str, str] = {}
    for candidate in candidates:
        data = extract_json_from_jar(jar_path, candidate)
        if data:
            merged.update(data)
    return merged


def _merge_lang_candidates(jar_path: Path, candidates: List[str]) -> Dict[str, str]:
    """Объединяет ключи из нескольких .lang-кандидатов локализации в один словарь."""
    merged: Dict[str, str] = {}
    for candidate in candidates:
        data = parse_lang_from_jar(jar_path, candidate)
        if data:
            merged.update(data)
    return merged


def build_translation_stub_tasks(mod_files: List[Path]) -> List[Dict[str, Any]]:
    """
    Проходит по всем .jar/.zip файлам и собирает список задач извлечения —
    по одной на каждый мод, у которого нашлась английская локализация.
    """
    tasks: List[Dict[str, Any]] = []
    for jar_path in mod_files:
        try:
            mods_info = find_mod_lang_files_in_archive(jar_path)
        except Exception:
            continue
        for mod_name, info in mods_info.items():
            has_json_en = bool(info['en_us_candidates'])
            has_lang_en = bool(info['en_lang_candidates'])
            if not has_json_en and not has_lang_en:
                continue  # нет английской локализации — нечего извлекать
            tasks.append({
                'jar_path': jar_path,
                'mod_name': mod_name,
                'info': info,
                'is_json': has_json_en,  # предпочитаем json формат, если есть оба
            })
    return tasks


def count_existing_ru_translations(tasks: List[Dict[str, Any]], output_dir: Path) -> int:
    """Считает, для скольких заданий уже есть файл перевода целевого языка в выходной папке."""
    count = 0
    for task in tasks:
        ru_filename = target_json_filename() if task['is_json'] else target_lang_filename()
        if (output_dir / task['mod_name'] / "lang" / ru_filename).exists():
            count += 1
    return count


def _write_lang_data(path: Path, data: Dict[str, str], is_json: bool) -> None:
    """Записывает словарь локализации либо как JSON, либо как .lang файл (key=value)."""
    if is_json:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        with open(path, 'w', encoding='utf-8') as f:
            for key, value in data.items():
                f.write(f"{key}={value}\n")


def extract_translation_stub(task: Dict[str, Any], output_dir: Path, overwrite_existing: bool) -> Dict[str, Any]:
    """
    Извлекает en_us и ru_ru (или создаёт заготовку из en_us, если ru в моде нет)
    для одного мода в output_dir/<mod_name>/lang/.
    """
    jar_path = task['jar_path']
    mod_name = task['mod_name']
    info = task['info']
    is_json = task['is_json']

    try:
        if is_json:
            en_data = _merge_json_candidates(jar_path, info['en_us_candidates'])
            ru_data = _merge_json_candidates(jar_path, info['ru_ru_candidates']) if info['ru_ru_candidates'] else None
            en_filename, ru_filename = "en_us.json", target_json_filename()
        else:
            en_data = _merge_lang_candidates(jar_path, info['en_lang_candidates'])
            ru_data = _merge_lang_candidates(jar_path, info['ru_lang_candidates']) if info['ru_lang_candidates'] else None
            en_filename, ru_filename = "en_US.lang", target_lang_filename()

        if not en_data:
            return {'mod_name': mod_name, 'status': 'error', 'error': 'Не удалось прочитать en_us из мода/архива'}

        mod_dir = output_dir / mod_name / "lang"
        mod_dir.mkdir(parents=True, exist_ok=True)

        en_path = mod_dir / en_filename
        ru_path = mod_dir / ru_filename

        # EN всегда перезаписываем — это эталонный референс, а не пользовательская работа
        _write_lang_data(en_path, en_data, is_json)

        if ru_path.exists() and not overwrite_existing:
            return {'mod_name': mod_name, 'status': 'skipped'}

        if ru_data:
            _write_lang_data(ru_path, ru_data, is_json)
            return {'mod_name': mod_name, 'status': 'created_from_mod'}
        else:
            # В моде нет русского — копируем английский текст как стартовую заготовку
            _write_lang_data(ru_path, en_data, is_json)
            return {'mod_name': mod_name, 'status': 'created_stub'}

    except Exception as e:
        return {'mod_name': mod_name, 'status': 'error', 'error': str(e)}


def extract_patchouli_stub_for_jar(jar_path: Path, output_dir: Path, overwrite_existing: bool) -> Dict[str, Any]:
    """
    Извлекает Patchouli-гайдбуки одного .jar/.zip файла: копирует en_us файлы как референс
    и копирует/создаёт ru_ru файлы (из мода, если перевод уже есть, иначе — копия en_us как заготовка).
    """
    stats: Dict[str, Any] = {
        'copied_en': 0, 'copied_ru_from_mod': 0, 'created_ru_stub': 0,
        'skipped_ru': 0, 'error': 0, 'errors': []
    }

    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            all_names = jar_file.namelist()
            patchouli_files = [n for n in all_names if 'patchouli_books' in n.replace('\\', '/').lower()]
            if not patchouli_files:
                return stats

            # Группируем: (mod, book) -> {'en': {rest: путь_внутри_jar}, 'ru': {...}}
            books: Dict[str, Dict[str, Any]] = {}
            for raw_name in patchouli_files:
                path = raw_name.replace('\\', '/')
                parts = path.split('/')
                try:
                    assets_idx = next(i for i, p in enumerate(parts) if p.lower() == 'assets')
                    pb_idx     = next(i for i, p in enumerate(parts) if p.lower() == 'patchouli_books')
                except StopIteration:
                    continue
                if pb_idx - assets_idx != 2 or len(parts) <= pb_idx + 2:
                    continue

                mod_name  = parts[assets_idx + 1]
                book_name = parts[pb_idx + 1]
                lang      = parts[pb_idx + 2].lower()
                rest      = '/'.join(parts[pb_idx + 3:])
                if not rest or raw_name.endswith('/'):
                    continue  # это папка, не файл

                key = f"{mod_name}/{book_name}"
                entry = books.setdefault(key, {'mod_name': mod_name, 'book_name': book_name, 'en': {}, 'ru': {}})
                if lang == 'en_us':
                    entry['en'][rest] = raw_name
                elif lang == TARGET_LANG_CODE:
                    entry['ru'][rest] = raw_name

            for book in books.values():
                if not book['en']:
                    continue  # нет английской версии — нечего извлекать

                book_out_dir = output_dir / book['mod_name'] / "patchouli_books" / book['book_name']

                for rest, jar_internal_path in book['en'].items():
                    try:
                        raw_bytes = jar_file.read(jar_internal_path)
                    except Exception as e:
                        stats['error'] += 1
                        stats['errors'].append(f"{book['mod_name']}/{book['book_name']}/{rest}: {e}")
                        continue

                    en_target = book_out_dir / "en_us" / Path(*rest.split('/'))
                    en_target.parent.mkdir(parents=True, exist_ok=True)
                    with open(en_target, 'wb') as f:
                        f.write(raw_bytes)
                    stats['copied_en'] += 1

                    ru_target = book_out_dir / TARGET_LANG_CODE / Path(*rest.split('/'))
                    if ru_target.exists() and not overwrite_existing:
                        stats['skipped_ru'] += 1
                        continue

                    ru_target.parent.mkdir(parents=True, exist_ok=True)
                    if rest in book['ru']:
                        try:
                            ru_raw = jar_file.read(book['ru'][rest])
                            with open(ru_target, 'wb') as f:
                                f.write(ru_raw)
                            stats['copied_ru_from_mod'] += 1
                        except Exception as e:
                            stats['error'] += 1
                            stats['errors'].append(f"{book['mod_name']}/{book['book_name']}/{rest}: {e}")
                    else:
                        with open(ru_target, 'wb') as f:
                            f.write(raw_bytes)
                        stats['created_ru_stub'] += 1

    except zipfile.BadZipFile:
        stats['error'] += 1
        stats['errors'].append(f"{jar_path.name}: повреждённый архив")

    return stats


def extract_all_translation_stubs(
    source_dir: Path,
    output_dir: Path,
    overwrite_existing: bool,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    phase_callback: Optional[Callable[[str], None]] = None,
    item_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Сканирует source_dir на .jar/.zip моды и для каждого извлекает en_us + перевод
    для текущего целевого языка (включая .lang файлы старых модов и Patchouli-гайдбуки)
    в output_dir, готовя структуру для последующего перевода.

    Args:
        progress_callback: (current, total) — числовой прогресс внутри текущей фазы.
        phase_callback: (text) — смена текущей фазы работы ("Поиск модов...", и т.д.).
        item_callback: (result_dict) — вызывается по каждому обработанному моду/архиву,
            удобно для живого лога в GUI. result_dict содержит как минимум 'mod_name'
            и 'status' (или 'phase': 'patchouli' для итогов по гайдбукам одного архива).

    Returns:
        Словарь со статистикой: created_from_mod, created_stub, skipped, error, errors,
        а также вложенный словарь 'patchouli' со статистикой по гайдбукам.
    """
    if phase_callback:
        phase_callback("Поиск модов...")

    mod_files = find_all_mod_files(source_dir)
    tasks = build_translation_stub_tasks(mod_files)
    total = len(tasks)

    stats: Dict[str, Any] = {
        'created_from_mod': 0,
        'created_stub': 0,
        'skipped': 0,
        'error': 0,
        'errors': [],
        'total': total,
        'patchouli': {
            'copied_en': 0, 'copied_ru_from_mod': 0, 'created_ru_stub': 0,
            'skipped_ru': 0, 'error': 0, 'errors': []
        }
    }

    if total > 0:
        if phase_callback:
            phase_callback("Извлечение ключей локализации...")

        max_workers = CONFIG.get("max_workers", 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(extract_translation_stub, task, output_dir, overwrite_existing): task
                       for task in tasks}

            for i, future in enumerate(as_completed(futures)):
                try:
                    result = future.result()
                except Exception as e:
                    task = futures[future]
                    result = {'mod_name': task['mod_name'], 'status': 'error', 'error': str(e)}

                status = result['status']
                stats[status] = stats.get(status, 0) + 1
                if status == 'error':
                    stats['errors'].append(f"{result['mod_name']}: {result.get('error')}")

                if progress_callback:
                    progress_callback(i + 1, total)
                if item_callback:
                    item_callback(result)

    # Извлекаем Patchouli-гайдбуки для всех найденных jar/zip файлов
    if mod_files:
        if phase_callback:
            phase_callback("Извлечение гайдбуков Patchouli...")

        pb_total = len(mod_files)
        for i, jar_path in enumerate(mod_files):
            result = extract_patchouli_stub_for_jar(jar_path, output_dir, overwrite_existing)
            for key in ('copied_en', 'copied_ru_from_mod', 'created_ru_stub', 'skipped_ru', 'error'):
                stats['patchouli'][key] += result[key]
            stats['patchouli']['errors'].extend(result['errors'])

            # Раньше прогресс на этой фазе не обновлялся — на больших наборах модов
            # это выглядело как "зависание" на несколько секунд/минут без обратной связи.
            if progress_callback:
                progress_callback(i + 1, pb_total)
            if item_callback and (result['copied_en'] or result['error']):
                item_callback({
                    'mod_name': jar_path.name,
                    'phase': 'patchouli',
                    'status': 'error' if result['error'] else 'ok',
                    'copied_en': result['copied_en'],
                    'copied_ru_from_mod': result['copied_ru_from_mod'],
                    'created_ru_stub': result['created_ru_stub'],
                })

    return stats


def check_mod_localization(jar_path: Path, mod_name: str, mod_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет локализацию одного мода внутри архива.
    """
    result = {
        'mod_name': f"{jar_path.name} ({mod_name})",
        'status': 'missing',
        'source': 'none',
        'ru_keys': 0,
        'en_keys': 0,
        'percentage': 0.0,
        'missing_keys': [],
        'extra_keys': [],
        'identical_keys': [],
        'identical_count': 0,
        'error': None,
        'patchouli': []
    }

    en_us_path, ru_ru_path, en_lang_path, ru_lang_path, has_lang_dir, has_lang_files = _select_mod_lang_paths(mod_info)

    if not has_lang_dir:
        result['status'] = 'skipped'
        result['error'] = 'Папка локализации (lang/language/lang_nei) не найдена в моде'
        return result

    en_data: Dict[str, str] = {}
    en_source = None

    # Собираем ключи из ВСЕХ en_us.json кандидатов (lang + lang_nei + language)
    for candidate in mod_info['en_us_candidates']:
        data = extract_json_from_jar(jar_path, candidate)
        if data:
            en_data.update(data)
            if en_source is None:
                en_source = candidate

    # Если JSON не нашли — пробуем все .lang кандидаты
    if not en_data:
        for candidate in mod_info['en_lang_candidates']:
            data = parse_lang_from_jar(jar_path, candidate)
            if data:
                en_data.update(data)
                if en_source is None:
                    en_source = candidate

    if not en_data:
        result['status'] = 'skipped'
        result['error'] = 'Файл en_us.json/en_US.lang не найден в моде'
        return result

    result['en_keys'] = len(en_data)
    if result['en_keys'] == 0:
        result['status'] = 'skipped'
        result['error'] = 'Файл en_us.json/en_US.lang пустой (мод пропущен)'
        return result

    if TRANSLATED_MODS_PATH is not None:
        translated_mods_result = check_translated_mods_localization(jar_path, mod_name, en_data, en_source)
        if translated_mods_result['found']:
            is_full = translated_mods_result['status'] == 'full'
            result.update({
                'status': 'translated' if is_full else 'partial',
                'source': 'translated_mods',
                'ru_keys': translated_mods_result['ru_keys'],
                'percentage': translated_mods_result['percentage'],
                'missing_keys': translated_mods_result['missing_keys'],
                'extra_keys': translated_mods_result['extra_keys'],
                'identical_keys': translated_mods_result.get('identical_keys', []),
                'identical_count': translated_mods_result.get('identical_count', 0)
            })
            return result

    # Собираем ru ключи из всех кандидатов (lang + lang_nei + language)
    ru_data: Dict[str, str] = {}
    for candidate in mod_info['ru_ru_candidates']:
        data = extract_json_from_jar(jar_path, candidate)
        if data:
            ru_data.update(data)

    if not ru_data:
        for candidate in mod_info['ru_lang_candidates']:
            data = parse_lang_from_jar(jar_path, candidate)
            if data:
                ru_data.update(data)

    if ru_data:
        result['source'] = 'jar'
        result.update(_compare_keys(en_data, ru_data))
        return result

    result['status'] = 'missing'
    result['error'] = f'Нет {target_json_filename()}/{target_lang_filename()}'
    return result


def check_jar_localization(jar_path: Path) -> List[Dict[str, Any]]:
    """
    Проверяет локализацию в одном .jar/.zip файле.
    Поддерживает как JSON (Minecraft 1.13+), так и .lang (Minecraft 1.12.2 и ниже) форматы.
    Поддерживает следующие пути локализации:
    - assets/<modname>/lang/
    - assets/<modname>/language/
    - assets/<modname>/lang_nei/

    Сначала проверяет наличие перевода в папке TranslatedMods.
    Если не найден, проверяет встроенные файлы локализации в архиве.

    Returns:
        Список результатов проверки для каждого найденного мода внутри архива
    """
    mods_info = find_mod_lang_files_in_archive(jar_path)

    # Проверяем Patchouli гайдбуки (один раз на весь jar)
    patchouli_books = check_patchouli_books(jar_path)
    # Индексируем по mod_name для быстрого поиска
    patchouli_by_mod: Dict[str, List[Dict]] = {}
    for book in patchouli_books:
        patchouli_by_mod.setdefault(book['mod_name'], []).append(book)

    if not mods_info:
        # Если обычной локализации нет, но есть Patchouli — всё равно возвращаем результат
        if patchouli_books:
            results = []
            for mod_name, books in patchouli_by_mod.items():
                results.append({
                    "mod_name": f"{jar_path.name} ({mod_name})",
                    "status": "skipped",
                    "source": "none",
                    "ru_keys": 0,
                    "en_keys": 0,
                    "percentage": 0.0,
                    "missing_keys": [],
                    "extra_keys": [],
                    "identical_keys": [],
                    "identical_count": 0,
                    "error": "Только Patchouli гайдбук (нет стандартной локализации)",
                    "patchouli": books
                })
            return results
        return [{
            "mod_name": jar_path.name,
            "status": "skipped",
            "source": "none",
            "ru_keys": 0,
            "en_keys": 0,
            "percentage": 0.0,
            "missing_keys": [],
            "extra_keys": [],
            "identical_keys": [],
            "identical_count": 0,
            "error": "Папка локализации (lang/language/lang_nei) не найдена в архиве (мод пропущен)"
        }]

    results: List[Dict[str, Any]] = []
    for mod_name, mod_info in sorted(mods_info.items()):
        mod_result = check_mod_localization(jar_path, mod_name, mod_info)
        # Прикрепляем данные Patchouli если они есть для этого мода
        mod_result['patchouli'] = patchouli_by_mod.get(mod_name, [])
        results.append(mod_result)

    return results


def scan_jars_directory(base_path: Path, progress_callback=None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Сканирует директорию с .jar и .zip файлами модов и проверяет локализацию.
    
    Args:
        base_path: Директория для сканирования
        progress_callback: Функция обратного вызова для обновления прогресса (current, total)
    
    Returns:
        Словарь с результатами по категориям
    """
    results = {
        "full": [],
        "partial": [],
        "missing": [],
        "translated": [],
        "outdated": []
    }
    
    # Папки которые нужно исключить из рекурсивного сканирования
    EXCLUDED_DIRS = {".connector", "mcef-cache"}

    # Собираем все .jar и .zip файлы рекурсивно, пропуская исключённые папки
    mod_files: List[Path] = []
    for item in base_path.rglob("*"):
        # Пропускаем файлы внутри исключённых папок
        if any(part in EXCLUDED_DIRS for part in item.parts):
            continue
        if item.is_file() and item.suffix.lower() in (".jar", ".zip"):
            mod_files.append(item)
    
    if not mod_files:
        return results
    
    total = len(mod_files)
    
    # Используем многопоточность для ускорения обработки
    max_workers = CONFIG.get("max_workers", 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_jar = {executor.submit(check_jar_localization, mod): mod for mod in mod_files}

        for i, future in enumerate(as_completed(future_to_jar)):
            try:
                jar_results = future.result()
            except Exception as e:
                jar_path = future_to_jar[future]
                log_warning("Обработка мода", f"{jar_path.name}: {e}")
                if progress_callback:
                    progress_callback(i + 1, total)
                continue

            if progress_callback:
                progress_callback(i + 1, total)

            for jar_result in jar_results:
                # Пропускаем моды без файлов локализации (статус skipped)
                if jar_result["status"] == "skipped":
                    continue

                # Если хотя бы один гайдбук не переведён полностью — понижаем статус
                patchouli = jar_result.get("patchouli", [])
                if patchouli and any(b["status"] != "full" for b in patchouli):
                    if jar_result["status"] in ("full", "translated"):
                        jar_result["status"] = "partial"

                status = jar_result["status"]

                # Моды из TranslatedMods со 100% — отдельная категория
                # Моды из TranslatedMods с неполным переводом — "Устаревший перевод"
                if status == "translated":
                    results["translated"].append(jar_result)
                elif jar_result.get("source") == "translated_mods" and status == "partial":
                    results["outdated"].append(jar_result)
                elif status == "full":
                    results["full"].append(jar_result)
                elif status == "partial":
                    results["partial"].append(jar_result)
                elif status == "missing":
                    results["missing"].append(jar_result)
    
    # Сортируем результаты по имени мода
    for category in results:
        results[category].sort(key=lambda x: x["mod_name"])
    
    return results


class LocalizationCheckerGUI:
    """Графический интерфейс для проверки локализации."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Localization Checker")
        # Окно 900x700 обрезало тулбар (например, выпадающий список языка) при таком
        # количестве кнопок — увеличили стартовый размер и минимальный тоже.
        self.root.geometry("1200x750")
        self.root.minsize(1000, 650)
        
        self.current_path = None
        self.results = None
        self.status_message = ""
        self.status_color = "gray"
        self.dark_mode = False
        self._extract_dialog_win = None
        
        # Отслеживание сортировки для каждой таблицы
        self.sort_state = {
            "full": {"column": None, "reverse": False},
            "partial": {"column": None, "reverse": False},
            "missing": {"column": None, "reverse": False},
            "translated": {"column": None, "reverse": False},
            "outdated": {"column": None, "reverse": False}
        }
        
        self.setup_ui()
        if get_system_theme() == "dark":
            self.toggle_theme()

        self.refresh_errors_tab()

        # F5 — горячая перезагрузка (обновление)
        self.root.bind_all("<F5>", lambda e: self.refresh_check())

        # Если TranslatedMods путь уже настроен в конфиге — кнопка доступна сразу,
        # без необходимости сначала выбирать папку с модами
        if TRANSLATED_MODS_PATH is not None and TRANSLATED_MODS_PATH.exists():
            self.open_translated_btn.config(state=tk.NORMAL)

        # Восстанавливаем последнюю использованную папку с модами (без автозапуска сканирования)
        last_dir = CONFIG.get("last_directory")
        if last_dir:
            last_path = Path(last_dir)
            if last_path.exists() and last_path.is_dir():
                self._apply_directory(last_path)
    
    def setup_ui(self):
        """Настройка пользовательского интерфейса."""
        # Верхняя панель с кнопками
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.select_btn = ttk.Button(self.top_frame, text="📁 Выбрать папку с модами", command=self.select_directory, style="Custom.TButton")
        self.select_btn.pack(side=tk.LEFT, padx=5)
        
        self.check_btn = ttk.Button(self.top_frame, text="▶️ Проверить", command=self.start_check, state=tk.DISABLED, style="Custom.TButton")
        self.check_btn.pack(side=tk.LEFT, padx=5)

        self.refresh_btn = ttk.Button(self.top_frame, text="🔄 Обновить", command=self.refresh_check, state=tk.DISABLED, style="Custom.TButton")
        self.refresh_btn.pack(side=tk.LEFT, padx=5)
        
        self.export_btn = ttk.Button(self.top_frame, text="💾 Экспорт в JSON", command=self.export_results, state=tk.DISABLED, style="Custom.TButton")
        self.export_btn.pack(side=tk.LEFT, padx=5)

        self.open_translated_btn = ttk.Button(self.top_frame, text="📂 TranslatedMods", command=self.open_translated_mods_folder, state=tk.NORMAL, style="Custom.TButton")
        self.open_translated_btn.pack(side=tk.LEFT, padx=5)

        self.extract_stub_btn = ttk.Button(self.top_frame, text="📦 Извлечь для перевода", command=self.open_extract_stub_dialog, style="Custom.TButton")
        self.extract_stub_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка переключения темы (кнопку "Закрыть" убрали)
        self.theme_btn = ttk.Button(self.top_frame, text="🌙 Тёмная тема", command=self.toggle_theme, style="Custom.TButton")
        self.theme_btn.pack(side=tk.RIGHT, padx=5)

        # ── Выбор целевого языка перевода ────────────────────────────────
        self.lang_label = tk.Label(self.top_frame, text="Язык:")
        self.lang_label.pack(side=tk.RIGHT, padx=(5, 2))

        self._lang_code_by_display = {label: code for code, label in AVAILABLE_LANGUAGES}
        self._lang_display_by_code = {code: label for code, label in AVAILABLE_LANGUAGES}

        current_code = get_target_language()
        current_display = self._lang_display_by_code.get(current_code, current_code)

        self.lang_var = tk.StringVar(value=current_display)
        self.lang_combo = ttk.Combobox(
            self.top_frame, textvariable=self.lang_var,
            values=[label for _, label in AVAILABLE_LANGUAGES],
            state="normal", width=20, style="Custom.TCombobox"
        )
        self.lang_combo.pack(side=tk.RIGHT, padx=(0, 5))
        self.lang_combo.bind("<<ComboboxSelected>>", self.on_language_selected)
        self.lang_combo.bind("<Return>", self.on_language_selected)
        self.lang_combo.bind("<FocusOut>", self.on_language_selected)
        
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

        # Выпадающий список Combobox (popdown) — это отдельный Tk-виджет, ttk.Style на него
        # не влияет, поэтому красим через option_add. Обновляется при смене темы в toggle_theme().
        self.root.option_add('*TCombobox*Listbox.background', 'white')
        self.root.option_add('*TCombobox*Listbox.foreground', 'black')
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#3a7bd5')
        self.root.option_add('*TCombobox*Listbox.selectForeground', 'white')
        self.root.option_add('*TCombobox*Listbox.font', 'TkDefaultFont')

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

        self.search_label = tk.Label(self.search_frame, text="🔍 Поиск по имени:")
        self.search_label.pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.on_search_change)
        self.search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var, width=30, style="Custom.TEntry")
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_entry.bind("<Control-KeyPress>", self.on_search_entry_shortcut)
        
        # Основная область с результатами
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Вкладки для категорий
        self.notebook = ttk.Notebook(self.main_frame, style="Custom.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка "Полный перевод"
        self.full_frame = tk.Frame(self.notebook)
        self.notebook.add(self.full_frame, text="[100%] Полный")
        self.full_tree = self.create_treeview(self.full_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Совпд. с EN"])
        
        # Вкладка "Неполный перевод"
        self.partial_frame = tk.Frame(self.notebook)
        self.notebook.add(self.partial_frame, text="[Частично] Неполный")
        self.partial_tree = self.create_treeview(self.partial_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Не хватает", "Совпд. с EN"])
        
        # Вкладка "Отсутствует"
        self.missing_frame = tk.Frame(self.notebook)
        self.notebook.add(self.missing_frame, text="[Нет] Отсутствует")
        self.missing_tree = self.create_treeview(self.missing_frame, ["Мод", "Ключи EN", "Причина"])

        # Вкладка "Переведён" (только из TranslatedMods, 100%)
        self.translated_frame = tk.Frame(self.notebook)
        self.notebook.add(self.translated_frame, text="[Переведён]")
        self.translated_tree = self.create_treeview(self.translated_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Совпд. с EN"])

        # Вкладка "Устаревший перевод" (из TranslatedMods, но не 100%)
        self.outdated_frame = tk.Frame(self.notebook)
        self.notebook.add(self.outdated_frame, text="[Устарел]")
        self.outdated_tree = self.create_treeview(self.outdated_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Не хватает", "Совпд. с EN"])

        # Вкладка "Ошибки" — сводка предупреждений/ошибок парсинга, накопленных за сессию
        self.errors_frame = tk.Frame(self.notebook)
        self.notebook.add(self.errors_frame, text="⚠️ Ошибки (0)")
        self.errors_tree = self.create_errors_treeview(self.errors_frame)

        # Кнопка "Скопировать моды" — размещена НЕ как обычная вкладка/кнопка тулбара,
        # а поверх полосы вкладок Notebook (через place), в её правом углу — там же,
        # где сама пользователь её и просил разместить, "в одной плоскости с категориями".
        self.copy_mods_btn = ttk.Button(self.notebook, text="📋 Скопировать моды",
                                         command=self.copy_category_mods, style="Custom.TButton")
        self.copy_mods_btn.place(relx=1.0, x=-6, y=3, anchor="ne")

        # Глобальная привязка Ctrl+C, чтобы копирование работало независимо от фокуса и раскладки
        self.root.bind_all("<Control-KeyPress>", self.on_copy_shortcut)

        # Drag & drop: папку можно перетащить прямо в окно
        self._setup_drag_and_drop()
        
        # Статус бар - используем tk.Frame для поддержки смены цветов фона
        self.status_frame = tk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Начальный цвет статуса серый — но при смене темы будем учитывать self.status_color
        self.status_label = tk.Label(self.status_frame, text="Выберите папку с .jar или .zip файлами модов", fg="gray")
        self.status_label.pack(side=tk.LEFT)
    
    def create_errors_treeview(self, parent):
        """Создаёт вкладку 'Ошибки': панель с кнопками + таблицу накопленных предупреждений/ошибок."""
        toolbar = tk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=4, pady=(4, 0))
        self.errors_toolbar = toolbar

        copy_all_btn = ttk.Button(toolbar, text="📋 Скопировать всё",
                                   command=self.copy_all_errors, style="Custom.TButton")
        copy_all_btn.pack(side=tk.LEFT, padx=(0, 5))

        clear_btn = ttk.Button(toolbar, text="🗑️ Очистить",
                                command=self.clear_errors_tab, style="Custom.TButton")
        clear_btn.pack(side=tk.LEFT)

        hint = tk.Label(toolbar, text=f"Полная история сохраняется в {LOG_FILE_NAME}", fg="gray")
        hint.pack(side=tk.RIGHT)
        self.errors_hint_label = hint

        columns = ["Время", "Контекст", "Сообщение"]
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended", style="Custom.Treeview")
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=90 if col == "Время" else (160 if col == "Контекст" else 480))

        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=4)

        self.setup_tree_tags(tree)
        tree.bind("<Control-KeyPress>", lambda e: self.on_errors_copy_shortcut(e, tree))

        return tree

    def refresh_errors_tab(self):
        """Перечитывает накопленные ошибки/предупреждения и обновляет вкладку 'Ошибки'."""
        if not hasattr(self, "errors_tree"):
            return
        for item in self.errors_tree.get_children():
            self.errors_tree.delete(item)

        entries = get_error_log()
        for entry in entries:
            self.errors_tree.insert("", tk.END, values=(entry["time"], entry["context"], entry["message"]))

        tab_index = self.notebook.index(self.errors_frame)
        self.notebook.tab(tab_index, text=f"⚠️ Ошибки ({len(entries)})")

    def copy_all_errors(self):
        """Копирует все строки вкладки 'Ошибки' в буфер обмена."""
        entries = get_error_log()
        if not entries:
            self.show_temporary_status("Ошибок нет — нечего копировать")
            return
        lines = [f"[{e['time']}] {e['context']}: {e['message']}" for e in entries]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.show_temporary_status(f"Скопировано {len(lines)} записей")

    def clear_errors_tab(self):
        """Очищает накопленный список ошибок/предупреждений (историю в лог-файле не трогает)."""
        clear_error_log()
        self.refresh_errors_tab()
        self.show_temporary_status("Список ошибок очищен")

    def on_errors_copy_shortcut(self, event, tree):
        """Ctrl+C/Ctrl+A на вкладке 'Ошибки', независимо от раскладки клавиатуры."""
        if not (event.state & 0x4):
            return None

        if self.is_select_all_shortcut(event):
            for item in tree.get_children():
                tree.selection_add(item)
            return "break"

        if self.is_copy_shortcut(event):
            selected = tree.selection()
            if selected:
                lines = []
                for item_id in selected:
                    values = tree.item(item_id).get("values", [])
                    if values:
                        lines.append(f"[{values[0]}] {values[1]}: {values[2]}")
                if lines:
                    self.root.clipboard_clear()
                    self.root.clipboard_append("\n".join(lines))
                    self.show_temporary_status(f"Скопировано {len(lines)} записей")
            return "break"
        return None

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
        tree.bind("<Button-3>", lambda e, t=tree: self.show_context_menu(e, t))

        return tree
    
    def get_patchouli_badge(self, mod: Dict) -> str:
        """Возвращает иконку статуса Patchouli гайдбука для отображения в таблице."""
        books = mod.get("patchouli", [])
        if not books:
            return ""
        # Берём наихудший статус среди всех книг
        if any(b["status"] == "missing" for b in books):
            return "  📖❌"
        if any(b["status"] == "partial" for b in books):
            return "  📖⚠️"
        return "  📖✅"

    def get_tree_category(self, tree):
        """Определяет категорию дерева по объекту."""
        if tree == self.full_tree:
            return "full"
        elif tree == self.partial_tree:
            return "partial"
        elif tree == self.missing_tree:
            return "missing"
        elif tree == self.translated_tree:
            return "translated"
        elif tree == self.outdated_tree:
            return "outdated"
        return None

    def get_source_tag(self, source: str) -> str:
        """Возвращает тег строки для указанного источника перевода."""
        if source == "jar":
            return "source_jar"
        if source == "translated_mods":
            return "source_translated_mods"
        return "source_missing"

    def get_percentage_tag(self, percentage: float) -> str:
        """Возвращает тег строки в зависимости от процента перевода (для вкладки «Неполный»)."""
        if percentage >= 75:
            return "pct_high"
        if percentage >= 50:
            return "pct_mid"
        return "pct_low"

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
                self.search_label.config(bg=dark_bg, fg=dark_fg)
            except Exception:
                pass
            try:
                self.lang_label.config(bg=dark_bg, fg=dark_fg)
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
                self.translated_frame.config(bg=dark_bg)
                self.outdated_frame.config(bg=dark_bg)
                self.errors_frame.config(bg=dark_bg)
                self.errors_toolbar.config(bg=dark_bg)
                self.errors_hint_label.config(bg=dark_bg, fg="#a8a8a8")
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

                # Выпадающий список (popdown) Combobox — отдельный Tk-виджет, красим отдельно
                self.root.option_add('*TCombobox*Listbox.background', '#1e1e1e')
                self.root.option_add('*TCombobox*Listbox.foreground', dark_fg)
                self.root.option_add('*TCombobox*Listbox.selectBackground', '#3a7bd5')
                self.root.option_add('*TCombobox*Listbox.selectForeground', 'white')
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
                self.search_label.config(bg=default_bg, fg="black")
            except Exception:
                pass
            try:
                self.lang_label.config(bg=default_bg, fg="black")
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
                self.translated_frame.config(bg=default_bg)
                self.outdated_frame.config(bg=default_bg)
                self.errors_frame.config(bg=default_bg)
                self.errors_toolbar.config(bg=default_bg)
                self.errors_hint_label.config(bg=default_bg, fg="gray")
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

                # Выпадающий список (popdown) Combobox — возвращаем светлые цвета
                self.root.option_add('*TCombobox*Listbox.background', 'white')
                self.root.option_add('*TCombobox*Listbox.foreground', 'black')
                self.root.option_add('*TCombobox*Listbox.selectBackground', '#3a7bd5')
                self.root.option_add('*TCombobox*Listbox.selectForeground', 'white')
            except Exception:
                pass
        
        # Пересоздаём теги для всех деревьев при смене темы — независимо от того,
        # есть ли уже результаты сканирования (баг: без этого теги оставались
        # от предыдущей темы, если тема менялась ДО первого сканирования)
        self.setup_tree_tags(self.full_tree)
        self.setup_tree_tags(self.partial_tree)
        self.setup_tree_tags(self.missing_tree)
        self.setup_tree_tags(self.translated_tree)
        self.setup_tree_tags(self.outdated_tree)
        self.setup_tree_tags(self.errors_tree)
        if self.results:
            # Пересоздаём результаты чтобы применить новые теги
            self.apply_filter()

        # Обновляем все открытые окна деталей
        for cb in getattr(self, "_detail_theme_callbacks", []):
            try:
                cb()
            except Exception:
                pass

    def setup_tree_tags(self, tree):
        """Создает теги для раскрашивания строк по источнику перевода."""
        theme = "dark" if self.dark_mode else "light"
        colors = get_row_colors(theme)
        
        jar_bg = colors.get("jar", "#d4f4dd")
        translated_bg = colors.get("translated_mods", "#9dcafa")
        missing_bg = colors.get("missing", "#ffe4e1")
        
        # Фон поля дерева: тёмный в тёмной теме, белый в светлой
        field_bg = "#1e1e1e" if self.dark_mode else "white"

        # Применяем фон для области Treeview (чтобы убрать серые полосы в тёмной теме)
        try:
            tree.configure(background=field_bg, fieldbackground=field_bg)
        except Exception:
            pass

        tree.tag_configure("source_jar", background=jar_bg, foreground="black")
        tree.tag_configure("source_translated_mods", background=translated_bg, foreground="black")
        tree.tag_configure("source_missing", background=missing_bg, foreground="black")

        # Теги подсветки по проценту перевода — используются только во вкладке «Неполный»
        if self.dark_mode:
            pct_high_bg = "#6b6b2e"   # 75-99% — жёлтый (приглушённый для тёмной темы)
            pct_mid_bg  = "#7a5a2e"   # 50-74% — оранжевый
            pct_low_bg  = "#7a3b3b"   # < 50%  — красный
        else:
            pct_high_bg = "#fff3b0"   # 75-99% — жёлтый
            pct_mid_bg  = "#ffd9a0"   # 50-74% — оранжевый
            pct_low_bg  = "#ffc2c2"   # < 50%  — красный

        tree.tag_configure("pct_high", background=pct_high_bg, foreground="black")
        tree.tag_configure("pct_mid",  background=pct_mid_bg,  foreground="black")
        tree.tag_configure("pct_low",  background=pct_low_bg,  foreground="black")

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
        """Возвращает tree активной вкладки, только если в нём есть выделение."""
        tab_index = self.notebook.index(self.notebook.select())
        tab_to_tree = {
            0: self.full_tree,
            1: self.partial_tree,
            2: self.missing_tree,
            3: self.translated_tree,
            4: self.outdated_tree,
        }
        active_tree = tab_to_tree.get(tab_index)
        if active_tree and active_tree.selection():
            return active_tree
        return None

    def get_active_category(self):
        """Возвращает (tree, category) для активной вкладки результатов, либо (None, None)
        если открыта, например, вкладка 'Ошибки'."""
        tab_index = self.notebook.index(self.notebook.select())
        tab_to_tree = {
            0: (self.full_tree, "full"),
            1: (self.partial_tree, "partial"),
            2: (self.missing_tree, "missing"),
            3: (self.translated_tree, "translated"),
            4: (self.outdated_tree, "outdated"),
        }
        return tab_to_tree.get(tab_index, (None, None))

    def copy_category_mods(self):
        """Копирует .jar/.zip файлы модов из активной вкладки результатов в выбранную папку."""
        if not self.current_path:
            messagebox.showwarning("Скопировать моды", "Сначала выберите и проверьте папку с модами.")
            return

        tree, category = self.get_active_category()
        if tree is None:
            messagebox.showwarning(
                "Скопировать моды",
                "Переключитесь на одну из вкладок с результатами (например, «Полный» или «Неполный») — "
                "во вкладке «Ошибки» копировать нечего."
            )
            return

        # Берём то, что реально показано в таблице сейчас — то есть с учётом поиска/фильтра,
        # раз пользователь именно это видит перед собой.
        jar_filenames = set()
        for item_id in tree.get_children():
            values = tree.item(item_id).get("values", [])
            if not values:
                continue
            mod_name = str(values[0])
            # Убираем бейдж Patchouli (например "  📖✅"), добавленный только для отображения
            mod_name = mod_name.split("  📖")[0] if "  📖" in mod_name else mod_name
            # Формат имени — "archive.jar (assets_namespace)"; берём часть до скобки
            jar_filename = mod_name.split(" (")[0].strip()
            if jar_filename:
                jar_filenames.add(jar_filename)

        if not jar_filenames:
            messagebox.showinfo(
                "Скопировать моды",
                f"Во вкладке «{CATEGORY_LABELS.get(category, category)}» сейчас нет ни одного мода для копирования."
            )
            return

        dest_dir = filedialog.askdirectory(
            title=f"Куда скопировать моды ({CATEGORY_LABELS.get(category, category)})"
        )
        if not dest_dir:
            return
        dest_path = Path(dest_dir)

        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Скопировать моды", f"Не удалось создать папку назначения:\n{e}")
            return

        copied = 0
        not_found = []
        errors = []

        for jar_filename in sorted(jar_filenames):
            src_path = self.current_path / jar_filename
            if not src_path.exists():
                # Резервный поиск: мод может лежать во вложенной подпапке
                # (основное сканирование идёт рекурсивно через rglob).
                matches = list(self.current_path.rglob(jar_filename))
                src_path = matches[0] if matches else None

            if src_path is None or not src_path.exists():
                not_found.append(jar_filename)
                continue

            if src_path.resolve() == (dest_path / src_path.name).resolve():
                # Источник и назначение совпадают — копировать некуда, просто пропускаем
                copied += 1
                continue

            try:
                shutil.copy2(src_path, dest_path / src_path.name)
                copied += 1
            except Exception as e:
                errors.append(f"{jar_filename}: {e}")

        summary = f"Скопировано модов: {copied} из {len(jar_filenames)}\nПапка: {dest_path}"
        if not_found:
            preview = ", ".join(not_found[:5]) + ("…" if len(not_found) > 5 else "")
            summary += f"\n\nНе найдены на диске ({len(not_found)}): {preview}"
        if errors:
            summary += "\n\nОшибки копирования:\n" + "\n".join(errors[:5])

        if not_found or errors:
            messagebox.showwarning("Скопировать моды", summary)
        else:
            messagebox.showinfo("Скопировать моды", summary)
        self.show_temporary_status(f"Скопировано модов: {copied}/{len(jar_filenames)}")

    def is_select_all_shortcut(self, event):
        """Проверяет, что нажат Ctrl+A на любой раскладке клавиатуры."""
        key = (event.keysym or "").lower()
        char = (event.char or "").lower()
        keysym_num = getattr(event, "keysym_num", None)
        keycode = getattr(event, "keycode", None)

        # На кириллической раскладке (ЙЦУКЕН) физическая клавиша A выдаёт "ф" (cyrillic_ef)
        if key in ("a", "а", "cyrillic_ef"):
            return True
        if char in ("a", "а", "ф"):
            return True
        if keysym_num in (ord("a"), ord("A"), ord("а"), ord("А")):
            return True
        # Виртуальные коды физической клавиши A: 65 (Windows VK_A), 38 (X11), 0 (macOS)
        if keycode in (65, 38, 0):
            return True
        return False

    def on_search_entry_shortcut(self, event):
        """Обрабатывает Ctrl+A и Ctrl+C в строке поиска независимо от раскладки клавиатуры."""
        if not (event.state & 0x4):
            return None

        if self.is_select_all_shortcut(event):
            self.search_entry.select_range(0, tk.END)
            self.search_entry.icursor(tk.END)
            return "break"

        if self.is_copy_shortcut(event):
            try:
                selected = self.search_entry.selection_get()
            except tk.TclError:
                selected = None
            if selected:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
            return "break"

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

    def show_context_menu(self, event, tree):
        """Показывает контекстное меню при нажатии ПКМ."""
        # Выделяем строку под курсором
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item)

        bg = "#2f2f2f" if self.dark_mode else "#ffffff"
        fg = "white" if self.dark_mode else "black"
        abg = "#393939" if self.dark_mode else "#e0e0e0"

        menu = tk.Menu(self.root, tearoff=0, bg=bg, fg=fg, activebackground=abg, activeforeground=fg)
        menu.add_command(label="📋 Скопировать", command=lambda: self.copy_selected_mod_name(tree))
        menu.add_command(label="📌 Вставить", command=lambda: self._paste_from_clipboard(tree))
        menu.add_separator()
        menu.add_command(label="📁 Расположение", command=lambda: self.open_mod_location(tree))
        menu.add_command(label="🔍 Подробнее", command=lambda: self.show_details(tree))
        menu.tk_popup(event.x_root, event.y_root)

    def open_mod_location(self, tree):
        """Открывает папку с .jar файлом мода в проводнике и выделяет файл."""
        selection = tree.selection()
        if not selection:
            return
        mod_name = tree.item(selection[0])["values"][0]
        # mod_name имеет формат "filename.jar (assets_name)" — берём часть до пробела
        jar_filename = mod_name.split(" (")[0]
        jar_path = self.current_path / jar_filename
        if not jar_path.exists():
            messagebox.showwarning("Расположение", f"Файл не найден:\n{jar_path}")
            return
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(jar_path)])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(jar_path)])
        else:
            subprocess.Popen(["xdg-open", str(jar_path.parent)])

    def _paste_from_clipboard(self, tree):
        """Вставляет текст из буфера обмена в строку поиска."""
        try:
            text = self.root.clipboard_get()
            current = self.search_var.get()
            self.search_var.set(current + text)
        except tk.TclError:
            pass

    def on_copy_shortcut(self, event):
        """Обрабатывает Ctrl+C на разных раскладках клавиатуры."""
        if not (event.state & 0x4):
            return None

        if self.is_copy_shortcut(event):
            target_tree = self.get_tree_with_selection()
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
            numeric_columns = ["%", "Ключи RU", "Ключи EN", "Не хватает", "Совпд. с EN"]
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

        elif column_name == "Совпд. с EN":
            # Сортировка по количеству ключей, совпадающих с английским значением
            return sorted(mods_list, key=lambda x: x.get("identical_count", 0), reverse=reverse)
        
        elif column_name == "Причина":
            # Сортировка по причине (для вкладки "Отсутствует")
            return sorted(mods_list, key=lambda x: x.get("error", ""), reverse=reverse)
        
        return mods_list
    
    def _setup_drag_and_drop(self):
        """Регистрирует обработчик drag & drop (требует tkinterdnd2)."""
        try:
            self.root.drop_target_register("DND_Files")
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            # tkinterdnd2 не установлен — drag & drop недоступен, ничего страшного
            pass

    def _on_drop(self, event):
        """Обрабатывает перетаскивание папки/файла в окно."""
        raw = event.data.strip()
        # tkinterdnd2 может обернуть путь с пробелами в фигурные скобки
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        path = Path(raw)
        if path.is_file():
            path = path.parent
        if path.is_dir():
            self._apply_directory(path)

    def _apply_directory(self, path: Path):
        """Устанавливает выбранную папку, ищет TranslatedMods и запоминает путь для следующего запуска."""
        self.current_path = path
        self.select_btn.config(text=f"📁 {self.current_path.name}")
        self.check_btn.config(state=tk.NORMAL)
        self.refresh_btn.config(state=tk.NORMAL)

        # Сохраняем последнюю папку в конфиг, чтобы подставить её при следующем запуске
        CONFIG["last_directory"] = str(self.current_path)
        save_config()

        translated_mods_path = find_translated_mods_directory(self.current_path)
        if translated_mods_path:
            set_translated_mods_path(translated_mods_path)
            self.open_translated_btn.config(state=tk.NORMAL)
            self.set_status_message(
                f"Папка: {self.current_path} | TranslatedMods найден: {translated_mods_path}",
                color="green"
            )
        else:
            self.open_translated_btn.config(state=tk.DISABLED)
            self.set_status_message(
                f"Папка: {self.current_path} | TranslatedMods не найден",
                color="green"
            )

    def refresh_check(self):
        """Повторно сканирует уже выбранную папку без диалога."""
        if self.current_path:
            self.start_check()

    def on_language_selected(self, event=None):
        """Обрабатывает выбор целевого языка перевода (из списка или введённого вручную кода)."""
        typed = self.lang_var.get().strip()
        if not typed:
            return

        # Если это одно из готовых значений — берём код языка из него,
        # иначе считаем, что пользователь ввёл код языка сам (например "cs_cz").
        new_code = self._lang_code_by_display.get(typed, normalize_lang_code(typed))

        if new_code == get_target_language():
            return

        set_target_language(new_code)
        CONFIG["target_language"] = get_target_language()
        save_config()

        display = self._lang_display_by_code.get(get_target_language(), get_target_language())
        self.lang_var.set(display)

        self.set_status_message(
            f"Целевой язык перевода: {get_target_language()}. Нажмите «Обновить» (F5), чтобы применить к текущей папке.",
            color="blue"
        )

    def open_translated_mods_folder(self):
        """Открывает папку TranslatedMods в проводнике. Не требует предварительного выбора папки с модами —
        если путь ещё не настроен, предлагает выбрать его вручную."""
        global TRANSLATED_MODS_PATH

        if TRANSLATED_MODS_PATH is None or not TRANSLATED_MODS_PATH.exists():
            directory = filedialog.askdirectory(title="Выберите папку TranslatedMods")
            if not directory:
                return
            set_translated_mods_path(Path(directory))
            self.open_translated_btn.config(state=tk.NORMAL)

        if platform.system() == "Windows":
            subprocess.Popen(["explorer", str(TRANSLATED_MODS_PATH)])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(TRANSLATED_MODS_PATH)])
        else:
            subprocess.Popen(["xdg-open", str(TRANSLATED_MODS_PATH)])

    def open_extract_stub_dialog(self):
        """Открывает окно для извлечения en_us + перевода (включая .lang и Patchouli-гайдбуки)
        из модов в отдельную папку для перевода.

        Повторный вызов (например, второй клик по кнопке) не создаёт новое окно — если оно уже
        открыто (в том числе свёрнуто), просто разворачивает и поднимает наверх старое."""
        existing = getattr(self, "_extract_dialog_win", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
            self._extract_dialog_win = None

        bg      = "#1e1e1e" if self.dark_mode else "#f5f5f5"
        fg      = "#e0e0e0" if self.dark_mode else "#1a1a1a"
        hdr_bg  = "#2a2a2a" if self.dark_mode else "#c8d0de"
        entry_bg = "#252525" if self.dark_mode else "#ffffff"
        box_bg  = "#252525" if self.dark_mode else "#ffffff"

        win = tk.Toplevel(self.root)
        self._extract_dialog_win = win
        win.title("Извлечь для перевода")
        win.geometry("640x480")
        win.minsize(520, 380)
        win.configure(bg=bg)
        win.resizable(True, True)

        # Папки запоминаются между запусками: сначала берём то, что использовалось
        # в диалоге извлечения в прошлый раз, а если их ещё не было — текущую папку модов.
        default_source = CONFIG.get("last_extract_source") or (str(self.current_path) if self.current_path else "")
        default_output = CONFIG.get("last_extract_output", "")

        source_var = tk.StringVar(value=default_source)
        output_var = tk.StringVar(value=default_output)

        title_label = tk.Label(win, text=f"📦 Извлечь en_us / {get_target_language()} из модов для перевода",
                                font=("Segoe UI", 11, "bold"), bg=bg, fg=fg)
        title_label.pack(anchor="w", padx=14, pady=(14, 2))

        subtitle_label = tk.Label(win, text="Поддерживает .lang файлы (старые моды) и гайдбуки Patchouli",
                                   font=("Segoe UI", 8), bg=bg, fg=("#a8a8a8" if self.dark_mode else "#5a5a5a"))
        subtitle_label.pack(anchor="w", padx=14, pady=(0, 8))

        row_widgets = []  # (row_frame, label, entry, button) — для обновления темы

        def make_row(label_text, var):
            row = tk.Frame(win, bg=bg)
            row.pack(fill=tk.X, padx=14, pady=4)
            label = tk.Label(row, text=label_text, width=18, anchor="w", bg=bg, fg=fg,
                              font=("Segoe UI", 9))
            label.pack(side=tk.LEFT)
            entry = tk.Entry(row, textvariable=var, bg=entry_bg, fg=fg,
                              insertbackground=fg, relief=tk.FLAT)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

            def browse():
                d = filedialog.askdirectory(title=label_text)
                if d:
                    var.set(d)

            btn = tk.Button(row, text="Обзор...", command=browse,
                             bg=hdr_bg, fg=fg, relief=tk.FLAT, padx=8)
            btn.pack(side=tk.RIGHT)
            row_widgets.append((row, label, entry, btn))
            return entry

        make_row("Папка с модами:", source_var)
        make_row("Сохранить в:", output_var)

        status_label = tk.Label(win, text="", bg=bg, fg=fg, font=("Segoe UI", 9), anchor="w")
        status_label.pack(fill=tk.X, padx=14, pady=(8, 0))

        progress_bar = ttk.Progressbar(win, mode="determinate", style="Custom.Horizontal.TProgressbar")
        progress_bar.pack(fill=tk.X, padx=14, pady=(6, 0))

        # ── Лог обработки: список модов по мере извлечения, с поддержкой копирования ──
        log_top = tk.Frame(win, bg=bg)
        log_top.pack(fill=tk.X, padx=14, pady=(10, 2))
        log_label = tk.Label(log_top, text="Ход извлечения:", font=("Segoe UI", 9, "bold"), bg=bg, fg=fg)
        log_label.pack(side=tk.LEFT)

        def copy_all_log():
            lines = list(log_listbox.get(0, tk.END))
            if not lines:
                return
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))
            self.show_temporary_status(f"Скопировано {len(lines)} строк")

        copy_log_btn = tk.Button(log_top, text="📋 Скопировать всё", font=("Segoe UI", 8),
                                  bg=hdr_bg, fg=fg, relief=tk.FLAT, padx=6, pady=1,
                                  command=copy_all_log)
        copy_log_btn.pack(side=tk.RIGHT)

        log_frame = tk.Frame(win, bg=box_bg, relief=tk.FLAT, bd=1)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        log_scroll = tk.Scrollbar(log_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_listbox = tk.Listbox(
            log_frame, font=("Consolas", 9), bg=box_bg, fg=fg,
            selectbackground="#3a7bd5", selectforeground="white",
            relief=tk.FLAT, bd=0, yscrollcommand=log_scroll.set,
            activestyle="none", exportselection=False
        )
        log_listbox.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=log_listbox.yview)

        def _log_copy_shortcut(event):
            # Ctrl+A / Ctrl+C работают независимо от раскладки клавиатуры (как и в остальной программе)
            if not (event.state & 0x4):
                return None
            if self.is_select_all_shortcut(event):
                log_listbox.select_set(0, tk.END)
                return "break"
            if self.is_copy_shortcut(event):
                selected = log_listbox.curselection()
                lines = [log_listbox.get(i) for i in selected] if selected else list(log_listbox.get(0, tk.END))
                if lines:
                    self.root.clipboard_clear()
                    self.root.clipboard_append("\n".join(lines))
                    self.show_temporary_status(f"Скопировано {len(lines)} строк")
                return "break"
            return None

        log_listbox.bind("<Control-KeyPress>", _log_copy_shortcut)

        def append_log(line: str):
            log_listbox.insert(tk.END, line)
            log_listbox.see(tk.END)

        btn_row = tk.Frame(win, bg=bg)
        btn_row.pack(fill=tk.X, padx=14, pady=14)

        start_btn = tk.Button(btn_row, text="▶️ Начать", bg=hdr_bg, fg=fg, relief=tk.FLAT, padx=10, pady=4)
        start_btn.pack(side=tk.LEFT)
        close_btn = tk.Button(btn_row, text="Закрыть", bg=hdr_bg, fg=fg, relief=tk.FLAT, padx=10, pady=4)
        close_btn.pack(side=tk.RIGHT)

        def on_start():
            source_str = source_var.get().strip()
            output_str = output_var.get().strip()

            if not source_str or not Path(source_str).is_dir():
                messagebox.showwarning("Извлечь для перевода", "Укажите существующую папку с модами.")
                return
            if not output_str:
                messagebox.showwarning("Извлечь для перевода", "Укажите папку для сохранения результата.")
                return

            output_path = Path(output_str)
            output_path.mkdir(parents=True, exist_ok=True)

            # Запоминаем папки для следующего открытия этого диалога
            CONFIG["last_extract_source"] = source_str
            CONFIG["last_extract_output"] = output_str
            save_config()

            start_btn.config(state=tk.DISABLED)
            status_label.config(text="Поиск модов...")
            progress_bar['value'] = 0
            log_listbox.delete(0, tk.END)

            threading.Thread(
                target=self._run_extract_stub_workflow,
                args=(Path(source_str), output_path, win, start_btn, status_label, progress_bar, append_log),
                daemon=True
            ).start()

        start_btn.config(command=on_start)

        # ── Живое обновление темы, пока окно открыто ────────────────────────
        def _on_theme_change():
            new_bg     = "#1e1e1e" if self.dark_mode else "#f5f5f5"
            new_fg     = "#e0e0e0" if self.dark_mode else "#1a1a1a"
            new_hdr_bg = "#2a2a2a" if self.dark_mode else "#c8d0de"
            new_entry_bg = "#252525" if self.dark_mode else "#ffffff"
            new_muted  = "#a8a8a8" if self.dark_mode else "#5a5a5a"

            win.configure(bg=new_bg)
            title_label.configure(bg=new_bg, fg=new_fg)
            subtitle_label.configure(bg=new_bg, fg=new_muted)
            status_label.configure(bg=new_bg, fg=new_fg)
            btn_row.configure(bg=new_bg)
            start_btn.configure(bg=new_hdr_bg, fg=new_fg)
            close_btn.configure(bg=new_hdr_bg, fg=new_fg)
            log_top.configure(bg=new_bg)
            log_label.configure(bg=new_bg, fg=new_fg)
            copy_log_btn.configure(bg=new_hdr_bg, fg=new_fg)
            log_frame.configure(bg=new_entry_bg)
            log_listbox.configure(bg=new_entry_bg, fg=new_fg)

            for row, label, entry, btn in row_widgets:
                row.configure(bg=new_bg)
                label.configure(bg=new_bg, fg=new_fg)
                entry.configure(bg=new_entry_bg, fg=new_fg, insertbackground=new_fg)
                btn.configure(bg=new_hdr_bg, fg=new_fg)

        self._detail_theme_callbacks = getattr(self, "_detail_theme_callbacks", [])
        self._detail_theme_callbacks.append(_on_theme_change)

        def _on_close():
            if _on_theme_change in self._detail_theme_callbacks:
                self._detail_theme_callbacks.remove(_on_theme_change)
            self._extract_dialog_win = None
            win.destroy()

        close_btn.config(command=_on_close)
        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _run_extract_stub_workflow(self, source_dir: Path, output_dir: Path, win, start_btn, status_label,
                                    progress_bar, append_log: Optional[Callable[[str], None]] = None):
        """Выполняется в фоновом потоке: сканирует моды, спрашивает про перезапись один раз, извлекает файлы."""
        mod_files = find_all_mod_files(source_dir)

        if not mod_files:
            self.root.after(0, lambda: (
                status_label.config(text="Моды (.jar/.zip) не найдены в указанной папке."),
                start_btn.config(state=tk.NORMAL)
            ))
            return

        tasks = build_translation_stub_tasks(mod_files)
        collisions = count_existing_ru_translations(tasks, output_dir) if tasks else 0

        # Если есть коллизии — спрашиваем один раз, применяем результат ко всем модам и гайдбукам
        overwrite_holder = {"value": True}
        if collisions > 0:
            answered = threading.Event()

            def ask():
                overwrite_holder["value"] = messagebox.askyesno(
                    "Файлы перевода уже существуют",
                    f"В выходной папке уже есть перевод для {collisions} мод(ов).\n"
                    f"Перезаписать их?"
                )
                answered.set()

            self.root.after(0, ask)
            answered.wait()

        overwrite_existing = overwrite_holder["value"]

        def progress_cb(current, total_count):
            self.root.after(0, lambda: (
                progress_bar.config(value=(current / total_count) * 100),
                status_label.config(text=f"Обработано: {current}/{total_count}")
            ))

        def phase_cb(text):
            self.root.after(0, lambda: status_label.config(text=text))

        item_status_icons = {
            'created_from_mod': '✅', 'created_stub': '📝',
            'skipped': '⏭️', 'error': '❌', 'ok': '📖',
        }

        def item_cb(result: Dict[str, Any]):
            if append_log is None:
                return
            if result.get('phase') == 'patchouli':
                icon = item_status_icons.get(result['status'], '📖')
                line = (f"{icon} {result['mod_name']}  [Patchouli] "
                        f"en:{result.get('copied_en', 0)} "
                        f"ru(мод):{result.get('copied_ru_from_mod', 0)} "
                        f"заготовок:{result.get('created_ru_stub', 0)}")
            else:
                icon = item_status_icons.get(result['status'], '•')
                status_text = {
                    'created_from_mod': 'перевод взят из мода',
                    'created_stub': 'создана заготовка (копия en_us)',
                    'skipped': 'пропущено (уже существовало)',
                    'error': f"ошибка: {result.get('error', '?')}",
                }.get(result['status'], result['status'])
                line = f"{icon} {result['mod_name']}  —  {status_text}"
            self.root.after(0, lambda: append_log(line))

        stats = extract_all_translation_stubs(source_dir, output_dir, overwrite_existing,
                                               progress_cb, phase_cb, item_cb)

        def show_summary():
            start_btn.config(state=tk.NORMAL)
            status_label.config(text="Готово")
            pb = stats.get('patchouli', {})
            summary = (
                f"Всего модов обработано: {stats['total']}\n"
                f"Извлечён перевод из мода: {stats['created_from_mod']}\n"
                f"Создана заготовка (копия en_us): {stats['created_stub']}\n"
                f"Пропущено (уже существовало): {stats['skipped']}\n"
                f"Ошибок: {stats['error']}\n"
                f"\n"
                f"📖 Patchouli-гайдбуки:\n"
                f"Страниц скопировано (en_us): {pb.get('copied_en', 0)}\n"
                f"Переводов взято из мода: {pb.get('copied_ru_from_mod', 0)}\n"
                f"Заготовок создано: {pb.get('created_ru_stub', 0)}\n"
                f"Пропущено: {pb.get('skipped_ru', 0)}\n"
                f"Ошибок: {pb.get('error', 0)}"
            )
            all_errors = stats['errors'] + pb.get('errors', [])
            if all_errors:
                summary += "\n\nОшибки:\n" + "\n".join(all_errors[:5])
            messagebox.showinfo("Извлечение завершено", summary)

        self.root.after(0, show_summary)

    def select_directory(self):
        """Открывает диалог выбора директории."""
        directory = filedialog.askdirectory(title="Выберите папку с .jar файлами модов")
        if directory:
            self._apply_directory(Path(directory))
    
    def update_progress(self, current, total):
        """Планирует обновление индикатора прогресса в главном потоке (thread-safe)."""
        self.root.after(0, self._apply_progress, current, total)

    def _apply_progress(self, current, total):
        """Реально обновляет виджеты прогресса — должен вызываться только из главного потока."""
        progress = (current / total) * 100
        self.progress_bar['value'] = progress
        self.progress_label.config(text=f"Обработано: {current}/{total}")
        self.root.update_idletasks()
    
    def start_check(self):
        """Запускает проверку локализации."""
        if not self.current_path:
            messagebox.showwarning("Предупреждение", "Сначала выберите папку с модами")
            return

        # Не запускать новое сканирование если уже идёт
        if getattr(self, '_scanning', False):
            return

        self._scanning = True
        self.check_btn.config(state=tk.DISABLED)
        self.refresh_btn.config(state=tk.DISABLED)
        self.select_btn.config(state=tk.DISABLED)
        self.export_btn.config(state=tk.DISABLED)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Начало проверки...")

        # Начинаем новое сканирование с чистого листа ошибок — старые уже осели в лог-файле
        clear_error_log()
        self.refresh_errors_tab()
        
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

        # На случай, если тема менялась до сканирования — обновляем теги перед показом
        self.setup_tree_tags(self.full_tree)
        self.setup_tree_tags(self.partial_tree)
        self.setup_tree_tags(self.missing_tree)
        self.setup_tree_tags(self.translated_tree)
        self.setup_tree_tags(self.outdated_tree)

        # Применяем фильтр для отображения результатов
        self.apply_filter()

        # Обновляем названия вкладок с количеством
        self.notebook.tab(0, text=f"[100%] Полный ({len(self.results['full'])})")
        self.notebook.tab(1, text=f"[Частично] Неполный ({len(self.results['partial'])})")
        self.notebook.tab(2, text=f"[Нет] Отсутствует ({len(self.results['missing'])})")
        self.notebook.tab(3, text=f"[Переведён] ({len(self.results.get('translated', []))})")
        self.notebook.tab(4, text=f"[Устарел] ({len(self.results.get('outdated', []))})")
        
        # Обновляем статус
        translated_count = len(self.results.get("translated", []))
        outdated_count = len(self.results.get("outdated", []))
        total = len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"]) + translated_count + outdated_count
        self.set_status_message(
            f"Всего: {total} | [100%]: {len(self.results['full'])} | [Частично]: {len(self.results['partial'])} | [Нет]: {len(self.results['missing'])} | Переведён: {translated_count} | Устарел: {outdated_count}",
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
        self._scanning = False
        self.check_btn.config(state=tk.NORMAL)
        self.refresh_btn.config(state=tk.NORMAL)
        self.select_btn.config(state=tk.NORMAL)
        self.export_btn.config(state=tk.NORMAL)
        self.progress_label.config(text="Проверка завершена")
        self.refresh_errors_tab()
    
    def on_search_change(self, *args):
        """Обработчик изменения текста поиска."""
        self.apply_filter()
    
    def get_patchouli_badge(self, mod: dict) -> str:
        """Возвращает иконку-бейдж Patchouli для отображения в таблице рядом с именем мода."""
        books = mod.get("patchouli", [])
        if not books:
            return ""
        if all(b["status"] == "full" for b in books):
            return "  📖✅"
        if any(b["status"] != "missing" for b in books):
            return "  📖⚠️"
        return "  📖❌"

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
        for item in self.translated_tree.get_children():
            self.translated_tree.delete(item)
        for item in self.outdated_tree.get_children():
            self.outdated_tree.delete(item)
        
        if not self.results:
            return
        
        # Показываем результаты - Полный перевод
        full_filtered = [mod for mod in self.results["full"] 
                       if search_text in mod["mod_name"].lower()]
        sort_col = self.sort_state["full"]["column"] or "Мод"
        full_sorted = self.sort_results(full_filtered, sort_col, self.sort_state["full"]["reverse"])
        
        for mod in full_sorted:
            self.full_tree.insert("", tk.END, values=(
                mod["mod_name"] + self.get_patchouli_badge(mod),
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                mod.get("identical_count", 0)
            ), tags=(self.get_source_tag(mod.get("source", "none")),))
        
        # Показываем результаты - Неполный перевод
        partial_filtered = [mod for mod in self.results["partial"]
                          if search_text in mod["mod_name"].lower()]
        sort_col = self.sort_state["partial"]["column"] or "Мод"
        partial_sorted = self.sort_results(partial_filtered, sort_col, self.sort_state["partial"]["reverse"])
        
        for mod in partial_sorted:
            missing_count = len(mod["missing_keys"])
            self.partial_tree.insert("", tk.END, values=(
                mod["mod_name"] + self.get_patchouli_badge(mod),
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                f"{missing_count} ключей",
                mod.get("identical_count", 0)
            ), tags=(self.get_percentage_tag(mod["percentage"]),))
        
        # Показываем результаты - Отсутствует
        missing_filtered = [mod for mod in self.results["missing"]
                          if search_text in mod["mod_name"].lower()]
        sort_col = self.sort_state["missing"]["column"] or "Мод"
        missing_sorted = self.sort_results(missing_filtered, sort_col, self.sort_state["missing"]["reverse"])
        
        for mod in missing_sorted:
            reason = mod.get("error", f"Нет {target_json_filename()}")
            self.missing_tree.insert("", tk.END, values=(
                mod["mod_name"] + self.get_patchouli_badge(mod),
                mod["en_keys"],
                reason
            ), tags=(self.get_source_tag(mod.get("source", "none")),))

        # Показываем результаты - Переведён (из TranslatedMods, 100%)
        translated_filtered = [mod for mod in self.results.get("translated", [])
                               if search_text in mod["mod_name"].lower()]
        sort_col = self.sort_state["translated"]["column"] or "Мод"
        translated_sorted = self.sort_results(translated_filtered, sort_col, self.sort_state["translated"]["reverse"])

        for mod in translated_sorted:
            self.translated_tree.insert("", tk.END, values=(
                mod["mod_name"] + self.get_patchouli_badge(mod),
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                mod.get("identical_count", 0)
            ), tags=(self.get_source_tag(mod.get("source", "none")),))

        # Показываем результаты - Устаревший перевод (из TranslatedMods, но не 100%)
        outdated_filtered = [mod for mod in self.results.get("outdated", [])
                             if search_text in mod["mod_name"].lower()]
        sort_col = self.sort_state["outdated"]["column"] or "Мод"
        outdated_sorted = self.sort_results(outdated_filtered, sort_col, self.sort_state["outdated"]["reverse"])

        for mod in outdated_sorted:
            missing_count = len(mod["missing_keys"])
            self.outdated_tree.insert("", tk.END, values=(
                mod["mod_name"] + self.get_patchouli_badge(mod),
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                f"{missing_count} ключей",
                mod.get("identical_count", 0)
            ), tags=(self.get_source_tag(mod.get("source", "none")),))
    
    def show_details(self, tree):
        """Показывает детали выбранного мода в структурированном окне."""
        selection = tree.selection()
        if not selection:
            return

        mod_name_raw = tree.item(selection[0])["values"][0]
        # Убираем badge Patchouli который добавляется при отображении
        mod_name = mod_name_raw.split("  📖")[0] if "  📖" in str(mod_name_raw) else mod_name_raw

        mod_info = None
        for category in ["full", "partial", "missing", "translated", "outdated"]:
            for mod in self.results[category]:
                if mod["mod_name"] == mod_name:
                    mod_info = mod
                    break
            if mod_info:
                break

        if not mod_info:
            return

        # Цвета темы
        bg      = "#1e1e1e" if self.dark_mode else "#f5f5f5"
        fg      = "#e0e0e0" if self.dark_mode else "#1a1a1a"
        hdr_bg  = "#2a2a2a" if self.dark_mode else "#c8d0de"
        box_bg  = "#252525" if self.dark_mode else "#ffffff"
        btn_bg  = "#3a3a3a" if self.dark_mode else "#b8c4d4"
        sep_col = "#444"    if self.dark_mode else "#c8c8c8"
        muted_fg = "#a8a8a8" if self.dark_mode else "#5a5a5a"
        pct_col = "#4caf50" if mod_info["percentage"] == 100 else ("#ff9800" if mod_info["percentage"] >= 50 else "#f44336")

        win = tk.Toplevel(self.root)
        win.title(f"Детали: {mod_name}")
        win.geometry("820x520")
        win.configure(bg=bg)
        win.resizable(True, True)

        # ── Шапка ──────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=hdr_bg, padx=14, pady=10)
        hdr.pack(fill=tk.X)

        tk.Label(hdr, text=mod_info["mod_name"], font=("Segoe UI", 11, "bold"),
                 bg=hdr_bg, fg=fg, anchor="w").pack(fill=tk.X)

        status_map = {"full": "✅ Полный", "partial": "⚠️ Неполный",
                      "missing": "❌ Отсутствует", "translated": "🌐 Переведён", "outdated": "🔄 Устарел"}
        source_map = {"jar": "встроен в JAR", "translated_mods": "TranslatedMods", "none": "—"}

        meta_line = (
            f"{status_map.get(mod_info['status'], mod_info['status'])}  │  "
            f"Источник: {source_map.get(mod_info.get('source','none'), mod_info.get('source','—'))}  │  "
            f"RU: {mod_info['ru_keys']}  │  EN: {mod_info['en_keys']}  │  "
        )
        meta_frame = tk.Frame(hdr, bg=hdr_bg)
        meta_frame.pack(anchor="w", pady=(4, 0))
        tk.Label(meta_frame, text=meta_line, font=("Segoe UI", 9),
                 bg=hdr_bg, fg=fg).pack(side=tk.LEFT)
        tk.Label(meta_frame, text=f"{mod_info['percentage']}%", font=("Segoe UI", 9, "bold"),
                 bg=hdr_bg, fg=pct_col).pack(side=tk.LEFT)

        if mod_info.get("error"):
            tk.Label(hdr, text=f"⚠️ {mod_info['error']}", font=("Segoe UI", 9),
                     bg=hdr_bg, fg="#f44336", anchor="w").pack(fill=tk.X, pady=(4, 0))

        identical_count = mod_info.get("identical_count", 0)
        if identical_count:
            tk.Label(hdr,
                     text=(f"≈ {identical_count} ключ(ей) переведены так же, как в en_us — "
                           f"возможно, забыли перевести (см. вкладку ниже)"),
                     font=("Segoe UI", 9), bg=hdr_bg, fg="#ff9800", anchor="w").pack(fill=tk.X, pady=(4, 0))

        # ── Разделитель ─────────────────────────────────────────────────────
        tk.Frame(win, bg=sep_col, height=1).pack(fill=tk.X)

        # ── Переключатель вкладок ───────────────────────────────────────────
        patchouli_books = mod_info.get("patchouli", [])
        tab_bar = tk.Frame(win, bg=hdr_bg)
        tab_bar.pack(fill=tk.X)

        # Контейнер для содержимого вкладки
        content_host = tk.Frame(win, bg=bg)
        content_host.pack(fill=tk.BOTH, expand=True)

        # ── Вкладка 1: Ключи локализации ────────────────────────────────────
        keys_frame = tk.Frame(content_host, bg=bg)

        cols_frame = tk.Frame(keys_frame, bg=bg)
        cols_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        cols_frame.columnconfigure(0, weight=1)
        cols_frame.columnconfigure(1, weight=1)

        # Список колбэков обновления темы — определяем ДО make_key_column
        all_theme_callbacks = []

        def make_key_column(parent, col, title, icon, keys, copy_label):
            frame = tk.Frame(parent, bg=bg)
            frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 0))

            top = tk.Frame(frame, bg=bg)
            top.pack(fill=tk.X, pady=(0, 4))
            tk.Label(top, text=f"{icon} {title} ({len(keys)})",
                     font=("Segoe UI", 9, "bold"), bg=bg, fg=fg).pack(side=tk.LEFT)

            def copy_all():
                self.root.clipboard_clear()
                self.root.clipboard_append("\n".join(keys))
                self.show_temporary_status(f"Скопировано {len(keys)} ключей")

            tk.Button(top, text=f"📋 {copy_label}", font=("Segoe UI", 8),
                      bg=btn_bg, fg=fg, relief=tk.FLAT, padx=6, pady=2,
                      activebackground=sep_col, activeforeground=fg,
                      command=copy_all, state=tk.NORMAL if keys else tk.DISABLED
                      ).pack(side=tk.RIGHT)

            list_frame = tk.Frame(frame, bg=box_bg, relief=tk.FLAT, bd=1)
            list_frame.pack(fill=tk.BOTH, expand=True)

            scrollbar = tk.Scrollbar(list_frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            listbox = tk.Listbox(
                list_frame,
                font=("Consolas", 9),
                bg=box_bg, fg=fg,
                selectbackground="#3a7bd5", selectforeground="white",
                relief=tk.FLAT, bd=0,
                yscrollcommand=scrollbar.set,
                activestyle="none",
                exportselection=False
            )
            listbox.pack(fill=tk.BOTH, expand=True)
            scrollbar.config(command=listbox.yview)

            for key in keys:
                listbox.insert(tk.END, f"  {key}")
            if not keys:
                listbox.insert(tk.END, "  (пусто)")
                listbox.config(fg=sep_col)

            def _copy_selection(event):
                if not (event.state & 0x4):
                    return None
                if not self.is_copy_shortcut(event):
                    return None
                selected = listbox.curselection()
                if selected:
                    lines = [listbox.get(i).strip() for i in selected]
                    self.root.clipboard_clear()
                    self.root.clipboard_append("\n".join(lines))
                    self.show_temporary_status(f"Скопировано {len(lines)} ключей")
                return "break"

            listbox.bind("<Control-KeyPress>", _copy_selection)

            def _apply_theme():
                is_dark    = self.dark_mode
                new_bg     = "#1e1e1e" if is_dark else "#f5f5f5"
                new_fg     = "#e0e0e0" if is_dark else "#1a1a1a"
                new_box_bg = "#252525" if is_dark else "#ffffff"
                new_btn_bg = "#3a3a3a" if is_dark else "#dde3ec"
                new_sep    = "#444"    if is_dark else "#c8c8c8"
                frame.config(bg=new_bg)
                top.config(bg=new_bg)
                list_frame.config(bg=new_box_bg)
                listbox.config(bg=new_box_bg, fg=new_fg if keys else new_sep)
                for w in top.winfo_children():
                    try:
                        if isinstance(w, tk.Label):
                            w.config(bg=new_bg, fg=new_fg)
                        elif isinstance(w, tk.Button):
                            w.config(bg=new_btn_bg, fg=new_fg,
                                     activebackground=new_sep, activeforeground=new_fg)
                    except Exception:
                        pass

            all_theme_callbacks.append(_apply_theme)

        missing_keys  = mod_info.get("missing_keys", [])
        extra_keys    = mod_info.get("extra_keys", [])
        identical_keys = mod_info.get("identical_keys", [])

        cols_frame.columnconfigure(2, weight=1)

        make_key_column(cols_frame, 0, "Не хватает", "❌", missing_keys, "Скопировать всё")
        tk.Frame(cols_frame, bg=sep_col, width=1).grid(row=0, column=0, sticky="nse", padx=(0, 5))
        make_key_column(cols_frame, 1, "Лишние ключи", "➕", extra_keys, "Скопировать всё")
        tk.Frame(cols_frame, bg=sep_col, width=1).grid(row=0, column=1, sticky="nse", padx=(0, 5))
        make_key_column(cols_frame, 2, "≈ Совпадает с EN", "⚠️", identical_keys, "Скопировать всё")

        # ── Вкладка 2: Patchouli гайдбуки ───────────────────────────────────
        pb_outer = tk.Frame(content_host, bg=bg)

        pb_canvas = tk.Canvas(pb_outer, bg=bg, highlightthickness=0)
        pb_scroll = tk.Scrollbar(pb_outer, orient=tk.VERTICAL, command=pb_canvas.yview)
        pb_canvas.configure(yscrollcommand=pb_scroll.set)
        pb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        pb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        pb_inner = tk.Frame(pb_canvas, bg=bg)
        pb_canvas_window = pb_canvas.create_window((0, 0), window=pb_inner, anchor="nw")

        def _resize_canvas(event):
            pb_canvas.itemconfig(pb_canvas_window, width=event.width)
        pb_canvas.bind("<Configure>", _resize_canvas)

        def _update_scroll(event):
            pb_canvas.configure(scrollregion=pb_canvas.bbox("all"))
        pb_inner.bind("<Configure>", _update_scroll)

        if patchouli_books:
            for book in patchouli_books:
                b_pct   = book['percentage']
                b_color = "#4caf50" if b_pct == 100 else ("#ff9800" if b_pct >= 50 else "#f44336")
                b_icon  = "✅" if b_pct == 100 else ("⚠️" if b_pct > 0 else "❌")

                card = tk.Frame(pb_inner, bg=hdr_bg, padx=10, pady=6)
                card.pack(fill=tk.X, padx=10, pady=(8, 0))

                title_row = tk.Frame(card, bg=hdr_bg)
                title_row.pack(fill=tk.X)
                tk.Label(title_row, text=f"{b_icon} {book['book_name']}",
                         font=("Segoe UI", 10, "bold"), bg=hdr_bg, fg=fg).pack(side=tk.LEFT)
                tk.Label(title_row,
                         text=f"  {b_pct}%  ({book['en_files'] - len(book['missing_files'])}/{book['en_files']} страниц)",
                         font=("Segoe UI", 9), bg=hdr_bg, fg=b_color).pack(side=tk.LEFT)

                if book['missing_files']:
                    missing_lf = tk.Frame(card, bg=box_bg)
                    missing_lf.pack(fill=tk.X, pady=(4, 0))

                    # Заголовок + кнопка копирования
                    lf_top = tk.Frame(missing_lf, bg=box_bg)
                    lf_top.pack(fill=tk.X)
                    tk.Label(lf_top, text=f"  Отсутствующие файлы ({len(book['missing_files'])}):",
                             font=("Segoe UI", 8), bg=box_bg, fg=muted_fg, anchor="w").pack(side=tk.LEFT)

                    missing_files_ref = book['missing_files']

                    def _copy_all_pb(files=missing_files_ref):
                        self.root.clipboard_clear()
                        self.root.clipboard_append("\n".join(files))
                        self.show_temporary_status(f"Скопировано {len(files)} файлов")

                    tk.Button(lf_top, text="📋 Скопировать всё",
                              font=("Segoe UI", 7), bg=btn_bg, fg=fg,
                              relief=tk.FLAT, padx=4, pady=1,
                              activebackground=sep_col, activeforeground=fg,
                              command=_copy_all_pb).pack(side=tk.RIGHT, padx=4)

                    lb = tk.Listbox(missing_lf, font=("Consolas", 8),
                                    bg=box_bg, fg=fg,
                                    selectbackground="#3a7bd5", selectforeground="white",
                                    relief=tk.FLAT, bd=0,
                                    height=min(6, len(book['missing_files'])),
                                    activestyle="none", exportselection=False)
                    lb.pack(fill=tk.X)
                    for f in book['missing_files']:
                        lb.insert(tk.END, f"  {f}")

                    def _copy_pb_selection(event, listbox=lb):
                        if not (event.state & 0x4):
                            return None
                        if not self.is_copy_shortcut(event):
                            return None
                        selected = listbox.curselection()
                        if selected:
                            lines = [listbox.get(i).strip() for i in selected]
                            self.root.clipboard_clear()
                            self.root.clipboard_append("\n".join(lines))
                            self.show_temporary_status(f"Скопировано {len(lines)} файлов")
                        return "break"

                    lb.bind("<Control-KeyPress>", _copy_pb_selection)
        else:
            tk.Label(pb_inner, text="📖 Patchouli гайдбуков не обнаружено",
                     font=("Segoe UI", 10), bg=bg, fg=muted_fg).pack(pady=40)

        # ── Кнопки вкладок ──────────────────────────────────────────────────
        active_tab_bg   = bg
        inactive_tab_bg = hdr_bg
        tab_fg          = fg

        pb_label = "📖 Гайдбук" if not patchouli_books else \
            f"📖 Гайдбук ({'✅' if all(b['status']=='full' for b in patchouli_books) else '⚠️' if any(b['status']!='missing' for b in patchouli_books) else '❌'})"

        tab_keys_btn = tk.Button(tab_bar, text="🔑 Ключи локализации",
                                 font=("Segoe UI", 9), relief=tk.FLAT, padx=12, pady=5,
                                 bg=active_tab_bg, fg=tab_fg, bd=0)
        tab_pb_btn   = tk.Button(tab_bar, text=pb_label,
                                 font=("Segoe UI", 9), relief=tk.FLAT, padx=12, pady=5,
                                 bg=inactive_tab_bg, fg=tab_fg, bd=0)
        tab_keys_btn.pack(side=tk.LEFT)
        tab_pb_btn.pack(side=tk.LEFT)

        # Нижняя черта активной вкладки
        tab_indicator = tk.Frame(tab_bar, bg="#3a7bd5", height=2)
        tab_indicator.place(in_=tab_keys_btn, relx=0, rely=1.0, relwidth=1.0, height=2, y=-2)

        keys_frame.pack(fill=tk.BOTH, expand=True)

        def show_keys_tab():
            pb_outer.pack_forget()
            keys_frame.pack(fill=tk.BOTH, expand=True)
            tab_keys_btn.config(bg=active_tab_bg)
            tab_pb_btn.config(bg=inactive_tab_bg)
            tab_indicator.place(in_=tab_keys_btn, relx=0, rely=1.0, relwidth=1.0, height=2, y=-2)

        def show_pb_tab():
            keys_frame.pack_forget()
            pb_outer.pack(fill=tk.BOTH, expand=True)
            tab_keys_btn.config(bg=inactive_tab_bg)
            tab_pb_btn.config(bg=active_tab_bg)
            tab_indicator.place(in_=tab_pb_btn, relx=0, rely=1.0, relwidth=1.0, height=2, y=-2)

        tab_keys_btn.config(command=show_keys_tab)
        tab_pb_btn.config(command=show_pb_tab)

        def _on_theme_change():
            is_dark    = self.dark_mode
            new_bg     = "#1e1e1e" if is_dark else "#f5f5f5"
            new_hdr_bg = "#2a2a2a" if is_dark else "#dde3ec"
            new_fg     = "#e0e0e0" if is_dark else "#1a1a1a"
            new_sep    = "#444"    if is_dark else "#c8c8c8"
            new_pct    = "#4caf50" if mod_info["percentage"] == 100 else ("#ff9800" if mod_info["percentage"] >= 50 else "#f44336")

            win.configure(bg=new_bg)
            content_host.configure(bg=new_bg)
            keys_frame.configure(bg=new_bg)
            cols_frame.configure(bg=new_bg)
            pb_outer.configure(bg=new_bg)
            pb_canvas.configure(bg=new_bg)
            pb_inner.configure(bg=new_bg)
            tab_bar.configure(bg=new_hdr_bg)
            tab_keys_btn.configure(bg=new_bg if keys_frame.winfo_ismapped() else new_hdr_bg, fg=new_fg)
            tab_pb_btn.configure(bg=new_bg if pb_outer.winfo_ismapped() else new_hdr_bg, fg=new_fg)

            # Шапка
            hdr.configure(bg=new_hdr_bg)
            for w in hdr.winfo_children():
                try:
                    w.configure(bg=new_hdr_bg, fg=new_fg)
                except Exception:
                    pass
                for ww in w.winfo_children():
                    try:
                        text = ww.cget("text") if hasattr(ww, "cget") else ""
                        if "%" in str(text):
                            ww.configure(bg=new_hdr_bg, fg=new_pct)
                        else:
                            ww.configure(bg=new_hdr_bg, fg=new_fg)
                    except Exception:
                        pass

            # Карточки гайдбуков в pb_inner
            for card in pb_inner.winfo_children():
                try:
                    card.configure(bg=new_hdr_bg)
                    for w in card.winfo_children():
                        try:
                            if isinstance(w, tk.Frame):
                                w.configure(bg=new_hdr_bg)
                                for ww in w.winfo_children():
                                    try:
                                        ww.configure(bg=new_hdr_bg, fg=new_fg)
                                    except Exception:
                                        pass
                            elif isinstance(w, tk.Label):
                                w.configure(bg=new_hdr_bg, fg=new_fg)
                        except Exception:
                            pass
                except Exception:
                    pass

            # Колонки ключей
            for cb in all_theme_callbacks:
                cb()

        # Регистрируем колбэк; при закрытии окна — снимаем
        self._detail_theme_callbacks = getattr(self, "_detail_theme_callbacks", [])
        self._detail_theme_callbacks.append(_on_theme_change)
        win.protocol("WM_DELETE_WINDOW", lambda: (
            self._detail_theme_callbacks.remove(_on_theme_change)
            if _on_theme_change in self._detail_theme_callbacks else None,
            win.destroy()
        ))

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
        
        outdated_count = len(self.results.get("outdated", []))
        output_data = {
            "scan_directory": str(self.current_path),
            "total_mods": len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"]) + len(self.results.get("translated", [])) + outdated_count,
            "summary": {
                "full_translation": len(self.results["full"]),
                "partial_translation": len(self.results["partial"]),
                "missing_translation": len(self.results["missing"]),
                "translated_mods": len(self.results.get("translated", [])),
                "outdated_translation": outdated_count
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
    load_config()
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
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

    parser.add_argument(
        "--lang", "--target-language",
        dest="lang",
        default=None,
        help="Код целевого языка перевода, например ru_ru, de_de, uk_ua (по умолчанию из config.json)"
    )

    args = parser.parse_args()

    if args.lang:
        set_target_language(args.lang)
        CONFIG["target_language"] = get_target_language()
        save_config()

    if args.gui:
        main_gui()
        return 0
    
    base_path = Path(args.directory).resolve()
    
    if not base_path.exists():
        print(f"❌ Ошибка: Директория '{base_path}' не существует")
        return 1

    print(f"🌐 Целевой язык перевода: {get_target_language()}")
    
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
    total_mods = len(results["full"]) + len(results["partial"]) + len(results["missing"]) + len(results.get("translated", []))
    
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
    print(f"   [Переведён] Из TranslatedMods (100%): {len(results.get('translated', []))}")
    print("=" * 60)
    
    # Сохранение результатов в JSON
    output_file = Path(args.output)
    output_data = {
        "scan_directory": str(base_path),
        "total_mods": total_mods,
        "summary": {
            "full_translation": len(results["full"]),
            "partial_translation": len(results["partial"]),
            "missing_translation": len(results["missing"]),
            "translated_mods": len(results.get("translated", []))
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
    # Если аргументов нет или есть --gui, запускаем GUI
    if len(sys.argv) == 1 or "--gui" in sys.argv:
        main_gui()
    else:
        sys.exit(main_cli())
