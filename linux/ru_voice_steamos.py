#!/usr/bin/env python3
"""Голоса Эмбервейла: установка русской озвучки Enshrouded на SteamOS / Linux (Proton).

Linux-замена VoiceInstaller.exe. Повторяет его проверки (сборка игры, сигнатуры
аудиофункций, SHA-256 всех файлов пакета, конфликты DLL) и добавляет то, что нужно
только под Proton. Proton не загружает winmm.dll (Shroudtopia) из папки игры, поэтому
наш runtime ставится как dbghelp.dll и сам подгружает EML (eml_dbghelp.dll); нужен один
DLL override для dbghelp. Озвучка включается при запуске (ru_voice_autostart.txt), без F3.
Индекс игры встраивает сам EML при первом запуске игры, как это задумано для Linux.

    python3 ru_voice_steamos.py install [ZIP или папка архива] [--game ПАПКА]
    python3 ru_voice_steamos.py status
    python3 ru_voice_steamos.py uninstall [--keep-backup]
    python3 ru_voice_steamos.py overrides

Только стандартная библиотека Python 3.8+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

APP_ID = "1203620"
MOD_ID = "ru_voice_separate_525"
LEGACY_MOD_ID = "ru_voice_separate_520"
DLLS = ("dbghelp.dll", "winmm.dll", "shroudtopia.dll")  # native/ entries of the package
# Proton does not load winmm.dll (Shroudtopia) from the game folder, but it does load dbghelp.dll there,
# as EML proves. Our runtime (runtime/voice_runtime.c built with RU_VOICE_DBGHELP_CHAIN) takes the
# dbghelp.dll slot and chain-loads EML renamed to eml_dbghelp.dll.
RUNTIME_DLL = "dbghelp.dll"
EML_DLL = "eml_dbghelp.dll"
LEGACY_DLLS = ("winmm.dll", "shroudtopia.dll")
LAYOUT = 2
INDEX_FILES = ("enshrouded.kfc", "enshrouded.kfc_resources")
EXPECTED_PAYLOAD_FILES = 1764
EXPECTED_MAPS = 1118
MIN_BUILD = 1269579
STATE_DIR = ".ru_voice_linux"
WINDOWS_STATE = Path(".ru_voice_backup") / "state.json"
LOG_NAME = "ru_voice_installer.log"
# Read by the runtime DLL built from runtime/voice_runtime.c: Russian voice is on from game start.
AUTOSTART_MARKER = "ru_voice_autostart.txt"
AUTOSTART_TEXT = "Русская озвучка включается при запуске игры. Удалите файл, чтобы включать её только по F3.\n"
PACKAGE_GLOB = "Voices-of-Embervale-RU-*"
OVERRIDES = {"dbghelp": "native,builtin"}
# Per-exe overrides: only enshrouded.exe prefers the DLLs from the game folder.
REG_SECTION = r"[Software\\Wine\\AppDefaults\\enshrouded.exe\\DllOverrides]"
LAUNCH_OPTIONS = 'WINEDLLOVERRIDES="dbghelp=n,b" %command%'
VERSION_RE = re.compile(rb"(?<!\d)(\d{6,10})\|\^/game\d+/branches/release_(\d+)\.(\d+)\|[^\x00\r\n]{1,100}")
REG_VALUE_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"=(.*)$')
# Same function entry signatures as installer/Program.cs and runtime/voice_runtime.c.
HOOK_SIGNATURES = (
    bytes.fromhex("48895C2410488974241848897C2420554154415541564157488D6C24C94881ECC00000008B712833C04D8BE84C8BFA48"),
    bytes.fromhex("48895C2410488974241848897C2420554154415541564157488D6C24C94881ECC0000000448B711033C0498BD84C8BEA"),
    bytes.fromhex("4489442418555741564881EC80000000418BD0498BF9488B"),
)


# How the user re-runs this tool: the curl one-liner sets RU_VOICE_HINT, a local run uses its own path.
COMMAND_HINT = os.environ.get("RU_VOICE_HINT") or f"python3 {Path(sys.argv[0]).resolve()}"


class Fail(Exception):
    """User-facing error; the message is printed without a traceback."""


# --------------------------------------------------------------------------- utils

def say(message: str = "") -> None:
    print(message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log(game: Path, message: str) -> None:
    try:
        path = game / LOG_NAME
        if path.exists() and path.stat().st_size > 2_000_000:
            os.replace(path, game / "ru_voice_installer.previous.log")
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        with path.open("a", encoding="utf-8") as file:
            file.write(f"[{stamp}] [linux] {message.replace(chr(10), ' ')}\n")
    except OSError:
        pass


def gib(size: int) -> str:
    return f"{size / 1024 ** 3:.2f} ГБ"


# ------------------------------------------------------------------- Steam lookup

def steam_roots() -> list[Path]:
    home = Path.home()
    candidates = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
                  home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    roots: list[Path] = []
    for candidate in candidates:
        if (candidate / "steamapps").is_dir():
            real = candidate.resolve()
            if real not in roots:
                roots.append(real)
    return roots


def library_paths() -> list[Path]:
    libraries: list[Path] = []
    for root in steam_roots():
        found = [root]
        for vdf in (root / "steamapps/libraryfolders.vdf", root / "config/libraryfolders.vdf"):
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"((?:[^"\\]|\\.)*)"', text)]
        for path in found:
            if (path / "steamapps").is_dir():
                real = path.resolve()
                if real not in libraries:
                    libraries.append(real)
    return libraries


def find_game(explicit: str | None) -> tuple[Path, Path | None]:
    """Return (game folder, Steam library that contains it)."""
    if explicit:
        game = Path(explicit).expanduser().resolve()
        if game.is_file():
            game = game.parent
        if not (game / "enshrouded.exe").is_file():
            raise Fail(f"В папке {game} нет enshrouded.exe.")
        library = game.parent.parent.parent if game.parent.name == "common" and game.parent.parent.name == "steamapps" else None
        return game, library
    for library in library_paths():
        install_dir = "Enshrouded"
        manifest = library / "steamapps" / f"appmanifest_{APP_ID}.acf"
        if manifest.is_file():
            match = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
            if match:
                install_dir = match.group(1)
        game = library / "steamapps/common" / install_dir
        if (game / "enshrouded.exe").is_file():
            return game, library
    raise Fail("Enshrouded не найден в библиотеках Steam. Укажите папку игры: --game /путь/к/Enshrouded")


def compat_dirs(library: Path | None) -> list[Path]:
    """Proton prefix candidates: the game's library first, then every other library."""
    libraries = ([library] if library else []) + [x for x in library_paths() if x != library]
    return [lib / "steamapps/compatdata" / APP_ID for lib in libraries]


