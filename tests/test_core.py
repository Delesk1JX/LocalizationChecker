"""
Тесты парсеров и сравнения ключей — страховка перед рефакторингом zip-логики.

Запуск:  python -m pytest tests/ -q
"""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_localization as cl


# ────────────────────────────── .lang ──────────────────────────────

def test_lang_basic_pairs():
    text = "tile.one=One\ntile.two=Два\n"
    assert cl._parse_lang_text(text, "t.lang") == {"tile.one": "One", "tile.two": "Два"}


def test_lang_hash_comment_skipped():
    cl.clear_error_log()
    assert cl._parse_lang_text("# комментарий\nkey=value\n", "t.lang") == {"key": "value"}
    assert cl.get_error_log() == []


def test_lang_slash_comment_skipped():
    """Строки-комментарии с // не должны попадать в отчёт об ошибках.

        Регрессия: в .lang модов встречаются комментарии с // (например «// Stairs»),
        и каждая такая строка засоряла вкладку «Ошибки».
    """
    cl.clear_error_log()
    text = "// Stairs\ntile.stairs=Лестница\n"
    assert cl._parse_lang_text(text, "t.lang") == {"tile.stairs": "Лестница"}
    assert cl.get_error_log() == []


def test_lang_line_without_separator_warns():
    cl.clear_error_log()
    result = cl._parse_lang_text("key=value\nбитая строка\n", "t.lang")
    assert result == {"key": "value"}
    errors = cl.get_error_log()
    assert len(errors) == 1
    assert "строка 2" in errors[0]["message"]
    assert "нет разделителя" in errors[0]["message"]


def test_lang_first_equals_wins_and_leading_spaces_kept():
    """Значение берётся по первому '=', ведущие пробелы в значении значимы.

        Хвостовые пробелы теряются: строка целиком приводится к strip() ДО
        разбиения по '=', поэтому «key=value  » даст «value».
    """
    text = "key=a=b\nspaced=  с пробелами  \n"
    assert cl._parse_lang_text(text, "t.lang") == {"key": "a=b", "spaced": "  с пробелами"}


def test_lang_empty_value_is_kept():
    """Пустое значение — валидный ключ (именно поэтому его надо отдельно ловить)."""
    assert cl._parse_lang_text("empty=\n", "t.lang") == {"empty": ""}


def test_lang_decode_cp1251():
    assert cl._decode_lang_bytes("key=Привет".encode("cp1251")) == "key=Привет"
    assert cl._decode_lang_bytes("key=Привет".encode("utf-8")) == "key=Привет"
    assert cl._decode_lang_bytes("key=Hello".encode("utf-8-sig")) == "key=Hello"


# ────────────────────────────── JSON ──────────────────────────────

def test_json_comments_removed():
    src = '{\n // строчный\n "a": 1, /* блок */ "b": 2,\n}'
    assert json.loads(cl.clean_json_with_comments(src)) == {"a": 1, "b": 2}


def test_json_comment_markers_inside_strings_kept():
    """Валидный JSON не должен ломаться из-за // или /* внутри значений."""
    src = '{"url": "https://example.com", "glob": "/* not a comment */", "n": 1}'
    assert json.loads(cl.clean_json_with_comments(src)) == {
        "url": "https://example.com",
        "glob": "/* not a comment */",
        "n": 1,
    }


def test_json_trailing_comma_removed():
    assert json.loads(cl.clean_json_with_comments('{"a": 1, "b": 2,}')) == {"a": 1, "b": 2}


def test_json_unterminated_block_comment():
    assert json.loads(cl.clean_json_with_comments('{"a": 1} /* tail')) == {"a": 1}


def test_json_escaped_quote_inside_string():
    src = '{"a": "say \\"hi\\" // not comment", "b": 2}'
    assert json.loads(cl.clean_json_with_comments(src)) == {"a": 'say "hi" // not comment', "b": 2}


