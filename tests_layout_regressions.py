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
from smgcore import lock as lock_mod  # noqa: E402
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
        elif low.replace(" ", "").startswith(("syllabuslayout", "syllablelayout")):
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

# --------------------------------------------------------------------------- 5


def plan_for(song: str, variant: str):
    """The whole pipeline for one case: which syllables each voice is given."""
    folder = os.path.join(_test_data(), song, variant)
    score_pdf = layout_pdf = None
    for entry in sorted(os.listdir(folder)):
        low = entry.lower()
        if low.startswith("english score"):
            score_pdf = os.path.join(folder, entry)
        elif low.replace(" ", "").startswith(("syllabuslayout", "syllablelayout")):
            layout_pdf = os.path.join(folder, entry)

    score_doc = score_mod.parse_score(open(score_pdf, "rb").read())
    layout_doc = layout_mod.parse_layout(open(layout_pdf, "rb").read())
    rows = layout_mod.to_editable(layout_doc, {})
    english, translated = layout_mod.split_in_half(
        rows, layout_doc.page_count, score_doc.sung_words()
    )
    pairs = pairing_mod.pair_layouts(english, translated).pairs
    english = layout_mod.inherit_pair_tags(english, pairs, translated)
    translation = pairing_mod.translation_map(pairs, translated, {}, english)
    lock = lock_mod.build_lock(english, translation)
    return score_doc, lock_mod.plan_voices(score_doc, lock, list(score_doc.voices))


def test_a_line_wrapping_at_a_system_break_is_read_as_one_line():
    """A one-note fragment belongs to the line it carries on into.

    Hands Drop Down / KIM breaks 'I will not let my hands drop down.' across a
    system: the harmony staves carry 'I' alone at the end of one system and
    'will not let my hands drop down.' at the start of the next. One word cannot
    say which written line it opens - 'I' opens 'I can clear-ly see' just as
    well - so the fragment was taking that other line's first syllable, and the
    syllable it should have had was folded onto the next note as 'Maku'.
    """
    _, plans = plan_for("Hands Drop Down", "KIM")
    plan = plans["Female Harmony 1"]
    fragments = [a for a in plan.assignments if a.english.strip() == "I"]
    assert fragments, "no one-word 'I' fragment on this staff"
    for assignment in fragments:
        assert assignment.tokens == ["Ma"], (
            f"the fragment 'I' was given {assignment.tokens!r}; it opens "
            "'Ma-ku ma-mi ka-nda zo-nda.', the line it carries on into"
        )


def test_a_voice_entering_mid_line_does_not_glue_the_skipped_syllables():
    """A part entering part-way through a row takes the box it enters on.

    On the staves that re-enter at 'will not let my hands drop down.' with no 'I'
    engraved, the skipped 'Ma' is folded onto the next note as 'Maku'. The
    hand-made score prints 'ku' alone there - 'Ma' was already sung on the
    previous system. Folding is right where a part enters inside a word; it is
    wrong where the earlier syllables have already been sung.
    """
    _, plans = plan_for("Hands Drop Down", "KIM")
    glued = [
        (voice, a.score_line_id, token)
        for voice, plan in plans.items()
        for a in plan.assignments for token in a.tokens
        if token.startswith("Maku")
    ]
    assert not glued, f"syllables folded onto one note: {glued[:3]}"


# --------------------------------------------------------------------------- 6


def test_a_doubling_voice_drops_the_words_it_does_not_sing():
    """A part singing a reduced version of the lead line takes box for box.

    By Faith / WY prints 'faith I move a moun-tain.' on the doubling staff where
    the lead sings 'faith I can move a moun-tain.'. Every word the part does sing
    must take the syllable locked to that word, and the box for 'can' must simply
    be passed over - not folded onto a neighbour, and not left to shift every
    syllable after it one note early.
    """
    _, plans = plan_for("By Faith", "WY")
    plan = plans["Male Lead Dbl 1"]
    line = next(
        a for a in plan.assignments
        if a.english.split()[:6] == ["faith", "I", "move", "a", "moun", "tain."]
    )
    # This staff picks the line up at 'faith', the 'By' having been sung at the
    # end of the previous system, so the row is read from 'neerü' onwards.
    assert line.tokens[:6] == ["neerü", "uu-", "kat", "ya-", "la-", "müin;"], (
        f"the doubling staff was given {line.tokens[:6]!r}; 'chi-' belongs to the "
        "'can' it does not sing and should have been passed over"
    )


