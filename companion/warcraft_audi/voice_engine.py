"""Local GPU/CPU worker: design a reference once, reuse it for each NPC."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class QwenEngine:
    def __init__(self, models: Path, voices: Path):
        self.models, self.voices = Path(models), Path(voices)
        self.repo = self.voices.resolve().parent
        self.manifest = json.loads((self.voices / "models.json").read_text())
        cards = sorted((self.voices / "npcs").glob("*.json"))
        self.revision = hashlib.sha256(b"qwen-npc-clone-v1" + (self.voices / "models.json").read_bytes() + b"".join(p.read_bytes() for p in cards)).hexdigest()
        self.loaded, self.prompts = {}, {}

    def _load(self, kind):
        if kind in self.loaded:
            return self.loaded[kind]
        import torch
        from qwen_tts import Qwen3TTSModel
        # Keep only one large speech model resident; NPC reference prompts are small.
        for other in list(self.loaded):
            if other != kind:
                del self.loaded[other]
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        entry = self.manifest[kind]
        root = self.models / entry["directory"]
        for relative, expected in entry["weights"].items():
            if file_hash(root / relative) != expected:
                raise ValueError("Speech model integrity check failed")
        torch.set_num_threads(4)
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.loaded[kind] = Qwen3TTSModel.from_pretrained(str(root), device_map=device,
            dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
        return self.loaded[kind]

    def _reference(self, request):
        import soundfile as sf
        npc = request["npc_id"]
        card_path = self.voices / "npcs" / f"{npc}.json"
        if npc and card_path.is_file():
            card = json.loads(card_path.read_text(encoding="utf-8"))
            path = (self.repo / card["reference"]).resolve()
            if not path.is_relative_to(self.repo) or file_hash(path) != card["reference_sha256"]:
                raise ValueError("NPC reference integrity check failed")
            return path, card["reference_text"], card["voice_version"]
        # The available race/type metadata is a starting point, not a visual diagnosis.
        identity = str(npc) if npc else request["speaker"] + ":" + request["npc_type"] + ":" + request["npc_gender"]
        key = hashlib.sha256(identity.encode()).hexdigest()
        directory = self.voices / "generated"
        directory.mkdir(exist_ok=True)
        path, metadata = directory / (key + ".wav"), directory / (key + ".json")
        if path.exists() and metadata.exists():
            card = json.loads(metadata.read_text(encoding="utf-8"))
            if file_hash(path) != card["sha256"]:
                raise ValueError("Generated NPC reference integrity check failed")
            return path, card["text"], key
        model = self._load("design")
        text = request["text"][:180].rsplit(" ", 1)[0] if len(request["text"]) > 180 else request["text"]
        description = f"A {request['npc_gender']} fantasy NPC. Species or race: {request['npc_race'] or request['npc_type'] or 'unspecified humanoid'}."
        instruction = description + " A natural character voice suited to this species, native Russian diction, clear words, calm conversational delivery, dry recording without music or echoes."
        import torch
        import numpy as np
        torch.manual_seed(int(key[:8], 16))
        steps = {"count": 0}
        def count_reference(module, args, output):
            steps["count"] += 1
        hook = model.model.talker.register_forward_hook(count_reference)
        try:
            waves, rate = model.generate_voice_design(text=text, language="Russian", instruct=instruction, max_new_tokens=600)
        finally:
            hook.remove()
        if steps["count"] >= 600 or not np.isfinite(waves[0]).all() or len(waves[0]) < rate or np.max(np.abs(waves[0])) < 0.001:
            raise ValueError("Invalid or truncated NPC reference")
        sf.write(path, waves[0], rate, subtype="PCM_16")
        metadata.write_text(json.dumps({"text": text, "description": description, "sha256": file_hash(path),
                                       "status": "automatic candidate; appearance and voice need review"}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path, text, key

    def generate(self, request):
        if request["locale"] != "ruRU":
            raise ValueError("Unsupported language")
        import numpy as np
        import soundfile as sf
        reference, reference_text, voice = self._reference(request)
        model = self._load("base")
        if voice not in self.prompts:
            self.prompts[voice] = model.create_voice_clone_prompt(ref_audio=str(reference), ref_text=reference_text, x_vector_only_mode=False)
            if len(self.prompts) > 32:
                oldest = next(iter(self.prompts))
                if oldest != voice:
                    self.prompts.pop(oldest)
        # Bound generation size, preserving words and sentences rather than dropping text.
        import textwrap
        chunks = textwrap.wrap(request["text"], width=280, break_long_words=False, break_on_hyphens=False)
        parts, rate = [], None
        for chunk in chunks:
            calls = {"count": 0}

            def count_steps(module, args, output):
                calls["count"] += 1

            hook = model.model.talker.register_forward_hook(count_steps)
            try:
                waves, rate = model.generate_voice_clone(text=chunk, language="Russian", voice_clone_prompt=self.prompts[voice],
                    max_new_tokens=768, do_sample=True, temperature=0.75, top_p=0.9)
            finally:
                hook.remove()
            if calls["count"] >= 768:
                raise ValueError("Speech generation reached its length limit")
            audio = np.asarray(waves[0])
            if not np.isfinite(audio).all() or len(audio) < 100 or np.max(np.abs(audio)) < 0.001:
                raise ValueError("Invalid generated audio")
            parts.extend([audio, np.zeros(int(rate * 0.15), dtype=audio.dtype)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf.write(root / "speech.wav", np.concatenate(parts), rate, subtype="PCM_16")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(root / "speech.wav"),
                            "-af", "loudnorm=I=-18:TP=-1.5:LRA=7", "-ar", "48000", "-ac", "1",
                            "-c:a", "libvorbis", "-b:a", "64k", str(root / "speech.ogg")], check=True)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(root / "speech.ogg"), "-f", "null", "-"], check=True)
            return (root / "speech.ogg").read_bytes(), "audio/ogg"
