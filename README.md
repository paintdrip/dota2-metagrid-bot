# dota2-metagrid-bot

**Русский** | [English](README.en.md)

Терминальная утилита для Windows, которая ежедневно скачивает метовую сетку
героев Dota 2 с [dota2protracker.com/meta-hero-grids](https://dota2protracker.com/meta-hero-grids)
(режим **D2PT Rating**) и записывает её прямо в конфиг игры:

```
<Steam>/userdata/<steam_account_id>/570/remote/cfg/hero_grid_config.json
```

Запустил один раз — и в драфте всегда актуальная мета. Сетка на сайте
обновляется ежедневно, утилита живёт в автозагрузке и делает всё сама.

Источник данных: [dota2protracker.com](https://dota2protracker.com) —
спасибо авторам сайта за их работу.

## Установка (пошагово)

Никаких дополнительных программ ставить не нужно — ни Python, ни что-либо
ещё. Только сама утилита.

### Шаг 1. Скачайте программу

Откройте страницу
[Releases](https://github.com/paintdrip/dota2-metagrid-bot/releases/latest)
и скачайте файл **`dota2-metagrid.exe`** (кнопка Assets → dota2-metagrid.exe).

### Шаг 2. Положите файл в постоянную папку

Создайте папку, например `C:\Tools\dota2-metagrid\`, и переместите туда
скачанный файл. Не оставляйте его в «Загрузках» — оттуда его легко
случайно удалить, а задача автозагрузки привяжется к этому пути.

### Шаг 3. Первый запуск

Откройте папку в Проводнике, зажмите `Shift`, нажмите правой кнопкой по
пустому месту и выберите «Открыть окно PowerShell здесь» (или «Открыть в
Терминале»). Введите:

```
.\dota2-metagrid.exe
```

При первом запуске Windows может показать синее окно «Система SmartScreen
защитила ваш компьютер» — это стандартное предупреждение для любых новых
программ без цифровой подписи. Нажмите **«Подробнее» → «Выполнить в любом
случае»**.

Программа скачает сетку и запишет её в конфиг Dota 2. В конце вы увидите
строку вида:

```
Готово: сетка D2PT Rating записана в C:\Program Files (x86)\Steam\userdata\...\570\remote\cfg\hero_grid_config.json
```

Если вместо этого появилась ошибка — прочитайте сообщение: оно на русском и
подсказывает, что не так (например, не найден Steam или сайт временно
недоступен).

### Шаг 4. Включите автозагрузку

В том же окне выполните:

```
.\dota2-metagrid.exe --install
```

Всё. Теперь сетка будет обновляться сама: при входе в Windows и ежедневно
в 12:00. Лишних запусков не будет — если сетка свежая (обновлялась менее
20 часов назад), программа просто завершится.

Убрать из автозагрузки можно в любой момент:

```
.\dota2-metagrid.exe --uninstall
```

### Шаг 5. Проверьте в игре

Запустите Dota 2, откройте вкладку «Герои» и в выпадающем списке сеток
выберите **Dota2ProTracker … - All Roles** (или сетку одной из ролей).

> Важно: Dota 2 читает `hero_grid_config.json` при запуске и перезаписывает
> его при выходе. Обновляйте сетку при закрытой игре — задачи автозагрузки
> обычно срабатывают именно в такие моменты.

## Сборка самостоятельно

Требуется Python 3.10+ и установленный Google Chrome.

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

## Использование (все команды)

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

## Автозагрузка (подробности)

`--install` создаёт две задачи Планировщика задач Windows:

- **Dota2MetaGrid-Logon** — запуск при входе в систему (`--auto`);
- **Dota2MetaGrid-Daily** — запуск ежедневно в 12:00 (`--auto`).

Момент последнего успешного обновления хранится в `state.json` рядом с exe
(либо в `%LOCALAPPDATA%\dota2-metagrid\state.json`).

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
из данных страницы и записывает его как есть.

Обновляются **все** аккаунты, найденные в `<Steam>/userdata` (если папки
`570/remote/cfg` нет — она создаётся).

Перед первой перезаписью существующего конфига делается backup:
`hero_grid_config_backup.json` (существующий backup не затирается).

Запись атомарная: временный файл + `os.replace`, повредить конфиг обрывом
записи нельзя.

Steam ищется через реестр (`HKCU\Software\Valve\Steam`, значение `SteamPath`);
при необходимости путь задаётся флагом `--steam-path`.

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
