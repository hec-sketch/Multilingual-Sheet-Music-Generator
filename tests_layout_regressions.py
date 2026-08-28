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


def test_d21_no_extender_lines_at_all():
    """No rule is left under a held note - the syllable on it says enough.

    An extender is the line an engraver draws after a syllable to mean "hold
    this vowel onward". Two of them reached the finished score: one the renderer
    drew itself, and one it inherited, because emptying the English lyric text
    leaves the English extenders behind as line art. Several then fell after a
    syllable the translation hyphenates, contradicting the layout outright.
    Hannah asked for all of them gone.

    Checked on the finished score rather than on either step, because that is
    where she saw them: no horizontal rule may sit on any sung line's baseline.
    """
    import pymupdf as fitz

    from smgcore import blankscore, render as render_module

    for song, variant in [("Rise Again", "QUB"), ("By Faith", "QU")]:
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
        assert held, f"{song}/{variant} has no held notes - the test proves nothing"

        settings = render_module.RenderSettings(
            max_size=score_doc.lyric_font[1],
            font_choice=list(render_module.BUNDLED_FONTS)[0],
        )
        pdf = render_module.render(score_doc, blank, placements, settings, held=held)

        on_page = {}
        for line in score_doc.lines:
            on_page.setdefault(line.page, []).append(line)
        offenders = []
        for page in fitz.open(stream=pdf, filetype="pdf"):
            for drawing in page.get_drawings():
                for item in drawing["items"]:
                    if item[0] != "l" or abs(item[1].y - item[2].y) > 0.4:
                        continue
                    x0, x1 = sorted((item[1].x, item[2].x))
                    if x1 - x0 < 6.0:
                        continue
                    for line in on_page.get(page.number, []):
                        if line.y + 2.0 <= item[1].y <= line.y + 12.0:
                            offenders.append((page.number + 1, round(item[1].y, 1),
                                              round(x0, 1), round(x1, 1)))
                            break
        assert not offenders, (
            f"{song}/{variant} still has {len(offenders)} extender(s) on a lyric "
            f"baseline: {offenders[:4]}"
        )