# --------------------------------------------------------------------------- 7


def test_a_syllable_is_not_printed_twice_running():
    """A fold must not put back the syllable the voice has just sung.

    Where a written row is picked up part-way through, the syllables before the
    entry are folded onto the first note so that a part does not open on the tail
    of a word. When the voice has just sung that syllable on a note of its own,
    folding it on again prints it twice: We Go Preaching / QII sang
    'Shuj- cu- na-' at the end of one system and then set 'na-|pa' as 'napa' on
    the first note of the next, so 'na' appeared on two notes running.
    """
    _, plans = plan_for("We Go Preaching", "QII")
    plan = plans["Female Lead 1"]
    entries = [
        a for a in plan.assignments
        if a.english.strip().startswith("go, there fore")
    ]
    assert entries, "the 'go, therefore,' entry is not on this staff"
    for assignment in entries:
        assert assignment.tokens[0] == "pa", (
            f"the entry was given {assignment.tokens!r}; 'na' was sung on the "
            "previous note and must not be folded on again"
        )


# --------------------------------------------------------------------------- 8


def test_a_phrase_wrapping_at_the_end_of_a_staff_follows_what_comes_next():
    """The last words of a staff belong to the row the voice carries on into.

    More Than Sparrows / WY ends a staff on "You're worth", which opens two
    written rows: 'You're worth more than man-y spar-rows,' and ''Cause you're
    worth more- so much more-'. The next line on that staff sings 'more than man
    y spar- rows,', so it is the first. Preferring a row further ahead in the
    layout used to win instead, putting the wrong two syllables on those notes.
    """
    _, plans = plan_for("More Than Sparrows", "WY")
    plan = plans["Lead 1"]
    wrapped = [
        a for a in plan.assignments if a.english.rstrip().endswith("You're worth")
    ]
    assert wrapped, "no staff ends on \"You're worth\""
    for assignment in wrapped:
        assert assignment.tokens[-2:] == ["Jee", "a-"], (
            f"the staff ended with {assignment.tokens[-2:]!r}; the voice goes on to "
            "sing 'more than man y spar- rows,', so these notes carry 'Jee a-'"
        )


# --------------------------------------------------------------------------- 9


def _lock_from(rows):
    """A small hand-built lock, for the shapes the corpus has no clean case of."""
    lines = []
    for row_id, section, tag, english, translated, classes in rows:
        lines.append(
            lock_mod.LockLine(
                id=row_id,
                section=section,
                tag=tag,
                english=list(english),
                keys=[lock_mod.fold(word) for word in english],
                translated=list(translated),
                semantic=list(classes),
            )
        )
    index, opens, running = {}, [], 0
    for number, line in enumerate(lines):
        opens.append(running)
        running += len(line)
        for position, key in enumerate(line.keys):
            index.setdefault(key, []).append((number, position))
    return lock_mod.Lock(lines=lines, index=index, opens=opens)


# The shape both of these pin: a line the layout writes out once, marked for the
# harmony that answers with it at the end of Chorus 1, which the lead also sings
# at a later repeat the layout does not write again.
SHARED_LINE = ["I", "will", "not", "let", "go."]
SHARED_WORDS = ["Ma-", "ku", "ma-", "mi", "kanda."]


def test_a_harmony_row_is_still_available_to_the_lead_elsewhere():
    """A part tag says who sings the row here, not who may never sing the words.

    A row marked '(Harmonies)' is kept from a lead while any other row can answer
    for the same words - that is what stops an answering phrase being pulled into
    the lead's line. But where the layout writes the line only once, refusing it
    outright leaves the lead's notes silent, and a silent note is worse than a
    row written for the part next door.
    """
    lock = _lock_from([
        (1, "Ch1", "Harmonies", SHARED_LINE, SHARED_WORDS, [""] * 5),
    ])
    found = lock_mod._segment(lock, [lock_mod.fold(w) for w in SHARED_LINE],
                              "Ch3", "Female Lead 1", None, 0)
    assert found is not None, (
        "the lead was refused the only written row carrying the line it sings, "
        "so those notes are left with nothing on them"
    )
    assert found[0] == 0 and len(found[1]) == 5


