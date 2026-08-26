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
HYPHENS = ("-", "‐", "‑", "–")

SECTION_BONUS = 0.75      # the written line is labelled with the section being sung
VOICE_TAG_BONUS = 2.5     # it is labelled for the part now being set
WRONG_VOICE_TAG = -3.0    # it is labelled for a different part
CONTINUES_BONUS = 1.5     # it carries straight on from the line just sung
NEAR_WORD = 0.6           # one spelling is the opening of the other
FORWARD_BONUS = 2.0       # it is still to come, rather than already sung
AHEAD_COST = 0.004        # ... and the nearer it is, the likelier it is
BACKWARD_COST = -1.0      # it is behind where this voice has reached


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
             after: int | None, floor: int):
    """The best stretch of a written line for the front of what is still to sing.

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
        hits = sum(_agrees(line.keys[run[index]], wanted[index]) for index in range(span))
        if hits / span < MIN_AGREEMENT:
            continue

        # How much of the English this stretch actually accounts for decides it.
        # The labels below only separate stretches that agree equally well - which
        # is exactly the case they exist for: two written lines carrying the same
        # English and different words, one for the lead and one for the harmony.
        hint = 0.0
        if wanted_section and normalize_section(line.section) == wanted_section:
            hint += SECTION_BONUS
        if line.tag:
            hint += VOICE_TAG_BONUS if _tag_fits(line.tag, voice) else WRONG_VOICE_TAG
        if after is not None and number == after + 1 and offset == 0:
            hint += CONTINUES_BONUS

        # The layout is written in the order the song is performed, so a voice
        # reads it forwards. The nearest stretch it has not sung yet is the one
        # it is singing now - and that, not the words, is what tells eleven
        # identical lines of English apart when two are translated differently.
        # It is a preference rather than a rule, so a part that enters late or a
        # line the layout leaves out cannot strand everything after it.
        ahead = lock.flat(number, offset) - floor
        hint += FORWARD_BONUS - AHEAD_COST * ahead if ahead >= 0 else BACKWARD_COST

        key = (round(hits, 6), hint)
        if best_key is None or key > best_key:
            best, best_key = (number, offset, span), key
    return best


def _fold_word_start(line: LockLine, offset: int, token: str) -> str:
    """Keep a part entering mid-word from opening on the tail of one.

    A harmony often comes in a bar after the lead, on the second syllable of a
    word. In English that still reads; where the phrase opens 'Jeho-vá', opening
    on 'vá' does not. The syllables before it are folded onto its first note,
    exactly as a translator does by hand.
    """
    head: list[str] = []
    back = offset - 1
    while (
        back >= 0
        and len(head) < MAX_FOLDED_SYLLABLES
        and line.english[back].rstrip().endswith(HYPHENS)
    ):
        head.insert(0, line.translated[back].rstrip())
        back -= 1
    if not head:
        return token
    if back >= 0 and line.english[back].rstrip().endswith(HYPHENS):
        return token  # the word runs back further than we may fold
    return "".join(head) + token


def _fold_same_stream_prefix(line: LockLine, offset: int, token: str) -> str:
    """Fold skipped translated boxes from the same colored stream onto entry note.

    A score voice can begin in the middle of a layout row. If the skipped boxes belong
    to the same semantic stream as the first matched box, their translated syllables
    still belong to that musical entry and are conventionally printed together on the
    first available note. This is exactly what happens with the Bridge: the Lead score
    begins at ``preach`` inside ``We | preach``, so ``Mun- | do`` must enter as ``Mun-do``;
    a Harmony entry beginning on a yellow box must never absorb the preceding blue Lead
    boxes.
    """
    if offset <= 0 or offset >= len(line.translated):
        return token
    sem = line.semantic if len(line.semantic) == len(line.translated) else [""] * len(line.translated)
    current = sem[offset]
    if not current:
        return token
    prefix = []
    back = offset - 1
    while back >= 0 and sem[back] == current:
        value = (line.translated[back] or "").strip()
        if value and value != "-":
            prefix.insert(0, value)
        back -= 1
    if not prefix:
        return token
    return "".join(prefix) + token


def place_line(lock: Lock, score_line, voice: str, cursor: int = 0, previous=None):
    """The syllables for one line of the score, and the written lines they came from.

    ``cursor`` is how far through the layout this voice has already sung.
    Preferring what lies ahead of it is what keeps eleven identical lines of
    English apart when two of them are translated differently.

    ``previous`` is the exact syllable the voice left off on, so a line carrying
    straight on from the one before is not mistaken for a fresh entry mid-word.

    Returns (tokens, written line ids, held syllables, cursor, where it left off).
    """
    wanted = [fold(anchor.text) for anchor in score_line.anchors]
    need = score_line.note_count
    tokens: list[str] = []
    used: list[int] = []
    last: LockLine | None = None
    after: int | None = None
    ends_at = previous
    floor = cursor

    while len(tokens) < need:
        found = _segment(lock, wanted[len(tokens):], score_line.section, voice, after, floor)
        if found is None:
            tokens.append("")
            ends_at = None
            continue
        number, offset, span = found
        line = lock.lines[number]
        span = min(span, need - len(tokens))
        for index in range(span):
            token = line.translated[offset + index]
            if index == 0 and offset > 0 and ends_at != (line.id, offset - 1):
                # First preserve a skipped prefix from the SAME semantic stream
                # (e.g. We|preach -> Mun-|do => Mun-do for a Lead entry).
                token = _fold_same_stream_prefix(line, offset, token)
                # Then handle the narrower case where the entry begins inside a
                # hyphenated English word.
                token = _fold_word_start(line, offset, token) if token == line.translated[offset] else token
            tokens.append(token)
        if line.id not in used:
            used.append(line.id)
        last, after = line, number
        ends_at = (line.id, offset + span - 1)
        floor = lock.flat(number, offset + span)

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

    for line in score_lines:
        need = line.note_count
        notes_total += need
        tokens, used, held, cursor, previous = place_line(
            lock, line, voice, cursor, previous
        )
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
