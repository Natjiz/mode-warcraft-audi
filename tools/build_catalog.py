"""Publish only recordings that actually exist, keyed by exact quest text."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.warcraft_audi.protocol import canonical_request


def build():
    entries = []
    demo = ROOT / "downloads/Akunda-character-voice-0.3.0.mp3"
    entries.append({"request": canonical_request({"action": "demo"}), "file": str(demo.relative_to(ROOT)), "mime": "audio/mpeg"})
    source = ROOT / "assets/akunda_streamed/manifest.json"
    if source.exists():
        entries.extend(json.loads(source.read_text(encoding="utf-8"))["items"])
    result = []
    for entry in entries:
        path = ROOT / entry["file"]
        result.append({"request": entry["request"], "url": "https://raw.githubusercontent.com/Natjiz/mode-warcraft-audi/main/" + path.relative_to(ROOT).as_posix(),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size, "mime": entry["mime"],
                       "voice": "akunda-approved-0.3.0"})
    target = ROOT / "companion/catalog.json"
    target.write_text(json.dumps({"schema_version": 1, "items": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catalog contains {len(result)} actual recordings")


if __name__ == "__main__":
    build()
