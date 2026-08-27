"""Run the whole test-data corpus through the app and score it against the keys.

Not a unit test - a measurement. It replays app.py's pipeline exactly (same call
order, no human edits, nothing dropped, no voice skipped), renders each score,
and compares it to the answer key row by row. That is what makes "this change
fixed something" a statement about the real documents rather than an opinion.

    python3 corpus_baseline.py                 every case
    python3 corpus_baseline.py "By Faith"      just the ones matching

Writes each generated PDF and a full row-by-row diff to out/ beside this file.
Reads test-data/ and never writes to it.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import traceback
import unicodedata
from dataclasses import dataclass

import pymupdf as fitz

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from smgcore import blankscore as blank_mod  # noqa: E402
from smgcore import layout as layout_mod  # noqa: E402
from smgcore import lock as lock_mod  # noqa: E402
from smgcore import pairing as pairing_mod  # noqa: E402
from smgcore import render as render_mod  # noqa: E402
from smgcore import score as score_mod  # noqa: E402

def _find_test_data() -> str:
    """The read-only corpus, wherever this repository has been checked out."""
    folder = HERE
    for _ in range(5):
        folder = os.path.dirname(folder)
        candidate = os.path.join(folder, "test-data")
        if os.path.isdir(candidate):
            return candidate
    raise SystemExit("test-data/ was not found beside this checkout")


TEST_DATA = _find_test_data()


@dataclass
class Case:
    song: str
    variant: str
    score_pdf: str
    layout_pdf: str
    key_pdf: str

    @property
    def name(self) -> str:
        return f"{self.song} / {self.variant}"


def discover_cases() -> list[Case]:
    """Every test-data folder holding a score, a layout and an answer key."""
    cases: list[Case] = []
    for song in sorted(os.listdir(TEST_DATA)):
        song_dir = os.path.join(TEST_DATA, song)
        if not os.path.isdir(song_dir):
            continue
        for variant in sorted(os.listdir(song_dir)):
            variant_dir = os.path.join(song_dir, variant)
            if not os.path.isdir(variant_dir):
                continue
            score = layout = key = None
            for entry in sorted(os.listdir(variant_dir)):
                if not entry.lower().endswith(".pdf"):
                    continue
                path = os.path.join(variant_dir, entry)
                low = entry.lower()
                if low.startswith("english score"):
                    score = path
                elif low.startswith("syllabus layout") or low.startswith("syllablelayout"):
                    layout = path
                else:
                    # the answer key: "Key_XX_osg_..." or "XX_osg_..."
                    key = path
            if score and layout and key:
                cases.append(Case(song, variant, score, layout, key))
    return cases


@dataclass
class Run:
    """Everything the pipeline produced, kept so a failure can be traced."""

    case: Case
    score_doc: object
    layout_doc: object
    all_rows: list
    english_lines: list
    translated_lines: list
    pairs: list
    pair_notes: list
    translation: dict
    lock: object
    plans: dict
    placements: dict
    held_notes: dict
    issues: list
    pdf: bytes | None
    error: str | None = None


def run_case(case: Case, *, render: bool = True) -> Run:
    english_bytes = open(case.score_pdf, "rb").read()
    layout_bytes = open(case.layout_pdf, "rb").read()

    # app.py:536
    score_doc = score_mod.parse_score(english_bytes)
    # app.py:543
    layout_doc = layout_mod.parse_layout(layout_bytes)
    # app.py:550
    blank_bytes, _ = blank_mod.strip_lyrics(english_bytes, score_doc)
    # app.py:554
    score_words = score_doc.sung_words()

    # app.py:567 - no user edits
    all_rows = layout_mod.to_editable(layout_doc, {})
    # app.py:571
    english_lines, editable_lines = layout_mod.split_in_half(
        all_rows, layout_doc.page_count, score_words
    )
    if not english_lines:
        return Run(case, score_doc, layout_doc, all_rows, [], [], [], [], {}, None,
                   {}, {}, {}, [], None,
                   error="split_in_half found no English half (app would stop here)")

    # app.py:583 - nothing dropped
    working_lines = list(editable_lines)
    # app.py:589
    pair_result = pairing_mod.pair_layouts(english_lines, working_lines)
    pairs = pair_result.pairs
    # app.py:594
    english_lines = layout_mod.inherit_pair_tags(english_lines, pairs, working_lines)
    # app.py:595 - no overrides
    translation = pairing_mod.translation_map(pairs, working_lines, {}, english_lines)

    # app.py:600 - no voice skipped
    active_voices = list(score_doc.voices)
    # app.py:606
    lock = lock_mod.build_lock(english_lines, translation)
    plans = lock_mod.plan_voices(score_doc, lock, active_voices)

    # app.py:1235-1266
    placements: dict[int, list[str]] = {}
    held_notes: dict[int, list] = {}
    issues: list[dict] = []
    for voice_name, voice_plan in plans.items():
        for assignment in voice_plan.assignments:
            tokens = list(assignment.tokens)
            need = len(assignment.tokens)
            if len(tokens) > need:
                tokens = tokens[:need]
            elif len(tokens) < need:
                issues.append({"voice": voice_name, "page": assignment.page + 1,
                               "notes": need, "given": len(tokens)})
                tokens = tokens + [""] * (need - len(tokens))
            placements[assignment.score_line_id] = tokens
            if assignment.held:
                held_notes[assignment.score_line_id] = list(assignment.held)

    pdf = None
    error = None
    if render:
        try:
            settings = render_mod.RenderSettings(
                max_size=11.0, baseline_offset=5.6,
                font_choice=list(render_mod.BUNDLED_FONTS)[2],
            )
            pdf = render_mod.render(score_doc, blank_bytes, placements, settings,
                                    held_notes, {})
        except Exception as exc:  # noqa: BLE001
            error = f"render failed: {exc}"

    return Run(case, score_doc, layout_doc, all_rows, english_lines, working_lines,
               pairs, pair_result.notes, translation, lock, plans, placements,
               held_notes, issues, pdf, error)




# ------------------------------------------------------------------ scoring

import difflib
import unicodedata


ROW_TOLERANCE = 4.0     # spans this close vertically are on the same printed row
ROW_MATCH = 12.0        # a key row and an output row this close are the same row


def _fonts(pdf_bytes: bytes) -> set[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = set()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        out.add(span["font"])
    return out


def _spans(pdf_bytes: bytes, keep: set[str]):
    """(page, y, x, text) for every span set in one of the given fonts."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = []
    for number, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text and span["font"] in keep:
                        out.append((number, round(span["bbox"][3], 1),
                                    round(span["bbox"][0], 1), text))
    return out


