"""Render a short, original character voice design audition outside WoW.

Uses a pinned public Qwen model. Checks the publisher's LFS SHA-256 hashes
before loading local safetensors. This does not alter installed voice packs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = Path("/workspace/.warcraft-audi-models/qwen3-voice-design")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_weights(model_path: Path, spec: dict) -> dict:
    url = f"https://huggingface.co/api/models/{spec['model']}/revision/{spec['revision']}?blobs=true"
    with urllib.request.urlopen(url, timeout=30) as response:
        metadata = json.load(response)
    if metadata["sha"] != spec["revision"]:
        raise ValueError("Model revision does not match the pinned revision.")
    verified = {}
    for item in metadata["siblings"]:
        if item["rfilename"].endswith(".safetensors"):
            relative = Path(item["rfilename"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid model artifact path.")
            actual = digest(model_path / relative)
            if actual != item["lfs"]["sha256"]:
                raise ValueError(f"Model checksum mismatch: {relative}")
            verified[str(relative)] = actual
    if len(verified) != 2:
        raise ValueError("Expected speech model and tokenizer weights.")
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--spec", type=Path, default=ROOT / "quests/akunda_voice_design.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".json").exists():
        parser.error("Output already exists; choose a new path to preserve earlier auditions.")
    if args.output.suffix != ".mp3":
        parser.error("Output must be an MP3 path.")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    print("Verifying pinned model weights", flush=True)
    verified = verify_weights(args.model_path, spec)

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(spec["seed"])
    start = time.monotonic()

    print("Loading voice design model on CPU", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        str(args.model_path), device_map="cpu", dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True,
    )
    steps = {"count": 0}

    def report_progress(module, inputs, output):
        steps["count"] += 1
        if steps["count"] % 24 == 0:
            print(f"Generated {steps['count']} audio steps; elapsed {time.monotonic() - start:.0f}s", flush=True)

    hook = model.model.talker.register_forward_hook(report_progress)
    print("Synthesizing the character audition", flush=True)
    try:
        with torch.inference_mode():
            waves, rate = model.generate_voice_design(
                text=spec["text"], language=spec["language"], instruct=spec["instruct"],
                non_streaming_mode=True, max_new_tokens=240, do_sample=True,
                temperature=0.8, top_p=0.9,
            )
    finally:
        hook.remove()
    if steps["count"] >= 240:
        raise ValueError("Speech reached the generation limit; do not publish potentially truncated audio.")
    audio = np.asarray(waves[0])
    if audio.ndim != 1 or not np.isfinite(audio).all() or len(audio) < rate or np.max(np.abs(audio)) < 0.001:
        raise ValueError("Generated audio is invalid, empty or silent.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
        work = Path(directory)
        sf.write(work / "voice.wav", audio, rate, subtype="PCM_16")
        encoded = work / "voice.mp3"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(work / "voice.wav"),
                        "-af", "loudnorm=I=-18:TP=-1.5:LRA=7", "-ar", "48000", "-ac", "1",
                        "-c:a", "libmp3lame", "-b:a", "192k", str(encoded)], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(encoded), "-f", "null", "-"], check=True)
        receipt = {"spec": spec, "weights": verified, "audio_sha256": digest(encoded),
                   "duration_seconds": len(audio) / rate, "generation_seconds": time.monotonic() - start,
                   "engine": "qwen-tts==0.1.1", "postprocess": "loudness normalization only; no pitch shift or distortion",
                   "status": "audition; pronunciation and character fit require listening"}
        args.output.with_suffix(".json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        encoded.replace(args.output)
    print(f"Generated audition: {args.output}; {len(audio) / rate:.2f} seconds", flush=True)


if __name__ == "__main__":
    main()