def find_user_reg(library: Path | None) -> Path | None:
    for compat in compat_dirs(library):
        if (compat / "pfx/user.reg").is_file():
            return compat / "pfx/user.reg"
    return None


def vdf_parse(text: str) -> dict:
    token = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')
    stack: list[dict] = [{}]
    key = None
    for match in token.finditer(text):
        if match.group(2) == "{":
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif match.group(2) == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif match.group(1) is not None:
            value = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return stack[0]


def find_launch_options(node) -> str | None:
    if not isinstance(node, dict):
        return None
    for key, value in node.items():
        if key == APP_ID and isinstance(value, dict):
            for name, option in value.items():
                if name.lower() == "launchoptions" and isinstance(option, str):
                    return option
        found = find_launch_options(value)
        if found is not None:
            return found
    return None


def launch_options() -> list[str]:
    result = []
    for root in steam_roots():
        for config in sorted(root.glob("userdata/*/config/localconfig.vdf")):
            try:
                option = find_launch_options(vdf_parse(config.read_text(encoding="utf-8", errors="replace")))
            except (OSError, RecursionError):
                continue
            if option:
                result.append(option)
    return result


def launch_overrides_ok(option: str) -> bool:
    match = re.search(r"""WINEDLLOVERRIDES=(?:"([^"]*)"|'([^']*)'|(\S+))""", option)
    if not match:
        return False
    modes = {}
    for entry in (match.group(1) or match.group(2) or match.group(3) or "").split(";"):
        names, _, mode = entry.partition("=")
        for name in names.split(","):
            name = name.strip().lower()
            modes[name[:-4] if name.endswith(".dll") else name] = mode.strip().lower()
    return all(modes.get(name, "").startswith("n") for name in OVERRIDES)


# -------------------------------------------------------------- process checks

def proc_entries():
    proc = Path("/proc")
    if not proc.is_dir():
        return
    for entry in proc.iterdir():
        if entry.name.isdigit() and int(entry.name) != os.getpid():
            yield entry


def game_running() -> bool:
    for entry in proc_entries():
        try:
            if b"enshrouded.exe" in (entry / "cmdline").read_bytes().lower():
                return True
        except OSError:
            continue
    return False


def prefix_busy() -> bool:
    """A live wineserver for the game prefix would overwrite user.reg on exit."""
    marker = f"compatdata/{APP_ID}".encode()
    for entry in proc_entries():
        try:
            if (entry / "comm").read_text().strip() != "wineserver":
                continue
        except OSError:
            continue
        try:
            if marker in (entry / "environ").read_bytes():
                return True
        except PermissionError:
            return True
        except OSError:
            continue
    return False


def ensure_closed() -> None:
    if game_running():
        raise Fail("Закройте Enshrouded перед изменением файлов.")
    for _ in range(30):
        if not prefix_busy():
            return
        time.sleep(1)
    raise Fail("Proton-окружение игры ещё работает (wineserver). Подождите несколько секунд после выхода из игры и повторите.")


# ------------------------------------------------------------------ game checks

def version_from_index(path: Path) -> str:
    if not path.is_file():
        raise Fail(f"Не найден индекс {path.name}.")
    versions = {re.sub(rb"[\x80-\xff]", b"?", m.group(0)) for m in VERSION_RE.finditer(path.read_bytes())}
    if len(versions) != 1:
        raise Fail("Не удалось однозначно определить версию игрового индекса.")
    match = VERSION_RE.match(versions.pop())
    build, major = int(match.group(1)), int(match.group(2))
    if major < 1 or build < MIN_BUILD:
        raise Fail(f"Сборка игры {build} старше минимальной {MIN_BUILD}.")
    return match.group(0).decode("ascii", "replace")


