# -*- coding: utf-8 -*-
"""Тесты надёжности записи: валидация сетки, детект запущенной Dota 2,
пост-валидация записи, фильтрация userdata, лог-файл. Всё моками, без сети."""

import json
import logging.handlers
import subprocess

import pytest

import metagrid


def _valid_grid() -> dict:
    """Минимально валидная сетка: 2 конфига, суммарно >= 50 героев."""
    return {
        "configs": [
            {
                "config_name": "Dota2ProTracker 7.41e - All Roles",
                "categories": [
                    {"category_name": "Carry", "hero_ids": list(range(1, 31))},
                    {"category_name": "Support", "hero_ids": list(range(31, 61))},
                ],
            },
            {
                "config_name": "Carry",
                "categories": [{"category_name": "Carry", "hero_ids": [1, 2, 3]}],
            },
        ],
        "version": 3,
    }


# --- validate_grid ---

def test_validate_grid_valid():
    metagrid.validate_grid(_valid_grid())  # не падает


@pytest.mark.parametrize("broken,match", [
    ({}, "configs"),
    ({"configs": []}, "configs"),
    ({"configs": [{"categories": [{"category_name": "A", "hero_ids": list(range(1, 60))}]}]},
     "config_name"),
    ({"configs": [{"config_name": "X"}]}, "categories"),
    ({"configs": [{"config_name": "X", "categories": []}]}, "categories"),
    ({"configs": [{"config_name": "X", "categories": [{"hero_ids": list(range(1, 60))}]}]},
     "category_name"),
    ({"configs": [{"config_name": "X", "categories": [{"category_name": "A", "hero_ids": "no"}]}]},
     "hero_ids"),
    ({"configs": [{"config_name": "X", "categories": [
        {"category_name": "A", "hero_ids": [1, 0, -3] + list(range(2, 60))}]}]},
     "невалидные hero_ids"),
    ({"configs": [{"config_name": "X", "categories": [
        {"category_name": "A", "hero_ids": [None, "5", True] + list(range(2, 60))}]}]},
     "невалидные hero_ids"),
    ({"configs": [{"config_name": "X", "categories": [
        {"category_name": "A", "hero_ids": []}, {"category_name": "B", "hero_ids": []}]}]},
     "все категории пусты"),
    ({"configs": [{"config_name": "X", "categories": [{"category_name": "A", "hero_ids": [1, 2, 3]}]}]},
     "подозрительно мало героев"),
])
def test_validate_grid_broken(broken, match):
    with pytest.raises(metagrid.MetaGridError, match=match):
        metagrid.validate_grid(broken)


def test_validate_grid_error_mentions_support():
    with pytest.raises(metagrid.MetaGridError, match="metagrid.log"):
        metagrid.validate_grid({})


# --- grid_summary ---

def test_grid_summary():
    lines = metagrid.grid_summary(_valid_grid())
    assert lines[0] == "Dota2ProTracker 7.41e - All Roles: 2 категорий, 60 героев"
    assert lines[1] == "Carry: 1 категорий, 3 героев"


# --- is_dota_running ---

def _proc(out: bytes):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=out, stderr=b"")


def test_dota_running_windows_detected(monkeypatch):
    monkeypatch.setattr(metagrid, "_is_windows", lambda: True)
    out = "dota2.exe                   12345 Console  1  1 234 567 КБ".encode("cp866")
    monkeypatch.setattr(metagrid.subprocess, "run", lambda *a, **kw: _proc(out))
    assert metagrid.is_dota_running() is True


def test_dota_not_running_windows(monkeypatch):
    monkeypatch.setattr(metagrid, "_is_windows", lambda: True)
    out = "INFO: No tasks are running which match the specified criteria.".encode()
    monkeypatch.setattr(metagrid.subprocess, "run", lambda *a, **kw: _proc(out))
    assert metagrid.is_dota_running() is False


def test_dota_running_linux_proc(tmp_path):
    (tmp_path / "1234").mkdir()
    (tmp_path / "1234" / "comm").write_text("dota2\n")
    (tmp_path / "5678").mkdir()
    (tmp_path / "5678" / "comm").write_text("chrome\n")
    assert metagrid._is_dota_running_linux(str(tmp_path)) is True
    (tmp_path / "1234" / "comm").write_text("steam\n")
    assert metagrid._is_dota_running_linux(str(tmp_path)) is False


# --- run_update: дота запущена ---

