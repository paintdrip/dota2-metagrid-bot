#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dota2-metagrid-bot — ежедневное обновление метовой сетки героев Dota 2.

Скачивает сетку с https://dota2protracker.com/meta-hero-grids (режим D2PT Rating)
и записывает её в <Steam>/userdata/<id>/570/remote/cfg/hero_grid_config.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

URL = "https://dota2protracker.com/meta-hero-grids"
DEFAULT_MODE = "d2ptrating"
AUTO_SKIP_HOURS = 20
CHROME_ATTEMPTS = 3
CHROME_TIMEOUT = 120  # секунд на одну попытку headless-рендера
STATE_DIR_NAME = "dota2-metagrid"
TASK_LOGON = "Dota2MetaGrid-Logon"
TASK_DAILY = "Dota2MetaGrid-Daily"


class MetaGridError(Exception):
    """Понятная пользователю ошибка (сообщение на русском)."""


# ---------------------------------------------------------------------------
# Загрузка страницы (Cloudflare bypass)
# ---------------------------------------------------------------------------

def _validate_html(html: str) -> bool:
    """Грубая проверка, что мы получили реальную страницу, а не заглушку Cloudflare."""
    return "start(app, element" in html and "grids" in html


def fetch_html_cffi(url: str, verbose: bool = False) -> str:
    """Основной путь: HTTP-запрос с impersonation Chrome через curl_cffi."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:  # pragma: no cover
        raise MetaGridError(
            "Не установлен пакет curl_cffi. Выполните: pip install -r requirements.txt"
        ) from exc

    resp = cffi_requests.get(url, impersonate="chrome", timeout=60)
    if resp.status_code != 200:
        raise MetaGridError(f"curl_cffi: HTTP {resp.status_code}")
    html = resp.text
    if not _validate_html(html):
        raise MetaGridError("curl_cffi: ответ похож на заглушку Cloudflare, а не на страницу")
    if verbose:
        print(f"[curl_cffi] получено {len(html)} байт")
    return html


def _browser_candidates(os_name: str | None = None) -> list[str]:
    """Возможные пути к браузеру на текущей платформе.

    Приоритет — Microsoft Edge: он предустановлен на всех Windows 10/11.
    Edge — Chromium, поэтому headless-флаги у него те же, что у Chrome.
    Firefox не подходит: headless-режима с выгрузкой DOM у него нет.
    """
    if os_name is None:
        os_name = os.name
    candidates: list[str] = []
    if os_name == "nt":
        prefixes = [
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rels = [
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
        ]
        candidates.extend(p + "\\" + r for p in prefixes if p for r in rels)
        # Путь из реестра: HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe
        try:
            import winreg

            for exe in ("msedge.exe", "chrome.exe"):
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                    ) as key:
                        candidates.append(winreg.QueryValueEx(key, "")[0])
                except OSError:
                    pass
        except ImportError:
            pass
    else:
        for name in ("microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
            path = shutil.which(name)
            if path:
                candidates.append(path)
    # Убираем дубликаты, сохраняя порядок
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c not in seen and os.path.exists(c):
            seen.add(c)
            result.append(c)
    return result


def find_browser() -> str:
    """Найти установленный браузер для headless-рендера (Edge в приоритете)."""
    candidates = _browser_candidates()
    if not candidates:
        raise MetaGridError(
            "Не найден ни Microsoft Edge, ни Google Chrome. Один из них нужен "
            "для обхода защиты Cloudflare, когда прямой запрос заблокирован."
        )
    return candidates[0]


def fetch_html_browser(url: str, verbose: bool = False) -> str:
    """Запасной путь: рендер страницы headless-браузером с временным профилем.

    Профиль сохраняется между попытками — кука cf_clearance, полученная на
    первой попытке, помогает на следующих.
    """
    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="metagrid-browser-")
    last_error = "неизвестная ошибка"
    try:
        for attempt in range(1, CHROME_ATTEMPTS + 1):
            if verbose:
                print(f"[browser] попытка {attempt}/{CHROME_ATTEMPTS}: {browser}")
            cmd = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=30000",
                "--dump-dom",
                url,
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=CHROME_TIMEOUT,
                    encoding="utf-8", errors="replace",
                )
            except subprocess.TimeoutExpired:
                last_error = f"таймаут {CHROME_TIMEOUT} с"
                continue
            html = proc.stdout or ""
            if _validate_html(html):
                if verbose:
                    print(f"[browser] получено {len(html)} байт")
                return html
            last_error = "ответ не похож на страницу (возможно, Cloudflare challenge)"
            time.sleep(2)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    raise MetaGridError(f"Headless-браузер: не удалось получить страницу ({last_error})")


def fetch_html(url: str, verbose: bool = False) -> str:
    """Скачать HTML страницы: сначала curl_cffi, при неудаче — headless-браузер."""
    try:
        return fetch_html_cffi(url, verbose)
    except MetaGridError as exc:
        if verbose:
            print(f"[fetch] прямой запрос не сработал: {exc}")
            print("[fetch] переключаюсь на headless-браузер (Edge/Chrome)...")
        return fetch_html_browser(url, verbose)


# ---------------------------------------------------------------------------
# Извлечение и парсинг данных сетки
# ---------------------------------------------------------------------------

def _extract_balanced(text: str, start: int) -> str:
    """Извлечь подстроку от открывающей скобки text[start] до парной закрывающей.

    Учитывает строковые литералы и экранирование внутри них.
    """
    pairs = {"[": "]", "{": "}"}
    opener = text[start]
    closer = pairs[opener]
    depth = 0
    in_str: str | None = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise MetaGridError("Не удалось найти конец массива data в HTML страницы (битый ответ?)")


def extract_data_literal(html: str) -> str:
    """Найти вызов start(app, element, {...}) и извлечь литерал массива data."""
    idx = html.find("start(app, element")
    if idx == -1:
        raise MetaGridError(
            "В HTML не найден вызов start(app, element, ...) — структура страницы "
            "изменилась или это не страница dota2protracker."
        )
    match = re.search(r"\bdata\s*:\s*\[", html[idx:])
    if not match:
        raise MetaGridError("После start(app, element ...) не найден массив data.")
    array_start = idx + match.end() - 1  # позиция '['
    return _extract_balanced(html, array_start)


def parse_js_literal(literal: str):
    """Распарсить JS-литерал (devalue): сначала json, затем json5 с предобработкой."""
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        pass
    try:
        import json5
    except ImportError as exc:  # pragma: no cover
        raise MetaGridError(
            "Не установлен пакет json5. Выполните: pip install -r requirements.txt"
        ) from exc
    # void 0 / undefined / NaN не являются валидным JSON — заменяем на null.
    # (Наивная замена может задеть строковые значения с такими словами — приемлемый риск.)
    cleaned = re.sub(r"\bvoid\s+0\b", "null", literal)
    cleaned = re.sub(r"\bundefined\b", "null", cleaned)
    try:
        return json5.loads(cleaned)
    except Exception as exc:
        raise MetaGridError(f"Не удалось распарсить данные страницы: {exc}") from exc


def _find_grids_container(data) -> dict:
    """Найти среди элементов массива data объект, содержащий поле grids (dict)."""
    if not isinstance(data, list):
        raise MetaGridError("Массив data на странице имеет неожиданный формат (не список).")

    def candidates(obj, depth: int):
        """Словари внутри obj на глубине до depth включительно."""
        if isinstance(obj, dict):
            yield obj
            if depth > 0:
                for value in obj.values():
                    yield from candidates(value, depth - 1)
        elif isinstance(obj, list) and depth > 0:
            for item in obj:
                yield from candidates(item, depth - 1)

    for element in data:
        for cand in candidates(element, depth=2):
            if isinstance(cand.get("grids"), dict):
                return cand
    raise MetaGridError(
        "В данных страницы не найден объект с полем grids — "
        "сайт изменил структуру данных, требуется обновление утилиты."
    )


def pick_grid(grids: dict, mode: str) -> dict:
    """Выбрать из grids нужный режим с fallback: точное имя -> 'd2pt' -> matches_wr -> первый."""
    if mode in grids:
        grid = grids[mode]
    else:
        d2pt_keys = [k for k in grids if "d2pt" in k.lower()]
        if d2pt_keys:
            grid = grids[d2pt_keys[0]]
        elif "matches_wr" in grids:
            grid = grids["matches_wr"]
        elif grids:
            grid = grids[next(iter(grids))]
        else:
            raise MetaGridError("Объект grids на странице пуст.")
    if not isinstance(grid, dict) or "configs" not in grid:
        raise MetaGridError(
            f"Сетка для режима '{mode}' имеет неожиданную структуру (нет поля configs)."
        )
    return grid


def parse_grid_from_html(html: str, mode: str = DEFAULT_MODE) -> dict:
    """Полный цикл: HTML -> готовая структура hero_grid_config.json."""
    literal = extract_data_literal(html)
    data = parse_js_literal(literal)
    container = _find_grids_container(data)
    return pick_grid(container["grids"], mode)


# ---------------------------------------------------------------------------
# Steam / запись конфига
# ---------------------------------------------------------------------------

def find_steam_path(override: str | None = None) -> Path:
    """Определить путь к установленному Steam."""
    if override:
        path = Path(override)
        if not path.is_dir():
            raise MetaGridError(f"Указанный путь Steam не существует: {path}")
        return path
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                return Path(winreg.QueryValueEx(key, "SteamPath")[0])
        except (ImportError, OSError) as exc:
            raise MetaGridError(
                "Не удалось найти Steam в реестре (HKCU\\Software\\Valve\\Steam). "
                "Укажите путь вручную флагом --steam-path."
            ) from exc
    # Linux/macOS — для разработки и тестов
    for candidate in (Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"):
        if candidate.is_dir():
            return candidate
    raise MetaGridError("Steam не найден. Укажите путь вручную флагом --steam-path.")


def find_cfg_dirs(steam_path: Path, user_id: str | None = None) -> list[Path]:
    """Найти директории cfg всех аккаунтов (userdata/<id>/570/remote/cfg)."""
    userdata = steam_path / "userdata"
    if not userdata.is_dir():
        raise MetaGridError(
            f"В {steam_path} нет директории userdata — Dota 2 ни разу не запускалась?"
        )
    ids = [user_id] if user_id else [
        d.name for d in sorted(userdata.iterdir()) if d.is_dir() and d.name.isdigit()
    ]
    if not ids:
        raise MetaGridError(f"В {userdata} не найдено ни одного аккаунта (числовых папок).")
    return [userdata / uid / "570" / "remote" / "cfg" for uid in ids]


def write_grid_config(cfg_dir: Path, grid: dict, verbose: bool = False) -> Path:
    """Записать hero_grid_config.json: backup при первой перезаписи + атомарная запись."""
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "hero_grid_config.json"
    backup = cfg_dir / "hero_grid_config_backup.json"
    if target.exists() and not backup.exists():
        shutil.copy2(target, backup)
        if verbose:
            print(f"[config] создан backup: {backup}")
    payload = json.dumps(grid, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=cfg_dir, prefix=".hero_grid_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


# ---------------------------------------------------------------------------
# State-файл (дедупликация запусков в режиме --auto)
# ---------------------------------------------------------------------------

def state_file_path() -> Path:
    """Путь к state.json: рядом с exe, в %LOCALAPPDATA% или в ~/.local/state."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / STATE_DIR_NAME / "state.json"
    localappdata = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and localappdata:
        return Path(localappdata) / STATE_DIR_NAME / "state.json"
    return Path.home() / ".local" / "state" / STATE_DIR_NAME / "state.json"


