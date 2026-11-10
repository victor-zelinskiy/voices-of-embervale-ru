# Голоса Эмбервейла на Steam Machine / SteamOS / Linux

На SteamOS Enshrouded работает через Proton, то есть это та же Windows-сборка игры, что и на ПК. Поэтому озвучка, Lua-мод EML и runtime-хуки подходят без изменений. Отличаются только две вещи:

1. `VoiceInstaller.exe` — Windows-приложение (WinForms). Его заменяет `ru_voice_steamos.py`.
2. На Windows runtime озвучки (`shroudtopia.dll`) подгружается через прокси `winmm.dll`. Под Proton игра его не загружает: на Steam Deck EML отработал, а runtime так и не загрузился. Поэтому на Linux runtime собран как прокси `dbghelp.dll` (`linux/bin/dbghelp.dll`, исходник `runtime/voice_runtime.c`). Он сам подгружает EML, переименованный в `eml_dbghelp.dll`, и пробрасывает ему все экспорты dbghelp. Скрипт прописывает в префикс Proton override `dbghelp=native,builtin` только для `enshrouded.exe`. Это тот же механизм, что `WINEDLLOVERRIDES` из инструкции EML для Linux.

Индекс игры EML встраивает сам при первом запуске, как и на Windows после обновления игры.

## Установка одной командой

В режиме рабочего стола откройте Konsole, закройте игру и выполните:

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash
```

Скрипт сам скачает всё нужное и сверит SHA-256: установщик, полный архив мода 0.3.3 beta (~570 МБ) из [GitHub Releases автора](https://github.com/Daber-devNull/voices-of-embervale-ru/releases/tag/v0.3.3-beta), runtime-DLL и проверочную утилиту. Игру он найдёт во всех библиотеках Steam, в том числе на microSD. Если загрузка оборвётся, повторный запуск её докачает.

Команду можно запускать сколько угодно раз. На уже установленном моде она ставит последнюю версию и включает автообновление. Установку прошлых версий скрипта (через `winmm.dll` и без автообновления) она переводит на новую схему на месте, без повторного скачивания.

Перед изменениями скрипт проверяет:

- сборку игры (не ниже 1269579);
- однозначность трёх аудиосигнатур в `enshrouded.exe`;
- SHA-256 всех 1764 файлов пакета;
- конфликты DLL с другими модами.

Затем он делает резервную копию `enshrouded.kfc` и `enshrouded.kfc_resources` в `.ru_voice_linux/`. Перед завершением мод прогоняется проверкой совместимости (см. ниже). При любой ошибке изменения откатываются.

## Автообновление и проверка совместимости

Установщик добавляет в параметры запуска Enshrouded в Steam `~/.local/share/voices-of-embervale-ru/launch.sh %command%`. Если там уже что-то было, это сохраняется. Чтобы Steam не затёр правку, установщик один раз перезапускает Steam.

Перед каждым запуском игры, в том числе в игровом режиме, `launch.sh`:

1. Сверяет `linux/release.json` в этом репозитории. Если вышла новая ревизия, сам скачивает и ставит её: установщик, runtime-DLL, исправленные файлы мода из `linux/overlay/`, а при смене пакета автора и новый архив.
2. Если с прошлой проверки изменилась игра или мод, проверяет совместимость:
   - сигнатуры аудиофункций в `enshrouded.exe`;
   - прогон Lua-мода нативным EML с флагом `--dry-run` (около секунды). Он выполняет мод по текущим файлам игры и ничего не записывает.
3. Если мод несовместим, например игра обновилась, а исправления ещё нет, мод временно отключается (`dbghelp.dll` → `dbghelp.dll.ru_voice_off`). Игра запускается без русской озвучки и показывает сообщение «Русская озвучка временно отключена…». Когда исправление выйдет, следующий запуск сам его поставит и включит озвучку обратно.
4. Запускает игру ровно один раз. Нет сети — проверка обновлений пропускается. Сбой или зависание проверки тоже не мешают запуску.

Сообщения показываются окнами zenity и закрываются сами, нажимать на геймпаде ничего не нужно. Обычный запуск без обновлений и изменений проходит без окон.

В игровом режиме окно запускается от имени игры: через `reaper SteamLaunch AppId=1203620` из её командной строки, системным `zenity` с UTF-8 локалью, без переменных Steam Runtime. Чтобы проверить, что окна видны, создайте файл `~/.local/share/voices-of-embervale-ru/dialog-test` и запустите игру: перед стартом появится тестовое окно. Результат каждого окна записывается в `ru_voice_installer.log`.

## После установки

- Первый запуск дольше обычного: EML один раз подключает 1118 записей.
- Русская озвучка включается сама при каждом запуске игры, на геймпаде ничего нажимать не нужно. F3 на клавиатуре по-прежнему временно выключает и включает её. Если удалить файл `ru_voice_autostart.txt` из папки игры, озвучка будет включаться только по F3, как в исходной версии.
- Проверка установки, версии и совместимости:

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash -s -- status
```

## Удаление

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash -s -- uninstall
```

Скрипт убирает `launch.sh` из параметров запуска (Steam при этом перезапускается), мод, DLL и overrides, а также скачанные файлы в `~/.local/share/voices-of-embervale-ru` и `~/.cache/voices-of-embervale-ru`. Если сборка игры не менялась, он возвращает индекс и DAT-файлы из резервной копии. Если игра успела обновиться, он попросит проверить целостность файлов в Steam.

## Если префикс Proton ещё не создан

Если игра ни разу не запускалась, скрипт не сможет записать overrides и предупредит об этом. Тогда запустите игру один раз, выйдите и выполните ту же команду с `bash -s -- overrides`. Второй вариант — вручную задать параметры запуска:

```
WINEDLLOVERRIDES="dbghelp=n,b" %command%
```

## Диагностика

Журналы лежат в папке игры: `ru_voice_installer.log` (там же решения `launch.sh` при каждом запуске), `ru_voice_runtime.log`, `logs/*eml.log`. Команда `status` разбирает их сама.

## Выпуск исправления (для разработчика)

После обновления игры:

1. Исправьте то, что сломалось:
   - Lua-мод: положите исправленный файл в `linux/overlay/` по пути внутри папки мода, например `linux/overlay/src/mod.lua`;
   - сигнатуры или runtime: исправьте `runtime/voice_runtime.c` и пересоберите `linux/bin/dbghelp.dll` по `runtime/BUILDING.md`;
   - новый пакет автора: обновите `PACKAGE` в `linux/make_release.py`.
2. Выполните `python linux/make_release.py`. Он пересчитает хэши и увеличит ревизию в `linux/release.json`.
3. Сделайте коммит и `git push fork steamos-installer:main`. Через несколько минут (кэш raw.githubusercontent.com) следующий запуск игры у игроков сам поставит исправление.

Проверочная утилита `linux/bin/emm-check.gz` — это EML 0.2.0 из `third_party/` с патчем `patches/eml-0.2.0-dry-run.patch`, собранный под Linux (Rust 1.88, x86_64, glibc 2.34+). Сборка в WSL или Linux:

```bash
git archive HEAD third_party/EML-0.2.0 | tar -x -C /tmp && cd /tmp/third_party/EML-0.2.0
patch -p1 < .../patches/eml-0.2.0-dry-run.patch
CARGO_PROFILE_RELEASE_STRIP=true cargo build --release --locked -p mod-manager-cli
gzip -9 -n -c target/release/emm > .../linux/bin/emm-check.gz
```