# ──────────────────────── сравнение ключей ────────────────────────

def test_compare_keys_full():
    result = cl._compare_keys({"a": "Alpha", "b": "Beta"}, {"a": "Альфа", "b": "Бета"})
    assert result["status"] == "full"
    assert result["percentage"] == 100.0
    assert result["missing_keys"] == []
    assert result["extra_keys"] == []
    assert result["identical_keys"] == []


def test_compare_keys_partial():
    result = cl._compare_keys({"a": "Alpha", "b": "Beta", "c": "Gamma"}, {"a": "Альфа"})
    assert result["status"] == "partial"
    assert result["percentage"] == round(100 / 3, 2)
    assert result["missing_keys"] == ["b", "c"]


def test_compare_keys_extra_keys_reported():
    result = cl._compare_keys({"a": "Alpha"}, {"a": "Альфа", "zz": "Лишний"})
    assert result["extra_keys"] == ["zz"]
    assert result["ru_keys"] == 2


def test_compare_keys_empty_en_marks_missing():
    result = cl._compare_keys({}, {})
    assert result["status"] == "missing"
    assert result["percentage"] == 0.0


def test_identical_detection_ignores_values_without_letters():
    """Числа, плейсхолдеры и чистые коды форматирования не считаются забытым переводом."""
    en = {"n": "10", "fmt": "%s", "code": "§c", "real": "Hello"}
    ru = {"n": "10", "fmt": "%s", "code": "§c", "real": "Привет"}
    result = cl._compare_keys(en, ru)
    assert result["identical_keys"] == []


def test_identical_detection_ignores_styled_same_color():
    """§c — не буква: раньше такие ключи попадали в «Совпадает с EN»."""
    assert cl._has_visible_letters("§c") is False
    assert cl._has_visible_letters("§4a§l") is False
    assert cl._has_visible_letters("§4a1f") is False
    assert cl._has_visible_letters("§cRed") is True
    assert cl._has_visible_letters("Привет") is True
    assert cl._has_visible_letters("10") is False


def test_identical_detection_ignores_placeholders():
    """Плейсхолдеры — не перевод: буква внутри «%s» не должна считаться текстом."""
    assert cl._has_visible_letters("%s") is False
    assert cl._has_visible_letters("%1$s") is False
    assert cl._has_visible_letters("%.2f") is False
    assert cl._has_visible_letters("{}") is False
    assert cl._has_visible_letters("{0}") is False
    assert cl._has_visible_letters("{name}") is False
    assert cl._has_visible_letters("%s apples") is True
    assert cl._has_visible_letters("Hello %s") is True


def test_compare_keys_empty_values_collected_but_percentage_unchanged():
    """Пустое значение попадает в empty_keys и НЕ портит процент по умолчанию."""
    cl.CONFIG.pop("count_empty_as_missing", None)
    en = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta"}
    ru = {"a": "Альфа", "b": "", "c": "   ", "d": "Дельта"}
    result = cl._compare_keys(en, ru)
    assert result["empty_keys"] == ["b", "c"]
    assert result["empty_count"] == 2
    assert result["missing_keys"] == []
    assert result["percentage"] == 100.0
    assert result["status"] == "full"


def test_compare_keys_flag_folds_empty_into_missing():
    """С count_empty_as_missing пустые ключи понижают процент."""
    cl.CONFIG["count_empty_as_missing"] = True
    try:
        en = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta"}
        ru = {"a": "Альфа", "b": "", "c": "   ", "d": "Дельта"}
        result = cl._compare_keys(en, ru)
        assert result["missing_keys"] == ["b", "c"]
        assert result["percentage"] == 50.0
        assert result["status"] == "partial"
    finally:
        cl.CONFIG.pop("count_empty_as_missing", None)


