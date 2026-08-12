# -*- coding: utf-8 -*-
"""Тесты извлечения и парсинга данных сетки из HTML страницы."""

from pathlib import Path

import pytest

import metagrid

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_data_from_old_fixture():
    """Реальный сохранённый HTML: массив data извлекается и парсится."""
    html = (FIXTURES / "grid_wb.html").read_text(encoding="utf-8")
    literal = metagrid.extract_data_literal(html)
    assert literal.startswith("[") and literal.endswith("]")
    data = metagrid.parse_js_literal(literal)
    assert isinstance(data, list) and len(data) >= 1


def test_parse_grid_from_old_fixture():
    """Реальный HTML: grids.d2ptrating извлекается в структуру hero_grid_config."""
    html = (FIXTURES / "grid_wb.html").read_text(encoding="utf-8")
    grid = metagrid.parse_grid_from_html(html, mode="d2ptrating")
    configs = grid["configs"]
    assert configs and "config_name" in configs[0]
    categories = configs[0]["categories"]
    assert categories and all("hero_ids" in c for c in categories)


def test_missing_grids_error_is_clear():
    """Если в данных нет grids, ошибка должна быть понятной."""
    with pytest.raises(metagrid.MetaGridError, match="grids"):
        metagrid._find_grids_container([{"type": "data", "data": {"other": 1}}])


def test_parse_modern_fixture_d2ptrating():
    """Современный формат: devalue-литерал (unquoted keys, void 0, NaN) -> grids.d2ptrating."""
    html = (FIXTURES / "grid_modern.html").read_text(encoding="utf-8")
    grid = metagrid.parse_grid_from_html(html, mode="d2ptrating")
    assert grid["version"] == 3
    configs = grid["configs"]
    assert configs[0]["config_name"] == "D2PT Rating"
    categories = configs[0]["categories"]
    assert categories[0]["hero_ids"] == [74, 11, 50, None]  # void 0 -> null
    assert categories[1]["hero_ids"] == [20, 26, 101]


def test_pick_grid_fallbacks():
    grids = {
        "matches": {"configs": []},
        "matches_wr": {"configs": []},
        "d2ptrating": {"configs": [{"config_name": "X", "categories": []}]},
    }
    # Точное совпадение
    assert metagrid.pick_grid(grids, "d2ptrating") is grids["d2ptrating"]
    # Нет точного — любой ключ с "d2pt"
    assert metagrid.pick_grid(grids, "d2ptrating_v2") is grids["d2ptrating"]
    # Нет d2pt — fallback на matches_wr
    del grids["d2ptrating"]
    assert metagrid.pick_grid(grids, "d2ptrating") is grids["matches_wr"]
    # Нет ничего знакомого — первый ключ
    del grids["matches_wr"]
    assert metagrid.pick_grid(grids, "d2ptrating") is grids["matches"]
    # Пустой grids — ошибка
    with pytest.raises(metagrid.MetaGridError):
        metagrid.pick_grid({}, "d2ptrating")
    # Сетка без configs — ошибка
    with pytest.raises(metagrid.MetaGridError, match="configs"):
        metagrid.pick_grid({"d2ptrating": {"nope": 1}}, "d2ptrating")


def test_extract_errors_are_clear():
    with pytest.raises(metagrid.MetaGridError, match="start"):
        metagrid.extract_data_literal("<html>no svelte here</html>")
    with pytest.raises(metagrid.MetaGridError, match="data"):
        metagrid.extract_data_literal("start(app, element, { node_ids: [0] })")
