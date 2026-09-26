"""
Удаление дубликатов ключей в JSON-файлах локализации (Minecraft-моды, TranslatedMods).

Ключевая идея: файл НЕ пересобирается через json.dumps. Правки вносятся прямо в
исходный текст — удаляются только строки/фрагменты с дублирующимися ключами и
добавляются недостающие запятые. Поэтому сохраняются комментарии (// и /* */),
пустые строки, отступы, исходный порядок и форматирование значений.

Безопасность:
  * режим «только отчёт» — ничего не записывается;
  * перед записью файл копируется в backup_<дата_время>/ по тому же пути;
  * запись атомарная: временный файл + os.replace;
  * после записи файл перечитывается и проверяется (парсится ли, остались ли
    дубликаты, совпал ли результат с ожидаемым) — при несовпадении откат из бэкапа;
  * кнопка «Откатить последний запуск».

Запуск:  python remove_duplicates.py
"""

import bisect
import datetime
import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import List, NamedTuple, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = SCRIPT_DIR / "remove_duplicates.json"

LANG_FILE_RE = re.compile(r"^[a-z]{2,3}([_-][a-z]{2,3})?\.json$", re.IGNORECASE)
LANG_DIR_NAMES = {"lang", "language"}
MAX_COMMA_FIXES = 200  # предохранитель от бесконечного цикла починки