def test_compare_keys_flag_zero_percent_is_missing():
    """В строгом режиме 0% — это «Отсутствует», а не «Частично».

        Файл перевода есть, но переведено в нём ровно ничего; показывать такое
        мод в «Неполный» с нулевым процентом бессмысленно.
    """
    en = {"a": "Alpha", "b": "Beta"}
    ru = {"a": "", "b": ""}
    cl.CONFIG.pop("count_empty_as_missing", None)
    assert cl._compare_keys(en, ru)["status"] == "full"          # дефолт не меняем
    cl.CONFIG["count_empty_as_missing"] = True
    try:
        result = cl._compare_keys(en, ru)
        assert result["percentage"] == 0.0
        assert result["status"] == "missing"
    finally:
        cl.CONFIG.pop("count_empty_as_missing", None)


def test_new_mod_result_has_all_fields():
    """Фабрика результата должна отдавать полный набор полей (иначе отчёт 'разъезжается')."""
    result = cl.new_mod_result("x.jar (x)", status="skipped", error="нет локализации")
    for field in ("mod_name", "status", "source", "ru_keys", "en_keys", "percentage",
                  "missing_keys", "extra_keys", "identical_keys", "identical_count",
                  "empty_keys", "empty_count", "error", "patchouli"):
        assert field in result, f"нет поля {field}"
    assert result["status"] == "skipped"
    assert result["error"] == "нет локализации"
    assert result["patchouli"] == []


def test_new_mod_result_patchouli_not_shared():
    """Список гайдбуков не должен быть общим объектом между результатами."""
    books = [{"book_name": "guide"}]
    first = cl.new_mod_result("a", patchouli=books)
    second = cl.new_mod_result("b")
    first["patchouli"].append({"book_name": "extra"})
    assert len(books) == 1
    assert second["patchouli"] == []


def test_identical_detection_flags_same_text():
    en = {"a": "Crafting Station", "b": "Hello"}
    ru = {"a": "Crafting Station", "b": "Привет"}
    result = cl._compare_keys(en, ru)
    assert result["identical_keys"] == ["a"]
    assert result["identical_count"] == 1


def test_identical_comparison_ignores_surrounding_spaces():
    en = {"a": "Hello"}
    ru = {"a": "  Hello  "}
    assert cl._compare_keys(en, ru)["identical_keys"] == ["a"]


# ───────────────────────── коды языков ─────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("RU", "ru_ru"),
    ("de-DE", "de_de"),
    ("en", "en_us"),
    ("zh", "zh_cn"),
    ("pt", "pt_br"),
    ("cs", "cs_cs"),
    ("", "ru_ru"),
    (None, "ru_ru"),
])
def test_normalize_lang_code(raw, expected):
    assert cl.normalize_lang_code(raw) == expected


def test_target_filenames_follow_language():
    cl.set_target_language("de_de")
    try:
        assert cl.target_json_filename() == "de_de.json"
        assert cl.target_lang_filename() == "de_DE.lang"
        assert cl.target_lang_display() == "DE"
        assert cl._is_target_lang_file("de_de.lang")
        assert not cl._is_target_lang_file("ru_RU.lang")
        # ловушка: имя не должно совпадать по подстроке где-то в пути
        assert not cl._is_target_lang_file("structure.lang")
    finally:
        cl.set_target_language("ru_ru")


# ──────────────────────────── палитра ────────────────────────────

def test_theme_palette_has_all_roles():
    for theme in ("light", "dark"):
        for role in ("bg", "fg", "panel", "box", "btn", "sep", "muted"):
            assert cl.THEMES[theme].get(role), f"{theme}.{role} отсутствует"


def test_theme_colors_returns_copy():
    first = cl.theme_colors(False)
    first["bg"] = "#000000"
    assert cl.theme_colors(False)["bg"] != "#000000"
    assert cl.theme_colors(True)["bg"] == cl.THEMES["dark"]["bg"]


def test_palettes_differ():
    assert cl.THEMES["light"] != cl.THEMES["dark"]


