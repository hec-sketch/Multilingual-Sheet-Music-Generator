"""Lock the English to the translation, then swap the words in the score.

The syllable layout already answers the only genuinely hard question in this
job: which translated syllable belongs under which English one. The translator
answered it by writing the two languages in the same columns, syllable above
syllable. Nothing else in either document knows that.

So the layout is read once into locked lines:

    English syllable  ->  translated syllable

Setting the score is then not a matching problem between two documents. Each
voice's line of English is read off the engraving, found among the locked lines,
and its syllables are swapped for the translated ones opposite them. A word the
lead changes changes wherever else it is sung, because it is the same locked
line that answers for every voice.

A line of the score does not always sit inside one written line: it can begin in
the middle of one where a harmony enters late, and it can run past the end of
one where the engraving breaks a sentence across two systems. So a line is
placed as a chain of segments, each segment the best-agreeing stretch of some
written line, taken in the order the score sings them.

This replaces an older design that aligned the two documents word by word, with
a section map, a repeat timeline and a chain of repairs on top. That design had
to be right about many things at once; this one has to be right about one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .align import Assignment, VoicePlan, _tag_fits, normalize_section
from .layout import BLANK_BOX
from . import textutil
from .textutil import fold

# How much of a stretch has to be recognised before its translation is trusted.
# Below this the notes are left for you rather than guessed at.
MIN_AGREEMENT = 0.6
# A syllable folded onto the note after it, so a part entering in the middle of a
# word does not open on the tail of one. One is what a translator will accept.
MAX_FOLDED_SYLLABLES = 1
# A syllable this voice has already sung is not folded onto a later note as well:
# it was printed on its own note, and folding it on again both repeats the word
# and leaves that note carrying something else.
HYPHENS = ("-", "‐", "‑", "–")

SECTION_BONUS = 0.75      # the written line is labelled with the section being sung
VOICE_TAG_BONUS = 2.5     # it is labelled for the part now being set
WRONG_VOICE_TAG = -3.0    # it is labelled for a different part
CONTINUES_BONUS = 1.5     # it carries straight on from the line just sung
# What the voice sings *next* is the strongest evidence there is for which
# written row a wrapped phrase belongs to, and it has to outweigh the mere
# preference for a row further ahead in the layout (FORWARD_BONUS below). A
# staff ending on "You're worth" opens both 'You're worth more than man-y
# spar-rows,' and ''Cause you're worth more- so much more-'; only the next line
# on that staff says which. Anything above about 4.0 settles it; 5.0 and 6.5
# score identically across the corpus.
CONTINUATION_BONUS = 5.0
LOOKAHEAD_WORDS = 4       # how much of the next line on the staff to look at
NEAR_WORD = 0.6           # one spelling is the opening of the other
FORWARD_BONUS = 2.0       # it is still to come, rather than already sung
AHEAD_COST = 0.004        # ... and the nearer it is, the likelier it is
BACKWARD_COST = -1.0      # it is behind where this voice has reached
# What a word the written row does *not* agree with costs, against the words it
# does. Without this, how much English a stretch accounts for is a plain count,
# so a long stretch that disagrees in places beats a short one that is exactly
# right: the score singing 'so much more- than man-y spar-rows.' took the whole
# of 'You're worth more than man-y spar-rows,' rather than the tail of ''Cause
# you're worth more- so much more-' and the line after it, which is what it sings.
MISMATCH_COST = 3.0  # 2.0 works nearly as well; below 2.0 nothing changes
# How many written boxes in a row may be passed over between two the score does
# sing. A doubling part drops a word here and there ('faith I move a moun-tain.'
# against the lead's 'faith I can move a moun-tain.'); it does not drop half the
# line. Two keeps it to the omissions a doubling really makes.
MAX_SKIPPED_BOXES = 1
# and how much of the line such a reading has to account for before it is
# believed: fewer boxes than this, or less than this share of what reading
# straight through would cover, and it is not a doubling but a wrong row.
# The layout writes the English with the punctuation the engraving prints, and
# a repeated line is very often repeated with different punctuation - 'trust in
# you.' in the chorus, 'trust in you!' at the end of the song. Where two written
# rows carry the same words, that is real evidence about which one is being sung,
# and folding the words for comparison throws it away.
PUNCTUATION_AGREES = 1.0
SUBSEQUENCE_MINIMUM = 3
SUBSEQUENCE_SHARE = 0.75


@dataclass
class LockLine:
    """One written line: its English, and the translation locked to it."""

    id: int
    section: str
    tag: str                            # 'Harmonies', 'Lead', ... if the layout says
    english: list[str]                  # as printed
    keys: list[str]                     # folded, for comparison
    translated: list[str]
    semantic: list[str] = field(default_factory=list)
    # Syllables the translation sings that the English prints no syllable for.
    # They belong on notes the English holds a vowel across.
    spare: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.keys)


@dataclass
class Lock:
    lines: list[LockLine]
    index: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # Where each written line begins if the whole layout is read as one run of
    # syllables, so 'how far ahead is this' has an answer.
    opens: list[int] = field(default_factory=list)

    def flat(self, number: int, offset: int) -> int:
        return self.opens[number] + offset


def build_lock(english_lines, translation: dict[int, list[str]]) -> Lock:
    """Read the layout into locked lines, in the order they are written."""
    lines: list[LockLine] = []
    for line in english_lines:
        translated = translation.get(line.id)
        if not translated:
            continue
        english = list(line.tokens)
        paired = min(len(english), len(translated))
        if not paired:
            continue
        lines.append(
            LockLine(
                id=line.id,
                section=line.section or "",
                tag=getattr(line, "tag", "") or "",
                english=english[:paired],
                keys=[fold(word) for word in english[:paired]],
                translated=list(translated[:paired]),
                semantic=list(getattr(line, "token_classes", [""] * paired))[:paired],
                spare=list(translated[paired:]),
            )
        )

    index: dict[str, list[tuple[int, int]]] = {}
    opens: list[int] = []
    running = 0
    for number, line in enumerate(lines):
        opens.append(running)
        running += len(line)
        for position, key in enumerate(line.keys):
            index.setdefault(key, []).append((number, position))
    return Lock(lines=lines, index=index, opens=opens)


def _agrees(written: str, sung: str) -> float:
    """How far a box's English agrees with the English printed on the note.

    Both sides are English - the layout's box against the engraving's syllable -
    so the elision apostrophe in a word like `ev'ry` is punctuation inside the
    word and not part of either syllable. The two documents put the syllable
    break on opposite sides of it: the engraving prints `ev'` and `ry`, the
    layout writes `ev-` and `'ry-`. Read letter for letter, `'ry` and `ry` do not
    agree at all, which was enough to throw away the whole three-note stretch
    they sit in - the notes came out empty and `do-` and `nan-` went unsung, over
    an apostrophe.
    """
    if not sung:
        return 1.0  # the engraving printed nothing here to disagree with
    if written == sung:
        return 1.0
    bare_written, bare_sung = written.strip("'"), sung.strip("'")
    if bare_written and bare_written == bare_sung:
        return 1.0
    if bare_written and (bare_written.startswith(bare_sung) or bare_sung.startswith(bare_written)):
        return NEAR_WORD
    return 0.0


def _engraved_as_one(line: LockLine, first: int, second: int, word: str) -> bool:
    """Whether the engraving set two of the row's boxes as a single printed word.

    A translator writes one box per syllable, because that is what a syllable
    layout is for. An engraver sets the lyric under the notes, and where two
    syllables fall on a slur the lyric is often typeset as the whole word once:
    Open Your Hand prints `Showing` under two notes where the layout properly
    writes `show-` and `ing`. Neither document is wrong; they are counting
    different things.

    Both boxes must carry English of their own, and the first must end in the
    hyphen with which a translator writes a syllable that runs on into the next.
    A blank box carries no English at all and folds to nothing, so without that
    first condition it joined with whatever followed it and matched: `-` and `en`
    read as the score's `en`, and the held note it stands for was swallowed.
    """
    if second >= len(line.keys):
        return False
    head, tail = line.keys[first], line.keys[second]
    if not head or not tail:
        return False
    if not line.english[first].rstrip().endswith(HYPHENS):
        return False
    return head + tail == word


def _straight_read(line: LockLine, run: list[int], wanted: list[str]):
    """Read a row against the notes one for one, and what it is worth.

    One box to one note, in order - except where the engraving set two boxes as
    one word. There the two boxes answer for one note together: the first takes
    it, and the second is passed over here and put on the note the score holds
    under that word, so that everything after it stays on its own note instead of
    sliding along by one.

    Merging is allowed only where the row has more boxes than the stretch has
    notes. That is the very situation a merged engraving creates - one printed
    word covering two of the layout's syllables leaves the row one box longer
    than the line - and confining it there keeps the reading honest: where the
    row is no longer than the notes, every box has a note of its own to go to
    and joining two of them could only take one away.

    Returns the box for each note, and how much of the English they agreed with.
    """
    places: list[int] = []
    hits = 0.0
    box = 0
    may_merge = len(run) > len(wanted)
    for word in wanted:
        if box >= len(run):
            break
        agreed = _agrees(line.keys[run[box]], word)
        if agreed < 1.0 and may_merge and box + 1 < len(run) and _engraved_as_one(
            line, run[box], run[box + 1], word
        ):
            places.append(run[box])
            hits += 1.0
            box += 2
            continue
        places.append(run[box])
        hits += agreed
        box += 1
    return places, hits


def _eligible_positions(line: LockLine, voice: str, relaxed: bool = False) -> list[int]:
    """Token-level routing for mixed rows.

    If a written row contains any yellow boxes, the lead may use only the boxes
    that are not yellow and the parts answering it may use only the yellow ones.
    Rows without yellow boxes keep the normal voice-routing behavior.

    Which side a voice falls on is settled by `textutil.is_lead_part`, so a
    harmony, an ad lib and a backing vocal are all read the same way - they are
    the same thing to a layout, and were only ever different here because this
    test spelt out a list of its own. It also has to be a test rather than a
    substring: a score routinely names an ad lib after the lead it answers, and
    reading `Male Lead Adlib 1` as the lead gave it the lead's words and left
    its own unsung.

    The routing says which part sings those words *here*. It does not say the
    words are barred from the other part everywhere in the song: a line written
    once, in yellow, as the harmony's answer at the end of one chorus may well be
    sung by the lead at another repeat the layout does not write out again. So
    ``relaxed`` drops the routing, for the second pass in ``_segment`` that is
    made only when respecting it would leave the notes with nothing at all.
    """
    if relaxed:
        return list(range(len(line.keys)))
    sem = line.semantic if len(line.semantic) == len(line.keys) else [""] * len(line.keys)
    if not any(c == "harmony" for c in sem):
        return list(range(len(line.keys)))
    if textutil.is_lead_part(voice):
        return [i for i, c in enumerate(sem) if c != "harmony"]
    return [i for i, c in enumerate(sem) if c == "harmony"]


def _tail_stop(text: str) -> str:
    """The sentence punctuation a printed word ends with, if any.

    A hyphen is a syllable break rather than punctuation, and is ignored; so is
    anything before the last alphanumeric character.
    """
    tail = ""
    for char in reversed(text or ""):
        if char.isalnum():
            break
        if char in ".,;:!?":
            tail = char + tail
    return tail


def _stops_agree(line: LockLine, places: list[int], printed: list[str]) -> float:
    """How much the punctuation of a written row and of the engraving agree.

    Only the stop that ends the written row is read. That is where a repeat
    differs from the line it repeats - 'trust in you.' in the chorus against
    'trust in you!' at the end of the song - and it is the one place the
    engraving and the layout can be relied on to agree. Punctuation inside a
    line is set to taste by whoever typed each document, and reading it there
    costs more rows than it wins.

    Only agreement counts. A disagreement is not evidence against a row: an
    engraver may punctuate a repeat differently, or not at all.
    """
    if not places or not printed:
        return 0.0
    last = min(len(places), len(printed)) - 1
    position = places[last]
    if position != len(line.english) - 1:
        return 0.0  # this stretch does not reach the end of the written row
    written = _tail_stop(line.english[position])
    return 1.0 if written and written == _tail_stop(printed[last]) else 0.0


def _segment(lock: Lock, wanted: list[str], section: str, voice: str,
             after: int | None, floor: int, following: list[str] | None = None,
             printed: list[str] | None = None):
    """The best stretch of a written line for the front of what is still to sing.

    ``following`` is the opening of what this same voice sings next, used only to
    tell apart written lines that the words in hand cannot.

    Returns (line number, offset into it, how many syllables it answers for).
    """
    if not lock.lines or not wanted:
        return None

    wanted_section = normalize_section(section)

    def openings(relaxed: bool) -> set[tuple[int, int]]:
        starts: set[tuple[int, int]] = set()
        for lead in range(min(4, len(wanted))):
            for number, position in lock.index.get(wanted[lead], ()):
                eligible = _eligible_positions(lock.lines[number], voice, relaxed)
                if position not in eligible:
                    continue
                start = position - lead
                if start < 0:
                    continue
                # The matched stretch must stay inside one contiguous semantic run.
                if all(start + k in eligible for k in range(lead + 1)):
                    starts.add((number, start))
        if after is not None and after + 1 < len(lock.lines):
            eligible = _eligible_positions(lock.lines[after + 1], voice, relaxed)
            if eligible:
                starts.add((after + 1, eligible[0]))  # carry on within this voice's stream
        return starts

    def search(allow_tagged: bool):
        best = None
        best_key = None
        candidates = sorted(openings(allow_tagged))
        # Which part a row is written for outranks how it is punctuated. Layouts
        # routinely leave the full stop off their harmony rows as a typing habit
        # while the engraving prints it, so where this voice has a row written
        # for it among the candidates, punctuation is not allowed to pull it onto
        # an untagged row instead. It is there to separate repeats of the same
        # line, not to decide which part is singing.
        own_row = any(
            lock.lines[number].tag and _tag_fits(lock.lines[number].tag, voice)
            for number, _ in candidates
        )
        for number, offset in candidates:
            line = lock.lines[number]
            # Explicit part tags route a row to the part it is written for: a
            # harmony-only row (yellow boxes or "(Harmonies)") is not offered to a
            # lead, and a lead-only row is not consumed by a harmony. This is what
            # keeps a two-syllable answering phrase out of the lead's line when the
            # English words happen to be identical.
            #
            # It is a rule about *this row*, though, not about the words on it. The
            # last line of a chorus can be written once, tagged for the harmony that
            # answers it there, and still be sung by the lead somewhere else in the
            # song. So the tag only rules a row out while some other row can answer
            # for the same words - which is what ``allow_tagged`` is for below: a
            # second pass, made only when the first found nothing at all, where a row
            # written for another part is better than leaving the notes silent.
            if line.tag and not _tag_fits(line.tag, voice) and not allow_tagged:
                continue
            eligible = _eligible_positions(line, voice, allow_tagged)
            if offset not in eligible:
                continue
            run = [offset]
            while run[-1] + 1 in eligible:
                run.append(run[-1] + 1)
            places, hits = _straight_read(line, run, wanted)
            span = len(places)
            if span <= 0:
                continue

            # A doubling part sings the lead's line with words left out - the score
            # prints 'faith I move a moun-tain.' where the lead sings 'faith I can
            # move a moun-tain.'. Read straight through, the written row disagrees
            # from the first omission onwards and every syllable after it lands a note
            # early. Read as a subsequence, each word the part does sing takes the box
            # locked to it and the box for the word it does not sing is passed over,
            # which is what the hand-made scores do. The two readings are compared on
            # what they are worth, not on how far they reach: reading straight through
            # always reaches further, and that is exactly the mistake.
            skipped = _subsequence(line, wanted, run)
            if skipped is not None:
                boxes, agreed = skipped
                worth = hits - MISMATCH_COST * (span - hits)
                # Only a reading where every word this part sings really is the word
                # written in the box it takes, and which accounts for most of the
                # line, is a doubling. A loose fit that agrees here and there is just
                # a wrong row found a different way.
                # Where a written row answers the whole of a line box for box,
                # that is the reading, and nothing shorter may displace it.
                #
                # `_subsequence` stops at the first word it cannot place and was
                # judged on how far it had got, so a single English word spelt
                # differently between the two documents could beat a reading that
                # accounts for everything. The score engraves `grat ti tude.`
                # where the layout writes `grat- i- tude.`, and stopping there
                # scored a flawless twelve of twelve against the straight
                # reading's thirteen of fourteen: it won, took twelve notes, and
                # left `pa-` unsung on the thirteenth of a row that matched the
                # line box for box.
                #
                # Only that case is protected. Stopping early is how a line that
                # carries on into another written row is read at all, so it stays
                # the rule everywhere the row and the line are not the same length
                # - which is why this asks for both, and allows one disagreement
                # in case the two documents spell one word differently.
                one_to_one = (
                    places[-1] == run[-1] and span == len(wanted)
                    and hits >= span - 1.0 - 1e-9
                )
                exact = agreed >= len(boxes) - 1e-9 and len(boxes) >= SUBSEQUENCE_MINIMUM
                if (exact and not one_to_one
                        and len(boxes) >= SUBSEQUENCE_SHARE * span and agreed > worth):
                    places, hits = boxes, agreed
                    span = len(places)

            if hits / span < MIN_AGREEMENT:
                continue

            # How much of the English this stretch actually accounts for decides it.
            # The labels below only separate stretches that agree equally well - which
            # is exactly the case they exist for: two written lines carrying the same
            # English and different words, one for the lead and one for the harmony.
            hint = 0.0
            # Whether this written row belongs to the part of the song being sung.
            # Unlabelled either side means "no opinion", not "disagrees".
            row_section = normalize_section(line.section)
            in_section = not (wanted_section and row_section) or row_section == wanted_section
            hint += SECTION_BONUS if (wanted_section and row_section and in_section) else 0.0
            if line.tag:
                if not _tag_fits(line.tag, voice):
                    hint += WRONG_VOICE_TAG
                elif in_section:
                    hint += VOICE_TAG_BONUS
                # A row tagged for this part but written under a different section is
                # some other repeat's harmony line. It is still eligible - a section
                # marker can be missing or sit a system away - but its part tag must
                # not out-argue a row that is in the right place. This is what let a
                # voice singing Chorus 1 take the harmony row written under Ch3.
            if printed and not own_row:
                hint += PUNCTUATION_AGREES * _stops_agree(line, places, printed)

            if after is not None and number == after + 1 and offset == 0:
                hint += CONTINUES_BONUS

            # A line that wraps at the end of a system leaves a fragment - sometimes a
            # single note - and one word is not enough to say which written line it
            # opens. 'I' opens both 'I can clearly see' and 'I will not let my hands
            # drop down'. What settles it is what this voice sings *next*: if this
            # written line carries on past what the fragment takes, the rest of it
            # should be the opening of the next line on the staff. Only considered
            # where the written line does continue, so a line this staff finishes has
            # nothing to prove.
            if following and offset + span < len(line.keys):
                rest = line.keys[offset + span:offset + span + len(following)]
                if rest:
                    agreed = sum(
                        _agrees(written, sung) for written, sung in zip(rest, following)
                    ) / len(rest)
                    hint += CONTINUATION_BONUS * agreed

            # The layout is written in the order the song is performed, so a voice
            # reads it forwards. The nearest stretch it has not sung yet is the one
            # it is singing now - and that, not the words, is what tells eleven
            # identical lines of English apart when two are translated differently.
            # It is a preference rather than a rule, so a part that enters late or a
            # line the layout leaves out cannot strand everything after it.
            ahead = lock.flat(number, offset) - floor
            hint += FORWARD_BONUS - AHEAD_COST * ahead if ahead >= 0 else BACKWARD_COST

            key = (round(hits - MISMATCH_COST * (span - hits), 6), hint)
            if best_key is None or key > best_key:
                best, best_key = (number, places), key
        return best

    # Respect the part tags first. Only if that leaves the notes with nothing at
    # all does the same search run again with the tags relaxed, so a row written
    # for another part is used rather than leaving the voice silent.
    return search(False) or search(True)


def _subsequence(line: LockLine, wanted: list[str], run: list[int]):
    """Match the words this voice sings against a row that prints more of them.

    The row is read forwards and may pass over a box or two between matches; the
    score's words may not be skipped, because every one of them has a note that
    has to carry something. Returns the box for each word matched, and how well
    they agreed, or None if it got no further than reading straight through.
    """
    places: list[int] = []
    hits = 0.0
    cursor = 0
    for word in wanted:
        found = None
        for index in range(cursor, min(len(run), cursor + MAX_SKIPPED_BOXES + 1)):
            agreed = _agrees(line.keys[run[index]], word)
            if agreed > 0:
                found = (index, agreed)
                break
        if found is None:
            break
        places.append(run[found[0]])
        hits += found[1]
        cursor = found[0] + 1
    return (places, hits) if places else None


def _fold_word_start(line: LockLine, offset: int, token: str,
                     sung: set | None = None, page: int = -1,
                     rows_sung: set | None = None) -> str:
    """Keep a part entering mid-word from opening on the tail of one.

    A harmony often comes in a bar after the lead, on the second syllable of a
    word. In English that still reads; where the phrase opens 'Jeho-vá', opening
    on 'vá' does not. The syllable before it is folded onto its first note,
    exactly as a translator does by hand.

    Which syllable is mid-word is decided by the **translation**, not by the
    English. The two languages do not break their words in the same places: the
    Aymara 'Jeho-|vá' sits under the English 'I | will', and the English words
    carry no hyphen at all. Reading the English side, as this once did, meant the
    fold never fired on precisely the entries the hand-made scores do fold -
    'Jeho-vá', 'i-man', 'ya-nap' - while a translation that does *not* continue
    ('Pay | la-doy') is left alone, which is also what they do.

    A syllable this voice has already sung on a note of its own is never folded
    on again, however the word is broken.
    """
    sung = sung or set()
    back = offset - 1
    if back < 0 or (line.id, back) in sung:
        return token
    if line.id in (rows_sung or set()):
        # This voice has been through this written row already, so the syllable
        # before the entry is one it has sung on a note of its own; the hand-made
        # scores leave the entry note to open on its own box. Folding belongs to a
        # part meeting the row for the first time.
        return token
    if not line.translated[back].rstrip().endswith(HYPHENS):
        return token  # the written word ends there; this note opens a new one
    head = [line.translated[back].rstrip()]
    if len(head) > MAX_FOLDED_SYLLABLES:
        return token
    if back - 1 >= 0 and line.translated[back - 1].rstrip().endswith(HYPHENS):
        return token  # the word runs back further than we may fold
    return "".join(head) + token


def _repeats_what_was_just_sung(combined: str, token: str, last: str) -> bool:
    """Whether a fold has put the syllable just sung onto this note as well.

    The guard on already-sung boxes catches a part re-entering the same written
    row. It cannot catch the same syllable arriving from a *different* row - a
    repeat written out again elsewhere - which is how 'Ka-' followed by
    'Ka-teerü' and 'Pai' followed by 'Pai-cu-' get printed. Nobody sets the same
    syllable twice running, so the text itself is the test.
    """
    if not last or combined == token:
        return False
    prefix = combined[: len(combined) - len(token)]
    return bool(prefix) and fold(prefix).strip("-") == fold(last).strip("-")


def place_line(lock: Lock, score_line, voice: str, cursor: int = 0, previous=None,
               following=None, sung: set | None = None, previous_text: str = "",
               rows_sung: set | None = None):
    """The syllables for one line of the score, and the written lines they came from.

    ``cursor`` is how far through the layout this voice has already sung.
    Preferring what lies ahead of it is what keeps eleven identical lines of
    English apart when two of them are translated differently.

    ``previous`` is the exact syllable the voice left off on, so a line carrying
    straight on from the one before is not mistaken for a fresh entry mid-word.

    ``following`` is the next line this voice sings, so a fragment left by a line
    wrapping at a system break is read as part of the line it continues into.

    Returns (tokens, written line ids, held syllables, cursor, where it left off).
    """
    wanted = [fold(anchor.text) for anchor in score_line.anchors]
    printed = [anchor.text for anchor in score_line.anchors]
    ahead_words = (
        [fold(anchor.text) for anchor in following.anchors[:LOOKAHEAD_WORDS]]
        if following is not None else []
    )
    need = score_line.note_count
    last_text = previous_text
    tokens: list[str] = []
    used: list[int] = []
    last: LockLine | None = None
    after: int | None = None
    ends_at = previous
    floor = cursor
    # (anchor index, syllable, whether the box was merged into the note's word)
    # for syllables the translation sings on notes the English holds a vowel
    # across, found in the middle of a written row.
    mid_held: list[tuple[int, str, bool]] = []

    while len(tokens) < need:
        found = _segment(lock, wanted[len(tokens):], score_line.section, voice,
                         after, floor, ahead_words, printed[len(tokens):])
        if found is None:
            tokens.append("")
            ends_at = None
            continue
        number, places = found
        line = lock.lines[number]
        places = places[:need - len(tokens)]
        if not places:
            tokens.append("")
            ends_at = None
            continue
        offset = places[0]
        for index, position in enumerate(places):
            # A box passed over inside this stretch is one of two different
            # things. If the English box has a word in it, this part simply does
            # not sing that word - a doubling voice reading the lead's line - and
            # the box is rightly left behind. If the English box is blank, it is a
            # note the English holds a vowel across: the score prints no syllable
            # there for the translation to replace, but the translation has
            # written one, and it belongs on that held note rather than nowhere.
            if index > 0:
                for skipped in range(places[index - 1] + 1, position):
                    # A box with English in it was passed over for one of two
                    # reasons. Either this part does not sing that word - a
                    # doubling voice reading the lead's line - and the box is
                    # rightly left behind; or the engraving set it and the box
                    # before it as a single printed word, in which case it is
                    # sung, on the note the score holds under that word.
                    note = len(tokens) - 1
                    merged = (
                        0 <= note < len(wanted)
                        and _engraved_as_one(line, places[index - 1], skipped, wanted[note])
                    )
                    if line.keys[skipped] and not merged:
                        continue
                    extra = (line.translated[skipped] or "").strip()
                    if extra and extra != BLANK_BOX and tokens:
                        mid_held.append((len(tokens) - 1, extra, merged))
            token = line.translated[position]
            if index == 0 and offset > 0 and ends_at != (line.id, offset - 1):
                plain = token
                # First preserve a skipped prefix from the SAME semantic stream
                # (e.g. We|preach -> Mun-|do => Mun-do for a Lead entry).
                pass  # EXPERIMENT: stream fold disabled
                # Then handle the narrower case where the entry begins inside a
                # hyphenated English word.
                token = (_fold_word_start(line, offset, token, sung, -1, rows_sung)
                         if token == line.translated[offset] else token)
                if _repeats_what_was_just_sung(token, plain, last_text):
                    token = plain
            tokens.append(token)
            if token:
                last_text = token
            if sung is not None:
                sung.add((line.id, position))
        if line.id not in used:
            used.append(line.id)
        if rows_sung is not None:
            rows_sung.add(line.id)
        last, after = line, number
        ends_at = (line.id, places[-1])
        floor = lock.flat(number, places[-1] + 1)

    # Anything the translation sings that the English prints no syllable for goes
    # on the notes the English holds, in the order it was written.
    held: list[tuple] = []
    taken: set[float] = set()
    for anchor_index, extra, merged in mid_held:
        free = [x for x in score_line.held_after(anchor_index) if x not in taken]
        if free:
            held.append((free[0], extra))
            taken.add(free[0])
        elif merged and 0 <= anchor_index < len(tokens):
            # The engraving set two of the layout's syllables as one word and gave
            # it a single note - We Go Preaching prints `preaching` where the
            # layout writes `preach-` and `ing.`. There is no held note to put the
            # second on, so both are sung on the one note, which is exactly what
            # the layout means by two syllables written in one box. Dropping it
            # instead, as this did, loses a syllable the translator wrote.
            tokens[anchor_index] = f"{tokens[anchor_index]} {extra}".strip()
    if last is not None and last.spare and need:
        spare = list(last.spare)
        for position in score_line.held_after(need - 1):
            if not spare:
                break
            if position in taken:
                continue
            held.append((position, spare.pop(0)))
            taken.add(position)
    return tokens, used, held, floor, ends_at


def plan_voice(voice: str, score_lines, lock: Lock) -> VoicePlan:
    assignments: list[Assignment] = []
    matched = covered = notes_total = 0
    cursor, previous = 0, None
    last_text = ""
    sung: set[tuple[int, int]] = set()
    rows_sung: set[int] = set()

    for index, line in enumerate(score_lines):
        need = line.note_count
        notes_total += need
        following = score_lines[index + 1] if index + 1 < len(score_lines) else None
        tokens, used, held, cursor, previous = place_line(
            lock, line, voice, cursor, previous, following, sung, last_text,
            rows_sung
        )
        for token in tokens:
            if token:
                last_text = token
        filled = sum(1 for token in tokens if token)
        covered += filled
        if filled == need and need:
            matched += 1

        if not filled:
            status = "unmatched"
            note = (
                "This line's English was not found in the layout, so there is nothing "
                "saying what it sings. Enter the syllables below."
            )
        elif filled < need:
            status = "partial"
            note = f"{need - filled} note(s) have no syllable written for them."
        else:
            status, note = "ok", ""

        assignments.append(
            Assignment(
                score_line_id=line.id,
                voice=voice,
                page=line.page,
                section=line.section,
                english=line.text,
                tokens=tokens,
                layout_line_ids=used,
                status=status,
                note=note,
                held=held,
            )
        )

    return VoicePlan(
        voice=voice,
        assignments=assignments,
        matched=matched,
        total=len(score_lines),
        cost=0.0,
        covered=covered,
        notes_total=notes_total,
    )


def plan_voices(score_doc, lock: Lock, voices) -> dict[str, VoicePlan]:
    grouped = score_doc.lines_by_voice()
    return {
        voice: plan_voice(voice, grouped[voice], lock)
        for voice in voices
        if grouped.get(voice)
    }


# --------------------------------------------------------------------------- proofing

ATTENTION = "attention"
RESOLVED = "resolved"


def attention_marks(assignment, tokens, resolved=None, unsettled_rows=None) -> list[str]:
    """Which notes of one score line have not been settled, one state per note.

    The app cannot know where it differs from a score somebody would set by hand -
    if it knew that, it would have set it that way. What it does know is where it
    was unsure, and that is what is worth a person's eyes:

    * a note it could not put a syllable on at all;
    * a line whose English it only partly recognised, where every syllable on the
      line is suspect rather than just one;
    * a line drawing on a written row where the two languages disagreed about how
      many syllables there are.

    ``resolved`` is the set of (score line id, note index) a person has already
    settled. Those go green. Everything a person has looked at and deliberately
    left alone stays red, because "I chose this" and "I never saw this" must not
    look the same on the page.
    """
    resolved = resolved or set()
    unsettled_rows = unsettled_rows or set()
    suspect_line = (
        assignment.status != "ok"
        or any(row in unsettled_rows for row in assignment.layout_line_ids)
    )
    out: list[str] = []
    for index, token in enumerate(tokens):
        if (assignment.score_line_id, index) in resolved:
            out.append(RESOLVED)
        elif not token or suspect_line:
            out.append(ATTENTION)
        else:
            out.append("")
    return out
