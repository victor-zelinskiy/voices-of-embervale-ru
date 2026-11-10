# Голоса Эмбервейла на Steam Machine / SteamOS / Linux

На SteamOS Enshrouded работает через Proton, то есть это та же Windows-сборка игры, что и на ПК. Поэтому озвучка, Lua-мод EML и runtime-хуки подходят без изменений. Отличаются только две вещи:

1. `VoiceInstaller.exe` — Windows-приложение (WinForms). Его заменяет `ru_voice_steamos.py`.
2. Proton по умолчанию берёт свои встроенные `dbghelp.dll` и `winmm.dll`, а не DLL из папки игры. Скрипт прописывает в префикс Proton override `native,builtin` только для `enshrouded.exe`. Это тот же механизм, что `WINEDLLOVERRIDES` из инструкции EML для Linux.

Индекс игры EML встраивает сам при первом запуске, как и на Windows после обновления игры.

## Установка одной командой

В режиме рабочего стола откройте Konsole, закройте игру и выполните:

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash
```

Скрипт скачает установщик и полный архив мода 0.3.3 beta (~570 МБ) из [GitHub Releases автора](https://github.com/Daber-devNull/voices-of-embervale-ru/releases/tag/v0.3.3-beta) и сверит его SHA-256. Игру он найдёт сам во всех библиотеках Steam, в том числе на microSD. Если загрузка оборвётся, повторный запуск её докачает. Можно запускать сколько угодно раз.

Перед изменениями скрипт проверяет сборку игры (не ниже 1269579), однозначность трёх аудиосигнатур в `enshrouded.exe`, SHA-256 всех 1764 файлов пакета и конфликты DLL с другими модами. Затем он делает резервную копию `enshrouded.kfc` и `enshrouded.kfc_resources` в `.ru_voice_linux/`. При любой ошибке изменения откатываются.

Без интернета или с уже скачанным архивом можно запустить установщик напрямую: `python3 ru_voice_steamos.py install /путь/к/архиву.zip [--game /путь/к/Enshrouded]`.

## После установки

- Первый запуск дольше обычного: EML один раз подключает 1118 записей.
- Если в игре выбран русский язык, озвучка включается автоматически. При другом языке её переключает F3. На геймпаде назначьте F3 на свободную кнопку: Steam → Enshrouded → Раскладка контроллера.
- После первого запуска выйдите из игры и выполните проверку:

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash -s -- status
```

Должно быть «подключено записей 1118 из 1118» и «Russian voice hooks active».

## Удаление

```bash
curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash -s -- uninstall
```

Если сборка игры не менялась, скрипт удалит мод, DLL и overrides, а затем вернёт индекс и DAT-файлы из резервной копии. Если игра успела обновиться, он попросит проверить целостность файлов в Steam.

## Если префикс Proton ещё не создан

Если игра ни разу не запускалась, скрипт не сможет записать overrides и предупредит об этом. Тогда запустите игру один раз, выйдите и выполните ту же команду с `bash -s -- overrides`. Второй вариант — вручную задать параметры запуска:

```
WINEDLLOVERRIDES="dbghelp,winmm=n,b" %command%
```

## Диагностика

Журналы лежат в папке игры: `ru_voice_installer.log`, `ru_voice_runtime.log`, `logs/*eml.log`. Команда `status` разбирает их сама.