def check_signatures(exe: Path) -> None:
    data = exe.read_bytes()
    if len(data) < 0x100 or data[:2] != b"MZ":
        raise Fail("enshrouded.exe не является ожидаемым PE-файлом.")
    pe = struct.unpack_from("<i", data, 0x3C)[0]
    if pe < 0 or pe > len(data) - 24 or data[pe:pe + 4] != b"PE\0\0":
        raise Fail("Неверный заголовок enshrouded.exe.")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional = struct.unpack_from("<H", data, pe + 20)[0]
    sections = pe + 24 + optional
    if count <= 0 or count > 96 or sections > len(data) - count * 40:
        raise Fail("Неверная таблица секций enshrouded.exe.")
    for i in range(count):
        header = sections + i * 40
        if data[header:header + 5] != b".text":
            continue
        length, offset = struct.unpack_from("<II", data, header + 16)
        if offset > len(data) or length > len(data) - offset:
            raise Fail("Секция кода enshrouded.exe повреждена.")
        end = offset + length
        for number, signature in enumerate(HOOK_SIGNATURES, 1):
            first = data.find(signature, offset, end)
            if first < 0 or data.find(signature, first + 1, end) >= 0:
                raise Fail(f"Аудиофункция {number} в enshrouded.exe изменилась или неоднозначна. "
                           "Эта сборка игры пока не поддерживается модом; установка остановлена.")
        return
    raise Fail("Секция кода enshrouded.exe не найдена.")


def check_game(game: Path) -> str:
    for name in ("enshrouded.exe",) + INDEX_FILES:
        if not (game / name).is_file():
            raise Fail(f"В папке игры нет {name}.")
    with (game / "enshrouded.kfc").open("rb") as file:
        if file.read(4) != b"KFC4":
            raise Fail("Неизвестный формат игрового индекса enshrouded.kfc.")
    version = version_from_index(game / "enshrouded.kfc")
    check_signatures(game / "enshrouded.exe")
    log(game, "Game version: " + version)
    log(game, "Three unique runtime hook signatures confirmed")
    return version


def dat_lengths(game: Path) -> dict[str, int]:
    return {path.name: path.stat().st_size for path in sorted(game.glob("enshrouded_*.dat"))}


# ---------------------------------------------------------------------- package

def check_rel(rel: str) -> None:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\\" in rel or ":" in rel or ".." in parts or "" in parts:
        raise Fail("Неверный путь в пакете: " + rel)


class Package:
    def __init__(self, source: Path):
        self.source = source
        self.zip = None
        self.root = None
        if source.is_file():
            if not zipfile.is_zipfile(source):
                raise Fail(f"{source} не является ZIP-архивом.")
            self.zip = zipfile.ZipFile(source)
            names = [n for n in self.zip.namelist() if n.endswith("payload/manifest.json")]
            if not names:
                raise Fail("В архиве нет payload/manifest.json. Нужен ПОЛНЫЙ архив мода, а не hotfix.")
            self.prefix = min(names, key=len)[:-len("manifest.json")]
        else:
            for candidate in [source, source / "payload", *sorted(source.glob("*/payload"))]:
                if (candidate / "manifest.json").is_file() and (candidate / "mod").is_dir():
                    self.root = candidate
                    break
            else:
                raise Fail(f"В {source} не найдена папка payload с manifest.json.")

    def open(self, rel: str):
        check_rel(rel)
        try:
            return self.zip.open(self.prefix + rel) if self.zip else (self.root / rel).open("rb")
        except (KeyError, FileNotFoundError):
            raise Fail("Пакет неполный, нет файла: " + rel) from None

    def size(self, rel: str) -> int:
        return self.zip.getinfo(self.prefix + rel).file_size if self.zip else (self.root / rel).stat().st_size

    def manifest(self) -> dict[str, str]:
        with self.open("manifest.json") as file:
            data = json.load(file)
        files = data.get("files") or data.get("Files")
        if not isinstance(files, dict) or not files:
            raise Fail("Манифест пакета пуст.")
        for rel in files:
            check_rel(rel)
        required = ["mod/mod.json", "mod/src/mod.lua"] + ["native/" + dll for dll in DLLS]
        missing = [rel for rel in required if rel not in files]
        if missing:
            raise Fail("Пакет озвучки неполный: " + ", ".join(missing))
        if len(files) != EXPECTED_PAYLOAD_FILES:
            raise Fail(f"Пакет озвучки неполный: {len(files)} файлов вместо {EXPECTED_PAYLOAD_FILES}. "
                       "Этот скрипт рассчитан на полный архив 0.3.3 beta.")
        return files


def find_package(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise Fail(f"Не найден {path}")
        return path
    home = Path.home()
    folders = [Path.cwd(), home / "Downloads", home / "Загрузки", home / "Desktop", home / "Рабочий стол", home]
    found = []
    for folder in folders:
        if folder.is_dir():
            found += [p for p in folder.glob(PACKAGE_GLOB) if p.suffix.lower() == ".zip" or p.is_dir()]
    if not found:
        raise Fail("Не найден архив Voices-of-Embervale-RU-*.zip. Скачайте ПОЛНЫЙ архив с Nexus Mods в ~/Downloads "
                   f"или передайте путь: {COMMAND_HINT} install /путь/к/архиву.zip")
    return max(found, key=lambda p: p.stat().st_mtime)


def extract_verified(package: Package, files: dict[str, str], staging: Path) -> None:
    """Stream every payload file once: verify SHA-256, keep mod/ and native/ in staging."""
    total = len(files)
    for index, (rel, expected) in enumerate(sorted(files.items()), 1):
        keep = rel.startswith(("mod/", "native/"))
        target = staging / rel
        digest = hashlib.sha256()
        if keep:
            target.parent.mkdir(parents=True, exist_ok=True)
        with package.open(rel) as source, (target.open("wb") if keep else open(os.devnull, "wb")) as out:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected.lower():
            raise Fail("Пакет повреждён: " + rel)
        if keep:
            os.chmod(target, 0o755 if rel.endswith(".dll") else 0o644)
        if index % 100 == 0 or index == total:
            print(f"\r  Проверено файлов пакета: {index}/{total}", end="", flush=True)
    say()


# ------------------------------------------------------------- Wine registry

def reg_read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="surrogateescape").split("\n")