logger = logging.getLogger("remove_duplicates")
logger.setLevel(logging.DEBUG)
logger.propagate = False
if not logger.handlers:
    _console = logging.StreamHandler()
    _console.setLevel(logging.WARNING)
    _console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_console)
    try:
        _file = RotatingFileHandler(
            SCRIPT_DIR / "remove_duplicates.log",
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        _file.setLevel(logging.DEBUG)
        _file.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(_file)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Разбор позиций
# ──────────────────────────────────────────────────────────────────────────────

class LineIndex:
    """Быстрое преобразование абсолютной позиции в (строка, столбец)."""

    def __init__(self, text: str):
        # regex вместо посимвольного цикла: на больших файлах разница в разы
        self.starts = [0] + [m.end() for m in _NEWLINE_RE.finditer(text)]

    def line_col(self, pos: int):
        idx = bisect.bisect_right(self.starts, pos) - 1
        return idx + 1, pos - self.starts[idx]  # 1-based


WHITESPACE = " \t\r\n"

_NEWLINE_RE = re.compile("\n")
# Строка целиком одним махом — в разы быстрее посимвольного обхода
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
# Символы, влияющие на структуру: всё остальное (пробелы, числа, true/false) пропускаем
_STRUCT_RE = re.compile(r'["{}\[\]]')
_WS_RE = re.compile(r"[ \t\r\n]*")


def skip_ws(text: str, i: int) -> int:
    """Пропускает пробельные символы начиная с позиции i."""
    m = _WS_RE.match(text, i)
    return m.end() if m else i


def scan_string_end(text: str, i: int) -> int:
    """i — позиция открывающей кавычки. Возвращает позицию ЗА закрывающей,
    либо -1, если строка не закрыта."""
    if i >= len(text) or text[i] != '"':
        return -1
    m = _STRING_RE.match(text, i)
    return m.end() if m else -1


def scan_value_end(text: str, i: int) -> int:
    """i — позиция значения (пропущены пробелы). Возвращает позицию ЗА значением."""
    i = skip_ws(text, i)
    n = len(text)
    if i >= n:
        return n
    c = text[i]
    if c == '"':
        return scan_string_end(text, i)
    if c in "{[":
        depth = 0
        while i < n:
            ch = text[i]
            if ch == '"':
                nxt = scan_string_end(text, i)
                if nxt < 0:
                    return -1
                i = nxt
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return -1
    # число / true / false / null
    while i < n and text[i] not in ",}]" and text[i] not in WHITESPACE:
        i += 1
    return i


# ──────────────────────────────────────────────────────────────────────────────
# Починка недостающих запятых
# ──────────────────────────────────────────────────────────────────────────────

class CommaFix:
    __slots__ = ("pos", "line", "col", "after")

    def __init__(self, pos: int, line: int, col: int, after: str):
        self.pos = pos      # позиция вставки (в очищенном от комментариев тексте)
        self.line = line
        self.col = col
        self.after = after  # какой символ стоял перед вставленной запятой

    def __repr__(self):
        return f"строка {self.line}, после {self.after!r}"


def find_comma_insert_pos(text: str, err_pos: int):
    """Где вставить запятую, чтобы JSON стал валиден.

    Возвращает (позиция вставки, символ перед ней) или (None, причина отказа).

    Вставлять нужно ПОСЛЕ последнего значащего символа, а не по err_pos:
    Python указывает на начало следующего токена, и вставка прямо туда даёт
    уродливое `"a": 1` + `,` + `"b": 2` -> "a": 1,,"b": 2 в одну строку.
    """
    i = err_pos - 1
    while i >= 0 and text[i] in WHITESPACE:
        i -= 1
    if i < 0:
        return None, "перед позицией ошибки нет значащего символа"
    ch = text[i]
    if ch in ",{[:}":
        # Значение только что началось — запятая тут не нужна, значит ошибка другая
        return None, f"предыдущий символ {ch!r} — это не «не хватает запятой»"
    return i + 1, ch


def repair_missing_commas(text: str):
    """Расставляет недостающие запятые, пока файл не станет валидным.

    Возвращает (новый_текст, список CommaFix, данные_json, ошибка|None).
    Меняются ТОЛЬКО ошибки вида "Expecting ',' delimiter" — любые другие
    (пропущенное двоеточие, незакрытая скобка) не трогаются и возвращаются
    вызывающему для показа пользователю.
    """
    fixes = []
    current = text
    for _ in range(MAX_COMMA_FIXES):
        try:
            return current, fixes, json.loads(current), None
        except json.JSONDecodeError as e:
            if e.msg != "Expecting ',' delimiter":
                return current, fixes, None, e
            pos, after = find_comma_insert_pos(current, e.pos)
            if pos is None:
                return current, fixes, None, e
            line, col = _line_col_of(current, pos)
            fixes.append(CommaFix(pos, line, col, after))
            current = current[:pos] + "," + current[pos:]
            logger.debug("Починил запятую: %s (исходная ошибка: line %d col %d)", after, e.lineno, e.colno)
    return current, fixes, None, json.JSONDecodeError(
        "слишком много пропущенных запятых", current, 0
    )


def _line_col_of(text: str, pos: int):
    idx = LineIndex(text)
    return idx.line_col(pos)


# ──────────────────────────────────────────────────────────────────────────────
# Поиск дубликатов
# ──────────────────────────────────────────────────────────────────────────────

class Entry:
    """Одно вхождение ключа в объекте."""

    __slots__ = ("key", "container", "key_start", "value_start", "value_end", "comma_pos", "start_line")

    def __init__(self, key, container, key_start, value_start, value_end, comma_pos, start_line):
        self.key = key
        self.container = container          # id объекта-владельца
        self.key_start = key_start          # позиция открывающей кавычки ключа
        self.value_start = value_start
        self.value_end = value_end
        self.comma_pos = comma_pos          # позиция запятой после значения или None
        self.start_line = start_line

    @property
    def end_pos(self) -> int:
        """Позиция сразу после всей записи (вместе с запятой, если она была)."""
        return self.comma_pos + 1 if self.comma_pos is not None else self.value_end

    def __repr__(self):
        return f"Entry({self.key!r}, line={self.start_line})"


def find_entries(text: str):
    """Находит все записи вида "ключ": значение с их позициями.

    Работает по символьным позициям, поэтому многострочные значения и вложенные
    объекты обрабатываются корректно. ВАЖНО: после записи мы НЕ перепрыгиваем
    значение, а продолжаем идти по нему — иначе ключи внутри вложенных объектов
    никогда не будут найдены.

    Возвращает (список Entry, файл_полностью_разобран|bool).
    """
    idx = LineIndex(text)
    entries = []
    containers = []          # стек id открытых объектов/массивов
    next_id = 1
    n = len(text)
    pos = 0
    while pos < n:
        # Прыгаем к следующему «структурному» символу минуя пробелы и литералы
        match = _STRUCT_RE.search(text, pos)
        if match is None:
            break
        i = match.start()
        c = match.group()
        if c == '"':
            str_end = scan_string_end(text, i)
            if str_end < 0:
                return entries, False       # незакрытая строка
            j = skip_ws(text, str_end)
            if j < n and text[j] == ":":
                value_start = skip_ws(text, j + 1)
                value_end = scan_value_end(text, value_start)
                if value_end < 0:
                    return entries, False
                k = skip_ws(text, value_end)
                comma_pos = k if k < n and text[k] == "," else None
                line, _ = idx.line_col(i)
                entries.append(Entry(
                    key=text[i + 1:str_end - 1],
                    container=tuple(containers),
                    key_start=i,
                    value_start=value_start,
                    value_end=value_end,
                    comma_pos=comma_pos,
                    start_line=line,
                ))
                pos = value_start   # заходим внутрь значения — там могут быть ключи
                continue
            pos = str_end
            continue
        if c in "{[":
            containers.append(next_id)
            next_id += 1
            pos = i + 1
            continue
        # c in "}]"
        if containers:
            containers.pop()
        pos = i + 1
    return entries, True


def find_duplicate_entries(entries, keep_first: bool):
    """Отбирает записи-дубликаты (второе и последующие вхождения ключа)."""
    groups = {}
    for e in entries:
        groups.setdefault((e.container, e.key), []).append(e)
    duplicates = []
    for group in groups.values():
        if len(group) < 2:
            continue
        # Одинаковые ключи в РАЗНЫХ объектах не считаются дублями: сортируем
        # по позиции, чтобы «первым» считалось то, что раньше в файле.
        group.sort(key=lambda e: e.value_start)
        duplicates.extend(group[1:] if keep_first else group[:-1])
    return duplicates


def plan_removals(text: str, entries, duplicates):
    """Строит список (start, end) фрагментов, которые нужно удалить.

    Удаляет и сами дубли (целиком, от кавычки ключа), и «осиротевшие» запятые:
    если последний элемент объекта удаляется, запятая после предыдущего
    элемента больше не нужна.
    """
    entry_spans = [(e.key_start, e.end_pos) for e in duplicates]
    drop_keys = {(e.container, e.key, e.key_start) for e in duplicates}

    # Последний элемент каждого объекта: если он удаляется — снимаем запятую
    # с предыдущего оставшегося элемента.
    by_container = {}
    for e in entries:
        by_container.setdefault(e.container, []).append(e)
    comma_spans = []
    for group in by_container.values():
        if not group:
            continue
        if (group[-1].container, group[-1].key, group[-1].key_start) not in drop_keys:
            continue
        for e in reversed(group):
            if (e.container, e.key, e.key_start) in drop_keys:
                continue
            if e.comma_pos is not None:
                comma_spans.append((e.comma_pos, e.comma_pos + 1))
            break

    # Если удаляемая запись занимала всю строку — убираем строку целиком,
    # иначе остаётся мусор: пустая строка или хвост вида `"b": 2  }`.
    # Запятые под это правило НЕ попадают: строка с ними остаётся осмысленной.
    widened = []
    for start, end in entry_spans:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end == -1:
            line_end = len(text)
        whole_line = (not text[line_start:start].strip()
                      and not text[end:line_end].strip())
        if whole_line:
            start, end = line_start, min(line_end + 1, len(text))
        widened.append((start, end, whole_line))

    spans = sorted(widened + [(s, e, False) for s, e in comma_spans])
    merged = []
    for start, end, whole in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, whole])
    return [(s, e, whole) for s, e, whole in merged]