def test_a_yellow_harmony_box_is_still_available_to_the_lead_elsewhere():
    """Same rule for the other way a layout marks a harmony: yellow cell fill.

    Yellow boxes route word by word rather than row by row, so this one used to
    bar the lead even more firmly - the row was never even offered as a place to
    start.
    """
    lock = _lock_from([
        (1, "Ch1", "", SHARED_LINE, SHARED_WORDS, ["harmony"] * 5),
    ])
    found = lock_mod._segment(lock, [lock_mod.fold(w) for w in SHARED_LINE],
                              "Ch3", "Female Lead 1", None, 0)
    assert found is not None, (
        "the lead was refused every box of the only row carrying its line"
    )
    assert found[0] == 0 and len(found[1]) == 5


def test_the_tag_still_wins_when_another_row_can_answer():
    """The relaxation is a last resort, not a softening of the rule.

    With an untagged row carrying the same English, the lead must take that one
    and leave the harmony's row alone. This is the case the tags exist for, and
    the fallback must not touch it.
    """
    lock = _lock_from([
        (1, "Ch1", "Harmonies", SHARED_LINE, ["A-", "a", "a-", "a", "aa."], [""] * 5),
        (2, "Ch3", "", SHARED_LINE, SHARED_WORDS, [""] * 5),
    ])
    found = lock_mod._segment(lock, [lock_mod.fold(w) for w in SHARED_LINE],
                              "Ch3", "Female Lead 1", None, 0)
    assert found is not None and found[0] == 1, (
        "the lead took the harmony's row while its own was available"
    )


# --------------------------------------------------------------------------- 10


def test_a_row_the_two_languages_disagree_about_is_marked_for_attention():
    """A count disagreement between the halves colours the whole score line.

    Where the translated row does not take the same number of notes as the
    English one it is locked to - and blank boxes cannot account for the
    difference - the pairing reports 'count'. Nothing downstream can tell which
    of those syllables is the one that slipped, so every syllable of every score
    line drawn from that row is marked, not just the ones that came out empty.

    This pins behaviour that already worked; it had never been exercised, because
    no case in the corpus produces a 'count' pair.
    """
    from smgcore.align import Assignment

    assignment = Assignment(
        score_line_id=7, voice="Female Lead 1", page=0, section="Ch1",
        english="I will not let go.", tokens=["Ma-", "ku", "ma-", "mi", "kanda."],
        layout_line_ids=[3], status="ok", note="",
    )
    settled = lock_mod.attention_marks(assignment, assignment.tokens, set(), set())
    assert not any(settled), (
        f"a line nothing is wrong with was marked: {settled!r}"
    )
    marked = lock_mod.attention_marks(assignment, assignment.tokens, set(), {3})
    assert marked == [lock_mod.ATTENTION] * 5, (
        f"the row the halves disagree about was marked {marked!r}; every syllable "
        "on it needs an eye, because the disagreement does not say which one moved"
    )
    settled_one = lock_mod.attention_marks(assignment, assignment.tokens, {(7, 2)}, {3})
    assert settled_one[2] == lock_mod.RESOLVED and settled_one[0] == lock_mod.ATTENTION, (
        "a syllable somebody has settled must stop being red while the rest of the "
        f"line stays red; got {settled_one!r}"
    )


# --------------------------------------------------------------------------- 11


def test_a_syllable_written_against_a_cell_border_stays_in_its_own_box():
    """Two boxes are two notes, even when the PDF hands them back as one word.

    A translator writing `p'un-` in one box and `chay` in the next leaves no gap
    at the border, so the PDF's text extraction returns the single word
    `p'un-chay` with a bounding box straddling both cells. Read as one word it
    lands wholly in whichever cell holds its centre: two syllables are squeezed
    onto one note, the other cell is read as a blank - a held note - and every
    syllable after it in the row lands a note early. Where the centre falls on
    the border itself, both cells used to claim it and the syllable was printed
    twice running.

    Rise Again / QUB writes its Pre-Ch3 line this way throughout.
    """
    _, translated = halves("Rise Again", "QUB")
    rows = [line for line in translated if line.section == "Pre-Ch3"]
    assert rows, "Rise Again / QUB has no Pre-Ch3 row in its translated half"
    row = text_of(rows[0])
    assert row == "Uj p'un- chay wa- ñus- qas kau- sa- ren- qan- ku,", (
        f"the Pre-Ch3 row was read as {row!r}; the layout writes it "
        "'Uj | p'un- | chay | wa- | ñus- | qas | kau- | sa- | ren- | qan- | ku,', "
        "one syllable per box"
    )


