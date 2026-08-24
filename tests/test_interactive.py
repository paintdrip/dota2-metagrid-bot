# -*- coding: utf-8 -*-
"""Тесты интерактивного предложения автозагрузки и паузы (моки, без сети и schtasks)."""

import builtins
import sys

import pytest

import metagrid


@pytest.fixture
def install_calls(monkeypatch):
    """Перехват вызовов install_autorun и отключение проверки существующих задач."""
    calls = []
    monkeypatch.setattr(metagrid, "install_autorun", lambda: calls.append(True))
    monkeypatch.setattr(metagrid, "autorun_tasks_exist", lambda: False)
    return calls


def _patch_input(monkeypatch, answer):
    if isinstance(answer, BaseException):
        def _raising_input(*a):
            raise answer

        monkeypatch.setattr(builtins, "input", _raising_input)
    else:
        monkeypatch.setattr(builtins, "input", lambda *a: answer)


# --- maybe_offer_autorun ---

@pytest.mark.parametrize("answer", ["", "y", "Y", "д", "Д", "да", "Да", "yes"])
def test_offer_accepts_yes_variants(monkeypatch, install_calls, answer):
    _patch_input(monkeypatch, answer)
    metagrid.maybe_offer_autorun(interactive=True, is_windows=True)
    assert install_calls == [True]


@pytest.mark.parametrize("answer", ["n", "N", "нет", "No", "что угодно"])
def test_offer_declined(monkeypatch, install_calls, answer):
    _patch_input(monkeypatch, answer)
    metagrid.maybe_offer_autorun(interactive=True, is_windows=True)
    assert install_calls == []


def test_offer_eof_or_ctrl_c_does_not_crash(monkeypatch, install_calls):
    _patch_input(monkeypatch, EOFError())
    metagrid.maybe_offer_autorun(interactive=True, is_windows=True)
    _patch_input(monkeypatch, KeyboardInterrupt())
    metagrid.maybe_offer_autorun(interactive=True, is_windows=True)
    assert install_calls == []


def test_no_offer_when_not_interactive(monkeypatch, install_calls):
    monkeypatch.setattr(
        builtins, "input",
        lambda *a: pytest.fail("input() не должен вызываться в неинтерактивном режиме"),
    )
    metagrid.maybe_offer_autorun(interactive=False, is_windows=True)
    assert install_calls == []


def test_no_offer_on_non_windows(monkeypatch, install_calls):
    monkeypatch.setattr(
        builtins, "input", lambda *a: pytest.fail("input() не должен вызываться не на Windows")
    )
    metagrid.maybe_offer_autorun(interactive=True, is_windows=False)
    assert install_calls == []


def test_no_offer_when_tasks_exist(monkeypatch, install_calls):
    monkeypatch.setattr(metagrid, "autorun_tasks_exist", lambda: True)
    monkeypatch.setattr(
        builtins, "input", lambda *a: pytest.fail("input() не должен вызываться, задачи уже есть")
    )
    metagrid.maybe_offer_autorun(interactive=True, is_windows=True)
    assert install_calls == []


# --- main: интерактивность, пауза, флаги ---

class _FakeStdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def no_update(monkeypatch):
    """run_update без сети и конфига."""
    monkeypatch.setattr(metagrid, "run_update", lambda args: None)


def test_main_interactive_offers_and_pauses(monkeypatch, no_update):
    """Запуск без аргументов с TTY: предложение автозагрузки + пауза перед выходом."""
    prompts = []
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(metagrid, "maybe_offer_autorun", lambda interactive: prompts.append(("offer", interactive)))
    monkeypatch.setattr(builtins, "input", lambda *a: prompts.append(("pause", a)) or "")

    assert metagrid.main([]) == 0
    assert ("offer", True) in prompts
    assert any(p[0] == "pause" for p in prompts)


def test_main_no_offer_when_not_tty(monkeypatch, no_update):
    """Без TTY (планировщик, пайп): ни вопроса, ни паузы."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    monkeypatch.setattr(
        metagrid, "maybe_offer_autorun", lambda interactive: pytest.fail("не должно быть предложения")
    )
    monkeypatch.setattr(
        builtins, "input", lambda *a: pytest.fail("не должно быть паузы без TTY")
    )
    assert metagrid.main([]) == 0


@pytest.mark.parametrize("argv", [["--auto"], ["--dry-run"], ["--install"], ["--uninstall"]])
def test_main_no_offer_with_flags(monkeypatch, argv):
    """Режимы с флагами — неинтерактивные: ни вопроса, ни паузы."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(metagrid, "run_update", lambda args: None)
    monkeypatch.setattr(metagrid, "install_autorun", lambda: None)
    monkeypatch.setattr(metagrid, "uninstall_autorun", lambda: None)
    monkeypatch.setattr(
        metagrid, "maybe_offer_autorun", lambda interactive: pytest.fail("не должно быть предложения")
    )
    monkeypatch.setattr(
        builtins, "input", lambda *a: pytest.fail("не должно быть паузы при флагах")
    )
    assert metagrid.main(argv) == 0


def test_main_pause_even_on_error(monkeypatch):
    """При ошибке обновления пауза всё равно есть — юзер должен увидеть сообщение."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))

    def boom(args):
        raise metagrid.MetaGridError("тестовая ошибка")

    monkeypatch.setattr(metagrid, "run_update", boom)
    monkeypatch.setattr(
        metagrid, "maybe_offer_autorun", lambda interactive: pytest.fail("при ошибке не предлагаем")
    )
    paused = []
    monkeypatch.setattr(builtins, "input", lambda *a: paused.append(True) or "")

    assert metagrid.main([]) == 1
    assert paused == [True]
