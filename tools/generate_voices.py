"""Generate Russian quest recordings locally with the official Silero v5 model.

This runs outside WoW. No speech service or secret is needed. Model downloads
use verified HTTPS; a receipt records the downloaded artifact's SHA-256.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.request
import wave
from xml.sax.saxutils import escape

from package_addon import STAGES

MODEL_URL = "https://models.silero.ai/models/tts/ru/v5_ru.pt"
DEFAULT_MODEL = Path("/workspace/.warcraft-audi-models/v5_ru.pt")
SAMPLE_RATE = 48000
PROFILES = {
    "commander": {"speaker": "eugene", "rate": "slow", "pitch": "medium"},
    "sage": {"speaker": "aidar", "rate": "slow", "pitch": "low"},
    "scout": {"speaker": "baya", "rate": "medium", "pitch": "medium"},
    "loa": {"speaker": "eugene", "rate": "slow", "pitch": "low"},
    "akunda_mature": {
        "speaker": "aidar", "rate": "medium", "pitch": "low",
        "postprocess": (
            "rubberband=tempo=0.97:pitch=0.94:formant=shifted,"
            "highpass=f=65,equalizer=f=160:t=q:w=0.9:g=1.2,"
            "equalizer=f=2700:t=q:w=0.9:g=1,"
            "acompressor=threshold=0.15:ratio=1.5:attack=20:release=150:makeup=1"
        ),
    },
}
DEMO_TEXT = (
    "Путник. Драконьи острова пробуждаются. "
    "Древняя угроза вновь нависла над Азеротом. "
    "Собери союзников и приготовься к пути. Нас ждут великие свершения."
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_path(model: Path) -> Path:
    return model.with_suffix(model.suffix + ".json")


def verify_model(model: Path) -> dict:
    receipt = json.loads(receipt_path(model).read_text(encoding="utf-8"))
    if receipt.get("url") != MODEL_URL or receipt.get("sha256") != sha256(model):
        raise ValueError("Model receipt or checksum does not match. Do not load this artifact.")
    return receipt


def fetch_model(model: Path) -> None:
    if model.exists():
        verify_model(model)
        print(f"Verified cached model: {model}")
        return
    model.parent.mkdir(parents=True, exist_ok=True)
    # Download to a fresh file; failures never leave an apparently usable model.
    with tempfile.TemporaryDirectory(dir=model.parent) as work:
        temporary = Path(work) / "download.pt"
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, temporary.open("wb") as output:
            if response.geturl() != MODEL_URL:
                raise ValueError("Unexpected model redirect; review the official source before downloading.")
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size < 1_000_000:
            raise ValueError("The download is too small to be the official speech model.")
        receipt = {"url": MODEL_URL, "sha256": sha256(temporary), "license": "CC-BY-NC-SA-4.0"}
        # The digest records a verified TLS download; it is not a publisher signature.
        receipt_path(model).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        temporary.replace(model)
    print(f"Downloaded model: {model}; SHA-256: {receipt['sha256']}")


def split_text(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Recording text must be a nonempty string.")
    paragraphs = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return [part for paragraph in paragraphs for part in textwrap.wrap(
        paragraph, width=350, break_long_words=False, break_on_hyphens=False)]


def make_ssml(text: str, profile: str) -> str:
    settings = PROFILES[profile]
    return (f'<speak><prosody rate="{settings["rate"]}" pitch="{settings["pitch"]}">'
            + escape(text) + "</prosody></speak>")


def load_model(path: Path):
    verify_model(path)  # PackageImporter loads code; only use the official download.
    import torch
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    return model


def render(model, text: str, profile: str, output: Path, model_digest: str) -> None:
    if output.suffix != ".ogg":
        raise ValueError("Output must have the .ogg extension (Vorbis).")
    profile_data = PROFILES[profile]
    settings = {"text": text, "profile": profile, "settings": profile_data, "model_sha256": model_digest,
                "sample_rate": SAMPLE_RATE, "encoder": "libvorbis-q5-loudnorm-18-v1"}
    metadata_path = output.with_suffix(".ogg.json")
    if output.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("input") != settings or metadata.get("audio_sha256") != sha256(output):
            raise ValueError(f"Existing recording differs; choose a new output directory: {output}")
        print(f"Reused {output}")
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg with libvorbis is required to encode recordings.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        work = Path(directory)
        wav_path = work / "speech.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            for chunk in split_text(text):
                signal = model.apply_tts(ssml_text=make_ssml(chunk, profile),
                                         speaker=profile_data["speaker"], sample_rate=SAMPLE_RATE)
                # Avoid NumPy/torchaudio dependencies and retain 16-bit little-endian PCM.
                samples = array("h", (round(max(-1.0, min(1.0, sample)) * 32767) for sample in signal.tolist()))
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    raise ValueError("Speech model returned empty audio.")
                wav.writeframes(samples.tobytes())
                wav.writeframes(bytes(int(SAMPLE_RATE * 0.15) * 2))
        encoded = work / "speech.ogg"
        filters = [profile_data["postprocess"]] if profile_data.get("postprocess") else []
        filters.append("loudnorm=I=-18:TP=-1.5:LRA=7")
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(wav_path), "-af",
                        ",".join(filters), "-ar", str(SAMPLE_RATE), "-ac", "1",
                        "-c:a", "libvorbis", "-q:a", "5", str(encoded)], check=True)
        # Decode the entire generated file, preserving a failing decoder exit status.
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(encoded), "-f", "null", "-"], check=True)
        metadata = {"input": settings, "audio_sha256": sha256(encoded), "source": MODEL_URL}
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        encoded.replace(output)
    print(f"Generated {output}")


def plan_quests(document: dict, profile: str) -> list[tuple[dict, str, str]]:
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("quests"), list):
        raise ValueError("Expected schema_version: 1 and a quests array.")
    result, seen = [], set()
    for row in document["quests"]:
        if not isinstance(row, dict):
            raise ValueError("Every quest must be an object.")
        quest_id, stage = row.get("quest_id"), row.get("stage")
        if type(quest_id) is not int or quest_id <= 0 or not isinstance(stage, str) or stage not in STAGES or row.get("locale") != "ruRU":
            raise ValueError("Generation requires positive quest_id, a valid stage and locale ruRU.")
        gender = row.get("gender")
        if gender is not None and gender not in ("male", "female"):
            raise ValueError("gender must be male, female or omitted.")
        key = (quest_id, stage, gender)
        if key in seen:
            raise ValueError(f"Duplicate quest stage: {key}")
        seen.add(key)
        selected_profile = row.get("tts_profile", profile)
        if not isinstance(selected_profile, str) or selected_profile not in PROFILES:
            raise ValueError(f"Unknown voice profile: {selected_profile}")
        text = row.get("tts_text", row.get("text"))
        split_text(text)  # Validate all rows before producing the first output.
        if row.get("audio"):
            raise ValueError("Input already has audio paths. Preserve those recordings or use a separate source export.")
        copy = dict(row)
        suffix = "_" + gender if gender else ""
        copy["audio"] = [f"ruRU/{quest_id}/{stage}{suffix}.ogg"]
        result.append((copy, text, selected_profile))
    if not result:
        raise ValueError("No quest texts to generate.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-model", help="Download the official model over HTTPS and record its checksum")
    demo = sub.add_parser("demo", help="Render an original audition line, not a real quest")
    demo.add_argument("--profile", choices=PROFILES, default="commander")
    demo.add_argument("--output", type=Path, required=True)
    quests = sub.add_parser("quests", help="Render texts exported by the addon")
    quests.add_argument("source", type=Path)
    quests.add_argument("--profile", choices=PROFILES, default="commander")
    quests.add_argument("--audio-root", type=Path, required=True)
    quests.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "fetch-model":
            fetch_model(args.model)
            return
        plan = None
        if args.command == "quests":
            if args.manifest.exists():
                raise ValueError("Manifest output already exists; choose a new name.")
            plan = plan_quests(json.loads(args.source.read_text(encoding="utf-8")), args.profile)
        model = load_model(args.model)
        digest = verify_model(args.model)["sha256"]
        if args.command == "demo":
            render(model, DEMO_TEXT, args.profile, args.output, digest)
        else:
            for row, text, profile in plan:
                render(model, text, profile, args.audio_root / row["audio"][0], digest)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps({"schema_version": 1, "quests": [row for row, _, _ in plan]},
                                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote voice-pack manifest: {args.manifest}")
    except (ValueError, OSError, ImportError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