def test_d21_removing_extenders_costs_no_music():
    """The extenders go; nothing else does.

    Clearing the area under a staff is what once deleted several hundred
    noteheads, stems and rests per file. Each extender is covered by its own
    sliver and only what the sliver covers completely is removed, so a slur or a
    stem crossing one survives.
    """
    import pymupdf as fitz

    from smgcore import blankscore, score as score_module
    from smgcore.striptext import strip_font_text

    def glyphs(pdf_bytes):
        total = 0
        for page in fitz.open(stream=pdf_bytes, filetype="pdf"):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        total += len(span["text"].strip())
        return total

    for song, variant in [("Rise Again", "QUB"), ("We Go Preaching", "WY")]:
        folder = os.path.join(_test_data(), song, variant)
        name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
        raw = open(os.path.join(folder, name), "rb").read()
        score_doc = score_module.parse_score(raw)

        doc = fitz.open(stream=raw, filetype="pdf")
        base, size = score_doc.lyric_font
        for page in doc:
            strip_font_text(page, doc, base, size)
        before = doc.tobytes()

        after_doc = fitz.open(stream=before, filetype="pdf")
        removed = blankscore.strip_lyric_extenders(after_doc, score_doc)
        assert removed, f"{song}/{variant} had no extenders - the test proves nothing"

        assert glyphs(after_doc.tobytes()) == glyphs(before), (
            f"{song}/{variant} lost music glyphs while removing {removed} extenders"
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


# ------------------------------------------------------------------------- D26
#
# The score is set in one font, and a font covers the one alphabet it was cut
# for. Asked for a character it does not have, it hands back .notdef - which
# PyMuPDF draws as a nul - so a score in Chinese or Korean came out with every
# syllable silently missing and the notes bare.


def _drawn_on(pdf_bytes) -> set:
    """Every character that actually reached the page."""
    import pymupdf as fitz

    seen: set = set()
    for page in fitz.open(stream=pdf_bytes, filetype="pdf"):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    seen |= set(span["text"])
    return seen


def _render_in(alphabet: str, song="By Faith", variant="QII"):
    """Set one corpus case in a given script, as if that were the translation."""
    from smgcore import blankscore, render as render_module

    score_doc, plans = plan_for(song, variant)
    folder = os.path.join(_test_data(), song, variant)
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    blank, _ = blankscore.strip_lyrics(open(os.path.join(folder, name), "rb").read(),
                                       score_doc)
    placements = {
        assignment.score_line_id: [alphabet[i % len(alphabet)]
                                   for i in range(len(assignment.tokens))]
        for plan in plans.values() for assignment in plan.assignments
    }
    assert placements, "this case placed no syllables at all"
    notes: list[str] = []
    settings = render_module.RenderSettings(max_size=score_doc.lyric_font[1])
    pdf = render_module.render(score_doc, blank, placements, settings,
                               warnings_out=notes)
    return pdf, placements, notes


@pytest.mark.parametrize("script,alphabet", [
    ("Chinese", "信心是活的东西"),
    ("Korean", "믿음은살아있는것"),
    ("Japanese", "しんこうはいきて"),
    ("Russian", "вераживая"),
    ("Greek", "πίστηζωντανή"),
    ("Hebrew", "אמונהחיה"),
    ("Arabic", "الإيمانحي"),
])
def test_d26_a_score_can_be_set_in_a_script_the_chosen_font_lacks(script, alphabet):
    """Every syllable reaches the page, whatever alphabet it is written in.

    Liberation Serif has no Chinese, no Korean, no Arabic. Before the font was
    chosen per syllable, all of these drew as nul characters: the render
    reported hundreds of syllables placed and the printed score had none of
    them on it.
    """
    pdf, placements, notes = _render_in(alphabet)
    wanted = {ch for tokens in placements.values() for token in tokens for ch in token}
    missing = wanted - _drawn_on(pdf)
    assert not missing, (
        f"{script}: {''.join(sorted(missing))} never reached the page - the font "
        "chain has no font that can draw them"
    )
    assert not notes, f"{script} should need no warning, but got {notes}"


def test_d26_a_script_no_font_can_draw_is_reported_rather_than_left_blank():
    """Printing gaps and saying nothing is the one outcome that must not happen.

    Nothing available draws Thai. That is a fair thing for the app not to do;
    it is not a fair thing for it to do quietly, because the score looks
    finished and is not.
    """
    _, _, notes = _render_in("ศรัทธามีชีวิต")
    assert notes, "a script with no font at all was rendered without a word about it"
    assert "fonts" in notes[0], f"the warning does not say how to fix it: {notes[0]}"


def test_d26_a_latin_score_is_still_set_in_the_chosen_font():
    """The fallbacks are for what the chosen font cannot draw, and nothing else.

    Every corpus case is written in a Latin alphabet, so every syllable must
    still be set in the font that was picked - not quietly moved to a fallback
    that happens to cover it too.
    """
    import collections

    import pymupdf as fitz

    from smgcore import blankscore, render as render_module

    score_doc, plans = plan_for("By Faith", "WY")
    folder = os.path.join(_test_data(), "By Faith", "WY")
    name = next(e for e in os.listdir(folder) if e.lower().startswith("english score"))
    blank, _ = blankscore.strip_lyrics(open(os.path.join(folder, name), "rb").read(),
                                       score_doc)
    placements = {
        assignment.score_line_id: assignment.tokens
        for plan in plans.values() for assignment in plan.assignments
    }
    notes: list[str] = []
    pdf = render_module.render(score_doc, blank, placements,
                               render_module.RenderSettings(), warnings_out=notes)
    assert not notes, f"a Latin score reported a font shortfall: {notes}"

    fonts = collections.Counter()
    for page in fitz.open(stream=pdf, filetype="pdf"):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        fonts[span["font"].split("+")[-1]] += 1
    assert fonts["LiberationSerif"], "nothing was set in the chosen font at all"


def test_d26_a_one_character_syllable_is_not_thrown_away_as_a_mis_key():
    """A lone consonant is a slip; a lone Chinese character is a whole syllable.

    A single letter that cannot be sung is dropped, because left in it takes a
    note of its own and pushes the real syllable off the end of the line. That
    reasoning is about an alphabet spelling a syllable out of consonants and
    vowels, and it was being applied to every script - so every one-character
    Chinese, Korean or Japanese syllable was silently deleted, along with every
    Cyrillic and Greek letter, whose vowels are not in the Latin vowel list
    either.
    """
    from smgcore.layout import _is_stray_letter

    for slip in ("b", "k", "j", "Z"):
        assert _is_stray_letter(slip), f"{slip!r} is a bare consonant and cannot be sung"
    for syllable in ("信", "믿", "し", "в", "а", "π", "א", "a", "e"):
        assert not _is_stray_letter(syllable), (
            f"{syllable!r} was thrown away as a mis-key; it is a syllable"
        )


@pytest.mark.parametrize("song,variant", CASES)
def test_d24_no_row_holds_the_same_word_twice_over(song, variant):
    """No cell anywhere in the corpus ends up holding one word written twice.

    A cell is one note. Two words in it is the signature of a row that was read
    from both the page and its annotation, and it is worth watching across every
    case rather than only the one that showed it.
    """
    _, translated = halves(song, variant)
    for line in translated:
        for token in line.tokens:
            parts = token.split()
            assert not (len(parts) == 2 and parts[0] == parts[1]), (
                f"{song}/{variant}: cell {token!r} holds the same word twice"
            )


# ---------------------------------------------------------------------------
# D27 - an accent written as a combining mark must land on its own letter.
#
# Nothing in this app shapes text. A combining mark is a zero-width glyph drawn
# wherever the pen has reached, so after a wide letter such as `ʉ` it falls over
# the *next* letter: `mʉ̃a` printed as `mʉã`, and `naʉ̃-ta` put the tilde on the
# hyphen. It round-trips as the right characters, so no text comparison could
# see it - only the printed page shows it. `ʉ` is written 352 times and the
# combining tilde 156 times across the answer keys, so this is the orthography
# of a real translation, not an edge case.
# ---------------------------------------------------------------------------


def _ink_span(text, size=60):
    """The horizontal extent of the ink a string actually puts on the page."""
    import pymupdf as fitz

    from smgcore.render import FontChain

    chain = FontChain("Serif (matches most scores)")
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    shape = page.new_shape()
    chain.draw(shape, page, (60.0, 130.0), text, size, (0.0, 0.0, 0.0))
    shape.commit()
    pix = page.get_pixmap(colorspace=fitz.csGRAY)
    data, wide = pix.samples, pix.width
    left = right = None
    for row in range(pix.height):
        line = data[row * wide:(row + 1) * wide]
        for column, value in enumerate(line):
            if value < 250:
                left = column if left is None else min(left, column)
                right = column if right is None else max(right, column)
    assert left is not None, f"{text!r} drew nothing at all"
    return left, right


def test_d27_a_combining_mark_sits_on_its_own_letter():
    """The tilde of `ʉ̃` must sit over the ʉ, not to the right of it."""
    bare = _ink_span("ʉ")
    marked = _ink_span("ʉ̃")
    # The mark is written above the letter, so it may make the ink taller but it
    # must not push it sideways: same left edge, and no wider to the right than
    # the letter itself plus a hair.
    assert abs(marked[0] - bare[0]) <= 2, (
        f"the mark moved the left edge: bare {bare}, marked {marked}"
    )
    assert marked[1] <= bare[1] + 2, (
        f"the mark was drawn to the right of its letter: bare {bare}, marked {marked}"
    )


def test_d27_a_combining_mark_does_not_land_on_the_next_letter():
    """`mʉ̃a` must not print as `mʉã`."""
    plain = _ink_span("mʉa")
    marked = _ink_span("mʉ̃a")
    assert marked[1] <= plain[1] + 2, (
        f"the mark widened the word, so it fell past its letter: {plain} -> {marked}"
    )


def test_d27_an_accent_is_written_as_the_single_letter_the_font_has():
    """`a` + combining tilde and `ã` are the same letter and must print alike."""
    from smgcore.render import compose

    assert compose("ã") == "ã"
    assert _ink_span("ã") == _ink_span("ã")


def test_d27_a_letter_with_no_single_form_is_still_measured_as_one():
    """`ʉ̃` has no precomposed character, so it must measure as the letter alone.

    The type size for the whole score comes from these widths; a mark counted as
    a character of its own would make every line carrying one measure too wide.
    """
    from smgcore.render import FontChain

    chain = FontChain("Serif (matches most scores)")
    assert chain.width("mʉ̃a", 11.0) == pytest.approx(chain.width("mʉa", 11.0))


def test_d27_the_corpus_orthography_is_all_drawable():
    """Every accented letter the answer keys use can be drawn, and drawn alone."""
    from smgcore.render import FontChain

    chain = FontChain("Serif (matches most scores)")
    for syllable in ["bʉ", "mʉ̃", "jẽ", "krĩ", "kʼã", "tẽã", "wũã", "duʼ",
                     "Maʉ̃ʉ̃", "ñ", "á", "í", "ú", "ó", "ü", "õ", "¿", "’"]:
        assert chain.face_for(syllable) is not None, f"{syllable!r} has no font"
    assert not chain.shortfall(), (
        f"the corpus orthography reported a shortfall: {chain.shortfall()}"
    )



# ------------------------------------------------------------------------- D31
#
# The first system of a score is indented, to leave room for the voice names
# written out in full beside it. Every system below it starts further left.
#
# Staff labels were read against one margin measured across the whole page,
# which is the narrowest of those - so the first system's own labels, printed in
# the gap between the two, fell on the wrong side of it and were thrown away.
# That staff came through unnamed and was called `Voice 1`, taking the first
# line of the song with it, and the real voice's plan began on the second line
# of the score: what Step 4 showed as a song starting in the middle of its own
# first sentence.
# ---------------------------------------------------------------------------


def test_d31_an_indented_first_system_is_named_from_its_own_margin():
    """A staff set further right than the rest still gets to keep its name."""
    path = os.path.join(_test_data(), "More Than Sparrows", "WY",
                        "English Score_jwb-140_More Than Sparrows_Full Score.PDF")
    score_doc = score_mod.parse_score(open(path, "rb").read())

    first = min((s for s in score_doc.staves if s.page == 0), key=lambda s: s.top)
    rest = [s for s in score_doc.staves if s.page == 0 and s is not first]
    assert first.x0 > min(s.x0 for s in rest) + 10, (
        "this score no longer indents its first system, so it cannot pin this defect"
    )
    assert first.label_raw, "the indented first staff was read as having no name"
    assert not first.voice.startswith("Voice "), (
        f"the indented first staff was labelled {first.voice!r} instead of being named"
    )


def test_d31_the_song_starts_on_the_score_s_first_line():
    """The voice that sings the first line is given the first line."""
    score_doc, plans = plan_for("More Than Sparrows", "WY")
    assert not any(v.startswith("Voice ") for v in plans), (
        f"a staff was left unnamed and took a line of the song with it: {list(plans)}"
    )
    first_line = min(score_doc.lines, key=lambda line: line.id)
    lead = plans["Lead 1"]
    assert lead.assignments[0].score_line_id == first_line.id, (
        "Lead 1 starts at score line "
        f"{lead.assignments[0].score_line_id}, not the score's own first line"
    )
    assert first_line.text.startswith("From"), first_line.text


# ------------------------------------------------------------------------- D32
#
# A score is written for the lead and for the parts that answer it. The layout
# marks an answering part's words as theirs - a yellow cell fill, or a marker
# beside the row - and those words must never be handed to the lead.
#
# Harmonies were the only part that rule was written for. Ad libs and backing
# vocals are written and marked identically and every rule about a harmony is a
# rule about them, but each site spelt out its own list and they had drifted:
# a row marked BGV or Backing fell past every branch of the tag test and was
# offered to the lead, and a row marked "(Ad Libs)" was offered *only* to a
# voice whose name contained "ad lib" with a space in it - so not to a harmony,
# and not to a voice spelling itself "Adlib".
# ---------------------------------------------------------------------------


SUPPORT_TAGS = ["Harmonies", "Harmony", "(Ad Libs)", "Ad Lib", "BGV", "Backing Vocals"]


def test_d32_no_part_that_answers_the_lead_is_offered_to_the_lead():
    """The one hard rule, and it must hold for every way of naming the part."""
    from smgcore.align import _tag_fits

    for tag in SUPPORT_TAGS:
        assert not _tag_fits(tag, "Male Lead 1"), (
            f"a row marked {tag!r} was offered to the lead"
        )


def test_d32_every_answering_part_is_offered_every_answering_row():
    """Harmony, ad lib and backing are one case, so they all reach one another."""
    from smgcore.align import _tag_fits

    for tag in SUPPORT_TAGS:
        for voice in ["Male Harmony 1", "Male Ad Libs 2", "Bgv 1", "Alto 1"]:
            assert _tag_fits(tag, voice), f"{tag!r} was refused to {voice!r}"


def test_d32_the_leads_own_ad_libs_stay_with_the_lead():
    """A name that says Lead says which singer it is, whatever it says after.

    By Faith writes `Male Lead Adlib 1` for the lead improvising over their own
    line; it sings the lead's words, and reading the word `Adlib` as a part of
    its own put the yellow Bridge row on it and cost that case a row.
    `Male Ad Libs 1`, with no lead in the name, is a part of its own.
    """
    from smgcore.textutil import is_lead_part, is_support_part

    assert is_lead_part("Male Lead Adlib 1")
    assert is_lead_part("Male Lead Dbl 1")
    assert is_lead_part("Male Lead 1")
    for name in ["Male Ad Libs 1", "Bgv 1", "Backing Vocal 2", "Male Harmony 1", "Alto 1"]:
        assert is_support_part(name), f"{name!r} was read as carrying the tune"


def test_d32_a_marker_beside_a_row_is_read_for_every_part():
    """The layout may name the part in the margin instead of colouring the cells."""
    from smgcore.layout import HARMONY_MARKER
    from smgcore.textutil import names_support_part

    for marker in ["(Harmonies)", "Harmony", "(Ad Libs)", "Ad Libs", "BGV",
                   "(Backing Vocals)", "Armonías"]:
        assert HARMONY_MARKER.match(marker), f"{marker!r} was not read as a part marker"
        assert names_support_part(marker), f"{marker!r} did not name a part"


def test_d32_ordinary_words_are_not_read_as_a_part_marker():
    """The corpus sings 'all', 'call' and 'small'; none of them names a part."""
    from smgcore.textutil import names_support_part

    for word in ["all", "call", "small", "fall.", "Chorus 1", "Bridge", "", "lead me"]:
        assert not names_support_part(word), f"{word!r} was read as naming a part"


def test_d32_a_row_marked_for_an_answering_part_is_kept_off_a_lead_row():
    """Pairing's hard constraint holds for every part, not only harmony."""
    from smgcore.pairing import _is_harmony_row

    class Row:
        def __init__(self, tag):
            self.tag = tag

    for tag in SUPPORT_TAGS:
        assert _is_harmony_row(Row(tag)), f"a row tagged {tag!r} was left unprotected"
    for tag in ["", "Lead", "Chorus 1"]:
        assert not _is_harmony_row(Row(tag))


def test_d32_a_mixed_row_splits_the_same_way_for_every_answering_part():
    """Yellow boxes to the parts that answer, the rest to the lead."""
    from smgcore.lock import LockLine, _eligible_positions

    line = LockLine(
        id=0, section="", tag="", english=["a", "b", "c", "d"],
        keys=["a", "b", "c", "d"], translated=["1", "2", "3", "4"],
        semantic=["", "harmony", "", "harmony"],
    )
    assert _eligible_positions(line, "Male Lead 1") == [0, 2]
    for voice in ["Male Harmony 1", "Male Ad Libs 2", "Bgv 1", "Backing Vocal 1"]:
        assert _eligible_positions(line, voice) == [1, 3], (
            f"{voice!r} was not given the boxes marked for the answering parts"
        )


# ------------------------------------------------------------------------- D33
#
# Open Your Hand / QUB, Female Lead, page 3. The score engraves the line as
# `... with grat ti tude.` and the layout writes it `... with grat- i- tude.` -
# fourteen notes against fourteen boxes, the same line, one English word split
# a syllable differently by the two documents.
#
# The reading that accounts for all fourteen was displaced by one that stopped
# at the disagreement, because a reading was judged on how well it agreed with
# what it had got through rather than on how much it accounted for. Twelve boxes
# matched flawlessly beat thirteen of fourteen, so twelve notes were set, `pa-`
# was never sung, and its note came out empty in the middle of a row that
# matched the line box for box.
# ---------------------------------------------------------------------------


def test_d33_a_row_that_answers_the_whole_line_is_not_displaced_by_a_shorter_read():
    """Fourteen boxes against fourteen notes: every one of them carries a syllable."""
    _, plans = plan_for("Open Your Hand", "QUB")
    lead = plans["Female Lead 1"]
    line = next(
        (a for a in lead.assignments if a.english.startswith("The way you do not hold back")),
        None,
    )
    assert line is not None, "the line is no longer in this score"
    assert len(line.tokens) == 14, f"expected 14 notes, got {len(line.tokens)}"
    assert all(line.tokens), (
        "a note was left empty on a row that matches the line box for box: "
        + " ".join(t or "·" for t in line.tokens)
    )
    assert line.tokens[-3:] == ["ya-", "na-", "pa-"] or "pa-" in line.tokens, (
        f"'pa-' was dropped: {' '.join(line.tokens)}"
    )


def test_d33_a_line_carrying_on_into_another_row_still_stops_where_it_should():
    """The guard is narrow: stopping early is how a wrapped line is read at all.

    Open Your Hand's Harmony 1 reads its opening from one row and carries on into
    two more. An earlier attempt at the fix above forbade a reading from stopping
    short at all, which pushed this line onto the wrong rows and put `Dios.` on it
    twice.
    """
    _, plans = plan_for("Open Your Hand", "QUB")
    line = next(
        (a for a in plans["Harmony 1"].assignments
         if a.english.startswith("I want to im it ate")),
        None,
    )
    assert line is not None, "the line is no longer in this score"
    placed = " ".join(line.tokens)
    assert "k'a- cha" in placed, f"the harmony took the wrong row: {placed}"
    assert placed.count("Dios.") <= 1, f"a syllable was set twice over: {placed}"
