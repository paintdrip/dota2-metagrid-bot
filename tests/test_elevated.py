# -*- coding: utf-8 -*-
"""Тесты гибридного повышения прав при установке автозагрузки.

Всё моками: без сети, реальных schtasks и WinAPI (ctypes).
"""

import subprocess

import pytest

import metagrid


@pytest.fixture
def as_windows(monkeypatch):
    """Притворяемся Windows без патчинга глобального os.name."""
    monkeypatch.setattr(metagrid, "_is_windows", lambda: True)


def _proc(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- _task_commands ---

def test_task_commands_plain_no_ru():
    cmds = metagrid._task_commands('"exe"')
    assert [name for name, _ in cmds] == [metagrid.TASK_LOGON, metagrid.TASK_DAILY]
    for _, cmd in cmds:
        assert "/RU" not in cmd and "/RL" not in cmd
        assert cmd[-1] == "/F"
        assert cmd[0] == "schtasks" and "/Create" in cmd


def test_task_commands_elevated_has_ru_and_rl():
    cmds = metagrid._task_commands('"exe"', user=r"HOME-PC\ivan")
    for _, cmd in cmds:
        i = cmd.index("/RU")
        assert cmd[i + 1] == r"HOME-PC\ivan"
        j = cmd.index("/RL")
        assert cmd[j + 1] == "LIMITED"
        assert cmd[-1] == "/F"


# --- _is_access_denied ---

@pytest.mark.parametrize("text", [
    "ERROR: Access is denied.",
    "Ошибка: Отказано в доступе.".encode("cp866").decode("cp866"),
])
def test_access_denied_detection(text):
    out = text.encode("cp866", errors="replace") if "Отказано" in text else text.encode()
    assert metagrid._is_access_denied(_proc(1, stderr=out)) is True


def test_access_denied_false_on_success_and_other_errors():
    assert metagrid._is_access_denied(_proc(0)) is False
    assert metagrid._is_access_denied(_proc(1, stderr=b"some other error")) is False


# --- install_autorun ---

def test_install_success_without_elevation(as_windows, monkeypatch, capsys):
    """Обычный путь: задачи созданы, повышение прав не вызывается."""
    runs = []
    monkeypatch.setattr(metagrid.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or _proc(0))
    monkeypatch.setattr(
        metagrid, "_relaunch_elevated", lambda: pytest.fail("runas не должен вызываться")
    )
    metagrid.install_autorun()
    assert len(runs) == 2
    out = capsys.readouterr().out
    assert metagrid.TASK_LOGON in out and metagrid.TASK_DAILY in out


def test_install_access_denied_triggers_runas(as_windows, monkeypatch):
    """Access denied от schtasks → перезапуск через UAC."""
    monkeypatch.setattr(
        metagrid.subprocess, "run",
        lambda cmd, **kw: _proc(1, stderr=b"ERROR: Access is denied."),
    )
    relaunched = []
    monkeypatch.setattr(metagrid, "_relaunch_elevated", lambda: relaunched.append(True))
    metagrid.install_autorun()
    assert relaunched == [True]


def test_install_other_schtasks_error_raises(as_windows, monkeypatch):
    monkeypatch.setattr(
        metagrid.subprocess, "run", lambda cmd, **kw: _proc(1, stderr=b"unknown failure")
    )
    monkeypatch.setattr(
        metagrid, "_relaunch_elevated", lambda: pytest.fail("runas не должен вызываться")
    )
    with pytest.raises(metagrid.MetaGridError, match="schtasks"):
        metagrid.install_autorun()


# --- _relaunch_elevated (ctypes мокаем) ---

class _FakeShell32:
    def __init__(self, rc):
        self.rc = rc
        self.calls = []

    def ShellExecuteW(self, hwnd, verb, exe, params, cwd, show):
        self.calls.append((verb, exe, params))
        return self.rc


def _patch_ctypes(monkeypatch, rc):
    import ctypes

    shell32 = _FakeShell32(rc)
    windll = type("windll", (), {"shell32": shell32})
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    return shell32


def test_relaunch_elevated_ok(as_windows, monkeypatch):
    shell32 = _patch_ctypes(monkeypatch, rc=42)
    monkeypatch.setenv("USERNAME", "ivan")
    monkeypatch.setenv("USERDOMAIN", "HOME-PC")
    metagrid._relaunch_elevated()
    verb, exe, params = shell32.calls[0]
    assert verb == "runas" and exe == metagrid.sys.executable
    assert "--install-elevated" in params
    # Исходный пользователь прокинут через переменную окружения
    assert metagrid.os.environ[metagrid.ELEVATED_ENV_VAR] == r"HOME-PC\ivan"


def test_relaunch_elevated_uac_declined(as_windows, monkeypatch):
    """Отказ в окне UAC (код <= 32) — понятная ошибка на русском."""
    _patch_ctypes(monkeypatch, rc=5)  # SE_ERR_ACCESSDENIED
    with pytest.raises(metagrid.MetaGridError, match="UAC"):
        metagrid._relaunch_elevated()


# --- install_autorun_elevated ---

def test_install_elevated_uses_env_user(as_windows, monkeypatch, capsys):
    """Повышенная копия создаёт задачи с /RU <исходный юзер> /RL LIMITED."""
    monkeypatch.setenv(metagrid.ELEVATED_ENV_VAR, r"HOME-PC\ivan")
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return _proc(0)

    monkeypatch.setattr(metagrid.subprocess, "run", fake_run)
    metagrid.install_autorun_elevated()
    assert len(runs) == 2
    for cmd in runs:
        assert cmd[cmd.index("/RU") + 1] == r"HOME-PC\ivan"
        assert cmd[cmd.index("/RL") + 1] == "LIMITED"
    out = capsys.readouterr().out
    assert r"HOME-PC\ivan" in out


def test_install_elevated_fallback_to_current_user(as_windows, monkeypatch):
    """Без переменной окружения — берём USERNAME/USERDOMAIN самого процесса."""
    monkeypatch.delenv(metagrid.ELEVATED_ENV_VAR, raising=False)
    monkeypatch.setenv("USERNAME", "ivan")
    monkeypatch.setenv("USERDOMAIN", "HOME-PC")
    runs = []
    monkeypatch.setattr(metagrid.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or _proc(0))
    metagrid.install_autorun_elevated()
    assert runs[0][runs[0].index("/RU") + 1] == r"HOME-PC\ivan"


# --- main: режим --install-elevated ---

def test_main_install_elevated_pauses(monkeypatch):
    """Повышенная копия в новом окне: пауза в конце, чтобы юзер увидел результат."""
    import builtins
    import sys

    monkeypatch.setattr(sys, "stdin", type("S", (), {"isatty": lambda self: False})())
    called = []
    monkeypatch.setattr(metagrid, "install_autorun_elevated", lambda: called.append(True))
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert metagrid.main(["--install-elevated"]) == 0
    assert called == [True]


def test_install_elevated_hidden_from_help():
    assert "--install-elevated" not in metagrid.build_parser().format_help()