def test_a_word_that_merely_overhangs_its_cell_is_not_cut_up():
    """Only a hyphen at the border is evidence of two boxes, not a wide word.

    Characters are not evenly spaced in a proportional font, so where the border
    falls inside a word can only be estimated. A cut made on that estimate alone
    invents a syllable and costs the whole row: Hands Drop Down / AP writes
    'yan' and 'chʼa-' in their own boxes, and splitting either of them puts an
    extra note's worth of text into the line.
    """
    _, translated = halves("Hands Drop Down", "AP")
    words = [token for line in translated for token in line.tokens]
    assert "yan" in words, "'yan' is not read as a syllable of its own"
    for fragment in ("ya", "n"):
        assert fragment not in words, (
            f"{fragment!r} appears as a syllable of its own; a word was cut at a "
            "cell border that runs through it rather than between two boxes"
        )


# --------------------------------------------------------------------------- 12


def test_a_syllable_written_where_the_english_is_blank_goes_on_the_held_note():
    """A blank English box is a note, and the translation may sing on it.

    Rise Again / QUB writes its pre-chorus as

        The | hour |  -   | is  | com- | ing
        Uj  | p'un-| chay | wa- | ñus- | qas

    The English holds 'hour' across the third box, so the engraving prints no
    syllable there for 'chay' to replace. Reading the row as a subsequence - the
    machinery that lets a doubling voice pass over a word it does not sing -
    passed that box over too, and 'chay' was dropped from the score entirely.
    The two cases look alike and are not: a box the English leaves blank is a
    note the voice does sing, and its syllable belongs on the note the English
    holds.
    """
    _, plans = plan_for("Rise Again", "QUB")
    lines = [
        assignment
        for plan in plans.values()
        for assignment in plan.assignments
        if assignment.english.split()[:4] == ["The", "hour", "is", "com"]
    ]
    assert lines, "no staff sings 'The hour is com ing when'"
    for assignment in lines:
        assert assignment.tokens[:2] == ["Uj", "p'un-"], (
            f"the line opens {assignment.tokens[:2]!r}, not on 'Uj p'un-'"
        )
        carried = [text for _, text in assignment.held]
        assert "chay" in carried, (
            f"'chay' was left off the score; the line carries {carried!r} on its "
            "held notes"
        )


# --------------------------------------------------------------------------- 13


def test_the_printed_punctuation_says_which_repeat_is_being_sung():
    """A line repeated with a different stop is a different written row.

    Trust in you / QUB writes 'I trust in you.' twice in the chorus and
    'I trust in you!' at the end of the song. The words are identical once
    folded for comparison, so the closing line was taking a chorus row and the
    voice sang the chorus's syllables at the end of the song. The engraving
    prints 'you!' there, and exactly one written row prints 'you!' too.
    """
    _, plans = plan_for("Trust in you", "QUB")
    closing = [
        assignment
        for plan in plans.values()
        for assignment in plan.assignments
        if assignment.english.strip() == "you! I trust in you!"
    ]
    assert closing, "the score does not end on 'you! I trust in you!'"
    for assignment in closing:
        assert assignment.tokens[1:] == ["wi-", "ñay-", "paj-", "min."], (
            f"the closing line was given {assignment.tokens!r}; the engraving "
            "prints 'you!', and the row written 'trust in you!' reads "
            "'wi- ñay- paj- min.'"
        )