def reg_write(path: Path, lines: list[str]) -> None:
    temp = path.with_name(path.name + ".ru_voice_tmp")
    temp.write_text("\n".join(lines), encoding="utf-8", errors="surrogateescape")
    shutil.copymode(path, temp)
    os.replace(temp, path)


def reg_section(lines: list[str]) -> tuple[int, int] | None:
    header = REG_SECTION.lower()
    for start, line in enumerate(lines):
        lowered = line.lower()
        if lowered == header or lowered.startswith(header + " "):
            end = start + 1
            while end < len(lines) and not lines[end].startswith("["):
                end += 1
            return start, end
    return None


def reg_find(lines: list[str], bounds: tuple[int, int], name: str) -> int | None:
    for index in range(bounds[0] + 1, bounds[1]):
        match = REG_VALUE_RE.match(lines[index])
        if match and match.group(1).lower() == name:
            return index
    return None


def reg_values(path: Path) -> dict[str, str | None]:
    lines = reg_read(path)
    bounds = reg_section(lines)
    result = {}
    for name in OVERRIDES:
        index = reg_find(lines, bounds, name) if bounds else None
        result[name] = lines[index].split("=", 1)[1] if index is not None else None
    return result


def reg_apply(path: Path) -> dict[str, str | None]:
    """Add per-exe overrides; return previous raw values (None = absent) for exact revert."""
    lines = reg_read(path)
    previous: dict[str, str | None] = {}
    bounds = reg_section(lines)
    if bounds is None:
        now = int(time.time())
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", f"{REG_SECTION} {now}", f"#time={(now + 11644473600) * 10 ** 7:x}"]
        lines += [f'"{name}"="{value}"' for name, value in OVERRIDES.items()] + [""]
        previous = {name: None for name in OVERRIDES}
    else:
        start, end = bounds
        for name, value in OVERRIDES.items():
            index = reg_find(lines, (start, end), name)
            if index is not None:
                previous[name] = lines[index].split("=", 1)[1]
                lines[index] = f'"{name}"="{value}"'
            else:
                previous[name] = None
                insert = end
                while insert > start + 1 and lines[insert - 1] == "":
                    insert -= 1
                lines.insert(insert, f'"{name}"="{value}"')
                end += 1
    reg_write(path, lines)
    return previous


def reg_revert(path: Path, previous: dict[str, str | None]) -> None:
    lines = reg_read(path)
    bounds = reg_section(lines)
    if bounds is None:
        return
    for name, value in previous.items():
        index = reg_find(lines, bounds, name)
        if index is None:
            continue
        if value is None:
            del lines[index]
            bounds = (bounds[0], bounds[1] - 1)
        else:
            lines[index] = f'"{name}"={value}'
    start, end = bounds
    if not any(REG_VALUE_RE.match(line) for line in lines[start + 1:end]):
        del lines[start:end]
    reg_write(path, lines)


def apply_overrides(game: Path, library: Path | None, state_dir: Path) -> dict | None:
    user_reg = find_user_reg(library)
    if user_reg is None:
        return None
    backup = state_dir / "user.reg.before"
    if not backup.exists():
        shutil.copy2(user_reg, backup)
    previous = reg_apply(user_reg)
    if any(value is None or value.strip('"') != OVERRIDES[name] for name, value in reg_values(user_reg).items()):
        raise Fail("Не удалось записать DLL overrides в реестр Proton.")
    log(game, f"Wine DLL overrides written to {user_reg}")
    return {"user_reg": str(user_reg), "previous": previous}


def overrides_status(library: Path | None) -> tuple[bool, str]:
    user_reg = find_user_reg(library)
    if user_reg is not None:
        values = reg_values(user_reg)
        if all(values[name] and values[name].strip('"').startswith("n") for name in OVERRIDES):
            return True, f"реестр Proton ({user_reg})"
    for option in launch_options():
        if launch_overrides_ok(option):
            return True, "параметры запуска Steam"
    if user_reg is None:
        return False, "префикс Proton ещё не создан (игра ни разу не запускалась)"
    return False, "не заданы"


# ------------------------------------------------------------------- commands

def state_path(game: Path) -> Path:
    return game / STATE_DIR / "state.json"


