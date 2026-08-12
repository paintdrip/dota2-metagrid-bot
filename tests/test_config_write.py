# -*- coding: utf-8 -*-
"""Тесты записи hero_grid_config.json: backup и атомарная запись."""

import json

import metagrid

GRID = {
    "configs": [
        {
            "config_name": "D2PT Rating",
            "categories": [
                {"category_name": "Core", "hero_ids": [1, 2, 3]},
            ],
        }
    ]
}


def test_write_creates_config(tmp_path):
    cfg = tmp_path / "570" / "remote" / "cfg"
    target = metagrid.write_grid_config(cfg, GRID)
    assert target.name == "hero_grid_config.json"
    assert json.loads(target.read_text(encoding="utf-8")) == GRID


def test_backup_created_once_and_not_overwritten(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    original = cfg / "hero_grid_config.json"
    original.write_text('{"old": true}', encoding="utf-8")

    metagrid.write_grid_config(cfg, GRID)
    backup = cfg / "hero_grid_config_backup.json"
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == {"old": True}

    # Повторная запись НЕ затирает существующий backup
    metagrid.write_grid_config(cfg, {"configs": []})
    assert json.loads(backup.read_text(encoding="utf-8")) == {"old": True}
    # А основной конфиг обновился
    assert json.loads(original.read_text(encoding="utf-8")) == {"configs": []}


def test_no_backup_when_no_existing_config(tmp_path):
    cfg = tmp_path / "cfg"
    metagrid.write_grid_config(cfg, GRID)
    assert not (cfg / "hero_grid_config_backup.json").exists()


def test_find_cfg_dirs_all_accounts(tmp_path):
    steam = tmp_path / "steam"
    (steam / "userdata" / "111").mkdir(parents=True)
    (steam / "userdata" / "222").mkdir(parents=True)
    (steam / "userdata" / "not-a-number").mkdir(parents=True)

    dirs = metagrid.find_cfg_dirs(steam)
    assert dirs == [
        steam / "userdata" / "111" / "570" / "remote" / "cfg",
        steam / "userdata" / "222" / "570" / "remote" / "cfg",
    ]
    # Фильтр по --user-id
    dirs = metagrid.find_cfg_dirs(steam, user_id="222")
    assert dirs == [steam / "userdata" / "222" / "570" / "remote" / "cfg"]


def test_find_steam_path_override(tmp_path):
    assert metagrid.find_steam_path(str(tmp_path)) == tmp_path


def test_find_steam_path_missing(tmp_path):
    import pytest

    with pytest.raises(metagrid.MetaGridError):
        metagrid.find_steam_path(str(tmp_path / "nope"))