def apply_removals(text: str, spans) -> str:
    """Удаляет фрагменты по списку (start, end) — справа налево, чтобы не сбивать позиции."""
    out = text
    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + out[end:]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Комментарии
# ──────────────────────────────────────────────────────────────────────────────

def strip_comments(text: str):
    """Убирает комментарии (// , /* */ и одиночный #), сохраняя нумерацию строк.

    Возвращает (clean, offsets), где offsets[i] — позиция символа clean[i]
    в исходном тексте. Переводы строк внутри комментариев сохраняются (сами
    символы — нет), поэтому строка N в clean соответствует строке N оригинала.

    Нужен потому, что json.loads не понимает комментарии, а в локализациях модов
    они встречаются постоянно. Сам файл при этом не переписывается — комментарии
    остаются на месте, мы лишь вырезаем дубликаты.

    Одиночный «#» — это комментарий в Minecraft-локализациях (формат .lang), но
    только вне строк: «#FF00AA» внутри значения остаётся значением.
    """
    out = []
    offsets = []
    n = len(text)
    i = 0
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            offsets.append(i)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                offsets.append(i + 1)
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            offsets.append(i)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # строчный комментарий — вырезаем до конца строки, переводы строк сохраняем
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "#":
            # Minecraft-стиль: одиночный «#» тоже комментарий. В строках он не
            # считается — цикл выше держит in_string, поэтому «#FF00AA» внутри
            # значения останется целым.
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                    offsets.append(i)
                i += 1
            i = min(i + 2, n)
            continue
        out.append(ch)
        offsets.append(i)
        i += 1
    return "".join(out), offsets


# ──────────────────────────────────────────────────────────────────────────────
# Проверка результата
# ──────────────────────────────────────────────────────────────────────────────

def expected_after_dedupe(clean_text: str, keep_first: bool):
    """Словарь, который ДОЛЖЕН получиться после удаления дубликатов.

    Считается через object_pairs_hook — тем же способом, которым парсер видит
    повторяющиеся ключи. Потом результат побайтно сравнивается с тем, что реально
    записали в файл: это ловит любую недоделку в алгоритме удаления.
    """
    holder = {}

    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                if keep_first:
                    continue
                del result[key]
            result[key] = value
        return result

    data = json.loads(clean_text, object_pairs_hook=hook)
    return data


def verify_result(original_text: str, new_text: str, keep_first: bool):
    """Проверяет, что новый текст корректен и равен ожидаемому.

    Возвращает текст ошибки или None. Три независимые проверки:
      1) новый файл разбирается как JSON;
      2) в нём не осталось дубликатов;
      3) данные совпадают с ожидаемыми (ключи И значения).
    """
    try:
        clean_new, _ = strip_comments(new_text)
    except Exception as e:
        return f"не удалось очистить комментарии: {e}"

    try:
        actual = json.loads(clean_new)
    except json.JSONDecodeError as e:
        return f"после правки файл не разбирается: {e.msg} (строка {e.lineno}, столбец {e.colno})"

    entries, ok = find_entries(clean_new)
    if not ok:
        return "после правки файл не разобран сканером"
    left = find_duplicate_entries(entries, keep_first)
    if left:
        return f"после правки осталось дубликатов: {len(left)} (например {left[0].key!r})"

    clean_old, _ = strip_comments(original_text)
    try:
        expected = expected_after_dedupe(clean_old, keep_first)
    except json.JSONDecodeError:
        return None  # исходный файл не парсится — не с чем сравнивать
    if actual != expected:
        return "после правки данные не совпадают с ожидаемыми"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Обработка текста файла целиком
# ──────────────────────────────────────────────────────────────────────────────

class FileReport:
    """Итог по одному файлу."""

    def __init__(self, path):
        self.path = path
        self.removed = []          # список (ключ, строка)
        self.comma_fixes = []      # список CommaFix
        self.new_text = None
        self.error = None
        self.notes = []            # предупреждения (смена кодировки и т.п.)

    @property
    def changed(self) -> bool:
        return bool(self.removed) or bool(self.comma_fixes)

    def summary(self) -> str:
        parts = []
        if self.comma_fixes:
            parts.append(f"запятых добавлено: {len(self.comma_fixes)}")
        if self.removed:
            parts.append(f"дубликатов удалено: {len(self.removed)}")
        return ", ".join(parts) or "без изменений"


