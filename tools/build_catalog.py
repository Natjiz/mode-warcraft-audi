"""Publish only recordings that actually exist, keyed by exact quest text."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.warcraft_audi.protocol import canonical_request
from companion.warcraft_audi.service import catalog_key, Catalog


def build():
    entries = []
    demo = ROOT / "downloads/Akunda-character-voice-0.3.0.mp3"
    demo_receipt = json.loads(demo.with_suffix('.json').read_text(encoding='utf-8'))
    entries.append({"request": canonical_request({"action": "demo"}), "file": str(demo.relative_to(ROOT)),
                    "mime": "audio/mpeg", "sha256": demo_receipt['audio_sha256'], "voice": "akunda-approved-0.3.0"})
    for source in sorted((ROOT / "assets").glob("*_streamed/manifest.json")):
        manifest = json.loads(source.read_text(encoding="utf-8"))
        if manifest.get('status') == 'rejected':
            continue
        for item in manifest['items']:
            entry = dict(item)
            voice = entry.get('voice') or manifest.get('voice')
            if not voice:
                card = ROOT / 'voices/npcs' / (str(entry['request']['npc_id']) + '.json')
                voice = json.loads(card.read_text(encoding='utf-8'))['voice_version']
            entry['voice'] = voice
            entries.append(entry)
    result = []
    for entry in entries:
        path = (ROOT / entry["file"]).resolve(strict=True)
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError('Catalog audio must stay inside the repository')
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry.get('sha256') != checksum:
            raise ValueError('Recording checksum does not match its manifest: ' + entry['file'])
        result.append({"request": canonical_request(entry["request"]), "url": "https://raw.githubusercontent.com/Natjiz/mode-warcraft-audi/main/" + path.relative_to(ROOT.resolve()).as_posix(),
                       "sha256": checksum, "size": path.stat().st_size, "mime": entry["mime"], "voice": entry['voice']})
    # A quest selected in the journal has no live NPC unit. Explicit aliases are
    # safe only when the exact quest text has one unambiguous recorded speaker.
    journal = {}
    for entry in result:
        if entry['request']['stage'] != 'detail' or not entry['request']['npc_id']:
            continue
        alias = {**entry, 'request': {**entry['request'], 'npc_id': 0, 'speaker': 'Рассказчик',
                                    'npc_race': '', 'npc_type': '', 'npc_gender': ''}, 'match': 'known-quest-journal'}
        journal.setdefault(catalog_key(alias['request']), []).append(alias)
    for candidates in journal.values():
        if len({(entry['voice'], entry['sha256']) for entry in candidates}) == 1:
            result.append(candidates[0])
    Catalog({'schema_version': 1, 'items': result})  # Reject duplicate/invalid entries before writing.
    target = ROOT / "companion/catalog.json"
    target.write_text(json.dumps({"schema_version": 1, "items": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catalog contains {len(result)} entries from {len(entries)} recordings (including journal aliases)")


if __name__ == "__main__":
    build()
