#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dota2-metagrid-bot — ежедневное обновление метовой сетки героев Dota 2.

Скачивает сетку с https://dota2protracker.com/meta-hero-grids (режим D2PT Rating)
и записывает её в <Steam>/userdata/<id>/570/remote/cfg/hero_grid_config.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
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
LOG_FILE_NAME = "metagrid.log"
LOG_MAX_BYTES = 1_000_000  # ~1 МБ на файл, всего 2 файла (текущий + 1 архивный)
MIN_TOTAL_HEROES = 50  # суммарно hero_ids во всех категориях — sanity check
TASK_LOGON = "Dota2MetaGrid-Logon"
TASK_DAILY = "Dota2MetaGrid-Daily"
SUPPORT_HINT = ("Если проблема повторяется — пришлите файл metagrid.log разработчикам: "
                "github.com/paintdrip/dota2-metagrid-bot/issues")

log = logging.getLogger("metagrid")


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
    log.info("Headless-браузер: %s", browser)
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
        html = fetch_html_cffi(url, verbose)
        log.info("Страница получена через curl_cffi (%d байт)", len(html))
        return html
    except MetaGridError as exc:
        log.info("curl_cffi не сработал (%s), переключаюсь на headless-браузер", exc)
        if verbose:
            print(f"[fetch] прямой запрос не сработал: {exc}")
            print("[fetch] переключаюсь на headless-браузер (Edge/Chrome)...")
        html = fetch_html_browser(url, verbose)
        log.info("Страница получена через headless-браузер (%d байт)", len(html))
        return html


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


def validate_grid(grid: dict) -> None:
    """Проверить структуру сетки перед записью (fail fast, ошибка на русском).

    Клиент Dota 2 молча игнорирует битый hero_grid_config.json, поэтому
    невалидную структуру нельзя писать в файл.
    """
    problems: list[str] = []
    configs = grid.get("configs") if isinstance(grid, dict) else None
    if not isinstance(configs, list) or not configs:
        problems.append("нет непустого списка configs")
    else:
        total_heroes = 0
        non_empty_categories = 0
        for i, config in enumerate(configs):
            if not isinstance(config, dict):
                problems.append(f"configs[{i}] — не объект")
                continue
            if not isinstance(config.get("config_name"), str) or not config["config_name"]:
                problems.append(f"configs[{i}]: нет строкового config_name")
            categories = config.get("categories")
            if not isinstance(categories, list) or not categories:
                problems.append(f"configs[{i}] ({config.get('config_name', '?')}): "
                                "нет непустого списка categories")
                continue
            for j, category in enumerate(categories):
                if not isinstance(category, dict):
                    problems.append(f"configs[{i}].categories[{j}] — не объект")
                    continue
                if not isinstance(category.get("category_name"), str):
                    problems.append(f"configs[{i}].categories[{j}]: нет строкового category_name")
                hero_ids = category.get("hero_ids")
                if not isinstance(hero_ids, list):
                    problems.append(f"configs[{i}].categories[{j}] "
                                    f"({category.get('category_name', '?')}): hero_ids — не список")
                    continue
                bad = [h for h in hero_ids if not isinstance(h, int) or isinstance(h, bool) or h <= 0]
                if bad:
                    problems.append(f"configs[{i}].categories[{j}] "
                                    f"({category.get('category_name', '?')}): "
                                    f"невалидные hero_ids: {bad[:5]}")
                if hero_ids:
                    non_empty_categories += 1
                total_heroes += len(hero_ids)
        if configs and non_empty_categories == 0:
            problems.append("все категории пусты (ни одного hero_id)")
        if not problems and total_heroes < MIN_TOTAL_HEROES:
            problems.append(f"подозрительно мало героев суммарно: {total_heroes} "
                            f"(ожидается не менее {MIN_TOTAL_HEROES}) — похоже на обрезанные данные")
    if problems:
        details = "; ".join(problems[:10])
        raise MetaGridError(
            f"Извлечённая сетка невалидна и НЕ была записана: {details}. "
            f"Сайт мог изменить формат данных. {SUPPORT_HINT}"
        )


