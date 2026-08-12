# dota2-metagrid-bot

Терминальная утилита для Windows, которая ежедневно скачивает метовую сетку
героев Dota 2 с [dota2protracker.com/meta-hero-grids](https://dota2protracker.com/meta-hero-grids)
(режим **D2PT Rating**) и записывает её в конфиг игры:

```
<Steam>/userdata/<steam_account_id>/570/remote/cfg/hero_grid_config.json
```

Сетка обновляется на сайте ежедневно — утилита предназначена для запуска из
автозагрузки, чтобы в игре всегда была актуальная мета.

Источник данных: [dota2protracker.com](https://dota2protracker.com) — спасибо
авторам сайта за их работу.

## Установка

### Готовый .exe (рекомендуется)

1. Скачайте `dota2-metagrid.exe` из раздела
   [Releases](https://github.com/paintdrip/dota2-metagrid-bot/releases).
2. Положите его в любую папку, например `C:\Tools\dota2-metagrid\`.
3. Запустите один раз, чтобы проверить работу:
   ```
   dota2-metagrid.exe
   ```
4. Добавьте в автозагрузку (Планировщик задач Windows):
   ```
   dota2-metagrid.exe --install
   ```

### Сборка самостоятельно

Требуется Python 3.10+ и установленный Google Chrome (см. ниже).

```bat
git clone https://github.com/paintdrip/dota2-metagrid-bot.git
cd dota2-metagrid-bot
build.bat
```

Готовый файл появится в `dist\dota2-metagrid.exe`.

Запуск из исходников без сборки:

```bat
pip install -r requirements.txt
python metagrid.py
```

## Использование

```
dota2-metagrid.exe                один прогон обновления с выводом прогресса
dota2-metagrid.exe --auto         тихий режим для автозагрузки: пропуск, если
                                  сетка обновлялась менее 20 часов назад
dota2-metagrid.exe --auto --force игнорировать проверку 20 часов
dota2-metagrid.exe --dry-run      скачать и распарсить сетку, не трогая конфиг
dota2-metagrid.exe --install      создать задачи в Планировщике (автозагрузка)
dota2-metagrid.exe --uninstall    удалить задачи из Планировщика
dota2-metagrid.exe --mode matches_wr   другой режим выбора героев
dota2-metagrid.exe --steam-path "D:\Steam"   указать Steam вручную
dota2-metagrid.exe --user-id 123456789      обновить только один аккаунт
dota2-metagrid.exe --verbose      подробный вывод
```

Коды возврата: `0` — успех (включая пропуск в `--auto`), `1` — ошибка
(сообщение выводится на русском в stderr).

## Автозагрузка

`--install` создаёт две задачи Планировщика задач Windows:

- **Dota2MetaGrid-Logon** — запуск при входе в систему (`--auto`);
- **Dota2MetaGrid-Daily** — запуск ежедневно в 12:00 (`--auto`).

Обе задачи запускают утилиту с флагом `--auto`: если с момента последнего
успешного обновления прошло меньше 20 часов, прогон пропускается. Момент
последнего успеха хранится в `state.json` рядом с exe (либо в
`%LOCALAPPDATA%\dota2-metagrid\state.json`).

## Что именно записывается

Файл `hero_grid_config.json` — это родной формат сетки героев Dota 2:

```json
{
  "configs": [
    {
      "config_name": "Dota2ProTracker 7.xx - All Roles",
      "categories": [
        {
          "category_name": "Carry",
          "x_position": 0,
          "y_position": 0,
          "width": 240,
          "height": 120,
          "hero_ids": [74, 11, 50]
        }
      ]
    }
  ],
  "version": 3
}
```

Утилита не конструирует эту структуру сама — она извлекает готовый объект
из данных страницы и записывает его как есть. Обновляются **все** аккаунты,
найденные в `<Steam>/userdata` (если папки `570/remote/cfg` нет — она
создаётся). Перед первой перезаписью существующего конфига делается backup:
`hero_grid_config_backup.json` (существующий backup не затирается). Запись
атомарная: временный файл + `os.replace`, повредить конфиг обрывом записи
нельзя.

Steam ищется через реестр (`HKCU\Software\Valve\Steam`, значение `SteamPath`);
при необходимости путь задаётся флагом `--steam-path`.

## Как работает обход Cloudflare — честно

Сайт закрыт Cloudflare managed challenge, поэтому утилита работает в два этапа:

1. **Основной путь** — прямой HTTPS-запрос через
   [curl_cffi](https://github.com/lexiforest/curl_cffi) с имитацией TLS/JA3-отпечатка
   реального Chrome. С обычных домашних IP этого, как правило, достаточно.
2. **Запасной путь** — если прямой запрос заблокирован (HTTP 403 или страница-
   заглушка), страница рендерится установленным Google Chrome в headless-режиме
   (`chrome --headless=new --dump-dom`) с временным профилем, до 3 попыток.
   Кука `cf_clearance`, полученная на первой попытке, сохраняется в профиле и
   помогает на следующих. Chrome ищется по стандартным путям установки и в
   реестре; при его отсутствии используется Microsoft Edge.

**Известное ограничение:** с некоторых IP-адресов (дата-центры, VPN, «плохая»
репутация IP) Cloudflare может не пропустить ни один из способов. В этом случае
утилита завершится с ошибкой и понятным сообщением — попробуйте позже или с
другого сетевого подключения.

Данные сетки встроены прямо в SSR-HTML страницы (вызов
`start(app, element, { ..., data: [...] })`, формат devalue). Утилита извлекает
массив `data` балансировкой скобок и парсит его (JSON → json5 с предобработкой
`void 0`/`undefined`/`NaN`), затем берёт объект `grids.d2ptrating`.

## Разработка

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/          # тесты (сетевых вызовов нет)
python metagrid.py --dry-run --verbose
```

На Linux/macOS для разработки Steam ищется в `~/.steam/steam` и
`~/.local/share/Steam`.

## Лицензия

[MIT](LICENSE)