def _rows(spans) -> dict[tuple[int, float], list[tuple[float, str]]]:
    """Group spans into printed rows: {(page, y): [(x, text) left to right]}."""
    spans = sorted(spans, key=lambda s: (s[0], s[1], s[2]))
    rows: dict[tuple[int, float], list[tuple[float, str]]] = {}
    keys: list[tuple[int, float]] = []
    for page, y, x, text in spans:
        hit = None
        for key in keys:
            if key[0] == page and abs(key[1] - y) <= ROW_TOLERANCE:
                hit = key
                break
        if hit is None:
            hit = (page, y)
            keys.append(hit)
            rows[hit] = []
        rows[hit].append((x, text))
    return {key: sorted(value) for key, value in rows.items()}


def fold(text: str) -> str:
    """Compare like a proofreader: case, accents, spacing and punctuation set aside.

    A syllable break is written `ta - nou` by one typesetter and `ta-nou` by the
    other, and an elongation is `jain_` in one and `jain` in the other. None of
    that is a difference in what is sung, so none of it survives folding.
    """
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _row_text(cells) -> str:
    return " ".join(text for _, text in cells)


def compare(output_pdf: bytes, key_pdf: bytes, blank_pdf: bytes) -> dict:
    engraving = _fonts(blank_pdf)
    key_lyric_fonts = _fonts(key_pdf) - engraving
    out_lyric_fonts = _fonts(output_pdf) - engraving

    key_rows = _rows(_spans(key_pdf, key_lyric_fonts))
    out_rows = _rows(_spans(output_pdf, out_lyric_fonts))

    # Best-gap-first matching, so a row is never stolen by a worse candidate.
    candidates = sorted(
        (abs(k[1] - o[1]), k, o)
        for k in key_rows for o in out_rows
        if k[0] == o[0] and abs(k[1] - o[1]) <= ROW_MATCH
    )
    pair_of: dict = {}
    taken: set = set()
    for _, k, o in candidates:
        if k not in pair_of and o not in taken:
            pair_of[k] = o
            taken.add(o)

    # The song's title. It is printed at the top of the key and exists in neither
    # half of any layout, so nothing in the inputs can produce it. Reported, never
    # counted against the reading.
    title = None
    first_page = [key for key in key_rows if key[0] == 0]
    if first_page:
        top = min(first_page, key=lambda key: key[1])
        if top not in pair_of:
            title = {"y": top[1], "text": _row_text(key_rows[top])}
            key_rows = {k: v for k, v in key_rows.items() if k != top}

    identical = 0
    differing: list[dict] = []
    missing: list[dict] = []
    letters_total = 0
    letters_matched = 0

    for key in sorted(key_rows):
        want_text = _row_text(key_rows[key])
        want = fold(want_text)
        letters_total += len(want)
        if key not in pair_of:
            missing.append({"page": key[0] + 1, "y": key[1], "expected": want_text})
            continue
        other = pair_of[key]
        got_text = _row_text(out_rows[other])
        got = fold(got_text)
        matcher = difflib.SequenceMatcher(None, want, got, autojunk=False)
        letters_matched += sum(block.size for block in matcher.get_matching_blocks())
        if want == got:
            identical += 1
        else:
            differing.append({
                "page": key[0] + 1, "key_y": key[1], "out_y": other[1],
                "expected": want_text, "got": got_text,
                "similarity": round(matcher.ratio(), 3),
                "diff": _letter_diff(want, got),
            })

    extra = [
        {"page": key[0] + 1, "y": key[1], "got": _row_text(out_rows[key])}
        for key in sorted(out_rows) if key not in taken
    ]

    return {
        "title_not_counted": title,
        "key_rows": len(key_rows),
        "out_rows": len(out_rows),
        "identical": identical,
        "differing": differing,
        "missing": missing,
        "extra": extra,
        "letters_total": letters_total,
        "letters_matched": letters_matched,
    }


