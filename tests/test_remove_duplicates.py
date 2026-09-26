"""
Тесты remove_duplicates: починка запятых, удаление дублей, сохранение
форматирования, безопасность записи (бэкап/откат/dry-run).

Запуск:  python -m pytest tests/ -q
"""
import json
import queue
import shutil
from pathlib import Path

import pytest

import remove_duplicates as rd

NL = "\n"
Q = '"'


@pytest.fixture(autouse=True)
def _backups_to_tmp(monkeypatch, tmp_path):
    """Резервные копии из тестов не должны появляться в папке проекта."""
    monkeypatch.setattr(rd, "SCRIPT_DIR", tmp_path / "_script_dir")
    (tmp_path / "_script_dir").mkdir(exist_ok=True)


def lines(*items):
    return NL.join(items)


def parsed(text):
    return json.loads(rd.strip_comments(text)[0])


# ──────────────────────────── починка запятых ────────────────────────────

def test_missing_comma_is_repaired():
    text = lines("{", f'  {Q}a{Q}: 1', f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert len(report.comma_fixes) == 1
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_repair_places_comma_at_end_of_line():
    """Запятая должна ставиться ПОСЛЕ значения, а не перед следующим ключом."""
    text = lines("{", f'  {Q}a{Q}: 1', f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.new_text == lines("{", f'  {Q}a{Q}: 1,', f'  {Q}b{Q}: 2', "}")


def test_several_missing_commas_repaired():
    text = lines("{", f'  {Q}a{Q}: 1', f'  {Q}b{Q}: 2', f'  {Q}c{Q}: 3', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert len(report.comma_fixes) == 2
    assert parsed(report.new_text) == {"a": 1, "b": 2, "c": 3}


def test_other_errors_are_not_touched():
    """Пропущенное двоеточие — не наша ошибка, файл трогать нельзя."""
    text = lines("{", f'  {Q}a{Q} 1', "}")
    report = rd.process_text(text)
    assert report.error is not None
    assert "Expecting ':'" in str(report.error)
    assert report.new_text is None
    assert report.comma_fixes == []


def test_comma_repair_can_be_disabled():
    text = lines("{", f'  {Q}a{Q}: 1', f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text, fix_commas=False)
    assert report.error is not None
    assert report.new_text is None


def test_broken_syntax_is_not_repaired():
    report = rd.process_text("{")
    assert report.error is not None


def test_insertion_refused_when_previous_symbol_opens_value():
    """Если перед позицией символ открывает значение — это не «не хватает запятой»."""
    pos, reason = rd.find_comma_insert_pos('{"a": {', 7)
    assert pos is None
    assert reason


# ──────────────────────────── удаление дублей ────────────────────────────

def test_duplicate_at_end_without_comma():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}b{Q}: 2,', f'  {Q}a{Q}: 3', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert [k for k, _ in report.removed] == ["a"]
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_duplicate_in_middle():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2,', f'  {Q}b{Q}: 3', "}")
    report = rd.process_text(text)
    assert [k for k, _ in report.removed] == ["a"]
    assert parsed(report.new_text) == {"a": 1, "b": 3}


def test_two_trailing_duplicates():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}b{Q}: 2,', f'  {Q}b{Q}: 3,', f'  {Q}b{Q}: 4', "}")
    report = rd.process_text(text)
    assert len(report.removed) == 2
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_keep_last_occurrence():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}")
    report = rd.process_text(text, keep_first=False)
    assert parsed(report.new_text) == {"a": 2}


def test_three_occurrences_keep_first():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2,', f'  {Q}a{Q}: 3', "}")
    report = rd.process_text(text)
    assert len(report.removed) == 2
    assert parsed(report.new_text) == {"a": 1}


def test_same_keys_in_different_objects_are_not_duplicates():
    text = lines("{", f'  {Q}one{Q}: {{{Q}x{Q}: 1}},', f'  {Q}two{Q}: {{{Q}x{Q}: 2}}', "}")
    report = rd.process_text(text)
    assert report.removed == []
    assert report.new_text == text


def test_duplicate_inside_nested_object():
    text = lines("{", f'  {Q}a{Q}: {{', f'    {Q}x{Q}: 1,', f'    {Q}x{Q}: 2', "  }", "}")
    report = rd.process_text(text)
    assert [k for k, _ in report.removed] == ["x"]
    assert parsed(report.new_text) == {"a": {"x": 1}}


