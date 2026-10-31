# Character voice direction

User requirement: each NPC's voice must fit that specific character's appearance,
species, build and personality. Atmospheric delivery must preserve clear Russian
speech. A rough or deep voice is not a universal Warcraft style.

- Akunda's approved massive ancient thunder-lizard bass belongs to Akunda only.
- Kiro is a small fox-like male vulpera. The user rejected the 0.4.1 voice as too
  bassy. Use a light, agile male tenor, forward/slightly nasal resonance, natural
  conversational expression and only a little dry grain. Avoid heavy chest bass.
- Kiro 0.4.2 was accepted by the user ("пойдет"). Keep
  `downloads/Kiro-character-voice-0.4.2.mp3` as his approved reference for future
  Kiro quests. Preserve the existing voice version ID and audio URLs.
- Keep one consistent reference per NPC across quests; never reuse Akunda's
  reference or direction as a generic male fallback.
- A prompt describing the right voice does not prove that the audio matches it.
  Compare new output with rejected/approved references, inspect pitch where useful,
  and verify intelligibility. Pitch measurements alone do not establish artistic
  fit. Do not claim to have listened when only machine checks were performed.
- Record user rejection in the voice metadata and remove rejected takes from the
  active catalog. Use versioned paths for replacement audio so old caches and URLs
  cannot silently serve a rejected voice as the new one.
- Mark new voices as candidates until the user has assessed them. Do not describe
  a candidate as approved or guarantee automatic visual matching from NPC IDs.

Continue authorized implementation and public GitHub publication without asking
the user to repeat permission. Keep instructions for the player short and concrete.