# ---------------------------------------------------------------------------
# Проверка, запущена ли Dota 2
# ---------------------------------------------------------------------------

def _is_dota_running_linux(proc_root: str = "/proc") -> bool:
    """Поиск процесса dota2 по /proc (для разработки и тестов на Linux)."""
    for comm in Path(proc_root).glob("[0-9]*/comm"):
        try:
            if comm.read_text(errors="replace").strip() == "dota2":
                return True
        except OSError:
            continue
    return False


def is_dota_running() -> bool:
    """True, если запущен клиент Dota 2 (dota2.exe / dota2)."""
    if _is_windows():
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq dota2.exe", "/NH"],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.warning("Не удалось проверить запущенные процессы (tasklist)")
            return False
        out = proc.stdout or b""
        # Ответ может быть в OEM-кодировке — имя процесса ASCII, ищем по байтам
        return b"dota2.exe" in out.lower()
    if os.name == "posix":
        return _is_dota_running_linux()
    return False


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
    """Найти директории cfg аккаунтов (userdata/<id>/570/remote/cfg).

    Берём только числовые папки реальных аккаунтов: служебные "0", "ac",
    "anonymous" и прочие нечисловые пропускаем.
    """
    userdata = steam_path / "userdata"
    if not userdata.is_dir():
        raise MetaGridError(
            f"В {steam_path} нет директории userdata — Dota 2 ни разу не запускалась?"
        )
    if user_id:
        ids = [user_id]
    else:
        ids = []
        for d in sorted(userdata.iterdir()):
            if not d.is_dir():
                continue
            if d.name.isdigit() and d.name != "0":
                ids.append(d.name)
            else:
                log.info("userdata: пропускаю служебную папку %s", d.name)
    if not ids:
        raise MetaGridError(f"В {userdata} не найдено ни одного аккаунта (числовых папок).")
    for uid in ids:
        log.info("Найден аккаунт Steam: %s", uid)
    return [userdata / uid / "570" / "remote" / "cfg" for uid in ids]


def verify_written_config(target: Path, grid: dict) -> None:
    """Пост-валидация: перечитать записанный файл и сравнить с тем, что писали."""
    try:
        written = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaGridError(
            f"Записанный файл {target} не читается как JSON: {exc}. {SUPPORT_HINT}"
        ) from exc
    if written.get("configs") != grid.get("configs"):
        raise MetaGridError(
            f"Файл {target} после записи не совпадает с тем, что записывалось "
            f"(кто-то перезаписал файл?). {SUPPORT_HINT}"
        )


def grid_summary(grid: dict) -> list[str]:
    """Строки сводки по конфигам: имя, число категорий и героев."""
    lines = []
    for config in grid.get("configs", []):
        categories = config.get("categories", [])
        heroes = sum(len(c.get("hero_ids", [])) for c in categories)
        lines.append(f'{config.get("config_name", "?")}: '
                     f'{len(categories)} категорий, {heroes} героев')
    return lines


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


def setup_logging(log_path: Path | None = None, verbose: bool = False) -> Path:
    """Настроить лог прогона: metagrid.log рядом с state.json, с ротацией.

    Уровень INFO, при --verbose — DEBUG. Ротация: ~1 МБ на файл, 2 файла всего.
    """
    if log_path is None:
        log_path = state_file_path().parent / LOG_FILE_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    for handler in list(log.handlers):
        log.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log_path


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

# Переменная окружения, через которую повышенной копии передаётся исходный пользователь
ELEVATED_ENV_VAR = "METAGRID_INSTALL_USER"


def _is_windows() -> bool:
    return os.name == "nt"


