"""
Тесты GUI: ленивая пересборка таблиц, фильтр, сортировка, чекбоксы, счётчик.

Запуск:  python -m pytest tests/ -q
Тесты требуют Tk; если графической среды нет — пропускаются.
"""
import pytest

tk = pytest.importorskip("tkinter", reason="нужен tkinter")

import check_localization as cl


@pytest.fixture(scope="module")
def gui():
    """Готовое окно программы (без запуска mainloop)."""
    cl.load_config()
    try:
        root = tk.Tk()
    except Exception as exc:  # нет дисплея
        pytest.skip(f"не удалось открыть окно Tk: {exc}")
    root.withdraw()
    app = cl.LocalizationCheckerGUI(root)
    yield app, root
    try:
        root.destroy()
    except Exception:
        pass


def _mod(name, pct=100.0, missing=0, source="jar", status="partial"):
    result = cl.new_mod_result(name, status=status, source=source)
    result.update(
        ru_keys=50, en_keys=100, percentage=pct,
        missing_keys=[f"k{i}" for i in range(missing)],
    )
    return result


def _results():
    return {
        "full": [_mod(f"full{i}.jar (mod{i})", 100.0, status="full") for i in range(5)],
        "partial": [_mod(f"part{i}.jar (mod{i})", 50.0 + i * 5, missing=i) for i in range(5)],
        "missing": [_mod(f"miss{i}.jar (mod{i})", 0.0, status="missing") for i in range(3)],
        "translated": [_mod(f"tr{i}.jar (mod{i})", 100.0, source="translated_mods", status="translated")
                       for i in range(2)],
        "outdated": [_mod(f"out{i}.jar (mod{i})", 80.0, source="translated_mods") for i in range(2)],
    }


def _ids(tree):
    return list(tree.get_children())


def test_only_active_tab_is_filled(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.full_frame)
    app.apply_filter()
    root.update()

    assert len(_ids(app.full_tree)) == 5
    # остальные вкладки намеренно не трогаются — они «грязные»
    assert _ids(app.partial_tree) == []
    assert "partial" in app._dirty_categories


def test_dirty_tab_fills_on_switch(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.full_frame)
    app.apply_filter()
    assert _ids(app.partial_tree) == []

    app.notebook.select(app.partial_frame)
    root.update()
    assert len(_ids(app.partial_tree)) == 5
    assert "partial" not in app._dirty_categories


def test_search_filter_applies_to_active_tab(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.partial_frame)
    app.search_var.set("part3")
    app.apply_filter()
    root.update()
    assert _ids(app.partial_tree) == ["part3.jar (mod3)"]
    app.search_var.set("")
    app.apply_filter()
    root.update()


def test_sort_by_percentage_descending(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.partial_frame)
    app.sort_state["partial"] = {"column": "%", "reverse": True}
    app.apply_filter()
    root.update()

    values = [app.partial_tree.set(i, "%") for i in _ids(app.partial_tree)]
    numbers = [float(v.rstrip("%")) for v in values]
    assert numbers == sorted(numbers, reverse=True)
    assert len(numbers) == 5

    app.sort_state["partial"] = {"column": "%", "reverse": False}
    app.apply_filter()
    root.update()
    ascending = [float(app.partial_tree.set(i, "%").rstrip("%")) for i in _ids(app.partial_tree)]
    assert ascending == sorted(ascending)


def test_selection_survives_rebuild(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.full_frame)
    app.apply_filter()
    target = _ids(app.full_tree)[2]
    app.full_tree.selection_set(target)

    app.apply_filter()
    root.update()
    assert app.full_tree.selection() == (target,)


def test_selection_survives_theme_switch(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.full_frame)
    app.apply_filter()
    target = _ids(app.full_tree)[1]
    app.full_tree.selection_set(target)

    app.toggle_theme()
    root.update()
    assert app.full_tree.selection() == (target,)
    app.toggle_theme()
    root.update()


def test_checkbox_and_counter(gui):
    app, root = gui
    app.results = _results()
    app.notebook.select(app.partial_frame)
    app.apply_filter()
    root.update()

    for category in app.checked_mods:
        app.checked_mods[category].clear()
    app._update_select_toggle_btn_text()

    first = _ids(app.partial_tree)[0]
    app.partial_tree.selection_set(first)
    app.on_tree_enter_key(type("E", (), {})(), app.partial_tree)
    root.update()

    assert app.checked_mods["partial"] == {"part0.jar (mod0)"}
    assert app.checked_counter_label.cget("text") == "Выбрано: 1"

    app.toggle_select_all()
    assert len(app.checked_mods["partial"]) == 5
    assert app.checked_counter_label.cget("text") == "Выбрано: 5"
    assert app.select_toggle_btn.cget("text").endswith("Снять все")

    app.toggle_select_all()
    assert app.checked_mods["partial"] == set()
    assert app.checked_counter_label.cget("text") == ""