# ─────────────────────── категории и реестр ───────────────────────

def test_category_labels_cover_all_results():
    results = {"full": [], "partial": [], "missing": [], "translated": [], "outdated": []}
    assert set(cl.CATEGORY_LABELS) == set(results)


def test_patched_books_compare_uses_translated_mods(tmp_path, monkeypatch):
    """Страницы гайдбука дополняются файлами из TranslatedMods."""
    jar = tmp_path / "bookmod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        for page in ("a", "b"):
            zf.writestr(f"assets/bookmod/patchouli_books/guide/en_us/entries/{page}.json", "{}")
        zf.writestr("assets/bookmod/patchouli_books/guide/ru_ru/entries/a.json", "{}")
        zf.writestr("assets/bookmod/lang/en_us.json", json.dumps({"k": "v"}))

    tm = tmp_path / "TranslatedMods"
    (tm / "bookmod" / "patchouli_books" / "guide" / "ru_ru" / "entries").mkdir(parents=True)
    (tm / "bookmod" / "patchouli_books" / "guide" / "ru_ru" / "entries" / "b.json").write_text("{}")

    monkeypatch.setattr(cl, "TRANSLATED_MODS_PATH", tm)
    books = cl.check_patchouli_books(jar)
    assert len(books) == 1
    book = books[0]
    assert book["status"] == "full"
    assert book["percentage"] == 100.0
    assert book["missing_files"] == []
    assert book["en_files"] == 2


def test_scan_categorizes_mods(tmp_path, monkeypatch):
    """Мод на 100% -> full, на 50% -> partial, без перевода -> missing."""
    mods = tmp_path / "mods"
    mods.mkdir()

    full = mods / "fullmod.jar"
    with zipfile.ZipFile(full, "w") as zf:
        zf.writestr("assets/fullmod/lang/en_us.json", json.dumps({"a": "A", "b": "B"}))
        zf.writestr("assets/fullmod/lang/ru_ru.json", json.dumps({"a": "A", "b": "Б"}))

    half = mods / "halfmod.jar"
    with zipfile.ZipFile(half, "w") as zf:
        zf.writestr("assets/halfmod/lang/en_us.json", json.dumps({"a": "A", "b": "B"}))
        zf.writestr("assets/halfmod/lang/ru_ru.json", json.dumps({"a": "A"}))

    none = mods / "nomod.jar"
    with zipfile.ZipFile(none, "w") as zf:
        zf.writestr("assets/nomod/lang/en_us.json", json.dumps({"a": "A"}))

    nolang = mods / "nolang.jar"
    with zipfile.ZipFile(nolang, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")

    monkeypatch.setattr(cl, "TRANSLATED_MODS_PATH", None)
    results = cl.scan_jars_directory(mods)

    by_name = {m["mod_name"]: m for cat in results.values() for m in cat}
    assert "fullmod.jar (fullmod)" in by_name
    assert by_name["fullmod.jar (fullmod)"]["status"] == "full"
    assert by_name["halfmod.jar (halfmod)"]["status"] == "partial"
    assert by_name["nomod.jar (nomod)"]["status"] == "missing"
    # мод без папки локализации вообще не должен попадать в результаты
    assert "nolang.jar" not in by_name
    assert len(results["full"]) == 1
    assert len(results["partial"]) == 1
    assert len(results["missing"]) == 1


def test_scan_skips_service_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "TRANSLATED_MODS_PATH", None)
    (tmp_path / ".connector").mkdir()
    jar = tmp_path / ".connector" / "hidden.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/x/lang/en_us.json", "{}")
    assert cl.find_all_mod_files(tmp_path) == []