def _run_command_line() -> str:
    """Команда, которой запускать утилиту из Планировщика."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autorun_tasks_exist() -> bool:
    """True, если задачи автозагрузки уже созданы в Планировщике (только Windows)."""
    if not _is_windows():
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
        is_windows = _is_windows()
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


def _task_commands(run: str, user: str | None = None) -> list[tuple[str, list[str]]]:
    """Команды schtasks для обеих задач.

    С user — добавляются /RU <user> /RL LIMITED (режим --install-elevated:
    задача привязывается к обычному пользователю, а не к админскому контексту).
    """
    tasks = [
        (TASK_LOGON, ["/SC", "ONLOGON"]),
        (TASK_DAILY, ["/SC", "DAILY", "/ST", "12:00"]),
    ]
    cmds: list[tuple[str, list[str]]] = []
    for name, schedule in tasks:
        cmd = ["schtasks", "/Create", "/TN", name, *schedule, "/TR", f"{run} --auto"]
        if user:
            cmd += ["/RU", user, "/RL", "LIMITED"]
        cmd.append("/F")
        cmds.append((name, cmd))
    return cmds


def _is_access_denied(proc: subprocess.CompletedProcess) -> bool:
    """True, если schtasks упал с ошибкой доступа (ru/en, любая кодировка консоли)."""
    if proc.returncode == 0:
        return False
    out = (proc.stdout or b"") + (proc.stderr or b"")
    if isinstance(out, bytes):
        # Консоль Windows может отвечать в OEM (cp866) или ANSI (cp1251) —
        # декодируем во всех вариантах и ищем без учёта регистра.
        variants = [out.decode(enc, errors="replace") for enc in ("utf-8", "cp866", "cp1251")]
    else:
        variants = [out]
    low = " ".join(variants).lower()
    return "access is denied" in low or "отказано в доступе" in low


def _current_user() -> str:
    """DOMAIN\\user текущего пользователя из переменных окружения."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def _relaunch_elevated() -> None:
    """Перезапустить утилиту с правами администратора (UAC) для установки задач.

    Исходный интерактивный пользователь прокидывается через переменную
    окружения ELEVATED_ENV_VAR — дочерний процесс её наследует.
    """
    import ctypes

    os.environ[ELEVATED_ENV_VAR] = _current_user()
    if getattr(sys, "frozen", False):
        params = "--install-elevated"
    else:
        params = f'"{os.path.abspath(__file__)}" --install-elevated'
    # ShellExecuteW с глаголом "runas" показывает окно UAC
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    if rc <= 32:  # SE_ERR_ACCESSDENIED=5 (отказ в UAC) и прочие ошибки запуска
        raise MetaGridError(
            "Права администратора не получены (в окне UAC нажата «Нет»?). "
            "Автозагрузка НЕ установлена — можно повторить позже: "
            "dota2-metagrid.exe --install"
        )
    print("Открыто окно с правами администратора — установка продолжается в нём.")


def install_autorun() -> None:
    """Создать задачи автозагрузки; при нехватке прав — повтор через UAC."""
    if not _is_windows():
        raise MetaGridError("--install поддерживается только на Windows.")
    for name, cmd in _task_commands(_run_command_line()):
        proc = subprocess.run(cmd, capture_output=True)
        if _is_access_denied(proc):
            print("Недостаточно прав — запрашиваю права администратора (окно UAC)...")
            _relaunch_elevated()
            return
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
            raise MetaGridError(f"schtasks вернул ошибку {proc.returncode}: {detail}")
        print(f"Создана задача Планировщика: {name}")
    print("Готово. Сетка будет обновляться при входе в систему и ежедневно в 12:00.")
    print("(Повторные запуски в течение 20 часов пропускаются автоматически.)")


def install_autorun_elevated() -> None:
    """Режим --install-elevated: создание задач из повышенного процесса.

    Задачи создаются с /RU <исходный пользователь> /RL LIMITED, чтобы они
    работали от обычного юзера, а не от админского контекста.
    """
    if not _is_windows():
        raise MetaGridError("--install-elevated поддерживается только на Windows.")
    user = os.environ.get(ELEVATED_ENV_VAR) or _current_user()
    for name, cmd in _task_commands(_run_command_line(), user=user):
        subprocess.run(cmd, check=True)
        print(f"Создана задача Планировщика: {name} (пользователь: {user})")
    print("Готово. Сетка будет обновляться при входе в систему и ежедневно в 12:00.")
    print("(Повторные запуски в течение 20 часов пропускаются автоматически.)")


