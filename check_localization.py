#!/usr/bin/env python3
"""
Программа для проверки русской локализации в модах Minecraft.
Работает напрямую с .jar файлами, проверяет наличие ru_ru.json и сравнивает ключи с en_us.json.
Также поддерживает проверку переводов в ресурспаке RTF:E (папка RTFE).

Категории:
- Полный перевод: 100% совпадение ключей с en_us.json (все ключи из en_us есть в ru_ru)
- Неполный перевод: есть ru_ru.json, но не все ключи из en_us присутствуют
- Отсутствует: есть en_us.json, но нет ru_ru.json

Важно: Моды без файла en_us.json не учитываются при проверке.

Лишние ключи в ru_ru.json (которых нет в en_us.json) сохраняются в отчете,
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


# Глобальная переменная для пути к папке RTFE
RTFE_PATH: Optional[Path] = None


def set_rtfe_path(path: Optional[Path]):
    """Устанавливает путь к папке RTFE."""
    global RTFE_PATH
    RTFE_PATH = path


def get_rtfe_path() -> Optional[Path]:
    """Возвращает текущий путь к папке RTFE."""
    return RTFE_PATH


def find_rtfe_directory(base_path: Path) -> Optional[Path]:
    """
    Ищет папку RTFE рядом с указанной директорией, в ней или внутри HelperTranslatorRU.
    
    Args:
        base_path: Базовая директория для поиска
        
    Returns:
        Путь к папке RTFE или None если не найдена
    """
    # Проверяем несколько возможных мест расположения RTFE
    possible_paths = [
        base_path / "RTFE",
        base_path.parent / "RTFE",
        Path.cwd() / "RTFE",
        base_path / "HelperTranslatorRU" / "RTFE",
        base_path.parent / "HelperTranslatorRU" / "RTFE",
        Path.cwd() / "HelperTranslatorRU" / "RTFE",
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
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            data = json.loads(content)
            return data
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        print(f"⚠️  Ошибка чтения {file_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {file_path}: {e}")
        return None


def find_ru_ru_in_rtfe(mod_name: str) -> Optional[Path]:
    """
    Ищет файл ru_ru.json для мода в папке RTFE.
    
    Структура RTFE:
    RTFE/
    ├── <название_мода>/
    │   └── lang/
    │       └── ru_ru.json
    
    Args:
        mod_name: Название мода (извлеченное из assets внутри .jar файла)
        
    Returns:
        Путь к файлу ru_ru.json или None если не найден
    """
    if RTFE_PATH is None:
        return None
    
    # mod_name уже является чистым именем мода из assets
    possible_names = [mod_name]
    
    # Пробуем найти папку мода в RTFE
    for name in possible_names:
        mod_dir = RTFE_PATH / name
        if mod_dir.exists() and mod_dir.is_dir():
            ru_ru_path = mod_dir / "lang" / "ru_ru.json"
            if ru_ru_path.exists():
                return ru_ru_path
    
    # Если точное совпадение не найдено, ищем по частичному совпадению
    if RTFE_PATH.exists():
        for item in RTFE_PATH.iterdir():
            if item.is_dir():
                # Проверяем, содержит ли название папки название мода или наоборот
                if mod_name.lower() in item.name.lower() or item.name.lower() in mod_name.lower():
                    ru_ru_path = item / "lang" / "ru_ru.json"
                    if ru_ru_path.exists():
                        return ru_ru_path
        
        # Дополнительная проверка: если имя папки является аббревиатурой или префиксом
        # Например, ali -> advancedlootinfo (a-l-i первые буквы слов)
        import re
        # Разбиваем mod_name на слова (по заглавным буквам или подчеркиваниям)
        words = re.split(r'[_-]', mod_name.lower())
        if len(words) == 1:
            # Если одно слово, пробуем разбить по заглавным буквам
            words = re.findall(r'[a-z]+', mod_name.lower())
        
        # Создаем аббревиатуру из первых букв слов
        if len(words) > 1:
            abbrev = ''.join([w[0] for w in words if w])
            for item in RTFE_PATH.iterdir():
                if item.is_dir() and item.name.lower() == abbrev:
                    ru_ru_path = item / "lang" / "ru_ru.json"
                    if ru_ru_path.exists():
                        return ru_ru_path
        
        # Также проверяем, начинается ли mod_name с названия папки
        for item in RTFE_PATH.iterdir():
            if item.is_dir() and len(item.name) >= 2:
                # Проверяем первые несколько букв
                if mod_name.lower().startswith(item.name.lower()):
                    ru_ru_path = item / "lang" / "ru_ru.json"
                    if ru_ru_path.exists():
                        return ru_ru_path
    
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


def check_rtfe_localization(jar_path: Path, en_data: Dict[str, str], en_us_path: str) -> Dict[str, Any]:
    """
    Проверяет наличие перевода для мода в папке RTFE.
    
    Args:
        jar_path: Путь к .jar файлу мода
        en_data: Данные из en_us.json внутри .jar файла
        en_us_path: Путь к en_us.json внутри архива
        
    Returns:
        Словарь с результатами проверки или None если RTFE не настроен
    """
    result = {
        "found": False,
        "status": "not_found",  # full, partial, not_found
        "source": None,  # "rtfe" или None
        "ru_keys": 0,
        "en_keys": len(en_data),
        "percentage": 0.0,
        "missing_keys": [],
        "extra_keys": [],
        "error": None
    }
    
    if RTFE_PATH is None:
        return result
    
    # Извлекаем имя мода из assets внутри .jar файла
    mod_name = extract_mod_name_from_assets(jar_path)
    
    if mod_name is None:
        result["error"] = "Не удалось извлечь имя мода из assets"
        return result
    
    # Ищем ru_ru.json в RTFE используя имя мода из assets
    ru_ru_path = find_ru_ru_in_rtfe(mod_name)
    
    if ru_ru_path is None:
        return result
    
    # Извлекаем данные из ru_ru.json
    ru_data = extract_json_from_file(ru_ru_path)
    
    if ru_data is None:
        result["error"] = f"Не удалось прочитать {ru_ru_path}"
        return result
    
    result["found"] = True
    result["source"] = "rtfe"
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
                content = f.read().decode('utf-8')
                data = json.loads(content)
                return data
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        print(f"⚠️  Ошибка чтения {lang_path} из {jar_path}: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Неожиданная ошибка при чтении {jar_path}: {e}")
        return None


def find_lang_files_in_jar(jar_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Ищет файлы en_us.json и ru_ru.json внутри .jar файла.
    
    Returns:
        Tuple[путь_к_en_us, путь_к_ru_ru] внутри архива (или None если не найдены)
    """
    en_us_path = None
    ru_ru_path = None
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar_file:
            for name in jar_file.namelist():
                # Ищем файлы локализации в папках lang
                if name.endswith('/lang/en_us.json') or name.endswith('\\lang\\en_us.json'):
                    en_us_path = name
                elif name.endswith('/lang/ru_ru.json') or name.endswith('\\lang\\ru_ru.json'):
                    ru_ru_path = name
    except zipfile.BadZipFile:
        return (None, None)
    
    return (en_us_path, ru_ru_path)


