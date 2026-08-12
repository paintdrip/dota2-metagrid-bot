# -*- coding: utf-8 -*-
"""Тесты дедупликации запусков через state-файл."""

import metagrid


def test_no_state_means_run(tmp_path):
    assert metagrid.should_skip(tmp_path / "state.json") is False


def test_recent_success_skips(tmp_path):
    state = tmp_path / "state.json"
    metagrid.write_last_success(state, ts=1000.0)
    # Прошло 5 часов (< 20) — пропускаем
    assert metagrid.should_skip(state, hours=20, now=1000.0 + 5 * 3600) is True


def test_stale_success_runs(tmp_path):
    state = tmp_path / "state.json"
    metagrid.write_last_success(state, ts=1000.0)
    # Прошло 25 часов (> 20) — запускаемся
    assert metagrid.should_skip(state, hours=20, now=1000.0 + 25 * 3600) is False


def test_broken_state_means_run(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("not json at all", encoding="utf-8")
    assert metagrid.should_skip(state, now=1000.0) is False
    state.write_text('{"last_success": "oops"}', encoding="utf-8")
    assert metagrid.should_skip(state, now=1000.0) is False


def test_read_write_roundtrip(tmp_path):
    state = tmp_path / "sub" / "state.json"  # директория создаётся автоматически
    metagrid.write_last_success(state, ts=1234.5)
    assert metagrid.read_last_success(state) == 1234.5