def test_duplicate_with_multiline_value():
    text = lines("{", f'  {Q}a{Q}: {{', f'    {Q}x{Q}: 1', "  },", f'  {Q}a{Q}: {{',
                 f'    {Q}y{Q}: 2', "  }", "}")
    report = rd.process_text(text)
    assert [k for k, _ in report.removed] == ["a"]
    assert parsed(report.new_text) == {"a": {"x": 1}}


def test_single_line_object():
    text = f'{{{Q}a{Q}: 1, {Q}a{Q}: 2, {Q}b{Q}: 3}}'
    report = rd.process_text(text)
    assert [k for k, _ in report.removed] == ["a"]
    assert parsed(report.new_text) == {"a": 1, "b": 3}


def test_clean_file_untouched():
    text = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.removed == []
    assert report.changed is False
    assert report.new_text == text


def test_result_is_idempotent():
    text = lines("{", f'  {Q}a{Q}: 1', f'  {Q}a{Q}: 2,', f'  {Q}b{Q}: 3', "}")
    first = rd.process_text(text)
    second = rd.process_text(first.new_text)
    assert second.removed == []
    assert second.new_text == first.new_text


# ──────────────────────── комментарии и формат ────────────────────────

def test_comments_are_preserved():
    text = lines(
        "{",
        "  // переводы",
        f'  {Q}a{Q}: 1,   // первый',
        f'  {Q}a{Q}: 2,',
        "  /* блок",
        "     комментарий */",
        f'  {Q}b{Q}: 3',
        "}",
    )
    report = rd.process_text(text)
    assert report.error is None
    for marker in ("// переводы", "// первый", "/* блок", "комментарий */"):
        assert marker in report.new_text
    assert parsed(report.new_text) == {"a": 1, "b": 3}