def read_state(game: Path) -> dict | None:
    path = state_path(game)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def save_state(game: Path, state: dict) -> None:
    path = state_path(game)
    temp = path.with_name("state.json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def windows_install_active(game: Path) -> bool:
    try:
        return json.loads((game / WINDOWS_STATE).read_text(encoding="utf-8-sig")).get("Status") == "installed"
    except (OSError, ValueError, AttributeError):
        return False


def find_runtime_dll(explicit: str | None) -> Path:
    here = Path(sys.argv[0]).resolve().parent
    for candidate in ([Path(explicit).expanduser()] if explicit else [here / RUNTIME_DLL, here / "bin" / RUNTIME_DLL]):
        if candidate.is_file():
            return candidate.resolve()
    raise Fail("Не найдена runtime-DLL (dbghelp.dll из linux/bin). Установите мод однострочником из README_STEAMOS.md "
               "или передайте --runtime-dll /путь/к/dbghelp.dll")


def migrate_layout(game: Path, library: Path | None, state: dict, runtime_dll: Path) -> None:
    """Switch an install made through winmm.dll/shroudtopia.dll to the dbghelp.dll chain in place.

    Mod files and the EML-patched index stay as they are, so nothing is downloaded or re-patched.
    """
    say("Обновляю установку: русская озвучка теперь загружается через dbghelp.dll вместо winmm.dll…")
    owned = state.get("owned_dlls", {})
    eml_hash = owned.get("dbghelp.dll")
    current, eml = game / RUNTIME_DLL, game / EML_DLL
    if not eml_hash or not current.is_file() or sha256(current) != eml_hash:
        raise Fail("dbghelp.dll изменён после установки. Выполните uninstall, затем install.")
    if eml.exists() and sha256(eml) != eml_hash:
        raise Fail(f"Конфликт: в папке игры уже есть другой {EML_DLL}.")
    runtime_hash = sha256(runtime_dll)
    os.replace(current, eml)
    try:
        shutil.copyfile(runtime_dll, current)
        if sha256(current) != runtime_hash:
            raise Fail("Не удалось скопировать runtime-DLL.")
    except BaseException:
        os.replace(eml, current)
        raise
    for dll in LEGACY_DLLS:
        target = game / dll
        if dll in owned and target.is_file():
            if sha256(target) == owned[dll]:
                target.unlink()
            else:
                say(f"  {dll} изменён после установки — оставлен на месте.")
    overrides = state.get("overrides")
    if overrides:
        legacy = {name: value for name, value in overrides["previous"].items() if name not in OVERRIDES}
        if legacy and Path(overrides["user_reg"]).is_file():
            reg_revert(Path(overrides["user_reg"]), legacy)
        overrides["previous"] = {name: value for name, value in overrides["previous"].items() if name in OVERRIDES}
    else:
        state["overrides"] = apply_overrides(game, library, game / STATE_DIR)
    if not (game / AUTOSTART_MARKER).is_file():
        (game / AUTOSTART_MARKER).write_text(AUTOSTART_TEXT, encoding="utf-8")
    state["owned_dlls"] = {RUNTIME_DLL: runtime_hash, EML_DLL: eml_hash}
    state["autostart"] = True
    state["layout"] = LAYOUT
    state["layout_at"] = time.time()
    save_state(game, state)
    log(game, "OK linux layout migration to dbghelp chain")
    say("Готово. Запустите игру: русская озвучка включится сама. Потом проверьте:  " + COMMAND_HINT + " status")


def cmd_install(args) -> None:
    game, library = find_game(args.game)
    say(f"Папка игры: {game}")
    log(game, "START linux install")
    if os.geteuid() == 0:
        raise Fail("Не запускайте установщик через sudo: файлы игры принадлежат обычному пользователю.")
    ensure_closed()
    version = check_game(game)
    say(f"Версия игры: {version.split('|')[0]} — поддерживается, аудиофункции найдены однозначно.")
    runtime_dll = find_runtime_dll(args.runtime_dll)
    state = read_state(game)
    if state and state.get("status") == "installed":
        if state.get("layout", 1) < LAYOUT:
            migrate_layout(game, library, state, runtime_dll)
            return
        raise Fail("Мод уже установлен этим скриптом. Проверка: status. Переустановка: uninstall, затем install.")
    mods = game / "mods"
    if (mods / LEGACY_MOD_ID).exists():
        raise Fail(f"Найдена старая версия mods/{LEGACY_MOD_ID}. Удалите её перед установкой.")
    if (mods / MOD_ID).exists():
        if windows_install_active(game):
            raise Fail("Мод уже установлен Windows-установщиком (.ru_voice_backup). Удалите его им или вручную по docs/HOTFIX_0.3.3_RU.md.")
        raise Fail(f"Папка mods/{MOD_ID} уже существует. Перенесите её за пределы mods и повторите.")
    eml_backup = game / "enshrouded.kfc.bak"
    if eml_backup.is_file() and sha256(eml_backup) != sha256(game / "enshrouded.kfc"):
        if version_from_index(eml_backup) == version:
            raise Fail("Индекс игры уже изменён другим модом EML. Установка остановлена, чтобы не испортить резервную копию.")

    source = find_package(args.source)
    say(f"Пакет мода: {source}")
    package = Package(source)
    files = package.manifest()
    dll_hashes = {RUNTIME_DLL: sha256(runtime_dll), EML_DLL: files["native/dbghelp.dll"].lower()}
    for dll, digest in dll_hashes.items():
        target = game / dll
        if target.exists() and sha256(target) != digest:
            raise Fail(f"Конфликт: в папке игры уже есть другой {dll} (другой мод или версия EML). Установка остановлена.")
    payload_size = sum(package.size(rel) for rel in files if rel.startswith(("mod/", "native/")))
    index_size = sum((game / name).stat().st_size for name in INDEX_FILES)
    need = payload_size * 2 + index_size + 300 * 1024 ** 2
    free = shutil.disk_usage(game).free
    if free < need:
        raise Fail(f"Мало места на диске: свободно {gib(free)}, нужно около {gib(need)}.")

    state_dir = game / STATE_DIR
    backup = state_dir / "backup"
    staging = state_dir / "staging"
    created_state_dir = not state_dir.exists()
    previous_state = state_path(game).read_text(encoding="utf-8") if state_path(game).is_file() else None
    backup.mkdir(parents=True, exist_ok=True)
    state = {
        "status": "installing", "tool": "ru_voice_steamos.py", "game_version": version,
        "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "original": {}, "dat_lengths_before": dat_lengths(game), "owned_dlls": {},
        "mod_files": sum(1 for rel in files if rel.startswith("mod/")), "overrides": None,
        "preexisting": {name: (game / name).exists() for name in ("enshrouded.kfc.bak", ".cache", "eml.json", "mods", AUTOSTART_MARKER)},
    }
    mod_created = False
    try:
        say("Резервная копия игрового индекса…")
        for name in INDEX_FILES:
            original = sha256(game / name)
            shutil.copy2(game / name, backup / name)
            if sha256(backup / name) != original:
                raise Fail("Резервная копия не прошла проверку: " + name)
            state["original"][name] = original
        save_state(game, state)

        say("Проверка SHA-256 и распаковка пакета…")
        if staging.exists():
            shutil.rmtree(staging)
        extract_verified(package, files, staging)
        manifest_id = json.loads((staging / "mod/mod.json").read_text(encoding="utf-8")).get("id")
        if manifest_id != MOD_ID:
            raise Fail(f"Неожиданный id мода в пакете: {manifest_id}")

        sources = {RUNTIME_DLL: staging / "runtime.dll", EML_DLL: staging / "native/dbghelp.dll"}
        shutil.copyfile(runtime_dll, sources[RUNTIME_DLL])
        if sha256(sources[RUNTIME_DLL]) != dll_hashes[RUNTIME_DLL]:
            raise Fail("Не удалось скопировать runtime-DLL.")

        mods.mkdir(exist_ok=True)
        os.replace(staging / "mod", mods / MOD_ID)
        mod_created = True
        for dll, source in sources.items():
            if not (game / dll).exists():
                os.replace(source, game / dll)
                state["owned_dlls"][dll] = dll_hashes[dll]
        (game / AUTOSTART_MARKER).write_text(AUTOSTART_TEXT, encoding="utf-8")
        state["autostart"] = True
        state["layout"] = LAYOUT
        state["layout_at"] = time.time()
        save_state(game, state)

        state["overrides"] = apply_overrides(game, library, state_dir)
        state["status"] = "installed"
        save_state(game, state)
        log(game, "OK linux install")
    except BaseException as error:
        log(game, f"FAIL linux install: {error!r}; rolling back")
        if mod_created and (mods / MOD_ID).is_dir() and not (mods / MOD_ID).is_symlink():
            shutil.rmtree(mods / MOD_ID)
        for dll, digest in state["owned_dlls"].items():
            if (game / dll).is_file() and sha256(game / dll) == digest:
                (game / dll).unlink()
        if state.get("overrides"):
            reg_revert(Path(state["overrides"]["user_reg"]), state["overrides"]["previous"])
        if not state["preexisting"][AUTOSTART_MARKER] and (game / AUTOSTART_MARKER).is_file():
            (game / AUTOSTART_MARKER).unlink()
        shutil.rmtree(staging, ignore_errors=True)
        if created_state_dir:
            shutil.rmtree(state_dir, ignore_errors=True)
        elif previous_state is not None:
            state_path(game).write_text(previous_state, encoding="utf-8")
        else:
            state_path(game).unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    say()
    say("Готово: файлы русской озвучки установлены (525 страниц дневника и 593 реплики NPC).")
    if state["overrides"] is None:
        say()
        say("ВНИМАНИЕ: префикс Proton для Enshrouded ещё не создан, поэтому DLL overrides не записаны.")
        say("Сделайте одно из двух:")
        say(f"  а) запустите игру один раз, выйдите и выполните:  {COMMAND_HINT} overrides")
        say(f"  б) или Steam → Enshrouded → Свойства → Параметры запуска:  {LAUNCH_OPTIONS}")
    say()
    say("Дальше:")
    say("  1. Запустите Enshrouded из Steam (можно в игровом режиме). Первый запуск дольше обычного:")
    say(f"     загрузчик EML один раз встраивает {EXPECTED_MAPS} записей в индекс игры (до нескольких минут).")
    if state.get("autostart"):
        say("  2. Русская озвучка включается сама при каждом запуске игры — на геймпаде ничего нажимать не нужно.")
        say("     (F3 на клавиатуре временно выключает и снова включает её.)")
    else:
        say("  2. Если язык игры русский, озвучка включается автоматически. При другом языке её переключает F3;")
        say("     на геймпаде назначьте F3 на кнопку: Steam → Enshrouded → Раскладка контроллера.")
    say(f"  3. После первого запуска выйдите из игры и проверьте:  {COMMAND_HINT} status")


def eml_summary(game: Path) -> dict | None:
    logs = sorted((game / "logs").glob("*eml.log"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return None
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [i for i, line in enumerate(lines) if "Running lua with options" in line]
    run = lines[starts[-1]:] if starts else lines
    errors = [line for line in run if '"level":"ERROR"' in line or "Error running mod loader" in line]
    version = next((line for line in run if "RU_VOICE_GAME_VERSION" in line), None)
    return {"file": logs[-1], "maps": sum("RU_VOICE_MAP " in line for line in run),
            "errors": errors, "version": version is not None}


def cmd_status(args) -> None:
    game, library = find_game(args.game)
    say(f"Папка игры: {game}")
    problems: list[str] = []
    try:
        version = check_game(game)
        say(f"Сборка игры: {version.split('|')[0]} — аудиофункции совместимы.")
    except Fail as error:
        problems.append(str(error))
        say(f"Сборка игры: {error}")
    state = read_state(game)
    mod = game / "mods" / MOD_ID
    if state is None:
        say("Установка этим скриптом: не найдена." + (" (Есть установка Windows-установщика.)" if windows_install_active(game) else ""))
    else:
        say(f"Установка этим скриптом: {state.get('status')} ({state.get('installed_at')}).")
    if mod.is_dir():
        count = sum(1 for p in mod.rglob("*") if p.is_file())
        expected = state.get("mod_files") if state else None
        ok = expected is None or count == expected
        say(f"Файлы мода: {count}" + (f" из {expected}" if expected else "") + (" — OK" if ok else " — НЕ ХВАТАЕТ"))
        if not ok:
            problems.append("Файлы мода повреждены или неполные. Переустановите мод.")
    else:
        say("Файлы мода: нет")
        problems.append("Мод не установлен.")
    if state and state.get("status") == "installed" and state.get("layout", 1) < LAYOUT:
        problems.append(f"Установка старой версии (через winmm.dll, Proton её не загружает). Обновите: {COMMAND_HINT} install")
    legacy = bool(state) and state.get("status") == "installed" and state.get("layout", 1) < LAYOUT
    loaders = (RUNTIME_DLL,) if legacy else (RUNTIME_DLL, EML_DLL)
    for dll in loaders:
        if not (game / dll).is_file():
            problems.append(f"Нет {dll} в папке игры.")
    say("DLL загрузчиков: " + ("OK" if all((game / dll).is_file() for dll in loaders) else "НЕ ВСЕ"))
    say("Автовключение озвучки при запуске: " + ("да" if (game / AUTOSTART_MARKER).is_file() else "нет (переключение по F3)"))
    ok, where = overrides_status(library)
    say(f"DLL overrides для Proton: {'OK, ' if ok else 'НЕТ — '}{where}")
    if not ok:
        problems.append(f"Без DLL overrides Proton не загрузит мод. Выполните: {COMMAND_HINT} overrides "
                        f"(после первого запуска игры) или задайте параметры запуска: {LAUNCH_OPTIONS}")

    summary = eml_summary(game)
    if summary is None:
        say("Журнал EML: нет (игра ещё не запускалась с модом или dbghelp.dll не загрузился).")
        if ok and mod.is_dir():
            problems.append("Запустите игру один раз и снова выполните status.")
    else:
        say(f"Журнал EML ({summary['file'].name}): подключено записей {summary['maps']} из {EXPECTED_MAPS}"
            + (f", ошибок {len(summary['errors'])}" if summary["errors"] else ""))
        for line in summary["errors"][-3:]:
            say("  " + line[:400])
        if summary["maps"] != EXPECTED_MAPS:
            problems.append("EML подключил не все записи. Приложите logs/*eml.log автору мода.")
    runtime = game / "ru_voice_runtime.log"
    if runtime.is_file():
        tail = runtime.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        say("ru_voice_runtime.log (последние строки):")
        for line in tail:
            say("  " + line)
        last_start = next((line for line in reversed(tail) if "hooks active" in line or "signature missing" in line
                           or "installation failed" in line or "initialization failed" in line), "")
        if "hooks active" not in last_start:
            problems.append("Хуки озвучки не установились: см. ru_voice_runtime.log.")
        elif (game / AUTOSTART_MARKER).is_file() and not any("ON at startup" in line for line in tail):
            problems.append("Автовключение не сработало: установлена DLL без поддержки ru_voice_autostart.txt, нужна F3.")
    else:
        say("ru_voice_runtime.log: нет (runtime-DLL ещё не загружалась).")
        launched_since = summary is not None and summary["file"].stat().st_mtime > (state or {}).get("layout_at", 0)
        if state and state.get("layout", 1) >= LAYOUT and not launched_since:
            problems.append("Запустите игру один раз и снова выполните status.")
        elif summary is not None and not legacy:
            problems.append("EML работает, но runtime озвучки не загрузился. Пришлите этот вывод status.")
    say()
    if problems:
        say("Нужно внимание:")
        for problem in dict.fromkeys(problems):
            say("  • " + problem)
    else:
        say("Всё в порядке: русская озвучка подключена.")


def cmd_overrides(args) -> None:
    game, library = find_game(args.game)
    ensure_closed()
    state = read_state(game)
    state_dir = game / STATE_DIR
    state_dir.mkdir(exist_ok=True)
    result = apply_overrides(game, library, state_dir)
    if result is None:
        raise Fail("Префикс Proton не найден. Запустите игру один раз, выйдите и повторите, "
                   f"либо задайте параметры запуска: {LAUNCH_OPTIONS}")
    if state is not None and state.get("overrides") is None:
        state["overrides"] = result
        save_state(game, state)
    say(f"DLL overrides записаны: {result['user_reg']}")


def cmd_uninstall(args) -> None:
    game, library = find_game(args.game)
    say(f"Папка игры: {game}")
    log(game, "START linux uninstall")
    ensure_closed()
    state = read_state(game)
    if not state or state.get("status") != "installed":
        raise Fail("Установка этим скриптом не найдена. Ручное удаление описано в docs/HOTFIX_0.3.3_RU.md.")
    state_dir = game / STATE_DIR
    backup = state_dir / "backup"
    try:
        current_version = version_from_index(game / "enshrouded.kfc")
    except Fail:
        current_version = None
    same_build = current_version is not None and current_version == state["game_version"]
    for name in INDEX_FILES:
        if same_build and sha256(backup / name) != state["original"][name]:
            raise Fail("Резервная копия повреждена: " + name)

    mod = game / "mods" / MOD_ID
    if mod.is_symlink():
        raise Fail(f"mods/{MOD_ID} является ссылкой; удалите её вручную.")
    if mod.is_dir():
        shutil.rmtree(mod)
    if not state.get("preexisting", {}).get("mods", True) and mod.parent.is_dir() and not any(mod.parent.iterdir()):
        mod.parent.rmdir()
    for dll, digest in state.get("owned_dlls", {}).items():
        target = game / dll
        if target.is_file():
            if sha256(target) == digest:
                target.unlink()
            else:
                say(f"  {dll} изменён после установки — оставлен на месте.")
    if not state.get("preexisting", {}).get(AUTOSTART_MARKER) and (game / AUTOSTART_MARKER).is_file():
        (game / AUTOSTART_MARKER).unlink()
    if state.get("overrides"):
        user_reg = Path(state["overrides"]["user_reg"])
        if user_reg.is_file():
            reg_revert(user_reg, state["overrides"]["previous"])

    if same_build:
        for name in INDEX_FILES:
            if sha256(game / name) != state["original"][name]:
                shutil.copy2(backup / name, game / name)
        before = state.get("dat_lengths_before", {})
        for name, size in dat_lengths(game).items():
            if name not in before:
                (game / name).unlink()
            elif before[name] == 0 and size != 0:
                os.truncate(game / name, 0)
            elif before[name] != size:
                say(f"  {name} изменён не этим модом — оставлен на месте.")
        pre = state.get("preexisting", {})
        bak = game / "enshrouded.kfc.bak"
        if not pre.get("enshrouded.kfc.bak") and bak.is_file() and sha256(bak) == state["original"]["enshrouded.kfc"]:
            bak.unlink()
        if not pre.get(".cache") and (game / ".cache").is_dir() and not (game / ".cache").is_symlink():
            shutil.rmtree(game / ".cache")
        if not pre.get("eml.json") and (game / "eml.json").is_file():
            (game / "eml.json").unlink()
        say("Игровой индекс восстановлен из резервной копии.")
    else:
        say("Игра обновилась после установки мода: старую копию индекса НЕ восстанавливаю.")
        say("Сделайте: Steam → Enshrouded → Свойства → Установленные файлы → Проверить целостность файлов.")

    if args.keep_backup:
        state["status"] = "removed"
        save_state(game, state)
    else:
        shutil.rmtree(state_dir)
    log(game, "OK linux uninstall")
    say("Русская озвучка удалена.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Голоса Эмбервейла: русская озвучка Enshrouded для SteamOS/Linux")
    parser.add_argument("--game", help="папка Enshrouded (по умолчанию ищется в библиотеках Steam)")
    sub = parser.add_subparsers(dest="command")
    install = sub.add_parser("install", help="установить мод (по умолчанию)")
    install.add_argument("source", nargs="?", help="ZIP-архив мода или распакованная папка")
    install.add_argument("--runtime-dll", help="runtime озвучки dbghelp.dll (по умолчанию рядом со скриптом или в bin/)")
    sub.add_parser("status", help="проверить установку и журналы после запуска игры")
    uninstall = sub.add_parser("uninstall", help="удалить мод и вернуть игровой индекс")
    uninstall.add_argument("--keep-backup", action="store_true", help="оставить резервную копию индекса")
    sub.add_parser("overrides", help="записать DLL overrides в префикс Proton")
    for child in sub.choices.values():
        child.add_argument("--game", dest="game_sub", help="папка Enshrouded")
    args = parser.parse_args()
    args.game = getattr(args, "game_sub", None) or args.game
    command = args.command or "install"
    if command == "install" and not hasattr(args, "source"):
        args.source = None
        args.runtime_dll = None
    try:
        {"install": cmd_install, "status": cmd_status, "uninstall": cmd_uninstall, "overrides": cmd_overrides}[command](args)
    except Fail as error:
        say()
        say(f"ОШИБКА: {error}")
        return 1
    except KeyboardInterrupt:
        say("\nПрервано.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
