#!/usr/bin/env bash
#
# Голоса Эмбервейла — русская озвучка Enshrouded на Steam Machine / Steam Deck / Linux.
#
# В РЕЖИМЕ РАБОЧЕГО СТОЛА (Konsole), при закрытой игре:
#   curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash
#
# Другие команды (тот же скрипт):
#   ... | bash -s -- status      проверить установку, версию и совместимость
#   ... | bash -s -- uninstall   удалить мод, автообновление и вернуть игровой индекс
#
# install (можно запускать повторно — это же обновление):
#   1. скачивает установщик ru_voice_steamos.py из этого репозитория;
#   2. по linux/release.json скачивает архив мода из GitHub Releases автора (Daber-devNull),
#      runtime-DLL и проверочную утилиту, сверяя SHA-256;
#   3. ставит мод, проверяет его совместимость с текущей сборкой игры и включает автообновление:
#      в параметры запуска Enshrouded в Steam добавляется launch.sh. Steam при этом перезапускается.
#      Дальше при каждом запуске игры мод сам обновляется и проверяется, без режима рабочего стола.
set -euo pipefail

REPO="victor-zelinskiy/voices-of-embervale-ru"
BRANCH="main"
TOOL_DIR="$HOME/.local/share/voices-of-embervale-ru"
TOOL="$TOOL_DIR/ru_voice_steamos.py"
TOOL_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/linux/ru_voice_steamos.py"

command -v curl    >/dev/null || { echo "!! Не найден curl"; exit 1; }
command -v python3 >/dev/null || { echo "!! Не найден python3"; exit 1; }

COMMAND="${1:-install}"
[ "$#" -gt 0 ] && shift
mkdir -p "$TOOL_DIR"

echo "==> Загрузка установщика…"
if curl -fsSL -H "Cache-Control: no-cache" -o "$TOOL.tmp" "$TOOL_URL"; then
  mv "$TOOL.tmp" "$TOOL"
else
  rm -f "$TOOL.tmp"
  [ -f "$TOOL" ] || { echo "!! Не удалось скачать $TOOL_URL"; exit 1; }
  echo "    (нет сети — используется сохранённая копия)"
fi

export RU_VOICE_HINT="curl -sSL https://raw.githubusercontent.com/$REPO/$BRANCH/linux/install-steamos.sh | bash -s --"
exec python3 "$TOOL" "$COMMAND" "$@"