def test_comment_with_missing_comma_is_repaired():
    text = lines("{", f'  {Q}a{Q}: 1   // пять', f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert len(report.comma_fixes) == 1
    assert "// пять" in report.new_text
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_strip_comments_keeps_line_numbers():
    text = lines("{", "  // коммент", "  /* блок", "     много строк */", f'  {Q}a{Q}: 1', "}")
    clean, offsets = rd.strip_comments(text)
    assert clean.count(NL) == text.count(NL)
    assert "//" not in clean and "/*" not in clean
    assert len(offsets) == len(clean)


def test_hash_comment_is_stripped():
    """Одиночный # — комментарий в Minecraft-локализациях."""
    text = lines("{", f'  {Q}a{Q}: 1', "  # сделал qoid_cr", f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert "# сделал qoid_cr" in report.new_text
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_hash_inside_string_is_kept():
    """# внутри значения — это данные, а не комментарий."""
    text = lines("{", f'  {Q}color{Q}: {Q}#FF00AA{Q},', f'  {Q}a{Q}: 1', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert parsed(report.new_text) == {"color": "#FF00AA", "a": 1}
    assert "#FF00AA" in report.new_text


def test_hash_comment_with_missing_comma_is_repaired():
    text = lines("{", f'  {Q}a{Q}: 1', "  # five", f'  {Q}b{Q}: 2', "}")
    report = rd.process_text(text)
    assert report.error is None
    assert len(report.comma_fixes) == 1
    assert parsed(report.new_text) == {"a": 1, "b": 2}


def test_crlf_preserved(tmp_path):
    p = tmp_path / "ru_ru.json"
    p.write_bytes(lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}").replace(NL, "\r\n").encode("utf-8"))
    rd.run_scan(tmp_path, False, True, queue.Queue(), lang_only=True, dry_run=False)
    data = p.read_bytes()
    assert b"\r\n" in data
    assert b"\r\r\n" not in data
    assert json.loads(rd.strip_comments(p.read_text(encoding="utf-8"))[0]) == {"a": 1}


def test_bom_preserved(tmp_path):
    p = tmp_path / "ru_ru.json"
    p.write_bytes(b"\xef\xbb\xbf" + lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}").encode("utf-8"))
    rd.run_scan(tmp_path, False, True, queue.Queue(), lang_only=True, dry_run=False)
    assert p.read_bytes()[:3] == b"\xef\xbb\xbf"
    assert json.loads(rd.strip_comments(p.read_text(encoding="utf-8-sig"))[0]) == {"a": 1}


def test_cp1251_stays_cp1251(tmp_path):
    p = tmp_path / "ru_ru.json"
    p.write_bytes(lines("{", f'  {Q}a{Q}: 1,   // примечание', f'  {Q}a{Q}: 2', "}").encode("cp1251"))
    rd.run_scan(tmp_path, False, True, queue.Queue(), lang_only=True, dry_run=False)
    assert "// примечание" in p.read_bytes().decode("cp1251")


def test_no_trailing_newline_preserved(tmp_path):
    p = tmp_path / "ru_ru.json"
    p.write_text(lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}"), encoding="utf-8")
    rd.run_scan(tmp_path, False, True, queue.Queue(), lang_only=True, dry_run=False)
    assert not p.read_text(encoding="utf-8").endswith(NL)


# ──────────────────────────── проверки ────────────────────────────

def test_verification_catches_data_mismatch():
    before = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}")
    after = lines("{", f'  {Q}b{Q}: 9', "}")
    problem = rd.verify_result(before, after, keep_first=True)
    assert problem is not None
    assert "совпад" in problem


def test_verification_passes_for_correct_result():
    before = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}")
    after = lines("{", f'  {Q}a{Q}: 1', "}")
    assert rd.verify_result(before, after, keep_first=True) is None


def test_verification_catches_left_duplicates():
    before = lines("{", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2', "}")
    problem = rd.verify_result(before, before, keep_first=True)
    assert problem is not None and "дубликат" in problem


# ──────────────────────── файловый конвейер ────────────────────────

def _make_tree(root):
    work = root / "work"
    (work / "sub").mkdir(parents=True)
    dup = lines("{", "  // коммент", f'  {Q}a{Q}: 1,', f'  {Q}a{Q}: 2,', f'  {Q}b{Q}: 3', "}")

    (work / "ru_ru.json").write_text(dup, encoding="utf-8")
    (work / "sub" / "ru_ru.json").write_text(lines("{", f'  {Q}x{Q}: 1', "}"), encoding="utf-8")
    (work / "blockstates").mkdir()
    (work / "blockstates" / "model.json").write_text(dup, encoding="utf-8")
    (work / "broken").mkdir()
    (work / "broken" / "ru_ru.json").write_text(lines("{", f'  {Q}a{Q} 1', "}"), encoding="utf-8")
    return work


def test_lang_only_filter_skips_blockstates(tmp_path):
    work = _make_tree(tmp_path)
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=True)
    assert stats["total"] == 3          # два ru_ru.json + broken
    assert (work / "blockstates" / "model.json").read_text(encoding="utf-8").count('"a": 2') == 1


def test_all_json_mode_includes_everything(tmp_path):
    work = _make_tree(tmp_path)
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=False, dry_run=True)
    assert stats["total"] == 4


def test_dry_run_writes_nothing(tmp_path):
    work = _make_tree(tmp_path)
    before = (work / "ru_ru.json").read_bytes()
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=True)
    assert stats["changed"] == 1
    assert (work / "ru_ru.json").read_bytes() == before
    assert "backup" not in stats


def test_apply_mode_changes_and_backs_up(tmp_path):
    work = _make_tree(tmp_path)
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=False)
    assert stats["changed"] == 1
    assert Path(stats["backup"]).exists()
    text = (work / "ru_ru.json").read_text(encoding="utf-8")
    assert '"a": 2' not in text
    assert "// коммент" in text
    assert json.loads(rd.strip_comments(text)[0]) == {"a": 1, "b": 3}
    shutil.rmtree(stats["backup"])


def test_broken_file_is_left_alone(tmp_path):
    work = _make_tree(tmp_path)
    broken = work / "broken" / "ru_ru.json"
    before = broken.read_bytes()
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=False)
    assert broken.read_bytes() == before
    assert stats["skipped"] == 1
    if stats.get("backup"):
        shutil.rmtree(stats["backup"])