def check_jar_localization(jar_path: Path) -> Dict[str, Any]:
    """
    Проверяет локализацию в одном .jar файле.
    Сначала проверяет наличие ru_ru.json внутри .jar файла.
    Если не найден, проверяет наличие перевода в папке RTFE.
    
    Returns:
        Словарь с результатами проверки
    """
    result = {
        "mod_name": jar_path.name,
        "status": "missing",  # full, partial, missing
        "source": "none",  # "jar", "rtfe", "none"
        "ru_keys": 0,
        "en_keys": 0,
        "percentage": 0.0,
        "missing_keys": [],  # Ключи из en_us, которых нет в ru_ru
        "extra_keys": [],    # Ключи из ru_ru, которых нет в en_us (для обратной совместимости)
        "error": None
    }
    
    # Находим пути к файлам внутри архива
    en_us_path, ru_ru_path = find_lang_files_in_jar(jar_path)
    
    if en_us_path is None:
        # Если нет файла en_us.json, мод не учитывается
        result["status"] = "skipped"
        result["error"] = "Файл en_us.json не найден в архиве (мод пропущен)"
        return result
    
    # Извлекаем en_us.json для сравнения
    en_data = extract_json_from_jar(jar_path, en_us_path)
    if en_data is None:
        result["error"] = "Не удалось прочитать en_us.json"
        return result
    
    result["en_keys"] = len(en_data)
    
    # Сначала проверяем ru_ru.json внутри .jar файла
    if ru_ru_path is not None:
        ru_data = extract_json_from_jar(jar_path, ru_ru_path)
        
        if ru_data is not None:
            # Есть встроенный перевод
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
    
    # Если встроенного перевода нет, проверяем RTFE
    if RTFE_PATH is not None:
        rtfe_result = check_rtfe_localization(jar_path, en_data, en_us_path)
        
        if rtfe_result["found"]:
            result["source"] = "rtfe"
            result["ru_keys"] = rtfe_result["ru_keys"]
            result["percentage"] = rtfe_result["percentage"]
            result["missing_keys"] = rtfe_result["missing_keys"]
            result["extra_keys"] = rtfe_result["extra_keys"]
            
            if rtfe_result["status"] == "full":
                result["status"] = "full"
            elif rtfe_result["status"] == "partial":
                result["status"] = "partial"
            else:
                result["status"] = "missing"
            
            return result
    
    # Перевода нет ни в .jar, ни в RTFE
    result["status"] = "missing"
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
    with ThreadPoolExecutor(max_workers=4) as executor:
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
        
        self.setup_ui()
    
    def setup_ui(self):
        """Настройка пользовательского интерфейса."""
        # Верхняя панель с кнопками
        top_frame = ttk.Frame(self.root, padding="10")
        top_frame.pack(fill=tk.X)
        
        self.select_btn = ttk.Button(top_frame, text="📁 Выбрать папку с модами", command=self.select_directory)
        self.select_btn.pack(side=tk.LEFT, padx=5)
        
        self.check_btn = ttk.Button(top_frame, text="▶️ Проверить", command=self.start_check, state=tk.DISABLED)
        self.check_btn.pack(side=tk.LEFT, padx=5)
        
        self.export_btn = ttk.Button(top_frame, text="💾 Экспорт в JSON", command=self.export_results, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(top_frame, text="❌ Закрыть", command=self.root.quit).pack(side=tk.RIGHT, padx=5)
        
        # Панель прогресса
        self.progress_frame = ttk.Frame(self.root, padding="10")
        self.progress_frame.pack(fill=tk.X)
        
        self.progress_label = ttk.Label(self.progress_frame, text="Готов к работе")
        self.progress_label.pack(side=tk.LEFT, padx=5)
        
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='determinate', length=400)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # Основная область с результатами
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Вкладки для категорий
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка "Полный перевод"
        self.full_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.full_frame, text="[100%] Полный")
        self.full_tree = self.create_treeview(self.full_frame, ["Мод", "Ключи RU", "Ключи EN", "%"])
        
        # Вкладка "Неполный перевод"
        self.partial_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.partial_frame, text="[Частично] Неполный")
        self.partial_tree = self.create_treeview(self.partial_frame, ["Мод", "Ключи RU", "Ключи EN", "%", "Не хватает"])
        
        # Вкладка "Отсутствует"
        self.missing_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.missing_frame, text="[Нет] Отсутствует")
        self.missing_tree = self.create_treeview(self.missing_frame, ["Мод", "Ключи EN", "Причина"])
        
        # Статус бар
        self.status_frame = ttk.Frame(self.root, padding="10")
        self.status_frame.pack(fill=tk.X)
        
        self.status_label = ttk.Label(self.status_frame, text="Выберите папку с .jar файлами модов", foreground="gray")
        self.status_label.pack(side=tk.LEFT)
    
    def create_treeview(self, parent, columns):
        """Создает Treeview для отображения результатов."""
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=100 if col != "Мод" else 300)
        
        # Добавляем полосу прокрутки
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Обработка двойного клика для показа деталей
        tree.bind("<Double-1>", lambda e: self.show_details(tree))
        
        return tree
    
    def select_directory(self):
        """Открывает диалог выбора директории."""
        directory = filedialog.askdirectory(title="Выберите папку с .jar файлами модов")
        if directory:
            self.current_path = Path(directory)
            self.select_btn.config(text=f"📁 {self.current_path.name}")
            self.check_btn.config(state=tk.NORMAL)
            self.status_label.config(text=f"Папка выбрана: {self.current_path}", foreground="black")
            
            # Автоматически ищем папку RTFE
            rtfe_path = find_rtfe_directory(self.current_path)
            if rtfe_path:
                set_rtfe_path(rtfe_path)
                self.status_label.config(
                    text=f"Папка выбрана: {self.current_path} | RTFE найден: {rtfe_path}", 
                    foreground="blue"
                )
            else:
                self.status_label.config(
                    text=f"Папка выбрана: {self.current_path} | RTFE не найден", 
                    foreground="orange"
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
        
        # Заполняем вкладку "Полный перевод"
        for mod in self.results["full"]:
            self.full_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%"
            ))
        
        # Заполняем вкладку "Неполный перевод"
        for mod in self.results["partial"]:
            missing_count = len(mod["missing_keys"])
            self.partial_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["ru_keys"],
                mod["en_keys"],
                f"{mod['percentage']}%",
                f"{missing_count} ключей"
            ))
        
        # Заполняем вкладку "Отсутствует"
        for mod in self.results["missing"]:
            reason = mod.get("error", "Нет ru_ru.json")
            self.missing_tree.insert("", tk.END, values=(
                mod["mod_name"],
                mod["en_keys"],
                reason
            ))
        
        # Обновляем статус
        total = len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"])
        self.status_label.config(
            text=f"Всего: {total} | [100%]: {len(self.results['full'])} | [Частично]: {len(self.results['partial'])} | [Нет]: {len(self.results['missing'])}",
            foreground="green"
        )
    
    def check_complete(self):
        """Завершение проверки."""
        self.check_btn.config(state=tk.NORMAL)
        self.select_btn.config(state=tk.NORMAL)
        self.export_btn.config(state=tk.NORMAL)
        self.progress_label.config(text="Проверка завершена")
        
        if self.results:
            total = len(self.results["full"]) + len(self.results["partial"]) + len(self.results["missing"])
            messagebox.showinfo("Готово", f"Проверено {total} модов (только с файлами локализации).\nРезультаты отображены во вкладках.")
    
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
    root = tk.Tk()
    app = LocalizationCheckerGUI(root)
    root.mainloop()


def main_cli():
    """Запускает консольную версию."""
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
        "--rtfe",
        type=str,
        default=None,
        help="Путь к папке RTFE с переводами (необязательно)"
    )
    
    args = parser.parse_args()
    
    if args.gui:
        main_gui()
        return 0
    
    base_path = Path(args.directory).resolve()
    
    if not base_path.exists():
        print(f"❌ Ошибка: Директория '{base_path}' не существует")
        return 1
    
    # Ищем и устанавливаем путь к RTFE
    rtfe_path = None
    if args.rtfe:
        rtfe_path = Path(args.rtfe)
        if not rtfe_path.exists():
            print(f"⚠️  Предупреждение: Указанная папка RTFE не существует: {rtfe_path}")
            rtfe_path = None
    else:
        rtfe_path = find_rtfe_directory(base_path)
    
    if rtfe_path:
        set_rtfe_path(rtfe_path)
        print(f"📦 RTFE найден: {rtfe_path}")
    else:
        print("📦 RTFE не найден (проверка только встроенных переводов)")
    
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