def process_text(text: str, keep_first: bool = True, fix_commas: bool = True) -> FileReport:
    """Полный конвейер для текста одного файла: починка запятых + удаление дублей.

    Ничего не записывает — только готовит результат и проверяет его.

    Порядок важен: сначала комментарии вырезаются в рабочую копию, в ней
    json уже понимает файл, а все правки в конце переносятся на оригинал по
    карте позиций. Иначе json.loads спотыкался бы о любой // комментарий.
    """
    report = FileReport(None)

    # 1. Рабочая копия без комментариев + карта позиций
    clean0, offsets0 = strip_comments(text)
    had_comments = clean0 != text

    # 2. Недостающие запятые — в координатах clean0
    if fix_commas:
        _, fixes, _, err = repair_missing_commas(clean0)
        if err is not None:
            report.error = err
            return report
        # Переносим вставки на оригинал
        orig_idx0 = LineIndex(text)
        insertions = []
        for fix in fixes:
            o_pos = offsets0[fix.pos] if fix.pos < len(offsets0) else len(text)
            line, col = orig_idx0.line_col(o_pos)
            report.comma_fixes.append(CommaFix(o_pos, line, col, fix.after))
            insertions.append(o_pos)
        text1 = _insert_commas(text, insertions)
    else:
        try:
            json.loads(clean0)
        except json.JSONDecodeError as e:
            report.error = e
            return report
        text1 = text

    # 3. Дубликаты — в координатах очищенного текста от text1.
    # Если комментариев не было и запятые не вставлялись, второй проход
    # strip_comments не нужен: clean1 == clean0 (это заметно экономит время
    # на больших коллекциях — проход посимвольный).
    if not had_comments and text1 is text:
        clean1, offsets1 = clean0, offsets0
    else:
        clean1, offsets1 = strip_comments(text1)

    entries, ok = find_entries(clean1)
    if not ok:
        report.error = ValueError("файл не разобран")
        return report

    duplicates = find_duplicate_entries(entries, keep_first)
    if not duplicates:
        report.new_text = text1
        return report

    report.removed = [(e.key, e.start_line) for e in duplicates]

    # 4. Переносим удаления на оригинал и проверяем результат
    clean_idx = LineIndex(clean1)
    orig_idx = LineIndex(text1)
    spans = plan_removals(clean1, entries, duplicates)
    orig_spans = [_to_original(span, clean_idx, orig_idx, offsets1, text1) for span in spans]
    text2 = apply_removals(text1, orig_spans)

    problem = verify_result(text1, text2, keep_first)
    if problem:
        report.error = ValueError(problem)
        return report

    report.new_text = text2
    return report


def _insert_commas(text: str, positions) -> str:
    """Вставляет запятые в исходный текст по позициям (справа налево)."""
    out = text
    for pos in sorted(set(positions), reverse=True):
        out = out[:pos] + "," + out[pos:]
    return out

def _to_original(span, clean_idx: LineIndex, orig_idx: LineIndex, offsets, text: str):
    """Переводит интервал из координат clean-текста в координаты оригинала.

    Номера строк совпадают (переводы строк внутри комментариев сохранены), поэтому
    ЦЕЛЫЕ СТРОКИ переносим по номерам, а точечные правки (запятая, правка внутри
    строки) — по карте позиций. Различать это обязательно: для JSON в одну строку
    перенос «по строкам» стёр бы весь файл.
    """
    start, end, whole_line = span
    if not whole_line:
        o_start = offsets[start] if start < len(offsets) else len(text)
        o_end = offsets[end] if end < len(offsets) else len(text)
        return (o_start, o_end)

    line_from, _ = clean_idx.line_col(start)
    line_to, _ = clean_idx.line_col(max(start, end - 1))
    o_start = orig_idx.starts[line_from - 1]
    o_end_idx = line_to  # starts[] индекс следующей строки
    o_end = orig_idx.starts[o_end_idx] if o_end_idx < len(orig_idx.starts) else len(text)
    return (o_start, o_end)


# ──────────────────────────────────────────────────────────────────────────────
# Чтение и запись с сохранением формата
# ──────────────────────────────────────────────────────────────────────────────

UTF8_BOM = b"\xef\xbb\xbf"


class SourceFormat:
    """Формат файла: BOM, кодировка, переводы строк, завершающий перевод."""

    __slots__ = ("bom", "encoding", "newline", "trailing_newline")

    def __init__(self, bom=False, encoding="utf-8", newline="\n", trailing_newline=True):
        self.bom = bom
        self.encoding = encoding
        self.newline = newline
        self.trailing_newline = trailing_newline


def read_source(path: Path):
    """Читает файл, запоминая формат. Возвращает (текст, SourceFormat)."""
    raw = path.read_bytes()
    bom = raw.startswith(UTF8_BOM)
    body = raw[len(UTF8_BOM):] if bom else raw
    try:
        text = body.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = body.decode("cp1251")
        encoding = "cp1251"
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, SourceFormat(bom, encoding, newline, text.endswith(("\n", "\r")))


def encode_source(text: str, fmt: SourceFormat):
    """Готовит байты по исходному формату. Возвращает (байты, примечания)."""
    notes = []
    if not fmt.trailing_newline and text.endswith(("\n", "\r")):
        text = text.rstrip("\r\n")
    # Сначала приводим к \n, иначе у файла с CRLF получится \r\r\n:
    # текст уже содержит \r\n, а replace("\n", "\r\n) добавит ещё один \r.
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if fmt.newline != "\n":
        body = body.replace("\n", fmt.newline)
    try:
        data = body.encode(fmt.encoding)
    except UnicodeEncodeError:
        notes.append(f"не удалось записать в {fmt.encoding} — сохранён как utf-8")
        data = body.encode("utf-8")
    if fmt.bom:
        data = UTF8_BOM + data
    return data, notes