def read_last_success(path: Path) -> float | None:
    """Прочитать timestamp последнего успешного обновления (None, если нет/битый)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = float(data.get("last_success"))
        return ts if ts > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def write_last_success(path: Path, ts: float | None = None) -> None:
    """Записать timestamp успешного обновления."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_success": ts if ts is not None else time.time()}),
        encoding="utf-8",
    )


def should_skip(path: Path, hours: float = AUTO_SKIP_HOURS, now: float | None = None) -> bool:
    """True, если последнее успешное обновление было меньше hours часов назад."""
    last = read_last_success(path)
    if last is None:
        return False
    return (now if now is not None else time.time()) - last < hours * 3600


# ---------------------------------------------------------------------------
# Автозагрузка Windows (Планировщик задач)
# ---------------------------------------------------------------------------

def _run_command_line() -> str:
    """Команда, которой запускать утилиту из Планировщика."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autorun_tasks_exist() -> bool:
    """True, если задачи автозагрузки уже созданы в Планировщике (только Windows)."""
    if os.name != "nt":
        return False
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_LOGON], capture_output=True
    )
    return proc.returncode == 0


def maybe_offer_autorun(interactive: bool, is_windows: bool | None = None) -> None:
    """Предложить добавить утилиту в автозагрузку.

    Только при интерактивном запуске без аргументов (двойной клик по exe),
    на Windows и если задачи ещё не созданы.
    """
    if not interactive:
        return
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return
    if autorun_tasks_exist():
        return
    try:
        answer = input("Добавить в автозагрузку, чтобы сетка обновлялась каждый день? [Y/n]: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer.strip().lower() in ("", "y", "yes", "д", "да"):
        try:
            install_autorun()
        except (MetaGridError, subprocess.CalledProcessError) as exc:
            print(f"Не удалось добавить в автозагрузку: {exc}", file=sys.stderr)


def install_autorun() -> None:
    if os.name != "nt":
        raise MetaGridError("--install поддерживается только на Windows.")
    run = _run_command_line()
    tasks = [
        (TASK_LOGON, ["/SC", "ONLOGON"]),
        (TASK_DAILY, ["/SC", "DAILY", "/ST", "12:00"]),
    ]
    for name, schedule in tasks:
        cmd = ["schtasks", "/Create", "/TN", name, *schedule, "/TR", f"{run} --auto", "/F"]
        subprocess.run(cmd, check=True)
        print(f"Создана задача Планировщика: {name}")
    print("Готово. Сетка будет обновляться при входе в систему и ежедневно в 12:00.")
    print("(Повторные запуски в течение 20 часов пропускаются автоматически.)")


def uninstall_autorun() -> None:
    if os.name != "nt":
        raise MetaGridError("--uninstall поддерживается только на Windows.")
    for name in (TASK_LOGON, TASK_DAILY):
        proc = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, text=True
        )
        if proc.returncode == 0:
            print(f"Удалена задача Планировщика: {name}")
        else:
            print(f"Задача {name} не найдена (уже удалена?).")


# ---------------------------------------------------------------------------
# Основной сценарий
# ---------------------------------------------------------------------------

def run_update(args: argparse.Namespace) -> None:
    state_path = state_file_path()
    if args.auto and not args.force and should_skip(state_path):
        last = read_last_success(state_path)
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "?"
        print(f"Пропуск: сетка уже обновлялась {when} (менее {AUTO_SKIP_HOURS} ч назад).")
        return

    print(f"Скачиваю метовую сетку с {URL} ...")
    html = fetch_html(URL, verbose=args.verbose)

    print(f"Извлекаю сетку (режим: {args.mode}) ...")
    grid = parse_grid_from_html(html, mode=args.mode)
    configs = grid.get("configs", [])
    total_heroes = sum(len(c.get("hero_ids", [])) for c in configs)
    print(f"Сетка получена: {len(configs)} категорий, {total_heroes} hero_ids.")

    if args.dry_run:
        print("Режим --dry-run: конфиг игры НЕ изменялся.")
        return

    steam_path = find_steam_path(args.steam_path)
    if args.verbose:
        print(f"[steam] путь: {steam_path}")
    cfg_dirs = find_cfg_dirs(steam_path, args.user_id)
    for cfg_dir in cfg_dirs:
        target = write_grid_config(cfg_dir, grid, verbose=args.verbose)
        print(f"Записано: {target}")

    write_last_success(state_path)
    print("Готово.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dota2-metagrid",
        description="Ежедневное обновление метовой сетки героев Dota 2 "
                    "с dota2protracker.com (режим D2PT Rating).",
    )
    parser.add_argument("--mode", default=DEFAULT_MODE,
                        help=f"режим выбора героев (по умолчанию: {DEFAULT_MODE})")
    parser.add_argument("--auto", action="store_true",
                        help="тихий режим для автозагрузки: пропуск, если обновлялись "
                             f"менее {AUTO_SKIP_HOURS} ч назад")
    parser.add_argument("--force", action="store_true",
                        help="игнорировать дедупликацию в режиме --auto")
    parser.add_argument("--install", action="store_true",
                        help="добавить задачи в Планировщик задач Windows (автозагрузка)")
    parser.add_argument("--uninstall", action="store_true",
                        help="удалить задачи из Планировщика задач Windows")
    parser.add_argument("--dry-run", action="store_true",
                        help="скачать и распарсить сетку, но не писать в конфиг игры")
    parser.add_argument("--verbose", action="store_true", help="подробный вывод")
    parser.add_argument("--steam-path", help="путь к Steam вручную (переопределяет реестр)")
    parser.add_argument("--user-id", help="обновить только указанный steam account id")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = sys.argv[1:] if argv is None else argv
    # Интерактивный запуск: без аргументов и с живой консолью (двойной клик по exe)
    interactive = not raw_args and sys.stdin.isatty()
    args = build_parser().parse_args(argv)
    try:
        if args.install:
            install_autorun()
            return 0
        if args.uninstall:
            uninstall_autorun()
            return 0
        run_update(args)
        if interactive:
            maybe_offer_autorun(interactive)
        return 0
    except MetaGridError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Ошибка: не удалось выполнить команду {exc.cmd}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем.", file=sys.stderr)
        return 1
    finally:
        # Пауза, чтобы окно консоли не закрылось мгновенно при двойном клике
        if interactive:
            try:
                input("Нажмите Enter для выхода...")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    sys.exit(main())