def test_punctuation_does_not_pull_a_voice_off_its_own_part():
    """Which part a row is written for outranks how it is punctuated.

    Hands Drop Down / QUB leaves the full stop off its '(Harmonies)' rows as a
    typing habit while the engraving prints it. Reading punctuation as evidence
    without ranking it below the part tag walked three harmony staves off their
    own rows and onto an untagged one, changing the syllables they sing.
    """
    _, plans = plan_for("Hands Drop Down", "QUB")
    lines = [
        assignment
        for voice, plan in plans.items() if "Harmony" in voice
        for assignment in plan.assignments
        if assignment.english.startswith("clear ly see")
        and "hands drop down" in assignment.english
    ]
    assert lines, "no harmony staff carries 'clear ly see ... hands drop down'"
    for assignment in lines:
        assert assignment.tokens[-2:] == ["a-", "ri."], (
            f"a harmony staff was given {assignment.tokens[-2:]!r}; its own "
            "'(Harmonies)' row reads 'a- ri.', and the missing full stop on that "
            "row is not evidence against it"
        )


# --------------------------------------------------------------------------- 14


def test_taking_the_lyrics_out_does_not_take_noteheads_with_them():
    """Removing the English lyrics must leave every mark of the music behind.

    The no-lyrics score used to be made by redacting each lyric's rectangle, and
    redaction deletes any glyph whose box merely *touches* the area cleared. In
    an engraved score the lyric sits directly under the staff, so noteheads,
    stems, ties and rests went with it - between one and four hundred music
    glyphs per file, on every score in the corpus.
    """
    import collections

    import pymupdf as fitz

    from smgcore import blankscore, score as score_module

    folder = os.path.join(_test_data(), "Rise Again", "QUB")
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    original = open(os.path.join(folder, name), "rb").read()
    score_doc = score_module.parse_score(original)
    blank, removed = blankscore.strip_lyrics(original, score_doc)
    assert removed, "no lyrics were removed at all"

    def glyphs(data: bytes) -> collections.Counter:
        counts: collections.Counter = collections.Counter()
        for page in fitz.open(stream=data, filetype="pdf"):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        counts[span["font"].split("+")[-1]] += len(span["text"].strip())
        return counts

    before, after = glyphs(original), glyphs(blank)
    lyric_font = score_doc.lyric_font[0]
    lost = {
        font: before[font] - after[font]
        for font in before
        if font != lyric_font and before[font] != after[font]
    }
    assert not lost, f"the music lost glyphs when the lyrics were taken out: {lost}"
    assert after[lyric_font] < before[lyric_font], "no lyric glyphs were removed"


# --------------------------------------------------------------------------- 15


def test_a_line_is_set_at_one_size_and_nothing_collides():
    """One size per lyric line, chosen so that no two syllables touch.

    Each syllable used to be sized against the room between its neighbours,
    which measures the wrong thing twice: it is drawn centred on its own note,
    which is not the middle of that room, and the neighbour's own width is never
    counted. So syllables came out at visibly different sizes along one line -
    the long ones tiny - and still ran into each other. We Go Preaching / WY
    printed "shua'a" and "waa'in." 7.4pt on top of one another.
    """
    import collections

    import pymupdf as fitz

    from smgcore import blankscore, lock as lock_module, render as render_module

    score_doc, plans = plan_for("We Go Preaching", "WY")
    folder = os.path.join(_test_data(), "We Go Preaching", "WY")
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    blank, _ = blankscore.strip_lyrics(open(os.path.join(folder, name), "rb").read(),
                                       score_doc)
    placements = {
        assignment.score_line_id: assignment.tokens
        for plan in plans.values() for assignment in plan.assignments
    }
    held = {
        assignment.score_line_id: assignment.held
        for plan in plans.values() for assignment in plan.assignments
        if assignment.held
    }
    pdf = render_module.render(score_doc, blank, placements,
                               render_module.RenderSettings(), held=held)

    rows = collections.defaultdict(list)
    for page in fitz.open(stream=pdf, filetype="pdf"):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["font"] != "LiberationSerif":
                        continue
                    rows[(page.number, round(span["origin"][1], 1))].append(
                        (span["bbox"][0], span["bbox"][2], span["text"], span["size"])
                    )
    assert rows, "the render produced no syllables at all"

    collisions = []
    for key, items in rows.items():
        items.sort()
        for left, right in zip(items, items[1:]):
            if right[0] - left[1] < -0.3:
                collisions.append((left[2], right[2], round(left[1] - right[0], 1)))
    assert not collisions, (
        f"{len(collisions)} pair(s) of syllables run into each other, worst by "
        f"{max(c[2] for c in collisions)}pt: {collisions[:3]}"
    )