def test_broken_archive_logged_as_error(tmp_path, monkeypatch):
    """Повреждённый архив не должен молча исчезать.

        Раньше он не попадал ни в одну вкладку И ни в «Ошибки»: BadZipFile гасился
        внутри find_mod_lang_files_in_archive, и до except в scan_jars_directory
        выполнение не доходило.
    """
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "broken.jar").write_bytes(b"not a zip at all")
    monkeypatch.setattr(cl, "TRANSLATED_MODS_PATH", None)
    cl.clear_error_log()

    results = cl.scan_jars_directory(mods)

    # в категории не попадает (локализацию подтвердить нечем)
    names = [m["mod_name"] for cat in results.values() for m in cat]
    assert "broken.jar" not in names
    # но обязательно виден во вкладке «Ошибки»
    errors = cl.get_error_log()
    assert any("broken.jar" in e["message"] and "повреждённый" in e["message"] for e in errors), errors


# ────────────────────── ассеты внутри архива ──────────────────────

def test_extract_mod_name_from_assets():
    import io
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("assets/advancedlootinfo/lang/en_us.json", "{}")
        assert cl.extract_mod_name_from_assets(path) == "advancedlootinfo"
    finally:
        os.unlink(path)


def test_safe_archive_subpath_blocks_zip_slip():
    assert cl._safe_archive_subpath("entries/a.json") == ["entries", "a.json"]
    assert cl._safe_archive_subpath("./entries/a.json") == ["entries", "a.json"]
    assert cl._safe_archive_subpath("../evil.json") is None
    assert cl._safe_archive_subpath("a/../../evil.json") is None
    assert cl._safe_archive_subpath("C:/evil.json") is None
    assert cl._safe_archive_subpath("") is None


# ────────────────────────── JarReader ──────────────────────────

@pytest.fixture()
def sample_jar(tmp_path):
    jar = tmp_path / "sample.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/sample/lang/en_us.json", json.dumps({"a": "Alpha"}))
        zf.writestr("assets/sample/lang/ru_ru.json", json.dumps({"a": "Альфа"}))
        zf.writestr("assets/sample/lang/EN_US.LANG", "k=V\n")
        zf.writestr("assets/sample/patchouli_books/guide/en_us/entries/p1.json", "{}")
    return jar


def test_jar_reader_resolves_case_insensitively(sample_jar):
    with cl.JarReader(sample_jar) as reader:
        assert reader.resolve("assets/sample/lang/en_us.json") == "assets/sample/lang/en_us.json"
        assert reader.resolve("ASSETS/SAMPLE/LANG/EN_US.JSON") == "assets/sample/lang/en_us.json"
        assert reader.resolve("assets\\sample\\lang\\en_us.json") == "assets/sample/lang/en_us.json"
        assert reader.resolve("assets/sample/lang/nope.json") is None


def test_jar_reader_reads_json_and_lang(sample_jar):
    with cl.JarReader(sample_jar) as reader:
        assert reader.read_json("assets/sample/lang/en_us.json") == {"a": "Alpha"}
        assert reader.read_json("assets/sample/lang/ru_ru.json") == {"a": "Альфа"}
        assert reader.read_json("assets/sample/lang/missing.json") is None
        assert reader.read_lang("assets/sample/lang/en_us.lang") == {"k": "V"}


def test_jar_reader_read_bytes_unknown_path(sample_jar):
    with cl.JarReader(sample_jar) as reader:
        with pytest.raises(KeyError):
            reader.read_bytes("nope.json")


def test_jar_reader_opens_archive_once(sample_jar, monkeypatch):
    """Ключевая оптимизация: один ZipFile на весь архив, а не на каждый файл."""
    opened = []
    orig = zipfile.ZipFile

    class Counting(orig):
        def __init__(self, *a, **k):
            opened.append(a[0] if a else None)
            super().__init__(*a, **k)

    monkeypatch.setattr(zipfile, "ZipFile", Counting)
    cl.check_jar_localization(sample_jar)
    assert len(opened) == 1, f"архив открыт {len(opened)} раз(а) вместо одного"


