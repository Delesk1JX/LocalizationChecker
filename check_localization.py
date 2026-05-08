#!/usr/bin/env python3
"""
Программа для проверки русской локализации в модах Minecraft.
Проверяет наличие ru_ru.json и сравнивает количество строк с en_us.json.

Категории:
- Полный перевод: 100% совпадение ключей с en_us.json
- Неполный перевод: есть ru_ru.json, но ключей меньше чем в en_us.json
- Отсутствует: нет ru_ru.json
"""

import os
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any


def count_keys_in_json(file_path: Path) -> int:
    """Подсчитывает количество ключей в JSON файле локализации."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return len(data)
    except (json.JSONDecodeError, FileNotFoundError, UnicodeDecodeError) as e:
        print(f"⚠️  Ошибка чтения файла {file_path}: {e}")
        return 0


def check_mod_localization(mod_path: Path) -> Tuple[str, int, int, float]:
    """
    Проверяет локализацию для одного мода.
    
    Returns:
        Tuple[status, ru_keys, en_keys, percentage]
        status: "full", "partial", "missing"
    """
    lang_dir = mod_path / "lang"
    
    if not lang_dir.exists():
        return ("missing", 0, 0, 0.0)
    
    en_us_file = lang_dir / "en_us.json"
    ru_ru_file = lang_dir / "ru_ru.json"
    
    if not en_us_file.exists():
        # Если нет английского файла, не можем сравнить
        if ru_ru_file.exists():
            ru_keys = count_keys_in_json(ru_ru_file)
            return ("partial", ru_keys, 0, 0.0)
        return ("missing", 0, 0, 0.0)
    
    if not ru_ru_file.exists():
        en_keys = count_keys_in_json(en_us_file)
        return ("missing", 0, en_keys, 0.0)
    
    en_keys = count_keys_in_json(en_us_file)
    ru_keys = count_keys_in_json(ru_ru_file)
    
    if en_keys == 0:
        return ("missing", ru_keys, 0, 0.0)
    
    percentage = (ru_keys / en_keys) * 100
    
    # ТОЛЬКО 100% считается полным переводом
    if percentage == 100.0:
        return ("full", ru_keys, en_keys, percentage)
    else:
        return ("partial", ru_keys, en_keys, percentage)


def scan_mods_directory(base_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """
    Сканирует директорию с модами и проверяет локализацию.
    
    Ожидается структура:
    base_path/
      assets/
        mod_name1/
          lang/
            en_us.json
            ru_ru.json
        mod_name2/
          ...
    """
    results = {
        "full": [],
        "partial": [],
        "missing": []
    }
    
    assets_dir = base_path / "assets"
    
    if not assets_dir.exists():
        print(f"⚠️  Директория 'assets' не найдена в {base_path}")
        return results
    
    for mod_dir in assets_dir.iterdir():
        if mod_dir.is_dir():
            status, ru_keys, en_keys, percentage = check_mod_localization(mod_dir)
            
            mod_info = {
                "mod_name": mod_dir.name,
                "ru_keys": ru_keys,
                "en_keys": en_keys,
                "percentage": round(percentage, 2)
            }
            
            if status == "full":
                results["full"].append(mod_info)
            elif status == "partial":
                results["partial"].append(mod_info)
            else:
                results["missing"].append(mod_info)
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Проверка русской локализации в модах Minecraft",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s                          # Проверить текущую директорию
  %(prog)s .                        # Проверить текущую директорию
  %(prog)s C:\\Games\\Minecraft\\mods  # Проверить конкретную папку
  %(prog)s . --output results.json  # Сохранить в другой файл
  %(prog)s -v                       # Подробный вывод
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
        "--verbose", "-v",
        action="store_true",
        help="Показывать подробную информацию о каждом моде"
    )
    
    args = parser.parse_args()
    
    base_path = Path(args.directory).resolve()
    
    if not base_path.exists():
        print(f"❌ Ошибка: Директория '{base_path}' не существует")
        return 1
    
    print(f"🔍 Сканирование директории: {base_path}")
    print("-" * 60)
    
    results = scan_mods_directory(base_path)
    
    # Подсчет статистики
    total_mods = len(results["full"]) + len(results["partial"]) + len(results["missing"])
    
    if total_mods == 0:
        print("⚠️  Моды не найдены! Убедитесь, что структура папок соответствует:")
        print("   assets/mod_name/lang/en_us.json")
        print("   assets/mod_name/lang/ru_ru.json")
        return 1
    
    if args.verbose:
        print("\n📊 Подробная информация:")
        
        if results["full"]:
            print("\n🟢 Полный перевод (100%):")
            for mod in results["full"]:
                print(f"   • {mod['mod_name']} ({mod['ru_keys']}/{mod['en_keys']} ключей)")
        
        if results["partial"]:
            print("\n🟡 Неполный перевод:")
            for mod in sorted(results["partial"], key=lambda x: x['percentage'], reverse=True):
                print(f"   • {mod['mod_name']} ({mod['ru_keys']}/{mod['en_keys']} ключей, {mod['percentage']}%)")
        
        if results["missing"]:
            print("\n🔴 Отсутствует русская локализация:")
            for mod in results["missing"]:
                if mod['en_keys'] > 0:
                    print(f"   • {mod['mod_name']} (английских ключей: {mod['en_keys']})")
                else:
                    print(f"   • {mod['mod_name']} (нет файлов локализации)")
    
    # Вывод статистики
    print("\n" + "=" * 60)
    print("📈 СТАТИСТИКА:")
    print(f"   Всего модов проверено: {total_mods}")
    print(f"   🟢 С полным переводом: {len(results['full'])}")
    print(f"   🟡 С неполным переводом: {len(results['partial'])}")
    print(f"   🔴 Без русского языка: {len(results['missing'])}")
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
        print(f"   - Полных переводов: {len(results['full'])}")
        print(f"   - Неполных переводов: {len(results['partial'])}")
        print(f"   - Без перевода: {len(results['missing'])}")
    except Exception as e:
        print(f"\n❌ Ошибка сохранения файла: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