def atomic_write(path: Path, data: bytes) -> None:
    """Пишет через временный файл в той же папке — файл не остаётся «наполовину»
    записанным даже при сбое или выключении питания посередине."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
        ) as f:
            tmp = Path(f.name)
            f.write(data)
            f.flush()
        tmp.replace(path)
    finally:
        if tmp is not None and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_and_verify(path: Path, text: str, fmt: SourceFormat, expected_text: str):
    """Записывает файл и проверяет результат чтением обратно.

    Возвращает список примечаний. При несовпадении содержимого вызывающий
    обязан откатить файл из бэкапа — здесь просто сообщаем о проблеме.
    """
    data, notes = encode_source(text, fmt)
    atomic_write(path, data)
    written, _ = read_source(path)
    if written != expected_text:
        raise OSError("после записи файл не совпадает с ожидаемым содержимым")
    return notes


# ──────────────────────────────────────────────────────────────────────────────
# Резервные копии и откат
# ──────────────────────────────────────────────────────────────────────────────

def new_backup_dir(base: Path = None) -> Path:
    """Создаёт новую папку резервных копий рядом со скриптом.

    base=None резолвится в SCRIPT_DIR В МОМЕНТ ВЫЗОВА (а не при определении
    функции) — иначе тесты не смогли бы подменить путь и насоздавали бы папки
    прямо в проекте.
    """
    base = Path(base) if base is not None else SCRIPT_DIR
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base / f"backup_{stamp}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = base / f"backup_{stamp}_{suffix}"
    path.mkdir(parents=True)
    return path


def backup_file(backup_dir: Path, path: Path, root: Path) -> None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    target = backup_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def write_manifest(backup_dir: Path, root: Path, files) -> None:
    manifest = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "files": [str(f) for f in files],
    }
    (backup_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_latest_backup(base: Path = SCRIPT_DIR):
    """Находит последнюю папку бэкапа с манифестом."""
    candidates = sorted(
        (p for p in base.glob("backup_*") if (p / "_manifest.json").exists()),
        key=lambda p: p.name,
    )
    return candidates[-1] if candidates else None


def restore_backup(backup_dir: Path):
    """Восстанавливает файлы из папки бэкапа. Возвращает (ок, список_ошибок)."""
    manifest_path = backup_dir / "_manifest.json"
    if not manifest_path.exists():
        return 0, [f"в {backup_dir.name} нет манифеста"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = Path(manifest.get("root", ""))
    restored, errors = 0, []
    for rel_str in manifest.get("files", []):
        source = backup_dir / Path(rel_str).relative_to(root)
        target = Path(rel_str)
        if not source.exists():
            errors.append(f"нет файла в бэкапе: {rel_str}")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1
        except OSError as e:
            errors.append(f"{rel_str}: {e}")
    return restored, errors


# ──────────────────────────────────────────────────────────────────────────────
# Выборка файлов
# ──────────────────────────────────────────────────────────────────────────────

def is_lang_file(path: Path) -> bool:
    """Похоже ли это на файл локализации (а не blockstates/модель/гайдбук)."""
    if LANG_FILE_RE.match(path.name):
        return True
    return any(part.lower() in LANG_DIR_NAMES for part in path.parts)


class FileScan(NamedTuple):
    """Результат выборки файлов."""

    files: List[Path]
    not_lang: int          # сколько отсеяно фильтром «только языковые»


def collect_files(directory: Path, recursive: bool, lang_only: bool) -> "FileScan":
    """Собирает файлы к обработке и считает, сколько отсеяно фильтром.

    Счётчик нужен, чтобы «ничего не найдено» не выглядело загадкой: молча
    отбрасывать сотни файлов нельзя — лучше сказать, сколько и почему.
    """
    pattern = "**/*.json" if recursive else "*.json"
    files: List[Path] = []
    not_lang = 0
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        if lang_only and not is_lang_file(path):
            not_lang += 1
            continue
        files.append(path)
    return FileScan(files, not_lang)


# ──────────────────────────────────────────────────────────────────────────────
# Сканирование
# ──────────────────────────────────────────────────────────────────────────────

class ScanContext:
    """Состояние одного прогона: папка, режим, резервные копии."""

    def __init__(self, root: Path, dry_run: bool, backup_base: Path = None):
        self.root = root
        self.dry_run = dry_run
        self.backup_base = backup_base
        self.backup_dir = None
        self.backed_up = []

    def ensure_backup(self, path: Path) -> None:
        """Копирует оригинал перед первой правкой этого файла."""
        if path in self.backed_up:
            return
        if self.backup_dir is None:
            self.backup_dir = new_backup_dir(self.backup_base)
        backup_file(self.backup_dir, path, self.root)
        self.backed_up.append(path)

    def rollback_file(self, path: Path) -> bool:
        """Возвращает файл к исходному состоянию из бэкапа."""
        if self.backup_dir is None:
            return False
        try:
            rel = path.relative_to(self.root)
            shutil.copy2(self.backup_dir / rel, path)
            return True
        except Exception:
            return False

    def finish(self) -> None:
        """Записывает манифест — без него откат невозможен."""
        if self.backup_dir is not None and self.backed_up:
            write_manifest(self.backup_dir, self.root, self.backed_up)


def process_file(path: Path, ctx: ScanContext, keep_first: bool, fix_commas: bool) -> FileReport:
    """Обрабатывает один файл: читает, правит, проверяет и (по решению) пишет."""
    report = FileReport(path)
    text, fmt = read_source(path)

    result = process_text(text, keep_first, fix_commas)
    report.removed = result.removed
    report.comma_fixes = result.comma_fixes
    if result.error is not None:
        report.error = result.error
        return report

    report.new_text = result.new_text
    if not result.changed or ctx.dry_run:
        return report

    ctx.ensure_backup(path)
    try:
        report.notes.extend(write_and_verify(path, result.new_text, fmt, result.new_text))
    except Exception as e:
        report.error = e
        if ctx.rollback_file(path):
            report.notes.append("файл восстановлен из резервной копии")
        else:
            report.error = OSError(f"{e}; откат из резервной копии не удался")
        logger.error("Ошибка записи %s: %s", path, e)
    return report


def run_scan(directory, recursive, keep_first, q, *, lang_only=True,
             fix_commas=True, dry_run=True, cancel=None, progress_every=25,
             backup_base=None):
    """Обрабатывает файлы и сообщает о ходе через очереди.

    В очередь кладутся кортежи:
      ("total", n)                       — найдено файлов
      ("progress", i, n)                — обработано i из n
      ("file", FileReport)              — результат по файлу
      ("done", stats)                   — итоги
    """
    scan = collect_files(directory, recursive, lang_only)
    files = scan.files
    q.put(("total", len(files)))

    stats = {
        "total": len(files),
        "changed": 0, "duplicates": 0, "commas": 0,
        "skipped": 0, "failed": 0, "dry_run": dry_run,
        "not_lang": scan.not_lang,
    }
    ctx = ScanContext(directory, dry_run, backup_base)

    for index, path in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            q.put(("log", "Остановлено пользователем\n", "warn"))
            break
        try:
            report = process_file(path, ctx, keep_first, fix_commas)
        except Exception as e:          # страховка: один файл не роняет прогон
            logger.exception("Непредвиденная ошибка на %s", path)
            report = FileReport(path)
            report.error = e
            report.notes.append("внутренняя ошибка обработки")

        if report.changed and report.error is None:
            stats["changed"] += 1
            stats["duplicates"] += len(report.removed)
            stats["commas"] += len(report.comma_fixes)
        elif report.error is not None:
            if report.changed:
                stats["failed"] += 1
            else:
                stats["skipped"] += 1
        q.put(("file", report))
        if index % progress_every == 0 or index == len(files):
            q.put(("progress", index, len(files)))

    ctx.finish()
    if ctx.backup_dir is not None:
        stats["backup"] = str(ctx.backup_dir)

    if dry_run and stats["changed"]:
        q.put(("log", "\nРежим «только отчёт» — файлы НЕ изменялись.\n"
                       "Снимите галочку и запустите снова, чтобы применить правки.\n",
                 "summary"))
    q.put(("done", stats))
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Настройки между запусками
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "recursive": True,
    "keep_first": True,
    "lang_only": True,
    "fix_commas": True,
    "dry_run": True,
    "last_directory": "",
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        if SETTINGS_PATH.exists():
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update({k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
    except Exception as e:
        logger.warning("Не удалось прочитать настройки: %s", e)
    return settings


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_PATH.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Не удалось сохранить настройки: %s", e)


def diff_lines(before: str, after: str, limit: int = 200):
    """Грубый построчный diff: строки, которые исчезли или добавились."""
    import difflib
    return list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="было", tofile="стало", lineterm="", n=1
    ))[:limit]


# ──────────────────────────────────────────────────────────────────────────────
# Интерфейс
# ──────────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.log_queue = queue.Queue()
        self.thread = None
        self.cancel_event = threading.Event()
        self.file_reports = {}     # путь -> FileReport
        self.line_map = {}         # строка лога -> путь
        self.busy = False
        self.original_texts = {}   # путь -> исходный текст (для диффа)

        root.title("Удаление дубликатов ключей в JSON")
        # Лог с перечнем файлов и кнопки в ряд ужимались на 880x620 — окно
        # открываем просторнее, особенно важно для режима «только отчёт»,
        # где по файлу видно, что именно изменится.
        root.geometry("1040x700")
        root.minsize(760, 500)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        top = ttk.Frame(root, padding=(12, 12, 12, 0))
        top.pack(fill="x")

        ttk.Label(top, text="Директория:").pack(side="left")
        self.path_var = tk.StringVar(value=self.settings.get("last_directory") or str(Path.cwd()))
        self.path_entry = ttk.Entry(top, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.copy_path_button = ttk.Button(top, text="📋 Копировать путь",
                                           command=self.copy_directory_path)
        self.copy_path_button.pack(side="left")
        self.browse_button = ttk.Button(top, text="Обзор…", command=self.choose_directory)
        self.browse_button.pack(side="left", padx=(6, 0))

        # ПКМ по строке пути — копирование, без выделения мышью.
        # У ttk.Entry нет опции contextmenu (это опция tk), поэтому вешаем
        # обработчик на <Button-3> и держим меню в self, иначе его съест
        # сборщик мусора.
        self.path_menu = tk.Menu(self.root, tearoff=0)
        self.path_menu.add_command(label="📋 Копировать путь", command=self.copy_directory_path)
        self.path_menu.add_command(label="📂 Открыть в проводнике", command=self.open_directory)
        self.path_entry.bind(
            "<Button-3>",
            lambda e: self.path_menu.tk_popup(e.x_root, e.y_root),
            add="+",
        )

        options = ttk.Frame(root, padding=(12, 8))
        options.pack(fill="x")
        self.recursive_var = tk.BooleanVar(value=self.settings["recursive"])
        self.keep_first_var = tk.BooleanVar(value=self.settings["keep_first"])
        self.lang_only_var = tk.BooleanVar(value=self.settings["lang_only"])
        self.fix_commas_var = tk.BooleanVar(value=self.settings["fix_commas"])
        self.dry_run_var = tk.BooleanVar(value=self.settings["dry_run"])

        ttk.Checkbutton(options, text="Включая подпапки",
                        variable=self.recursive_var).pack(side="left")
        ttk.Checkbutton(options, text="Только языковые файлы",
                        variable=self.lang_only_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="Починить пропущенные запятые",
                        variable=self.fix_commas_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="Оставлять первое вхождение",
                        variable=self.keep_first_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="Только отчёт (не записывать)",
                        variable=self.dry_run_var).pack(side="left", padx=(16, 0))

        actions = ttk.Frame(root, padding=(12, 0))
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="▶ Начать", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="⏹ Остановить", command=self.cancel,
                                         state="disabled")
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.rollback_button = ttk.Button(actions, text="↩ Откатить последний запуск",
                                          command=self.rollback)
        self.rollback_button.pack(side="left", padx=(6, 0))
        self.backup_button = ttk.Button(actions, text="📂 Папка резервных копий",
                                        command=self.open_backup_folder)
        self.backup_button.pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=12, pady=(10, 0))

        self.log = scrolledtext.ScrolledText(root, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, padx=12, pady=(8, 6))
        self.log.tag_configure("ok", foreground="#1a7f37")
        self.log.tag_configure("warn", foreground="#9a6700")
        self.log.tag_configure("error", foreground="#c62828")
        self.log.tag_configure("dry", foreground="#6e49cb")
        bold = tkfont.Font(font=self.log.cget("font"))
        bold.configure(weight="bold")
        self.log.tag_configure("summary", foreground="#0969da", font=bold)
        self.log.tag_configure("head", foreground="#57606a")
        self.log.bind("<Double-Button-1>", self.on_double_click)
        self.log.bind("<Motion>", self.on_motion)

        self.status_var = tk.StringVar(
            value="Выберите директорию и нажмите «Начать». Двойной клик по файлу в логе — что изменится."
        )
        ttk.Label(root, textvariable=self.status_var, anchor="w", padding=(12, 4)).pack(fill="x")

        self.update_backup_buttons()
        self.root.after(80, self.poll_queue)

    # ── настройки ──
    def collect_settings(self):
        return {
            "recursive": self.recursive_var.get(),
            "keep_first": self.keep_first_var.get(),
            "lang_only": self.lang_only_var.get(),
            "fix_commas": self.fix_commas_var.get(),
            "dry_run": self.dry_run_var.get(),
            "last_directory": self.path_var.get().strip(),
        }

    def save_state(self):
        self.settings.update(self.collect_settings())
        save_settings(self.settings)

    # ── кнопки ──
    def current_directory(self) -> Path:
        return Path(self.path_var.get().strip().strip('"'))

    def copy_directory_path(self):
        """Копирует путь из строки «Директория:» в буфер обмена."""
        path = self.path_var.get().strip().strip('"')
        if not path:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self.status_var.set(f"Путь скопирован: {path}")
        except tk.TclError as e:
            logger.warning("Не удалось скопировать путь: %s", e)

    def open_directory(self):
        path = self.current_directory()
        if not path.is_dir():
            messagebox.showerror("Папка", f"Директория не найдена:\n{path}", parent=self.root)
            return
        try:
            self.root.tk.call("tk::Start", path.as_posix())
        except Exception as e:
            messagebox.showerror("Папка", f"Не удалось открыть:\n{e}", parent=self.root)

    def choose_directory(self):
        initial = self.path_var.get().strip() or str(Path.cwd())
        selected = filedialog.askdirectory(
            title="Выберите директорию с JSON файлами", initialdir=initial
        )
        if selected:
            self.path_var.set(selected)

    def start(self):
        if self.busy:
            return
        directory = self.current_directory()
        if not directory.is_dir():
            messagebox.showerror("Ошибка", f"Директория не найдена:\n{directory}", parent=self.root)
            return

        scan = collect_files(directory, self.recursive_var.get(), self.lang_only_var.get())
        preview = scan.files
        if not preview:
            self.report_nothing_found(directory, scan)
            return
        if len(preview) > 5000 and not self.dry_run_var.get():
            if not messagebox.askyesno(
                "Много файлов",
                f"Найдено {len(preview)} файлов. Обработка будет менять их на месте.\n"
                "Продолжить?",
                parent=self.root,
            ):
                return

        self.save_state()
        self.clear_log()
        self.cancel_event.clear()
        self.set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("Запуск…")

        self.thread = threading.Thread(
            target=self._worker,
            args=(directory,),
            daemon=True,
        )
        self.thread.start()

    def report_nothing_found(self, directory: Path, scan):
        """Объясняет, ПОЧЕМУ не нашлось файлов, и предлагает выход.

        Молча отбрасывать файлы нельзя: если «подходящих» не оказалось, но
        фильтр что-то отсёк — говорим об этом прямо и предлагаем снять галочку.
        """
        if not scan.not_lang:
            messagebox.showinfo(
                "Ничего не найдено",
                f"В папке\n{directory}\nне найдено ни одного .json файла.",
                parent=self.root,
            )
            return

        if messagebox.askyesno(
            "Файлы отсеяны фильтром",
            f"{scan.not_lang} файлов не подходят под «только языковые файлы»\n"
            "(нужно имя вида ru_ru.json или папка lang/language).\n\n"
            "Обработать их тоже?",
            parent=self.root,
        ):
            self.lang_only_var.set(False)
            self.start()

    def _worker(self, directory: Path):
        try:
            run_scan(
                directory,
                self.recursive_var.get(),
                self.keep_first_var.get(),
                self.log_queue,
                lang_only=self.lang_only_var.get(),
                fix_commas=self.fix_commas_var.get(),
                dry_run=self.dry_run_var.get(),
                cancel=self.cancel_event,
            )
        except Exception as e:
            logger.exception("Сбой сканирования")
            self.log_queue.put(("log", f"Сбой сканирования: {e}\n", "error"))

    def cancel(self):
        self.cancel_event.set()
        self.status_var.set("Останавливаю…")

    def rollback(self):
        backup = find_latest_backup()
        if backup is None:
            messagebox.showinfo("Откат", "Резервных копий пока нет.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Откатить последний запуск",
            f"Файлы будут восстановлены из папки:\n{backup}\n\nПродолжить?",
            parent=self.root,
        ):
            return
        restored, errors = restore_backup(backup)
        message = f"Восстановлено файлов: {restored}"
        if errors:
            message += "\n\nОшибки:\n" + "\n".join(errors[:10])
        messagebox.showinfo("Откат", message, parent=self.root)
        logger.info("Откат из %s: восстановлено %d", backup, restored)

    def open_backup_folder(self):
        latest = find_latest_backup()
        target = latest if latest is not None else SCRIPT_DIR
        try:
            if target.is_dir():
                self.root.tk.call("tk::Start", target.as_posix())
            else:
                messagebox.showinfo("Папка", "Папка не найдена.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Папка", f"Не удалось открыть:\n{e}", parent=self.root)

    def set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if not busy:
            self.update_backup_buttons()

    def update_backup_buttons(self):
        latest = find_latest_backup()
        state = "normal" if latest is not None else "disabled"
        self.rollback_button.configure(state=state)
        self.backup_button.configure(state=state)
        if latest is not None:
            self.rollback_button.configure(text=f"↩ Откатить ({latest.name})")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.file_reports = {}
        self.line_map = {}
        self.original_texts = {}
        self.progress["value"] = 0

    def append_log(self, message, tag=None):
        start_line = int(self.log.index("end-1c").split(".")[0])
        self.log.configure(state="normal")
        self.log.insert("end", message, tag or ())
        self.log.configure(state="disabled")
        self.log.see("end")
        return start_line

    # ── работа с логом ──
    def on_motion(self, event):
        lineno = int(self.log.index(f"@{event.x},{event.y}").split(".")[0])
        self.log.configure(cursor="hand2" if lineno in self.line_map else "xterm")

    def on_double_click(self, event):
        lineno = int(self.log.index(f"@{event.x},{event.y}").split(".")[0])
        path = self.line_map.get(lineno)
        if path:
            self.show_details(path)

    def show_details(self, path):
        report = self.file_reports.get(path)
        if report is None:
            return
        window = tk.Toplevel(self.root)
        window.title(f"Изменения — {Path(path).name}")
        window.geometry("760x560")
        window.minsize(520, 380)
        window.transient(self.root)

        text = scrolledtext.ScrolledText(window, wrap="none")
        text.pack(fill="both", expand=True)
        text.tag_configure("head", foreground="#57606a")
        text.tag_configure("add", foreground="#1a7f37")
        text.tag_configure("del", foreground="#c62828")

        text.insert("end", f"{path}\n", "head")
        if report.error is not None and not report.changed:
            text.insert("end", "\nОшибка: ", "head")
            text.insert("end", f"{report.error}\n")
            if isinstance(report.error, json.JSONDecodeError):
                text.insert("end",
                            f"  (строка {report.error.lineno}, столбец {report.error.colno})\n",
                            "head")
        else:
            text.insert("end", f"Итог: {report.summary()}\n", "head")

        if report.comma_fixes:
            text.insert("end", "\nДобавленные запятые:\n", "head")
            for fix in report.comma_fixes:
                text.insert("end", f"  • строка {fix.line} — после {fix.after!r}\n")

        if report.removed:
            text.insert("end", "\nУдалённые дубликаты:\n", "head")
            for key, line in report.removed:
                text.insert("end", f"  • строка {line}: \"{key}\"\n")

        for note in report.notes:
            text.insert("end", f"\n{note}\n", "head")

        before = self.original_texts.get(path)
        if before and report.new_text and report.changed:
            text.insert("end", "\nИзменения ( Unified diff ):\n", "head")
            for line in diff_lines(before, report.new_text):
                tag = "del" if line.startswith("-") else "add" if line.startswith("+") else None
                text.insert("end", line + "\n", tag or ())

        text.configure(state="disabled")
        ttk.Button(window, text="Закрыть", command=window.destroy).pack(pady=(0, 10))
        window.bind("<Escape>", lambda e: window.destroy())

    # ── очередь ──
    def poll_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.append_log(item[1], item[2])
                elif kind == "total":
                    self.status_var.set(f"Найдено файлов: {item[1]}")
                elif kind == "progress":
                    _, index, total = item
                    if total:
                        self.progress["value"] = (index / total) * 100
                    self.status_var.set(f"Обработка: {index} / {total}")
                elif kind == "file":
                    self.handle_file_report(item[1])
                elif kind == "done":
                    self.handle_done(item[1])
        except queue.Empty:
            pass
        self.root.after(80, self.poll_queue)

    def handle_file_report(self, report: FileReport):
        path = str(report.path) if report.path else ""
        dry = self.dry_run_var.get()

        if report.changed and report.error is None:
            self.file_reports[path] = report
            verb = "будет изменён" if dry else "изменён"
            line = self.append_log(
                f"[{verb}] {report.summary()} — {report.path}\n",
                "dry" if dry else "ok",
            )
            self.line_map[line] = path
            if report.new_text:
                self.original_texts[path] = report.new_text
        elif report.error is not None and report.changed:
            line = self.append_log(f"[Ошибка] {report.path}: {report.error}\n", "error")
            self.line_map[line] = path
        elif report.error is not None:
            self.append_log(f"[Пропуск] {report.path}: {report.error}\n", "warn")

    def handle_done(self, stats: dict):
        self.set_busy(False)
        self.progress["value"] = 100 if stats["total"] else 0
        if stats.get("cancelled"):
            self.append_log("\nОстановлено пользователем.\n", "summary")

        parts = [
            f"Файлов: {stats['total']}",
            f"изменено: {stats['changed']}",
            f"дубликатов: {stats['duplicates']}",
            f"запятых: {stats['commas']}",
            f"пропущено: {stats['skipped']}",
        ]
        if stats["failed"]:
            parts.append(f"ошибок записи: {stats['failed']}")
        if stats.get("backup"):
            parts.append(f"бэкап: {Path(stats['backup']).name}")
        summary = " | ".join(parts)
        self.status_var.set(summary)
        self.append_log("\n" + summary + "\n", "summary")
        if stats.get("backup"):
            self.update_backup_buttons()

        if stats["dry_run"] and stats["changed"]:
            messagebox.showinfo(
                "Только отчёт",
                f"Изменений найдено: {stats['changed']} файлов.\n\n"
                "Ничего не записано. Снимите галочку «Только отчёт» и запустите снова.",
                parent=self.root,
            )
        else:
            messagebox.showinfo("Готово", summary, parent=self.root)

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(
                "Выйти?", "Сканирование ещё идёт. Выйти всё равно?", parent=self.root
            ):
                return
            self.cancel_event.set()
        self.save_state()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
