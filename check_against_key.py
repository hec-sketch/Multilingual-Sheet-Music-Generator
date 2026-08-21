"""Compare the app's output against the hand-made Aymara score, staff line by staff line."""

import re
import unicodedata
from collections import defaultdict

import pymupdf as fitz

from smgcore import align as A
from smgcore import layout as L
from smgcore import pairing as P
from smgcore import score as S

U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
KEY = U + "a68e5fa0-AP_osg_Dont_Let_Your_Hands_Drop_Down__Full_Score.pdf"


def flat(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for apostrophe in "’ʼʾ'´`":
        text = text.replace(apostrophe, "'")
    return re.sub(r"[^a-z']", "", text)


def key_lines():
    doc = fitz.open(KEY)
    parsed = S.parse_score(open(KEY, "rb").read())
    staves = defaultdict(list)
    for staff in parsed.staves:
        staves[staff.page].append(staff)
    out = defaultdict(str)
    for index, page in enumerate(doc):
        ordered = sorted(staves[index], key=lambda s: s.top)
        bands = S._lyric_bands(ordered, page.rect.height)
        runs = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["font"].startswith("Arial") and span["text"].strip():
                        runs.append((span["bbox"][1], span["bbox"][0], span["text"]))
        runs.sort()
        # The overlay is a set of free text boxes; read them left to right within
        # each staff, not in the order the PDF happens to store them.
        placed = defaultdict(list)
        for y, x, text in runs:
            for staff, (top, bottom) in zip(ordered, bands):
                if top - 3 <= y + 4 <= bottom + 3:
                    placed[(index, staff.index)].append((x, text))
                    break
        for entry, items in placed.items():
            out[entry] = "".join(text for _, text in sorted(items))
    return out


def mine():
    score = S.parse_score(open(U + "69deabc6-jwb143_DoNotLetYourHandsDropDown_Full_Score.pdf", "rb").read())
    english = L.to_editable(
        L.parse_layout(open(U + "b6fa29ed-jwb143_Do_Not_Let_Your_Hands_Drop_Down_SyllableLayout.pdf", "rb").read())
    )
    translated = L.to_editable(
        L.parse_layout(open(U + "1c671057-jwb143_SyllableLayoutaymara.pdf", "rb").read())
    )
    paired = P.pair_layouts(english, translated)
    table = P.translation_map(paired.pairs, translated, None, english)
    mapping = A.build_section_map(
        [line.section for line in english if line.section],
        [name for _, _, _, name in score.sections],
    )
    plans = A.align_all_by_text(score, english, table, mapping)
    by_id = {line.id: line for line in score.lines}
    out = defaultdict(str)
    voices = {}
    for voice, plan in plans.items():
        for assignment in plan.assignments:
            line = by_id[assignment.score_line_id]
            out[(line.page, line.staff)] += " ".join(assignment.tokens)
            voices[(line.page, line.staff)] = voice
    return out, voices, plans


key = key_lines()
ours, voices, plans = mine()
keys = sorted(set(list(key) + list(ours)))
same, wrong = 0, []
for entry in keys:
    if flat(ours.get(entry, "")) == flat(key.get(entry, "")):
        same += 1
    else:
        wrong.append(entry)

print(f"Staff lines identical to the answer key: {same}/{len(keys)}  ({same / len(keys):.1%})")
notes = sum(p.notes_total for p in plans.values())
filled = sum(p.covered for p in plans.values())
print(f"Notes filled: {filled}/{notes}")
for entry in wrong:
    print(f"\n  page {entry[0] + 1} staff {entry[1]} [{voices.get(entry, '?')}]")
    print(f"    ours: {ours.get(entry, '')[:160]}")
    print(f"    key : {' '.join(key.get(entry, '').split())[:160]}")
