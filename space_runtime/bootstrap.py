from __future__ import annotations
import os
from pathlib import Path


def build():
    # Import spaces before torch; ZeroGPU installs CUDA emulation for root loading.
    import spaces
    from companion.warcraft_audi.voice_engine import QwenEngine
    from companion.warcraft_audi.library import QuestLibrary,HubLibraryStore
    from tools.fetch_voice_models import fetch
    from .ui import make_app
    # Compile CPU pitch analysis before any GPU allocation/time quota is used.
    import numpy as np
    from companion.warcraft_audi.voice_quality import median_pitch
    median_pitch(np.sin(np.arange(8000)*2*np.pi*220/16000).astype(np.float32)*.1,16000)
    repo=os.environ.get('WCA_LIBRARY_REPO','')
    token=os.environ.get('HF_TOKEN','')
    if not repo or not token:raise RuntimeError('Configure WCA_LIBRARY_REPO and the HF_TOKEN secret before starting the Space')
    store=HubLibraryStore(repo,token)
    root=Path(__file__).resolve().parents[1]
    model_root=Path(os.environ.get('WCA_MODELS', '/tmp/warcraft-audi-models'))
    fetch(model_root)
    device=os.environ.get('WCA_DEVICE','cuda')
    engine=QwenEngine(model_root,root/'voices',device=device,keep_models=True)
    # Placement at module startup is required for efficient ZeroGPU transfers.
    engine._load('base');engine._load('design')
    def duration(request):
        references=engine.reference_files(request)
        extra=25 if references and not all(p.exists() for p in references) else 8
        return min(120,max(15,extra+len(request['text'])//12))
    @spaces.GPU(duration=duration)
    def compute(request):
        return engine.generate(request)
    return make_app(QuestLibrary(store,engine,compute))
