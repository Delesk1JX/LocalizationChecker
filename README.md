# LocalizationChecker 🔍

Программа для проверки русской локализации в модах Minecraft.  
Автоматически сканирует моды, проверяет наличие `ru_ru.json` и сравнивает количество строк с `en_us.json`.

##  Возможности

- **Проверка наличия** русской локализации (`ru_ru.json`)
-  **Определение полноты перевода**:
  -  **Полный перевод** — 100% совпадение ключей с английским файлом
  - **Неполный перевод** — есть русский файл, но ключей меньше чем в английском
  -  **Отсутствует** — нет файла `ru_ru.json`
- 💾 **Экспорт результатов** в JSON-файл
- 🖥️ **Подробный режим** с детальной информацией по каждому моду
- 📦 **Готовый .exe файл** для запуска без установки Python

## 📁 Структура папок

Программа ожидает следующую структуру:
```
ваша_папка/
└── assets/
    ├── mod_name1/
    │   └── lang/
    │       ├── en_us.json
    │       └── ru_ru.json
    ├── mod_name2/
    │   └── lang/
    │       └── en_us.json
    └── ...
```

## 🛠️ Использование

### Из исходного кода (Python)

```bash
# Проверка текущей директории
python check_localization.py

# Проверка конкретной папки
python check_localization.py C:\Games\Minecraft\mods

# Подробный вывод
python check_localization.py -v

# Сохранить в другой файл
python check_localization.py -o results.json
```

### Запуск готового .exe файла

1. Скачайте или найдите файл `LocalizationChecker.exe` в папке `dist/`
2. Откройте командную строку (cmd) в папке с модами
3. Запустите:
   ```
   LocalizationChecker.exe
   ```
   
Или перетащите папку с модами на файл `LocalizationChecker.exe`

## 📤 Формат выходного файла

Результаты сохраняются в `localization_results.json`:

```json
{
  "scan_directory": "C:\\Mods",
  "total_mods": 150,
  "summary": {
    "full_translation": 45,
    "partial_translation": 30,
    "missing_translation": 75
  },
  "mods": {
    "full": [
      {
        "mod_name": "mod_with_full_translation",
        "ru_keys": 120,
        "en_keys": 120,
        "percentage": 100.0
      }
    ],
    "partial": [
      {
        "mod_name": "mod_with_partial_translation",
        "ru_keys": 80,
        "en_keys": 100,
        "percentage": 80.0
      }
    ],
    "missing": [
      {
        "mod_name": "mod_without_russian",
        "ru_keys": 0,
        "en_keys": 150,
        "percentage": 0.0
      }
    ]
  }
}
```

## 📋 Примеры использования

### Пример 1: Быстрая проверка
```bash
LocalizationChecker.exe
```
Проверит текущую папку и создаст `localization_results.json`

### Пример 2: Проверка с подробным выводом
```bash
LocalizationChecker.exe -v
```
Покажет список всех модов с их статусом в консоли

### Пример 3: Проверка конкретной папки
```bash
LocalizationChecker.exe "C:\Games\Minecraft\mods"
```

### Пример 4: Свой файл результатов
```bash
LocalizationChecker.exe -o my_results.json
```

## 💡 Советы по использованию

1. **Распакуйте моды заранее** — программа работает с распакованными `.jar` файлами (структура папок `assets/mod_name/lang/`)
2. **Используйте подробный режим** (`-v`) для быстрой оценки ситуации
3. **Анализируйте JSON** — используйте результаты для планирования работы по переводу
4. **Сортируйте моды** — начните с модов, у которых высокий процент перевода (80-99%), их легче всего завершить

## 🔧 Сборка .exe файла

Если вы хотите собрать исполняемый файл самостоятельно:

```bash
# Установите PyInstaller
pip install pyinstaller

# Соберите .exe
pyinstaller --onefile --name LocalizationChecker check_localization.py

# Готовый файл будет в папке dist/
```

## ⚠️ Важные замечания

- **Полный перевод = 100%** — даже одна отсутствующая строка делает перевод неполным
- Программа считает **количество ключей**, а не качество перевода
- Для корректной работы необходимы оба файла: `en_us.json` и `ru_ru.json`
- Поддерживается только структура `assets/mod_name/lang/`

## 📝 Лицензия

Свободное использование для любых целей.
