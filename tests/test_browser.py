# -*- coding: utf-8 -*-
"""Тесты поиска браузера для headless-fallback: Edge в приоритете, без сети и реальных браузеров."""

import pytest

import metagrid


def test_windows_edge_before_chrome(monkeypatch):
    """На Windows Edge (msedge.exe) должен идти раньше Chrome."""
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")
    monkeypatch.setattr(metagrid.os.path, "exists", lambda p: True)

    candidates = metagrid._browser_candidates(os_name="nt")
    edge = [c for c in candidates if "msedge.exe" in c]
    chrome = [c for c in candidates if "chrome.exe" in c]
    assert edge and chrome
    assert candidates[0] == r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    assert min(map(candidates.index, edge)) < min(map(candidates.index, chrome))


def test_linux_edge_before_chrome(monkeypatch):
    """На Linux microsoft-edge из PATH идёт раньше google-chrome."""
    paths = {"microsoft-edge": "/usr/bin/microsoft-edge", "google-chrome": "/usr/bin/google-chrome"}
    monkeypatch.setattr(metagrid.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(metagrid.os.path, "exists", lambda p: True)

    candidates = metagrid._browser_candidates(os_name="posix")
    assert candidates == ["/usr/bin/microsoft-edge", "/usr/bin/google-chrome"]


def test_find_browser_returns_first(monkeypatch):
    monkeypatch.setattr(metagrid, "_browser_candidates", lambda: ["/edge", "/chrome"])
    assert metagrid.find_browser() == "/edge"


def test_find_browser_error_message(monkeypatch):
    """Если браузеров нет — понятная ошибка про Edge и Chrome."""
    monkeypatch.setattr(metagrid, "_browser_candidates", lambda: [])
    with pytest.raises(metagrid.MetaGridError, match="Microsoft Edge"):
        metagrid.find_browser()


def test_candidates_deduplicated(monkeypatch):
    """Дубликаты путей (например, из реестра и PATH) убираются, порядок сохраняется."""
    monkeypatch.setattr(metagrid.shutil, "which", lambda name: "/usr/bin/same")
    monkeypatch.setattr(metagrid.os.path, "exists", lambda p: True)

    assert metagrid._browser_candidates(os_name="posix") == ["/usr/bin/same"]
