"""Compare the app's We Go Preaching output against the hand-made finished copy."""

import re
import sys
import unicodedata
from collections import defaultdict

import pymupdf as fitz

from smgcore import score as S

U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
KEY = U + "cf7c1b8b-WY_osg_We_Go_Preaching__Full_Score.pdf"
ENGLISH = U + "3a755121-jwb147_We_Go_Preaching_Full_Score.pdf"


def flat(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for apostrophe in "’ʼʾ'´`":
        text = text.replace(apostrophe, "'")
    return re.sub(r"[^a-z']", "", text)


def read(path, wanted_fonts, geometry_from=None):
    doc = fitz.open(path)
    parsed = S.parse_score(open(geometry_from or path, "rb").read())
    staves = defaultdict(list)
    for staff in parsed.staves:
        staves[staff.page].append(staff)
    out = defaultdict(list)
    for index, page in enumerate(doc):
        ordered = sorted(staves[index], key=lambda s: s.top)
        bands = S._lyric_bands(ordered, page.rect.height)
        for block in page.get_text("dict")["blocks"]:
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    if not any(span["font"].startswith(f) for f in wanted_fonts):
                        continue
                    x0, y0 = span["bbox"][0], span["bbox"][1]
                    for staff, (top, bottom) in zip(ordered, bands):
                        if top - 3 <= y0 + 4 <= bottom + 3:
                            out[(index, staff.index)].append((x0, span["text"]))
                            break
    return {k: "".join(t for _, t in sorted(v)) for k, v in out.items()}


def score(produced):
    key = read(KEY, ["TimesNewRomanPSMT"], geometry_from=ENGLISH)
    ours = read(produced, ["LiberationSerif", "DejaVu"], geometry_from=ENGLISH)
    entries = sorted(set(list(key) + list(ours)))
    good, bad = 0, []
    for entry in entries:
        if flat(ours.get(entry, "")) == flat(key.get(entry, "")):
            good += 1
        else:
            bad.append(entry)
    print(f"{produced.split('/')[-1]}")
    print(f"Staff lines identical to the answer key: {good}/{len(entries)}  ({good / len(entries):.1%})")
    for entry in bad:
        print(f"\n  page {entry[0] + 1} staff {entry[1]}")
        print(f"    this file : {' '.join(ours.get(entry, '').split())[:150]}")
        print(f"    answer key: {' '.join(key.get(entry, '').split())[:150]}")
    return good, len(entries)


if __name__ == "__main__":
    score(sys.argv[1] if len(sys.argv) > 1 else "_wegopreaching_out.pdf")
