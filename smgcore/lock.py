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
    if not sung:
        return 1.0  # the engraving printed nothing here to disagree with
    if written == sung:
        return 1.0
    if written and (written.startswith(sung) or sung.startswith(written)):
        return NEAR_WORD
    return 0.0


def _eligible_positions(line: LockLine, voice: str) -> list[int]:
    """Token-level routing for mixed rows.

    If a written row contains any yellow Harmony boxes, Lead voices may use only
    the non-Harmony boxes and harmony voices may use only the yellow boxes. Rows
    without yellow boxes keep the normal voice-routing behavior.
    """
    sem = line.semantic if len(line.semantic) == len(line.keys) else [""] * len(line.keys)
    if not any(c == "harmony" for c in sem):
        return list(range(len(line.keys)))
    if "lead" in voice.lower() or "solo" in voice.lower() or "melody" in voice.lower():
        return [i for i, c in enumerate(sem) if c != "harmony"]
    return [i for i, c in enumerate(sem) if c == "harmony"]


def _segment(lock: Lock, wanted: list[str], section: str, voice: str,
             after: int | None, floor: int, following: list[str] | None = None):
    """The best stretch of a written line for the front of what is still to sing.

    ``following`` is the opening of what this same voice sings next, used only to
    tell apart written lines that the words in hand cannot.

    Returns (line number, offset into it, how many syllables it answers for).
    """
    if not lock.lines or not wanted:
        return None

    starts: set[tuple[int, int]] = set()
    for lead in range(min(4, len(wanted))):
        for number, position in lock.index.get(wanted[lead], ()):
            eligible = _eligible_positions(lock.lines[number], voice)
            if position not in eligible:
                continue
            start = position - lead
            if start < 0:
                continue
            # The matched stretch must stay inside one contiguous semantic run.
            if all(start + k in eligible for k in range(lead + 1)):
                starts.add((number, start))
    if after is not None and after + 1 < len(lock.lines):
        eligible = _eligible_positions(lock.lines[after + 1], voice)
        if eligible:
            starts.add((after + 1, eligible[0]))  # carry on within this voice's stream


    wanted_section = normalize_section(section)
    best = None
    best_key = None
    for number, offset in sorted(starts):
        line = lock.lines[number]
        # Explicit part tags are hard routing constraints, not merely a scoring
        # preference. A harmony-only row (yellow boxes or “(Harmonies)”) must never
        # be eligible for a lead, and a lead-only/ad-lib row must not be consumed by
        # an unrelated harmony. This is the protection that prevents harmony lyrics
        # from leaking into the lead when the English words happen to be identical.
        if line.tag and not _tag_fits(line.tag, voice):
            continue
        eligible = _eligible_positions(line, voice)
        if offset not in eligible:
            continue
        run = [offset]
        while run[-1] + 1 in eligible:
            run.append(run[-1] + 1)
        span = min(len(run), len(wanted))
        if span <= 0:
            continue
        places = run[:span]
        hits = sum(_agrees(line.keys[run[index]], wanted[index]) for index in range(span))

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
            exact = agreed >= len(boxes) - 1e-9 and len(boxes) >= SUBSEQUENCE_MINIMUM
            if exact and len(boxes) >= SUBSEQUENCE_SHARE * span and agreed > worth:
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

    while len(tokens) < need:
        found = _segment(lock, wanted[len(tokens):], score_line.section, voice,
                         after, floor, ahead_words)
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
    if last is not None and last.spare and need:
        spare = list(last.spare)
        for position in score_line.held_after(need - 1):
            if not spare:
                break
            held.append((position, spare.pop(0)))
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