def _letter_diff(want: str, got: str) -> list[str]:
    """A compact description of what the output is missing or has invented."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, want, got, autojunk=False).get_opcodes():
        if tag == "delete":
            out.append(f"-{want[i1:i2]}")
        elif tag == "insert":
            out.append(f"+{got[j1:j2]}")
        elif tag == "replace":
            out.append(f"-{want[i1:i2]}/+{got[j1:j2]}")
    return out


# ------------------------------------------------------------------ the run


OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)


def main(only: str | None = None) -> None:
    table = []
    for case in discover_cases():
        if only and only.lower() not in case.name.lower():
            continue
        row = {"case": case.name}
        try:
            run = run_case(case)
            if run.error:
                row["status"] = run.error
                table.append(row)
                print(json.dumps(row))
                continue

            slug = case.name.replace("/", "-").replace(" ", "_")
            path = os.path.join(OUT, f"{slug}.pdf")
            with open(path, "wb") as handle:
                handle.write(run.pdf)

            english_bytes = open(case.score_pdf, "rb").read()
            blank_bytes, _ = blank_mod.strip_lyrics(english_bytes, run.score_doc)
            result = compare(run.pdf, open(case.key_pdf, "rb").read(), blank_bytes)

            row.update({
                "status": "ok",
                "voices": len(run.score_doc.voices),
                "layout_rows_total": len(run.all_rows),
                "english_rows": len(run.english_lines),
                "translated_rows": len(run.translated_lines),
                "pairs_not_ok": sum(1 for p in run.pairs if p.status != "ok"),
                "notes_total": sum(len(v) for v in run.placements.values()),
                "notes_empty": sum(1 for v in run.placements.values() for t in v if not t),
                "key_rows": result["key_rows"],
                "out_rows": result["out_rows"],
                "rows_identical": result["identical"],
                "rows_differing": len(result["differing"]),
                "rows_missing": len(result["missing"]),
                "rows_extra": len(result["extra"]),
                "letters": f"{result['letters_matched']}/{result['letters_total']}",
            })
            with open(os.path.join(OUT, f"{slug}.compare.json"), "w") as handle:
                json.dump(result, handle, indent=1, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            row["status"] = "CRASH"
            row["traceback"] = traceback.format_exc().splitlines()[-3:]
        table.append(row)
        print(json.dumps(row, ensure_ascii=False))

    with open(os.path.join(OUT, "baseline.json"), "w") as handle:
        json.dump(table, handle, indent=1, ensure_ascii=False)

    print()
    print(f"{'case':46} {'rows ok':>9} {'diff':>5} {'miss':>5} {'extra':>6} {'notes empty':>12} {'letters':>14}")
    for row in table:
        if row.get("status") != "ok":
            print(f"{row['case']:46} {row.get('status','?')}")
            continue
        print(f"{row['case']:46} "
              f"{row['rows_identical']:>4}/{row['key_rows']:<4} "
              f"{row['rows_differing']:>5} {row['rows_missing']:>5} {row['rows_extra']:>6} "
              f"{row['notes_empty']:>5}/{row['notes_total']:<6} {row['letters']:>14}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
