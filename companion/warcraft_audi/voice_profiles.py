"""Appearance-informed directions, with explicit uncertainty for missing NPC data."""
from __future__ import annotations

import hashlib
import re

# These are directions for candidates, never a claim of artistic approval.
PROFILES = {
    'vulpera': (('вульпера','vulpera'), 'A small slender fox-like adult. Light agile forward resonance, slightly nasal color, bright upper-middle speaking register, nimble friendly conversational expression. Very little dry grain. Small-creature vocal proportions.'),
    'gnome': (('гном','gnome'), 'A very small adult gnome. Light bright agile resonance, articulate and quick-witted, expressive upper-middle speaking register. A natural adult voice with clear consonants.'),
    'goblin': (('гоблин','goblin'), 'A small wiry goblin. Light sharp nasal resonance, quick conversational expression, a little dry rasp, lively and resourceful.'),
    'tauren': (('таурен','tauren'), 'A huge broad bovine humanoid. Low warm resonant voice with substantial chest weight, calm deliberate speech, clear words and restrained authority.'),
    'troll': (('тролль','troll'), 'A tall lean tusked troll. Forward slightly nasal earthy resonance, a little textured grain, relaxed rhythmic conversational delivery and precise Russian diction.'),
    'dwarf': (('дворф','dwarf'), 'A short broad sturdy dwarf. Compact chest resonance, a little rough grain, grounded direct conversational expression and clearly spoken words.'),
    'orc': (('орк','orc'), 'A broad muscular tusked orc. Strong chest resonance, modest dry roughness, direct grounded speech. Keep the words distinct and the emotion restrained.'),
    'elf': (('эльф','elf'), 'A slender adult elf. Clear smooth flexible resonance, poised natural conversation, precise consonants, measured expressive pacing.'),
    'human': (('человек','human'), 'An adult human. Natural conversational middle register, clear words and individual expressive phrasing.'),
    'dragon': (('дракон','dragon','драконид','dragonkin'), 'A large scaled draconic creature. Weighted resonant voice, restrained throat texture and deliberate clearly articulated speech.'),
}


def _matches(text,alias):
    return bool(re.search(r'(?<![a-z])'+re.escape(alias)+r'(?![a-z])',text)) if alias.isascii() else alias in text


def profile_for(request):
    race = request.get('npc_race','').strip().casefold()
    kind = request.get('npc_type','').strip().casefold()
    selected = next((key for key,(aliases,_) in PROFILES.items() if any(_matches(race,a) for a in aliases)), None)
    if not selected:
        selected = next((key for key,(aliases,_) in PROFILES.items() if any(_matches(kind,a) for a in aliases)), None)
    sex = request.get('npc_gender','unknown')
    identity = 'An adult male voice.' if sex == 'male' else 'An adult female voice.' if sex == 'female' else 'An adult character voice.'
    if selected:
        direction = identity + ' ' + PROFILES[selected][1]
        if selected in {'vulpera','gnome','goblin'} and sex == 'male':
            direction += ' Use a light male tenor with comfortably elevated speaking pitch.'
        quality = 'species-informed candidate'
    else:
        direction = identity + ' Clear natural conversational middle register, modest vocal weight, precise pronunciation.'
        quality = 'appearance unknown; candidate only'
    if selected=='vulpera' and sex=='male':
        direction='A light-voiced adult male fox-like vulpera, small slender build, lively and quick-witted. A clear HIGH-MID male TENOR register, thin bright forward resonance and slightly nasal color. A light male tenor, agile friendly conversational speech, very little dry grain. Keep speaking pitch comfortably elevated.'
    instruction = direction + ' Native Russian, intelligible speech, dry clean recording, natural emotion and pacing.'
    bounds=([165,360] if sex=='male' else [190,420]) if selected in {'vulpera','gnome','goblin'} else [55,450]
    version = hashlib.sha256(('character-direction-v3:' + instruction+str(bounds)).encode()).hexdigest()
    return {'id': selected or 'unspecified', 'instruction': instruction, 'revision': version,
            'status': quality, 'approved': False,'pitch_bounds_hz':bounds}


def reference_key(request):
    identity = str(request['npc_id']) if request['npc_id'] else request['speaker']
    return hashlib.sha256((identity + ':' + profile_for(request)['revision']).encode()).hexdigest()