def uninstall_autorun() -> None:
    # Удаление задач текущего пользователя прав администратора не требует
    if not _is_windows():
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
        log.info("Пропуск: последнее успешное обновление %s", when)
        return

    print(f"Скачиваю метовую сетку с {URL} ...")
    log.info("Скачиваю %s (режим: %s)", URL, args.mode)
    html = fetch_html(URL, verbose=args.verbose)

    print(f"Извлекаю сетку (режим: {args.mode}) ...")
    grid = parse_grid_from_html(html, mode=args.mode)
    validate_grid(grid)
    for line in grid_summary(grid):
        print(f"  {line}")
        log.info("Сетка: %s", line)

    if args.dry_run:
        print("Режим --dry-run: конфиг игры НЕ изменялся.")
        log.info("dry-run: запись пропущена")
        return

    # Клиент Dota 2 перезаписывает hero_grid_config.json при выходе из игры —
    # запись при запущенной игре бесполезна (файл затрётся старой версией)
    if is_dota_running():
        message = ("Dota 2 сейчас запущена. Игра перезаписывает hero_grid_config.json "
                   "при выходе — обновлённая сетка затрётся. Закройте Dota 2 полностью "
                   "и запустите утилиту ещё раз (либо используйте --force на свой риск).")
        if not args.force:
            log.error("Dota 2 запущена — запись отменена")
            raise MetaGridError(message)
        print(f"ВНИМАНИЕ: {message}")
        log.warning("Dota 2 запущена, но запись разрешена флагом --force")

    steam_path = find_steam_path(args.steam_path)
    log.info("Путь Steam: %s", steam_path)
    if args.verbose:
        print(f"[steam] путь: {steam_path}")
    cfg_dirs = find_cfg_dirs(steam_path, args.user_id)
    for cfg_dir in cfg_dirs:
        target = write_grid_config(cfg_dir, grid, verbose=args.verbose)
        verify_written_config(target, grid)
        print(f"Записано и проверено: {target}")
        log.info("Записано и проверено: %s", target)

    write_last_success(state_path)
    print("Готово. Если Dota 2 была запущена — перезапустите игру, чтобы увидеть сетку.")
    log.info("Обновление завершено успешно")


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
                        help="игнорировать дедупликацию в режиме --auto и "
                             "разрешить запись при запущенной Dota 2")
    parser.add_argument("--install", action="store_true",
                        help="добавить задачи в Планировщик задач Windows (автозагрузка)")
    # Скрытый флаг: служебный режим, вызывается только повышенной копией через UAC
    parser.add_argument("--install-elevated", action="store_true", help=argparse.SUPPRESS)
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
        setup_logging(verbose=args.verbose)
        log.info("Запуск: %s", " ".join(raw_args) or "(без аргументов)")
    except OSError as exc:
        # Лог — вспомогательная вещь, без него работаем дальше
        print(f"Предупреждение: не удалось открыть metagrid.log: {exc}", file=sys.stderr)
    # Повышенная копия запущена в новом окне — пауза в конце, чтобы юзер увидел результат
    pause_at_end = interactive or args.install_elevated
    try:
        if args.install_elevated:
            install_autorun_elevated()
            return 0
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
        log.error("%s", exc)
        print(f"Ошибка: {exc}", file=sys.stderr)
        if SUPPORT_HINT not in str(exc):
            print(SUPPORT_HINT, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        log.error("команда %s: %s", exc.cmd, exc)
        print(f"Ошибка: не удалось выполнить команду {exc.cmd}: {exc}", file=sys.stderr)
        print(SUPPORT_HINT, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем.", file=sys.stderr)
        return 1
    finally:
        # Пауза, чтобы окно консоли не закрылось мгновенно при двойном клике
        if pause_at_end:
            try:
                input("Нажмите Enter для выхода...")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    sys.exit(main())
