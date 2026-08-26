# Multi-lingual Sheet Music Generator — v8

This build focuses on the actual failure modes found in the supplied **By Faith** and **We Go, Therefore, Preaching** files.

## What is fixed in this build

### 1. Physical box count is authoritative
The syllable layout is read from the drawn grid cells, not from extracted text alone. A real cell counts even when it contains only `-` or is visually blank. Duplicate vector descriptions of the same cell are collapsed before counting.

### 2. Harmony is a token-level semantic rule
The important distinction is that a visual row can contain **both Lead and Harmony boxes**. The program therefore no longer treats the whole row as Harmony just because one yellow section is present.

Each actual box is classified from its border color:

- black/neutral = ordinary/verse material
- blue = pre-chorus/lead stream
- red = chorus
- yellow = Harmony-only

A mixed row stays mixed. The Lead can use only its non-yellow boxes; a harmony voice uses the yellow boxes when a row contains yellow Harmony material.

### 3. Yellow Harmony can never leak into Lead
The restriction is enforced in the **voice-placement lock**, not merely in the row matcher. This prevents a strong English word match from selecting a yellow box for a Lead voice.

### 4. Same-stream entry prefixes are preserved
When a score voice enters partway through a mixed layout row, translated boxes from the skipped prefix are folded onto the entry note only when those skipped boxes belong to the **same semantic stream**. This fixes the Bridge case where `We | preach` maps to `Mun- | do` but the Lead score starts on `preach`: the rendered entry becomes `Mun-do`, while a Harmony entry beginning on yellow boxes does not absorb the preceding Lead boxes.

### 5. Direct final-score correction UI remains included
The clickable preview/nudge UI from v7 is retained. It is a final-proofing tool; it does not replace the source mapping logic.

## Validation performed on the supplied files

- `tests_semantic_pairing.py` — PASS
- `tests_harmony_pairing.py` — PASS
- `tests_byfaith_exact.py` — PASS
- `tests_mixed_harmony.py` — PASS
- Python compile check for `app.py` and all `smgcore/*.py` — PASS

The exact By Faith test reads **23 English + 23 translated layout lines**, with matching physical box counts including blank/dash boxes, and confirms `By faith` ↔ `ta-noujain` as the Harmony pair.

The We Go regression confirms the intentionally mixed Bridge row: the first two boxes are Lead/blue and the following boxes are Harmony/yellow, and confirms the Lead Bridge output begins `Mun-do / Jeho- / vá / Mai- / pi` while Harmony receives the yellow stream instead.

## Running

```bash
pip install -r requirements.txt
streamlit run app.py
```

The renderer will use an available Unicode system font. No font binaries are bundled in this distribution.