# --------------------------------------------------------------------- D21-D24
#
# Four defects Hannah found on the Pre-Chorus of Rise Again, all visible in one
# screenshot: a stray extender line after "nus-", a syllable with no box to
# click, every line set at a different size, and a wait after every keystroke.


def _rendered(song, variant, **kwargs):
    """Render one corpus case at the app's real settings and hand back the layout."""
    import pymupdf as fitz

    from smgcore import blankscore, render as render_module

    score_doc, plans = plan_for(song, variant)
    folder = os.path.join(_test_data(), song, variant)
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    blank, _ = blankscore.strip_lyrics(open(os.path.join(folder, name), "rb").read(),
                                       score_doc)
    placements = {
        assignment.score_line_id: assignment.tokens
        for plan in plans.values() for assignment in plan.assignments
    }
    held = {
        assignment.score_line_id: assignment.held
        for plan in plans.values() for assignment in plan.assignments
        if assignment.held
    }
    settings = render_module.RenderSettings(
        max_size=(score_doc.lyric_font[1] if score_doc.lyric_font else 11.0),
        font_choice=list(render_module.BUNDLED_FONTS)[0],
    )
    layout = []
    pdf = render_module.render(score_doc, blank, placements, settings, held=held,
                               layout_out=layout, **kwargs)
    return score_doc, plans, placements, held, pdf, layout


def test_d21_no_extender_after_a_hyphenated_syllable():
    """An extender says "hold this vowel to here", so a hyphen forbids it.

    Where the layout hyphenates - "nus-" - the word carries on to the next note,
    so the held notes belong to the syllable that follows, and a line drawn there
    tells the singer to hold a vowel the layout has already broken.

    Proved by rendering the same score twice, once as it is and once with those
    hyphens taken off. Only the hyphen differs, so every extender that appears
    in the second render and not the first is one the hyphen was suppressing.
    """
    import pymupdf as fitz

    from smgcore import blankscore, render as render_module

    score_doc, plans = plan_for("Rise Again", "QUB")
    folder = os.path.join(_test_data(), "Rise Again", "QUB")
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    blank, _ = blankscore.strip_lyrics(open(os.path.join(folder, name), "rb").read(),
                                       score_doc)
    placements = {
        assignment.score_line_id: list(assignment.tokens)
        for plan in plans.values() for assignment in plan.assignments
    }
    held = {
        assignment.score_line_id: assignment.held
        for plan in plans.values() for assignment in plan.assignments
        if assignment.held
    }
    settings = render_module.RenderSettings(
        max_size=score_doc.lyric_font[1], font_choice=list(render_module.BUNDLED_FONTS)[0]
    )

    def horizontal_lines(pdf_bytes):
        found = set()
        for page in fitz.open(stream=pdf_bytes, filetype="pdf"):
            for drawing in page.get_drawings():
                for item in drawing["items"]:
                    if item[0] == "l" and abs(item[1].y - item[2].y) < 0.2:
                        found.add((page.number, round(item[1].y, 1),
                                   round(min(item[1].x, item[2].x), 1)))
        return found

    engraved = horizontal_lines(blank)
    as_written = horizontal_lines(
        render_module.render(score_doc, blank, placements, settings, held=held)
    ) - engraved

    # The same score with the hyphen taken off every syllable that owns a held run.
    opened = {line_id: list(tokens) for line_id, tokens in placements.items()}
    hyphens = 0
    for line in score_doc.lines:
        if not line.held_notes or not line.anchors:
            continue
        index = min(max(after for after, _ in line.held_notes), len(line.anchors) - 1)
        tokens = opened.get(line.id) or []
        if index < len(tokens) and tokens[index].strip().endswith("-"):
            tokens[index] = tokens[index].strip().rstrip("-")
            hyphens += 1
    assert hyphens, "no held run in this case follows a hyphen - the test proves nothing"

    without = horizontal_lines(
        render_module.render(score_doc, blank, opened, settings, held=held)
    ) - engraved

    assert without > as_written, (
        "taking the hyphens off changed no extender, so the hyphen is not being read"
    )
    assert not (as_written - without), (
        "the hyphenated render drew an extender the un-hyphenated one did not"
    )