def test_rollback_restores_files(tmp_path):
    work = _make_tree(tmp_path)
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=False)
    backup = Path(stats["backup"])
    target = work / "ru_ru.json"
    assert '"a": 2' not in target.read_text(encoding="utf-8")

    restored, errors = rd.restore_backup(backup)
    assert errors == []
    assert restored == 1
    assert '"a": 2' in target.read_text(encoding="utf-8")
    shutil.rmtree(backup)


def test_rollback_without_backup_reports_error(tmp_path):
    n, errors = rd.restore_backup(tmp_path / "backup_нет")
    assert n == 0 and errors


def test_second_run_finds_nothing(tmp_path):
    work = _make_tree(tmp_path)
    rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=False)
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=True)
    assert stats["changed"] == 0


def test_cancel_stops_scan(tmp_path):
    work = _make_tree(tmp_path)
    stop = rd.threading.Event()
    stop.set()
    stats = rd.run_scan(work, True, True, queue.Queue(), lang_only=True, dry_run=True, cancel=stop)
    assert stats["changed"] == 0


# ──────────────────────────── фильтр файлов ────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("ru_ru.json", True),
    ("en_US.json", True),
    ("zh_cn.json", True),
    ("pt_br.json", True),
    ("model.json", False),
    ("blockstates.json", False),
    ("index.json", False),
])
def test_lang_file_detection(name, expected):
    assert rd.is_lang_file(Path(name)) is expected


def test_lang_dir_counts_as_lang_file():
    assert rd.is_lang_file(Path("some/mod/lang/whatever.json")) is True
    assert rd.is_lang_file(Path("some/mod/blockstates/whatever.json")) is False


def test_no_folder_is_excluded(tmp_path):
    """Ни одна папка не исключается — раньше молча пропускалась «переводы для RTFE»,
    из-за чего 408 файлов выглядели как «ничего не найдено»."""
    root = tmp_path / "mods"
    special = root / "переводы для RTFE"
    special.mkdir(parents=True)
    (special / "ru_ru.json").write_text("{}", encoding="utf-8")
    (root / "ru_ru.json").write_text("{}", encoding="utf-8")

    scan = rd.collect_files(root, True, True)
    assert len(scan.files) == 2
    assert not hasattr(scan, "excluded")


def test_cyrillic_and_spaces_in_path_are_fine(tmp_path):
    """Русские буквы, пробелы и капс в путях — штатная ситуация."""
    root = tmp_path / "Мои переводы (2026)"
    nested = root / "ПЕРЕВОДЫ ДЛЯ RTFE" / "мод"
    nested.mkdir(parents=True)
    (nested / "ru_ru.json").write_text("{}", encoding="utf-8")
    scan = rd.collect_files(root, True, True)
    assert len(scan.files) == 1
    assert rd.run_scan(root, True, True, queue.Queue(), dry_run=True)["total"] == 1


def test_not_lang_count_is_reported(tmp_path):
    """Отсеянное фильтром «только языковые» считаем — молчать нельзя."""
    root = tmp_path / "mods"
    (root / "models").mkdir(parents=True)
    (root / "models" / "block.json").write_text("{}", encoding="utf-8")
    (root / "ru_ru.json").write_text("{}", encoding="utf-8")

    scan = rd.collect_files(root, True, True)
    assert [p.name for p in scan.files] == ["ru_ru.json"]
    assert scan.not_lang == 1

    all_files = rd.collect_files(root, True, False)
    assert len(all_files.files) == 2
    assert all_files.not_lang == 0

    stats = rd.run_scan(root, True, True, queue.Queue(), lang_only=True, dry_run=True)
    assert stats["not_lang"] == 1


# ──────────────────────────── настройки ────────────────────────────

def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "SETTINGS_PATH", tmp_path / "settings.json")
    assert rd.load_settings() == rd.DEFAULT_SETTINGS
    rd.save_settings({"dry_run": False, "recursive": False, "keep_first": False,
                      "lang_only": True, "fix_commas": True, "last_directory": "C:/x"})
    loaded = rd.load_settings()
    assert loaded["dry_run"] is False
    assert loaded["recursive"] is False
    assert loaded["last_directory"] == "C:/x"


def test_settings_ignore_garbage(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text("{не json", encoding="utf-8")
    monkeypatch.setattr(rd, "SETTINGS_PATH", p)
    assert rd.load_settings() == rd.DEFAULT_SETTINGS
