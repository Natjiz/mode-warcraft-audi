"""Build installable WoW addon archives using only the Python standard library."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STAGES = {"detail", "progress", "complete"}
LOCALES = {"ruRU", "enUS", "enGB", "deDE", "frFR", "esES", "esMX", "itIT", "ptBR", "koKR", "zhCN", "zhTW"}


def lua_string(value: str) -> str:
    # Lua 5.1 does not understand JSON's \u escapes.
    return '"' + "".join(
        "\\\\" if char == "\\" else '\\"' if char == '"'
        else f"\\{ord(char):03d}" if ord(char) < 32 else char
        for char in value
    ) + '"'


def build_addon(output: Path, with_demo: bool = False) -> None:
    if with_demo:
        demo = ROOT / "addon" / "WarcraftAudi" / "Audio" / "demo.ogg"
        if not demo.is_file() or not demo.read_bytes().startswith(b"OggS"):
            raise ValueError("Cannot build an audible demo: generate addon/WarcraftAudi/Audio/demo.ogg first.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "addon" / "WarcraftAudi").rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(ROOT / "addon")))


def build_pack(source: Path, audio_root: Path, name: str, output: Path) -> int:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise ValueError("Pack name must start with a Latin letter and contain only letters, digits or underscores (max 64).")
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("quests"), list):
        raise ValueError("Expected schema_version: 1 and a quests array.")
    audio_root = audio_root.resolve(strict=True)
    if not audio_root.is_dir():
        raise ValueError("Audio root must be a directory.")
    addon = "WarcraftAudi_" + name
    entries, files, seen = [], {}, set()
    for row in document["quests"]:
        if not isinstance(row, dict):
            raise ValueError("Every quest entry must be an object.")
        quest_id, stage, locale = row.get("quest_id"), row.get("stage"), row.get("locale")
        if type(quest_id) is not int or quest_id <= 0 or not isinstance(stage, str) or stage not in STAGES or not isinstance(locale, str) or locale not in LOCALES:
            raise ValueError("Invalid quest_id, stage or locale.")
        gender = row.get("gender")
        if gender is not None and gender not in ("male", "female"):
            raise ValueError("gender must be male, female or omitted.")
        key = (locale, quest_id, stage, gender)
        if key in seen:
            raise ValueError(f"Duplicate quest stage: {key}")
        seen.add(key)
        audio = row.get("audio", [])
        if not isinstance(audio, list):
            raise ValueError(f"audio must be an array: {key}")
        if not audio:
            continue  # Exports can contain quests that have not been recorded yet.
        paths = []
        for item in audio:
            if not isinstance(item, str) or not item or "\\" in item:
                raise ValueError("Audio paths must use relative forward-slash paths.")
            relative = PurePosixPath(item)
            if relative.is_absolute() or any(part in {"..", "."} for part in item.split("/")):
                raise ValueError(f"Unsafe audio path: {item}")
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", item) or relative.suffix.lower() not in {".ogg", ".mp3"}:
                raise ValueError(f"Use ASCII .ogg or .mp3 paths: {item}")
            path = (audio_root / item).resolve(strict=True)
            if not path.is_relative_to(audio_root) or not path.is_file():
                raise ValueError(f"Audio file is outside audio root or is not a file: {item}")
            with path.open("rb") as handle:
                header = handle.read(4)
            is_ogg = header.startswith(b"OggS")
            is_mp3 = header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224)
            if not (is_ogg if relative.suffix.lower() == ".ogg" else is_mp3):
                raise ValueError(f"File does not have an expected audio header: {item}")
            archive_path = f"{addon}/Audio/{relative.as_posix()}"
            files[archive_path] = path
            paths.append(lua_string("Interface\\AddOns\\" + archive_path.replace("/", "\\")))
        gender_field = ", gender = " + lua_string(gender) if gender else ""
        entries.append("    { questID = %d, locale = %s, stage = %s%s, files = { %s } }," % (
            quest_id, lua_string(locale), lua_string(stage), gender_field, ", ".join(paths)))
    if not entries:
        raise ValueError("No recorded stages: add at least one existing audio file to an audio array.")
    manifest = (
        "local ok, reason = WarcraftAudi:RegisterPack(" + lua_string(name) + ", {\n"
        + "\n".join(entries) + "\n})\n"
        + 'if not ok then WarcraftAudi:Notify("Voice pack rejected: " .. tostring(reason)) end\n'
    )
    toc = (
        f"## Interface: 100207\n## Title: Warcraft Audi: {name}\n"
        "## Version: 0.2.0\n## Dependencies: WarcraftAudi\nVoices.lua\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{addon}/{addon}.toc", toc)
        archive.writestr(f"{addon}/Voices.lua", manifest)
        for target, path in sorted(files.items()):
            archive.write(path, target)
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("addon", help="Build the base addon ZIP")
    base.add_argument("--output", type=Path, default=ROOT / "dist" / "WarcraftAudi.zip")
    base.add_argument("--with-demo", action="store_true", help="Require a generated demo recording in the archive")
    pack = sub.add_parser("pack", help="Build a voice pack from exported quest JSON and recordings")
    pack.add_argument("source", type=Path)
    pack.add_argument("--audio-root", type=Path, required=True)
    pack.add_argument("--name", required=True)
    pack.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "addon":
            build_addon(args.output, with_demo=args.with_demo)
            print(f"Built addon: {args.output}")
        else:
            count = build_pack(args.source, args.audio_root, args.name, args.output)
            print(f"Built voice pack with {count} recorded quest stages: {args.output}")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
