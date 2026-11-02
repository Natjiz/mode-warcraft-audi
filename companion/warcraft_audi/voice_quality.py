"""Acoustic screening, not a substitute for artistic listening/approval."""
from __future__ import annotations


def median_pitch(audio,rate):
    import librosa
    import numpy as np
    waveform=np.asarray(audio,dtype=np.float32)
    if rate!=16000:waveform=librosa.resample(waveform,orig_sr=rate,target_sr=16000)
    f0,voiced,probability=librosa.pyin(waveform,fmin=55,fmax=450,sr=16000,frame_length=1024,hop_length=160)
    values=f0[np.isfinite(f0)&voiced&(probability>.15)]
    return float(np.median(values)) if len(values)>=10 else None


def within(pitch,limits):
    return pitch is not None and limits[0]<=pitch<=limits[1]


def limits_for(profile,reference_pitch=None):
    limits=profile.get('pitch_bounds_hz',[55,450])
    if reference_pitch:
        limits=[max(limits[0],reference_pitch*.7),min(limits[1],reference_pitch*1.5)]
    return limits
