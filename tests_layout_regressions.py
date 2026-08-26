"""Regressions found by running the app over the real documents in test-data/.

Each test here pins one defect that was reproduced against a real layout and a
real answer key, and each one fails on v1.0. They read the test corpus but never
write to it.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from smgcore import layout as layout_mod  # noqa: E402
from smgcore import pairing as pairing_mod  # noqa: E402
from smgcore import score as score_mod  # noqa: E402


def _test_data() -> str:
    """The read-only corpus, wherever the repository has been checked out."""
    folder = HERE
    for _ in range(5):
        folder = os.path.dirname(folder)
        candidate = os.path.join(folder, "test-data")
        if os.path.isdir(candidate):
            return candidate
    pytest.skip("test-data/ is not beside this checkout")


def halves(song: str, variant: str):
    """The English and translated halves of one test case, as the app reads them."""
    folder = os.path.join(_test_data(), song, variant)
    score_pdf = layout_pdf = None
    for entry in sorted(os.listdir(folder)):
        low = entry.lower()
        if low.startswith("english score"):
            score_pdf = os.path.join(folder, entry)
        elif low.startswith("syllabus layout"):
            layout_pdf = os.path.join(folder, entry)
    assert score_pdf and layout_pdf, f"{folder} is missing a score or a layout"

    score_doc = score_mod.parse_score(open(score_pdf, "rb").read())
    layout_doc = layout_mod.parse_layout(open(layout_pdf, "rb").read())
    rows = layout_mod.to_editable(layout_doc, {})
    english, translated = layout_mod.split_in_half(
        rows, layout_doc.page_count, score_doc.sung_words()
    )
    assert english and translated, f"{folder}: the layout did not split into two halves"
    return english, translated


def text_of(line) -> str:
    return " ".join(line.tokens)


# --------------------------------------------------------------------------- 1


def test_section_label_does_not_leak_across_the_halves():
    """The translation half starts a fresh reading of the song, not mid-Bridge.

    Section labels are printed down the left margin and carry forward onto the
    rows beneath them. That is right within a half, and wrong across the join:
    the last label of the English half ("Bridge") must not still be in force when
    the translated half's first row is read, because that row is the first line
    of the song, not the bridge.

    By Faith / QII prints its first sung row above the "1" label on both halves,
    so both first rows should carry the same (empty) section.
    """
    english, translated = halves("By Faith", "QII")
    assert english[0].section == translated[0].section, (
        f"English half opens in section {english[0].section!r} but the translated "
        f"half opens in {translated[0].section!r}; the label leaked across the join"
    )


# --------------------------------------------------------------------------- 2


def test_the_opening_line_is_given_its_translation():
    """The first sung line of the song must not be left with nothing opposite it."""
    english, translated = halves("By Faith", "QII")
    pairs = pairing_mod.pair_layouts(english, translated).pairs
    opening = next(
        pair for pair in pairs
        if pair.english_id is not None and pair.english_text.startswith("Faith is a")
    )
    assert opening.translated_id is not None, (
        "'Faith is a living thing.' was paired with nothing, so the whole first "
        "line of the song is dropped from the finished score"
    )
    assert "Ñu-" in opening.translated_text, (
        f"'Faith is a living thing.' was paired with {opening.translated_text!r}, "
        "which is not its translation"
    )


# --------------------------------------------------------------------------- 3


CASES = [
    ("By Faith", "QII"),
    ("Hands Drop Down", "AP"),
    ("By Faith", "WY"),
    ("Hands Drop Down", "EMB-Diphthong"),
    ("Hands Drop Down", "KIM"),
    ("Hands Drop Down", "QUB"),
    ("More Than Sparrows", "EMB (Missing Syllable in Chorus)"),
    ("More Than Sparrows", "WY"),
    ("We Go Preaching", "QII"),
    ("We Go Preaching", "WY"),
]


@pytest.mark.parametrize("song,variant", CASES)
def test_pairing_keeps_both_halves_in_order(song, variant):
    """Rows are read down the page, so a pairing may never run backwards.

    The two halves are the same song written twice. If the nth English row is
    paired with some translated row, every later English row must be paired with
    a later translated row. A pairing that crosses over has put the words of one
    part of the song under the notes of another.
    """
    english, translated = halves(song, variant)
    order_e = {line.id: index for index, line in enumerate(english)}
    order_t = {line.id: index for index, line in enumerate(translated)}
    pairs = pairing_mod.pair_layouts(english, translated).pairs

    matched = [
        (order_e[pair.english_id], order_t[pair.translated_id], pair)
        for pair in pairs
        if pair.english_id is not None and pair.translated_id is not None
    ]
    matched.sort()
    crossings = [
        (a, b) for (_, a, _), (_, b, _) in zip(matched, matched[1:]) if b <= a
    ]
    assert not crossings, (
        f"{len(crossings)} pairing(s) run backwards through the translated half, "
        f"first at translated rows {crossings[0]}"
    )


# --------------------------------------------------------------------------- 4


def test_a_cell_printed_low_stays_in_its_own_row():
    """One printed row is one row, even when a cell is drawn slightly lower.

    In By Faith / QII the translator drew the last cell of the opening row
    ("lla.") about half a cell below the rest of it. Read as a row of its own it
    takes a line the English half has no counterpart for, and everything after it
    is one row out.
    """
    _, translated = halves("By Faith", "QII")
    opening = [line for line in translated if text_of(line).startswith("Ñu-")]
    assert opening, "the translated half does not begin with 'Ñu-'"
    assert text_of(opening[0]).rstrip().endswith("lla."), (
        f"the opening translated row reads {text_of(opening[0])!r}; "
        "'lla.' was split off into a row of its own"
    )