def test_d22_held_note_syllables_can_be_clicked():
    """A syllable on a held note has no anchor, and so used to have no hit area.

    It is drawn on the page like any other, so a person can see it and cannot
    edit it - the exact complaint. The layout the renderer reports has to name
    it, because that is what the preview builds its boxes from.
    """
    _, _, _, held, _, layout = _rendered("Rise Again", "QUB")
    assert held, "this case has no held-note syllables - the test proves nothing"

    reported = [item for item in layout if item["slot"] == "held"]
    expected = sum(1 for seats in held.values() for _, text in seats if (text or "").strip())
    assert len(reported) >= expected, (
        f"{expected} syllables sit on held notes but the layout names {len(reported)}"
    )
    for item in reported:
        assert item["text"], "a held-note syllable was reported with no text"
        assert item["index"] is not None, "a held-note syllable has no place in its run"


def test_d23_one_type_size_for_the_whole_score():
    """Every line at the same size, which is what an engraver does.

    Sizing each line on its own crowding put 6pt lines next to 11pt ones on the
    same page. Crowding is now answered by moving syllables apart instead.
    """
    import collections

    import pymupdf as fitz

    for song, variant in [("Rise Again", "QUB"), ("We Go Preaching", "WY")]:
        score_doc, _, _, _, pdf, layout = _rendered(song, variant)
        target = score_doc.lyric_font[1]
        sizes = collections.Counter(round(item["size"], 2) for item in layout if item["text"])
        assert sizes, f"{song}/{variant} drew nothing"
        commonest, count = sizes.most_common(1)[0]
        assert count / sum(sizes.values()) > 0.98, (
            f"{song}/{variant} is set at {len(sizes)} different sizes: {dict(sizes)}"
        )
        assert abs(commonest - target) < 0.05, (
            f"{song}/{variant} is set at {commonest}pt but the score sets its own "
            f"lyrics at {target}pt"
        )


def test_d23_moving_syllables_apart_still_leaves_no_collision():
    """Uniform size must not be bought with syllables running into each other."""
    import collections

    import pymupdf as fitz

    for song, variant in [("Rise Again", "QUB"), ("We Go Preaching", "WY")]:
        _, _, _, _, pdf, _ = _rendered(song, variant)
        rows = collections.defaultdict(list)
        for page in fitz.open(stream=pdf, filetype="pdf"):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if span["font"] != "LiberationSerif":
                            continue
                        rows[(page.number, round(span["origin"][1], 1))].append(
                            (span["bbox"][0], span["bbox"][2], span["text"])
                        )
        collisions = []
        for items in rows.values():
            items.sort()
            for left, right in zip(items, items[1:]):
                if right[0] - left[1] < -0.3:
                    collisions.append((left[2], right[2]))
        assert not collisions, f"{song}/{variant}: {len(collisions)} collide: {collisions[:3]}"


def test_d23_a_crowded_syllable_moves_rather_than_shrinks():
    """The trade Hannah asked for, stated as a test.

    On the Pre-Chorus line that used to force 6.86pt, the syllables must now be
    at the score's own size and simply sit a little off their notes.
    """
    score_doc, _, placements, _, _, layout = _rendered("Rise Again", "QUB")
    target = score_doc.lyric_font[1]
    by_line = {}
    for item in layout:
        if item["text"]:
            by_line.setdefault(item["line_id"], []).append(item)

    moved = 0
    for line in score_doc.lines:
        items = by_line.get(line.id)
        if not items:
            continue
        assert all(abs(item["size"] - target) < 0.05 for item in items), (
            f"line {line.id} was set smaller than the score's own {target}pt"
        )
        anchors = {round(a.placement_x, 1) for a in line.anchors}
        for item in items:
            if item["slot"] == "english" and round(item["x"], 1) not in anchors:
                moved += 1
    assert moved, "no syllable was moved at all - crowding is not being resolved this way"