def test_duplicate_mod_names_share_checkbox(gui):
    """Два одноимённых мода из разных подпапок: iid разный, отметка общая."""
    app, root = gui
    app.results = _results()
    app.notebook.select(app.full_frame)
    app.apply_filter()
    root.update()

    for category in app.checked_mods:
        app.checked_mods[category].clear()
    tree = app.full_tree
    name = "same.jar (same)"
    tree.insert("", tk.END, iid=name, values=(name, 1, 1, "100%", 0))
    second = app._tree_insert_mod_row(tree, name, (name, 1, 1, "100%", 0), ())
    assert second == f"{name}#2"

    tree.selection_set(second)
    app.on_tree_enter_key(type("E", (), {})(), tree)
    root.update()

    assert app.checked_mods["full"] == {name}
    # глиф проставился у ОБЕИХ строк, иначе вторая выглядела бы неотмеченной
    assert str(tree.item(name, "values")[0]).startswith(cl.CHECKBOX_ON)
    assert str(tree.item(second, "values")[0]).startswith(cl.CHECKBOX_ON)


def test_paste_into_search_replaces_selection(gui):
    app, root = gui
    entry = app.search_entry
    root.clipboard_clear()
    root.clipboard_append("jei")

    entry.delete(0, tk.END)
    entry.insert(0, "abcdef")
    entry.select_range(1, 4)
    app._paste_into_search()
    assert app.search_var.get() == "ajeief"

    entry.delete(0, tk.END)
    entry.icursor(0)
    entry.select_range(0, 0)
    app._paste_from_clipboard(None)
    assert app.search_var.get() == "jei"
    app.search_var.set("")


def test_details_window_opens_larger(gui):
    app, root = gui
    app.results = {"full": [], "partial": [_mod("alpha.jar (alpha)", 50.0, missing=2)],
                   "missing": [], "translated": [], "outdated": []}
    app.notebook.select(app.partial_frame)
    app.apply_filter()
    app.partial_tree.selection_set("alpha.jar (alpha)")
    app.show_details(app.partial_tree)
    root.update()

    win = app._detail_windows["alpha.jar (alpha)"]
    assert win.geometry().startswith("1000x640"), win.geometry()
    assert win.minsize() == (720, 440), win.minsize()
    win.destroy()


def test_details_window_not_duplicated(gui):
    """Повторный вызов для того же мода поднимает существующее окно, а не создаёт второе."""
    app, root = gui
    app.results = {"full": [], "partial": [_mod("alpha.jar (alpha)", 50.0, missing=2)],
                   "missing": [], "translated": [], "outdated": []}
    app.notebook.select(app.partial_frame)
    app.apply_filter()
    app.partial_tree.selection_set("alpha.jar (alpha)")

    app.show_details(app.partial_tree)
    root.update()
    win = app._detail_windows["alpha.jar (alpha)"]
    tops_before = len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)])

    app.show_details(app.partial_tree)
    app.show_details(app.partial_tree)
    root.update()
    tops_after = len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)])

    assert tops_after == tops_before, (tops_before, tops_after)
    assert len(app._detail_windows) == 1
    assert app._detail_windows["alpha.jar (alpha)"] is win

    # скрытое окно повторным вызовом поднимается
    win.withdraw()
    root.update()
    app.show_details(app.partial_tree)
    root.update()
    assert win.state() == "normal", win.state()

    win.destroy()


def test_details_windows_per_mod(gui):
    app, root = gui
    app.results = {"full": [], "partial": [_mod("alpha.jar (alpha)", 50.0),
                                            _mod("beta.jar (beta)", 40.0)],
                   "missing": [], "translated": [], "outdated": []}
    app.notebook.select(app.partial_frame)
    app.apply_filter()

    for name in ("alpha.jar (alpha)", "beta.jar (beta)"):
        app.partial_tree.selection_set(name)
        app.show_details(app.partial_tree)
        root.update()
    assert len(app._detail_windows) == 2

    # закрытое окно освобождает место: мод можно открыть заново
    beta_win = app._detail_windows["beta.jar (beta)"]
    beta_win.destroy()
    root.update()
    assert "beta.jar (beta)" not in app._detail_windows

    app.partial_tree.selection_set("beta.jar (beta)")
    app.show_details(app.partial_tree)
    root.update()
    assert app._detail_windows["beta.jar (beta)"] is not beta_win
    assert app._detail_windows["beta.jar (beta)"].winfo_exists()

    app._close_all_detail_windows()
    root.update()
    assert app._detail_windows == {}


def test_details_window_has_four_key_columns(gui):
    app, root = gui
    mod = _mod("m.jar (m)", 75.0, missing=2)
    mod.update(identical_keys=["a"], identical_count=1,
               empty_keys=["e1", "e2"], empty_count=2)
    app.results = {"full": [], "partial": [mod], "missing": [],
                   "translated": [], "outdated": []}
    # таблица заполняется только для активной вкладки
    app.notebook.select(app.partial_frame)
    app.apply_filter()
    app.partial_tree.selection_set("m.jar (m)")
    app.show_details(app.partial_tree)
    root.update()

    win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    titles = []

    def walk(widget):
        for child in widget.winfo_children():
            try:
                text = str(child.cget("text"))
                if any(key in text for key in ("Не хватает", "Лишние", "Совпадает", "Пустые")):
                    titles.append(text)
            except Exception:
                pass
            walk(child)

    walk(win)
    assert len(titles) == 4, titles
    assert any("Пустые (2)" in t for t in titles), titles
    win.destroy()
