#!/usr/bin/env bash
#
# Голоса Эмбервейла — установка русской озвучки Enshrouded на Steam Machine / Steam Deck / Linux.
#
# В РЕЖИМЕ РАБОЧЕГО СТОЛА (Konsole), при закрытой игре:
#   curl -sSL https://raw.githubusercontent.com/victor-zelinskiy/voices-of-embervale-ru/main/linux/install-steamos.sh | bash
#
# Другие команды (тот же скрипт):
#   ... | bash -s -- status      проверить установку и журналы после запуска игры
#   ... | bash -s -- uninstall   удалить мод и вернуть игровой индекс
#   ... | bash -s -- overrides   записать DLL overrides, если префикс Proton появился после установки
#
# Что делает install (можно запускать повторно):
#   1. скачивает ru_voice_steamos.py (установщик) из этого репозитория;
#   2. скачивает полный архив мода из GitHub Releases автора (Daber-devNull) и сверяет SHA-256;
#   3. скачивает dbghelp.dll из этого репозитория (runtime озвучки + загрузчик EML) и сверяет SHA-256;
#   4. запускает установщик: проверки сборки игры и сигнатур, копирование файлов,
#      DLL override для Proton, резервная копия индекса, маркер автовключения.
#      Установку прошлой версии (через winmm.dll) тот же запуск обновляет на месте.
set -euo pipefail

REPO="victor-zelinskiy/voices-of-embervale-ru"
BRANCH="main"
MOD_REPO="${RU_VOICE_MOD_REPO:-Daber-devNull/voices-of-embervale-ru}"
# Pinned to the release the installer was verified against (payload layout and counts are checked).
TAG="${RU_VOICE_TAG:-v0.3.3-beta}"
ASSET="${RU_VOICE_ASSET:-Voices-of-Embervale-RU-0.3.3-beta-Nexus.zip}"

TOOL_DIR="$HOME/.local/share/voices-of-embervale-ru"
CACHE_DIR="$HOME/.cache/voices-of-embervale-ru"
TOOL="$TOOL_DIR/ru_voice_steamos.py"
TOOL_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/linux/ru_voice_steamos.py"
# Runtime built from runtime/voice_runtime.c as a dbghelp.dll proxy that chain-loads EML (Proton ignores
# winmm.dll in the game folder) and turns Russian voice on at startup (gamepad: no F3 needed).
RUNTIME_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/linux/bin/dbghelp.dll"
RUNTIME_SHA="aa30449865d7a2d1540efdbe634b659649e0f3a3a40d689365582f371e63b15d"
RUNTIME="$TOOL_DIR/dbghelp.dll"
ASSET_URL="https://github.com/$MOD_REPO/releases/download/$TAG/$ASSET"
ZIP="$CACHE_DIR/$ASSET"

command -v curl    >/dev/null || { echo "!! Не найден curl"; exit 1; }
command -v python3 >/dev/null || { echo "!! Не найден python3"; exit 1; }
command -v sha256sum >/dev/null || { echo "!! Не найден sha256sum"; exit 1; }

COMMAND="${1:-install}"
[ "$#" -gt 0 ] && shift
mkdir -p "$TOOL_DIR" "$CACHE_DIR"

echo "==> Загрузка установщика…"
if ! curl -fsSL -o "$TOOL.tmp" "$TOOL_URL"; then
  rm -f "$TOOL.tmp"
  [ -f "$TOOL" ] || { echo "!! Не удалось скачать $TOOL_URL"; exit 1; }
  echo "    (нет сети — используется сохранённая копия)"
else
  mv "$TOOL.tmp" "$TOOL"
fi

export RU_VOICE_HINT="curl -sSL https://raw.githubusercontent.com/$REPO/$BRANCH/linux/install-steamos.sh | bash -s --"

if [ "$COMMAND" != "install" ]; then
  exec python3 "$TOOL" "$COMMAND" "$@"
fi

echo "==> Архив озвучки: $TAG ($ASSET, ~570 МБ)"
EXPECTED="$(curl -fsSL "$ASSET_URL.sha256.txt" | awk '{print tolower($1)}')" || EXPECTED=""
if ! [[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]]; then
  echo "!! Не удалось получить контрольную сумму $ASSET_URL.sha256.txt" >&2
  exit 1
fi
if [ -f "$ZIP" ] && [ "$(sha256sum "$ZIP" | awk '{print $1}')" = "$EXPECTED" ]; then
  echo "    уже скачан и проверен: $ZIP"
else
  # -C - resumes an interrupted download; a corrupt partial file is discarded after the hash check.
  curl -fL# -C - -o "$ZIP" "$ASSET_URL" || curl -fL# -o "$ZIP" "$ASSET_URL"
  ACTUAL="$(sha256sum "$ZIP" | awk '{print $1}')"
  if [ "$ACTUAL" != "$EXPECTED" ]; then
    rm -f "$ZIP"
    echo "!! Архив повреждён (SHA-256 не совпал). Запустите команду ещё раз." >&2
    exit 1
  fi
  echo "    SHA-256 OK"
fi

echo "==> Runtime-DLL с автовключением озвучки…"
if [ ! -f "$RUNTIME" ] || [ "$(sha256sum "$RUNTIME" | awk '{print $1}')" != "$RUNTIME_SHA" ]; then
  curl -fsSL -o "$RUNTIME" "$RUNTIME_URL"
fi
if [ "$(sha256sum "$RUNTIME" | awk '{print $1}')" != "$RUNTIME_SHA" ]; then
  rm -f "$RUNTIME"
  echo "!! dbghelp.dll (runtime) повреждён (SHA-256 не совпал). Запустите команду ещё раз." >&2
  exit 1
fi
echo "    SHA-256 OK"

echo "==> Установка…"
python3 "$TOOL" install "$ZIP" --runtime-dll "$RUNTIME" "$@"

echo
echo "Архив сохранён в $ZIP (нужен только для переустановки; можно удалить)."
echo "Без интернета те же команды:  python3 $TOOL status   (или uninstall)"