@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Сеть/Steam/state замоканы, запись идёт в tmp_path."""
    steam = tmp_path / "steam"
    (steam / "userdata" / "12345").mkdir(parents=True)
    monkeypatch.setattr(metagrid, "fetch_html", lambda *a, **kw: "<html>")
    monkeypatch.setattr(metagrid, "parse_grid_from_html", lambda html, mode: _valid_grid())
    monkeypatch.setattr(metagrid, "state_file_path", lambda: tmp_path / "state.json")
    return steam


def _args(*argv):
    return metagrid.build_parser().parse_args(list(argv))


def test_run_update_fails_when_dota_running(fake_env, monkeypatch):
    monkeypatch.setattr(metagrid, "is_dota_running", lambda: True)
    args = _args("--steam-path", str(fake_env))
    with pytest.raises(metagrid.MetaGridError, match="Закройте Dota 2"):
        metagrid.run_update(args)
    # Файл НЕ записан
    assert not (fake_env / "userdata" / "12345" / "570" / "remote" / "cfg" /
                "hero_grid_config.json").exists()


def test_run_update_force_writes_when_dota_running(fake_env, monkeypatch, capsys):
    monkeypatch.setattr(metagrid, "is_dota_running", lambda: True)
    args = _args("--steam-path", str(fake_env), "--force")
    metagrid.run_update(args)
    out = capsys.readouterr().out
    assert "ВНИМАНИЕ" in out and "Записано и проверено" in out
    target = fake_env / "userdata" / "12345" / "570" / "remote" / "cfg" / "hero_grid_config.json"
    assert json.loads(target.read_text(encoding="utf-8"))["configs"] == _valid_grid()["configs"]


def test_run_update_summary_printed(fake_env, monkeypatch, capsys):
    monkeypatch.setattr(metagrid, "is_dota_running", lambda: False)
    args = _args("--steam-path", str(fake_env))
    metagrid.run_update(args)
    out = capsys.readouterr().out
    assert "Dota2ProTracker 7.41e - All Roles: 2 категорий, 60 героев" in out
    assert "Записано и проверено" in out


def test_run_update_invalid_grid_not_written(fake_env, monkeypatch):
    monkeypatch.setattr(metagrid, "parse_grid_from_html", lambda html, mode: {"configs": []})
    args = _args("--steam-path", str(fake_env))
    with pytest.raises(metagrid.MetaGridError, match="невалидна"):
        metagrid.run_update(args)


# --- verify_written_config ---

def test_verify_written_config_ok(tmp_path):
    target = metagrid.write_grid_config(tmp_path, _valid_grid())
    metagrid.verify_written_config(target, _valid_grid())  # не падает


def test_verify_written_config_mismatch(tmp_path):
    target = metagrid.write_grid_config(tmp_path, _valid_grid())
    target.write_text(json.dumps({"configs": [{"config_name": "Other"}]}), encoding="utf-8")
    with pytest.raises(metagrid.MetaGridError, match="не совпадает"):
        metagrid.verify_written_config(target, _valid_grid())


def test_verify_written_config_broken_json(tmp_path):
    target = tmp_path / "hero_grid_config.json"
    target.write_text("{битый", encoding="utf-8")
    with pytest.raises(metagrid.MetaGridError, match="не читается"):
        metagrid.verify_written_config(target, _valid_grid())


# --- find_cfg_dirs: фильтрация служебных папок ---

def test_find_cfg_dirs_skips_system_folders(tmp_path):
    steam = tmp_path / "steam"
    for name in ("12345", "0", "ac", "anonymous"):
        (steam / "userdata" / name).mkdir(parents=True)
    dirs = metagrid.find_cfg_dirs(steam)
    assert dirs == [steam / "userdata" / "12345" / "570" / "remote" / "cfg"]


# --- setup_logging ---

def test_setup_logging_creates_rotating_file(tmp_path):
    log_path = metagrid.setup_logging(tmp_path / "logs" / "metagrid.log")
    assert log_path == tmp_path / "logs" / "metagrid.log"
    handler = metagrid.log.handlers[0]
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == metagrid.LOG_MAX_BYTES
    assert metagrid.log.level == 20  # INFO
    metagrid.log.info("тестовая запись")
    for h in metagrid.log.handlers:
        h.flush()
    assert "тестовая запись" in log_path.read_text(encoding="utf-8")


def test_setup_logging_verbose_debug(tmp_path):
    log_path = metagrid.setup_logging(tmp_path / "metagrid.log", verbose=True)
    assert metagrid.log.level == 10  # DEBUG
    metagrid.log.debug("отладочная запись")
    for h in metagrid.log.handlers:
        h.flush()
    assert "отладочная запись" in log_path.read_text(encoding="utf-8")
