"""Package the retained Akunda recordings and addon together; no model required."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from generate_voices import PROFILES, plan_quests, sha256
from package_addon import ROOT, build_addon, build_pack

PACK = "WarcraftAudi_Akunda_ruRU"
ATTRIBUTION = """Warcraft Audi — Akunda's Blessing (quest 50913)
Russian quest text: World of Warcraft / Blizzard Entertainment.
Text reference: https://www.wowhead.com/ru/quest=50913&xml
Quest identity reference: WoW-Pro Guides, BFA_Voldun.lua, quest 50913.
Speech generated locally using Silero Models v5_ru, eugene voice.
Silero Team: https://github.com/snakers4/silero-models
Model: https://models.silero.ai/models/tts/ru/v5_ru.pt
Model license: CC BY-NC-SA 4.0
https://github.com/snakers4/silero-models/blob/master/LICENSE
Processing: SSML slow/low delivery, mono OGG Vorbis, loudness normalization.
This is a synthetic audition voice, not an original Blizzard voice recording.
The player's name is omitted; the description selects male/female grammar.
"""
INSTALL = """Warcraft Audi 0.2.0 — Благословение Акунды

1. Полностью закройте WoW.
2. Распакуйте обе папки WarcraftAudi и WarcraftAudi_Akunda_ruRU
   в Interface/AddOns используемого клиента (иногда внутри _retail_).
3. Запустите WoW 10.2.7 и включите оба аддона в списке модификаций.
4. В настройках звука включите звук и канал «Диалоги».
5. Введите в чат /wca demo: зазвучит описание квеста (мужское обращение).
6. Откройте «Благословение Акунды» (50913) у Акунды в Вол'дуне:
   запись нужного этапа включится автоматически, с учётом пола персонажа.
   /wca play — повтор, /wca stop — остановка, /wca auto — автозапуск.

В этой пробной сборке озвучен только квест 50913.
Другие квесты пока сохраняются для экспорта и остаются без озвучки.
Голос синтетический; его актёрская подача ещё не утверждена.
Проверка в самом клиенте WoW Circle ещё требуется.
"""


def build(output: Path) -> None:
    audio_root = ROOT / "assets" / "akunda"
    manifest = audio_root / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    rows = document["quests"]
    source = json.loads((ROOT / "quests" / "akunda_ruRU.json").read_text(encoding="utf-8"))
    expected = [row for row, _, _ in plan_quests(source, "loa")]
    if rows != expected:
        raise ValueError("Recorded manifest does not match the current quest source; regenerate it.")
    for row in rows:
        for name in row["audio"]:
            path = (audio_root / name).resolve(strict=True)
            if not path.is_relative_to(audio_root.resolve()):
                raise ValueError("Recording escapes audio root.")
            metadata = json.loads(path.with_suffix(".ogg.json").read_text(encoding="utf-8"))
            inputs = metadata["input"]
            if metadata["audio_sha256"] != sha256(path) or inputs["text"] != row.get("tts_text", row["text"]) or inputs["settings"] != PROFILES[row["tts_profile"]]:
                raise ValueError(f"Recording integrity or input mismatch: {name}")
    demo = ROOT / "addon" / "WarcraftAudi" / "Audio" / "demo.ogg"
    recording = audio_root / "ruRU" / "50913" / "detail_male.ogg"
    demo.parent.mkdir(parents=True, exist_ok=True)
    if demo.exists() and sha256(demo) != sha256(recording):
        raise ValueError("Existing demo differs; preserve it before choosing another demo.")
    if not demo.exists():
        shutil.copyfile(recording, demo)
    (demo.parent / "ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary)
        build_addon(work / "base.zip", with_demo=True)
        if build_pack(manifest, audio_root, "Akunda_ruRU", work / "voices.zip") != 4:
            raise ValueError("Expected four Akunda recordings, including both description variants.")
        combined = work / "bundle.zip"
        with zipfile.ZipFile(combined, "w", zipfile.ZIP_DEFLATED) as destination:
            names = set()
            for archive in ("base.zip", "voices.zip"):
                with zipfile.ZipFile(work / archive) as source_zip:
                    for info in source_zip.infolist():
                        if info.filename in names:
                            raise ValueError(f"Duplicate archive path: {info.filename}")
                        names.add(info.filename)
                        destination.writestr(info, source_zip.read(info.filename))
            destination.writestr(f"{PACK}/ATTRIBUTION.txt", ATTRIBUTION)
            destination.writestr("INSTALL-RU.txt", INSTALL)
        combined.replace(output)
    print(f"Built audible addon and quest pack: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "WarcraftAudi-0.2.0-Akunda.zip")
    args = parser.parse_args()
    try:
        build(args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