def test_jar_reader_closes_file(sample_jar):
    reader = cl.JarReader(sample_jar)
    reader.close()
    with pytest.raises(Exception):
        reader.read_bytes("assets/sample/lang/en_us.json")


def test_one_shot_wrappers(sample_jar):
    """Одноразовые обёртки над JarReader: открывают архив, читают и закрывают."""
    assert cl.extract_json_from_jar(sample_jar, "assets/sample/lang/en_us.json") == {"a": "Alpha"}
    assert cl.parse_lang_from_jar(sample_jar, "assets/sample/lang/en_us.lang") == {"k": "V"}
    assert cl.extract_json_from_jar(sample_jar, "нет/такого.json") is None
    assert cl.parse_lang_from_jar(sample_jar, "нет/такого.lang") is None

    info = cl.find_mod_lang_files_in_archive(sample_jar)
    assert "sample" in info
    assert info["sample"]["en_us_candidates"] == ["assets/sample/lang/en_us.json"]


def test_wrappers_report_broken_archive(tmp_path):
    """Битый архив не должен ронять обёртки — они возвращают пусто и пишут в «Ошибки»."""
    broken = tmp_path / "broken.jar"
    broken.write_bytes(b"definitely not a zip")
    cl.clear_error_log()
    assert cl.extract_json_from_jar(broken, "assets/x/lang/en_us.json") is None
    assert cl.find_mod_lang_files_in_archive(broken) == {}
    errors = cl.get_error_log()
    assert any("broken.jar" in e["message"] for e in errors), errors


def test_check_jar_localization_reports_broken_archive(tmp_path):
    broken = tmp_path / "broken.jar"
    broken.write_bytes(b"definitely not a zip")
    cl.clear_error_log()
    assert cl.check_jar_localization(broken) == []
    assert any("broken.jar" in e["message"] for e in cl.get_error_log())


# ──────────────────────── Patchouli-разбор ────────────────────────

def test_parse_patchouli_index_groups_pages():
    names = [
        "assets/mod/patchouli_books/guide/en_us/entries/a.json",
        "assets/mod/patchouli_books/guide/en_us/entries/b.json",
        "assets/mod/patchouli_books/guide/ru_ru/entries/a.json",
        "assets/mod/patchouli_books/guide/de_de/entries/a.json",
        "assets/mod/patchouli_books/guide/",              # папка — игнорируем
        "assets/mod/lang/en_us.json",                    # не гайдбук
    ]
    index = cl.parse_patchouli_index(names)
    assert set(index) == {"mod/guide"}
    book = index["mod/guide"]
    assert set(book["en"]) == {"entries/a.json", "entries/b.json"}
    assert set(book["ru"]) == {"entries/a.json"}
    assert book["en"]["entries/a.json"] == "assets/mod/patchouli_books/guide/en_us/entries/a.json"


def test_parse_patchouli_index_ignores_wrong_layout():
    names = [
        "other/mod/patchouli_books/guide/en_us/entries/a.json",  # нет assets/ сразу перед модом
        "assets/mod/deep/patchouli_books/guide/en_us/entries/a.json",  # лишний сегмент
    ]
    assert cl.parse_patchouli_index(names) == {}


def test_patchouli_stats():
    stats = cl._patchouli_stats({"a", "b", "c"}, {"a", "b"})
    assert stats["status"] == "partial"
    assert stats["percentage"] == round(200 / 3, 2)
    assert stats["missing_files"] == ["c"]
    assert stats["extra_files"] == []
    assert stats["en_files"] == 3 and stats["ru_files"] == 2

    full = cl._patchouli_stats({"a"}, {"a"})
    assert full["status"] == "full" and full["percentage"] == 100.0

    none = cl._patchouli_stats({"a"}, set())
    assert none["status"] == "missing" and none["percentage"] == 0.0

    extra = cl._patchouli_stats({"a"}, {"a", "zz"})
    assert extra["extra_files"] == ["zz"]
