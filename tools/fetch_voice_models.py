"""Fetch pinned server models and verify the publisher's SHA-256 values."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.warcraft_audi.voice_engine import file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/workspace/.warcraft-audi-models"))
    parser.add_argument("--kind", choices=("base", "design", "all"), default="all")
    args = parser.parse_args()
    fetch(args.root, args.kind)


def fetch(root: Path, kind_choice="all"):
    from huggingface_hub import snapshot_download
    manifest = json.loads((ROOT / "voices/models.json").read_text())
    for kind, entry in manifest.items():
        if kind_choice not in {"all", kind}:
            continue
        target = root / entry["directory"]
        missing = False
        for relative, expected in entry["weights"].items():
            path = target / relative
            if path.exists() and file_hash(path) != expected:
                raise ValueError(f"Corrupt model artifact: {path}. Preserve or remove the corrupt file before downloading it again.")
            missing |= not path.is_file()
        if missing:
            snapshot_download(entry["repo"], revision=entry["revision"], local_dir=target,
                allow_patterns=["*.json", "*.txt", "*.safetensors", "*.onnx", "speech_tokenizer/*"], max_workers=2, token=False)
        for relative, expected in entry["weights"].items():
            if file_hash(target / relative) != expected:
                raise ValueError(f"Checksum mismatch for {relative}")
        print(f"Verified {kind}: {entry['repo']} @ {entry['revision']}", flush=True)



if __name__ == "__main__":
    main()
