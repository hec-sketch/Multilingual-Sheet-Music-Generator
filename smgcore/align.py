"""Work out which words each voice sings, and where each syllable goes.

Everything here is built on one idea: match the words, do not count them. Every
syllable printed in the English score is compared against every syllable of the
English layout, and the two are aligned end to end. Because the words themselves
are being matched, the result is not a guess - the app knows that *this* note
carries *that* layout syllable, so the translated syllable paired with it lands
exactly there. Repeats, late entries, dropouts, canons and lines that wrap across
systems and pages all fall out of the alignment for free.

That needs English syllable lines to align against. They come either from the
layout document itself, where it holds both languages, or from the score, cut
into phrases by :mod:`smgcore.lyricsdoc`. Either way this module is given them
and never has to fall back to counting.

Everything it decides stays editable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .layout import BLANK_BOX
from . import textutil
from .textutil import fold


def sung_positions(english_lines) -> dict:
    """Position of every sung box in the layout, counted as the aligner counts.

    Blank boxes are not part of the alignment, so they take up no position. The
    timeline and the matcher must agree about this or every voice after the first
    is held to the wrong moment.
    """
    positions: dict[tuple, int] = {}
    running = 0
    for line in english_lines:
        for index, token in enumerate(line.tokens):
            if token == BLANK_BOX:
                continue
            positions[(line.id, index)] = running
            running += 1
    return positions

INFINITY = float("inf")

# Text-alignment scoring. Positive numbers are rewards, negative are penalties.
SAME_WORD = 2.0
NEAR_WORD = 1.0
WRONG_WORD = -1.5
SECTION_AGREES = 0.4
SECTION_DISAGREES = -1.2
WRONG_VOICE_FOR_TAG = -0.6
# When several places in the layout read the same, the deciding evidence is what
# the rest of the choir is singing at that moment. The busiest voice is aligned
# first and every other voice is then held loosely to its position in time.
TIMELINE_FREE = 1  # syllables of slack before the pull starts
TIMELINE_PULL = 0.08  # per syllable of drift beyond that
TIMELINE_LIMIT = 1.5
# A syllable that has to be folded onto a neighbouring note to keep a word whole.
# One is what a translator will accept; two is asking a singer to swallow a word.
MAX_FOLDED_SYLLABLES = 3
# Skipping layout the voice does not sing must stay cheap: a part that enters in
# the last chorus has to step over the whole song to reach its words. Small but
# not zero, so that where two readings tie the earlier one wins.
SKIP_LAYOUT_SYLLABLE = -0.04
NOTE_WITH_NO_WORD = -1.5  # leaving a note empty is expensive


@dataclass
class Assignment:
    """The syllables placed on one score line."""

    score_line_id: int
    voice: str
    page: int
    section: str
    english: str
    tokens: list[str] = field(default_factory=list)
    layout_line_ids: list[int] = field(default_factory=list)
    status: str = "ok"  # ok | partial | unmatched | edited
    note: str = ""
    # Syllables sung on notes the English holds a vowel across, so there is no
    # printed English syllable to hang them on: (x of the note, the syllable).
    held: list[tuple] = field(default_factory=list)

    @property
    def note_count(self) -> int:
        return len(self.tokens)

    @property
    def held_text(self) -> str:
        return " ".join(text for _, text in self.held)


@dataclass
class VoicePlan:
    voice: str
    assignments: list[Assignment]
    matched: int
    total: int
    cost: float
    covered: int = 0
    notes_total: int = 0

    @property
    def complete(self) -> bool:
        return self.matched == self.total

    @property
    def coverage(self) -> float:
        return self.covered / self.notes_total if self.notes_total else 0.0


# --------------------------------------------------------------------------- sections


def normalize_section(name: str) -> str:
    """Reduce a section label to a comparable key. 'Ch1' and 'Chorus 1' agree."""
    text = (name or "").strip().lower()
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return f"verse{text}"
    text = re.sub(r"[\.\-_]+", " ", text)
    # 'ch1' and 'v2' are written without a space; give the number one.
    text = re.sub(r"(?<=[a-z])(?=\d)", " ", text)
    text = re.sub(r"\bpre\s*(ch(orus|oro|oru)?|coro)\b", "prechorus", text)
    text = re.sub(r"\b(ch(orus|oro|oru)?|coro|estribillo|refr(ain|ao|ão)|refrein)\b", "chorus", text)
    # A verse is written 'Verse', 'Verso', 'Vers', 'Estrofa' or just its number,
    # depending on the language the sheet was written in.
    text = re.sub(r"\bv(erse|erso|ers|s)?\b", "verse", text)
    text = re.sub(r"\b(estrofa|strofa|couplet)\b", "verse", text)
    text = re.sub(r"\b(bridge|br|puente|ponte|brucke|brücke|pont)\b", "bridge", text)
    text = re.sub(r"\bend(ing)?\b", "ending", text)
    digits = re.findall(r"\d+", text)
    stem = re.sub(r"[^a-z]", "", text)
    return stem + (digits[-1] if digits else "")


def section_set(value) -> frozenset:
    """A section-map entry as a set, whether it was stored as one name or several."""
    if not value:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(value)


def build_section_map(layout_sections: list[str], score_sections: list[str]) -> dict[str, frozenset]:
    """Map each layout section label onto the score section(s) it covers.

    A translator writes a repeated section once: one ``Ch`` block for a chorus the
    score sings three times as ``Chorus 1``, ``Chorus 2`` and ``Chorus 3``. So a
    label may legitimately cover several score sections, and the value is a set.
    A label that names its repeat explicitly (``Ch1``) still maps to just that one.
    """
    mapping: dict[str, frozenset] = {}
    by_key: dict[str, list[str]] = {}
    by_base: dict[str, list[str]] = {}
    for name in score_sections:
        key = normalize_section(name)
        by_key.setdefault(key, []).append(name)
        by_base.setdefault(re.sub(r"\d+$", "", key), []).append(name)

    unresolved = []
    for label in layout_sections:
        key = normalize_section(label)
        if key in by_key:
            mapping[label] = frozenset(by_key[key])
        elif key in by_base:
            # "Ch" with no number covers every numbered chorus in the score.
            mapping[label] = frozenset(by_base[key])
        else:
            unresolved.append(label)

    # Fall back to order when the two documents use different wording.
    if unresolved and len(layout_sections) == len(score_sections):
        for label, name in zip(layout_sections, score_sections):
            mapping.setdefault(label, frozenset({name}))
        for label in unresolved:
            index = layout_sections.index(label)
            mapping[label] = frozenset({score_sections[index]})
    return mapping


def unroll_to_performance_order(english_lines, section_map, score_sections):
    """Repeat the layout's blocks into the order the score actually sings them.

    The alignment walks each voice's notes and the layout's syllables forward
    together, once. That only works if the layout is in performance order. When a
    layout writes the chorus once but the score sings it three times, the chorus
    block has to appear three times, in the right places, or every note after the
    first chorus has nothing left to match against.

    Each repeat is a copy with its own id and ``repeat_of`` pointing at the line
    the translator actually wrote, so the timeline can tell the repeats apart while
    the translation still resolves to one edited line. Calling this on a list that
    has already been unrolled returns it unchanged.
    """
    lines = list(english_lines)
    if not lines or not score_sections:
        return lines
    if any(getattr(line, "repeat_of", None) is not None for line in lines):
        return lines  # already in performance order

    blocks: list[tuple[str, list]] = []
    for line in lines:
        if blocks and blocks[-1][0] == line.section:
            blocks[-1][1].append(line)
        else:
            blocks.append((line.section, [line]))

    # A block whose label names no score section travels with the block before it,
    # so an unlabelled or unrecognised run is never stranded or duplicated.
    carriers: list[tuple[frozenset, list]] = []
    for label, block_lines in blocks:
        names = section_set(section_map.get(label))
        if names or not carriers:
            carriers.append((names, list(block_lines)))
        else:
            carriers[-1][1].extend(block_lines)

    if not any(len(names) > 1 for names, _ in carriers):
        return lines  # nothing repeats; the document is already in order

    next_id = max(line.id for line in lines) + 1
    seen: set[int] = set()
    out: list = []
    for name in score_sections:
        for names, block_lines in carriers:
            if name not in names:
                continue
            for line in block_lines:
                if line.id not in seen:
                    seen.add(line.id)
                    out.append(line)
                    continue
                copy = replace(line, id=next_id, repeat_of=line.repeat_of or line.id)
                next_id += 1
                out.append(copy)
    leftover = [
        line
        for names, block_lines in carriers
        if not names
        for line in block_lines
        if line.id not in seen
    ]
    return (out + leftover) if out else lines


def expand_translation(translation: dict, english_lines) -> dict:
    """Give every repeated copy of a layout line the same translated syllables."""
    out = dict(translation)
    for line in english_lines:
        origin = getattr(line, "repeat_of", None)
        if origin is not None and line.id not in out and origin in translation:
            out[line.id] = translation[origin]
    return out


# --------------------------------------------------------------------------- voices


def _is_lead(voice: str) -> bool:
    return textutil.is_lead_part(voice)


def _tag_fits(tag: str, voice: str) -> bool:
    """Whether a row the layout marked for a part may be offered to this voice.

    A row marked for any part that answers the lead - harmony, ad lib, backing -
    is offered to every voice that is not the lead, and to no lead. They are one
    case, not three: the layout marks such a row to keep it off the lead, and
    which answering part takes it is settled by the words and the notes like
    anything else.

    Reading them as three cases is what went wrong before. The ad-lib branch
    asked the voice's name to contain 'ad lib', so a row marked '(Ad Libs)' was
    refused to a voice called `Male Lead Adlib 1` - which spells it without the
    space, and which the lead test had already claimed anyway, on the strength
    of the word 'Lead' in a name that means the opposite.
    """
    if not tag:
        return True
    lowered = tag.lower()
    if "harmon" in lowered or "armon" in lowered or textutil.names_support_part(lowered):
        return not _is_lead(voice)
    if "lead" in lowered or "solo" in lowered:
        return _is_lead(voice)
    return True


def _tag_bonus(tag: str, voice: str) -> float:
    if not tag or _tag_fits(tag, voice):
        return 0.0
    return WRONG_VOICE_FOR_TAG


def _allowed_sections(score_lines) -> list[set]:
    """Which sections a note could plausibly belong to.

    A section marker is printed above the system it opens, but the phrase often
    starts on a pick-up note at the end of the system before it. Those notes are
    labelled with the outgoing section even though they sing the incoming one, so
    the first and last note of every line are allowed to belong to either.
    """
    out: list[set] = []
    for index, line in enumerate(score_lines):
        count = line.note_count
        before = score_lines[index - 1].section if index else ""
        after = score_lines[index + 1].section if index + 1 < len(score_lines) else ""
        for position in range(count):
            allowed = {line.section}
            if position == 0 and before:
                allowed.add(before)
            if position == count - 1 and after:
                allowed.add(after)
            out.append(allowed)
    return out


# --------------------------------------------------------------------------- text alignment


def _word_score(a: str, b: str) -> float:
    if not a or not b:
        return WRONG_WORD
    if a == b:
        return SAME_WORD
    # A phrase the layout prints inside quotation marks carries the opening mark
    # on its first word - 'Love against Love - and the mark is punctuation round
    # the phrase, not part of the word that is sung.
    stripped_a, stripped_b = a.strip("'"), b.strip("'")
    if stripped_a and stripped_a == stripped_b:
        return SAME_WORD
    if min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)):
        return NEAR_WORD
    return WRONG_WORD


def build_timeline(score_lines, mapping, english_lines) -> dict:
    """Where in the layout the music has got to, at each moment of each system.

    Built from one voice that has already been aligned. Other voices then have a
    reference for *when* they are singing, which is what tells two identical
    lines apart.
    """
    positions = sung_positions(english_lines)

    timeline: dict[tuple, list] = {}
    anchors = [anchor for line in score_lines for anchor in line.anchors]
    for anchor, entry in zip(anchors, mapping):
        if entry is None:
            continue
        where = positions.get(entry)
        if where is None:
            continue
        key = (anchor.page, anchor.system)
        timeline.setdefault(key, []).append(((anchor.x0 + anchor.x1) / 2, where))
    for key in timeline:
        timeline[key].sort()
    return timeline


TIMELINE_REACH = 12  # how far past the reference voice's last note we dare read


def _expected_positions(anchors, timeline) -> list[float | None]:
    """For each anchor, where the reference voice was in the layout at that moment.

    Read off by interpolating across the system, so a part that answers after the
    lead has stopped singing — the parenthesised echo at the end of a piece — is
    understood to be *later* in the words, not stuck on the lead's last note.
    """
    if not timeline:
        return [None] * len(anchors)
    out: list[float | None] = []
    for anchor in anchors:
        points = timeline.get((anchor.page, anchor.system))
        if not points:
            out.append(None)
            continue
        centre = (anchor.x0 + anchor.x1) / 2
        if len(points) == 1:
            out.append(float(points[0][1]))
            continue
        first, last = points[0], points[-1]
        if first[0] <= centre <= last[0]:
            left, right = first, last
            for lower, upper in zip(points, points[1:]):
                if lower[0] <= centre <= upper[0]:
                    left, right = lower, upper
                    break
        else:
            left, right = first, last  # extrapolate on the system's overall pace
        if right[0] == left[0]:
            out.append(float(left[1]))
            continue
        value = left[1] + (right[1] - left[1]) * (centre - left[0]) / (right[0] - left[0])
        out.append(
            max(first[1] - TIMELINE_REACH, min(last[1] + TIMELINE_REACH, value))
        )
    return out


def map_voice_to_layout(
    voice, score_lines, english_lines, section_map, timeline=None
) -> list[tuple | None]:
    """For each note of this voice, which English layout syllable sits on it.

    Returns one entry per anchor: ``(layout_line_id, index_within_line)`` or
    ``None`` where the alignment found nothing convincing.

    This is a semi-global alignment. Every note of the voice must be accounted
    for, but the layout is free to start and end wherever it likes and to skip
    whole lines cheaply, because most voices sing only part of the song.
    """
    anchors = [anchor for line in score_lines for anchor in line.anchors]
    if not anchors or not english_lines:
        return [None] * len(anchors)

    allowed = _allowed_sections(score_lines)
    left = [(fold(a.text), allowed[i]) for i, a in enumerate(anchors)]
    right: list[tuple] = []
    running = 0
    for line in english_lines:
        section = section_set(section_map.get(line.section, line.section))
        # Voice routing is a hard constraint for harmony-only material.  In
        # particular, a yellow "Harmonies" row must never be offered as a
        # candidate to a Lead voice, even when its English words are identical
        # to a lead row.  A score penalty is not sufficient because a strong word
        # match could still win the alignment.
        if not _tag_fits(line.tag, voice):
            continue
        bonus = _tag_bonus(line.tag, voice)
        token_classes = getattr(line, "token_classes", [""] * len(line.tokens))
        has_harmony_tokens = any(cls == "harmony" for cls in token_classes)
        for index, token in enumerate(line.tokens):
            # Harmony is a token-level semantic rule. A mixed row can contain ordinary
            # lead boxes followed by yellow Harmony boxes on the same visual line.
            # Lead voices may never consume the yellow boxes; when a mixed row is used
            # by a non-lead harmony voice, only the yellow boxes are eligible. Rows with
            # no yellow boxes remain available to all voices as before.
            token_class = token_classes[index] if index < len(token_classes) else ""
            if has_harmony_tokens:
                if _is_lead(voice) and token_class == "harmony":
                    continue
                if not _is_lead(voice) and token_class != "harmony":
                    continue
            # A blank box is a note the English holds a syllable across, so there
            # is no English word here to match a printed syllable against. It is
            # left out of the alignment entirely and filled afterwards, from the
            # note in the engraving that carries no lyric.
            if token == BLANK_BOX:
                continue
            right.append((fold(token), section, bonus, line.id, index, running))
            running += 1

    expected = _expected_positions(anchors, timeline or {})

    rows, cols = len(left), len(right)
    previous = [0.0] * (cols + 1)  # free start: the layout may begin anywhere
    moves = [[0] * (cols + 1) for _ in range(rows + 1)]

    for i in range(1, rows + 1):
        word, sections = left[i - 1]
        want = expected[i - 1]
        current = [0.0] * (cols + 1)
        current[0] = previous[0] + NOTE_WITH_NO_WORD
        moves[i][0] = 1
        for j in range(1, cols + 1):
            other, other_sections, bonus, _, _, position = right[j - 1]
            score = _word_score(word, other) + bonus
            if sections and other_sections and any(sections):
                score += SECTION_AGREES if (other_sections & sections) else SECTION_DISAGREES
            if want is not None:
                drift = abs(position - want) - TIMELINE_FREE
                if drift > 0:
                    score -= min(TIMELINE_LIMIT, drift * TIMELINE_PULL)
            diagonal = previous[j - 1] + score
            up = previous[j] + NOTE_WITH_NO_WORD
            leftward = current[j - 1] + SKIP_LAYOUT_SYLLABLE
            if diagonal >= up and diagonal >= leftward:
                current[j], moves[i][j] = diagonal, 0
            elif up >= leftward:
                current[j], moves[i][j] = up, 1
            else:
                current[j], moves[i][j] = leftward, 2
        previous = current

    end = max(range(cols + 1), key=lambda j: previous[j])  # free end on the layout side
    mapping: list[tuple | None] = [None] * rows
    i, j = rows, end
    while i > 0:
        move = moves[i][j]
        if move == 0:
            word, _ = left[i - 1]
            other = right[j - 1]
            # Refuse an outright wrong word: better to show a gap than a lie.
            if _word_score(word, other[0]) > WRONG_WORD:
                mapping[i - 1] = (other[3], other[4])
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    return mapping


def recover_transposed(mapping, score_lines, english_lines) -> dict[int, list[int]]:
    """Repair notes the word-for-word alignment left empty or out of order.

    Three things are put right, in the order they have to be.

    First, reading order. The engraver and the translator sometimes write the
    same line with two words the other way round - the score sings "it e-ven if
    we", the layout writes "it if e-ven we" - and matching English word to
    English word then lands the notes on the layout out of sequence. That cannot
    stand: the English words may be reordered, but the *translation* is a
    sentence of its own and has to be sung in the order it is written. Where a
    run of notes reads its layout line out of sequence, the syllables are laid
    back into the order the translator wrote them.

    Then the gaps that ordering alone settles, and finally a word the layout
    breaks into more boxes than the score gives it notes.

    Modifies ``mapping`` in place and returns {note index: [positions]} for any
    note that has to carry several boxes at once.
    """
    anchors: list[str] = []
    for line in score_lines:
        texts = [anchor.text for anchor in line.anchors]
        need = line.note_count
        anchors.extend((texts + [""] * need)[:need])
    if len(anchors) < len(mapping):
        anchors.extend([""] * (len(mapping) - len(anchors)))

    words_by_line = {line.id: [fold(t) for t in line.tokens] for line in english_lines}

    _stay_on_the_line(mapping, anchors, words_by_line)
    _enforce_reading_order(mapping, words_by_line)
    used: set[tuple[int, int]] = {entry for entry in mapping if entry is not None}
    _fill_bracketed_gaps(mapping, words_by_line, used)
    return _recover_split_words(mapping, anchors, words_by_line, used)


def _runs(mapping):
    """Maximal stretches of notes reading from one layout line, gaps included."""
    out: list[tuple[int, list[int]]] = []
    current_line = None
    slots: list[int] = []
    for index, entry in enumerate(mapping):
        if entry is None:
            if current_line is not None:
                slots.append(index)
            continue
        line_id = entry[0]
        if line_id != current_line:
            if current_line is not None and slots:
                out.append((current_line, slots))
            current_line, slots = line_id, []
        slots.append(index)
    if current_line is not None and slots:
        out.append((current_line, slots))
    # A gap at the end of a run belongs to whatever follows, not to this line.
    trimmed = []
    for line_id, group in out:
        while group and mapping[group[-1]] is None:
            group = group[:-1]
        if group:
            trimmed.append((line_id, group))
    return trimmed


def _stay_on_the_line(mapping, anchors, words_by_line) -> None:
    """Do not leave a written line while it still has the word being sung.

    The same word closes more than one line at once - the lead sings 'time.' at
    the end of its line while a harmony sings 'time)' at the end of its own - and
    a match on the word alone can send a voice into the other part's line one
    note before its own line has finished.

    Where the note before this one was still reading a line, and the very next
    word of that line is the word this note sings, the note belongs there.
    """
    used = {entry for entry in mapping if entry is not None}
    for index in range(1, len(mapping)):
        entry, previous = mapping[index], mapping[index - 1]
        if entry is None or previous is None or entry[0] == previous[0]:
            continue
        line_id, position = previous[0], previous[1] + 1
        words = words_by_line.get(line_id, [])
        if position >= len(words) or (line_id, position) in used:
            continue
        if not _same_word(words[position], fold(anchors[index])):
            continue
        used.discard(entry)
        mapping[index] = (line_id, position)
        used.add((line_id, position))


def _enforce_reading_order(mapping, words_by_line) -> None:
    """Sing each written line in the order it is written.

    Only a run that is actually out of sequence is touched, and only when the
    line has a syllable for every note in it - so this straightens what the
    English word order bent, and leaves a part that genuinely sings some of a
    line alone.
    """
    for line_id, slots in _runs(mapping):
        words = words_by_line.get(line_id, [])
        positions = [mapping[s][1] for s in slots if mapping[s] is not None]
        if not positions:
            continue
        here = set(slots)
        shared = any(
            entry is not None and entry[0] == line_id and index not in here
            for index, entry in enumerate(mapping)
        )
        if len(slots) == len(words) and not shared:
            # These notes are the whole of that written line, so they sing all of
            # it, in order - whatever the English word order did to the matching.
            target = range(len(words))
        elif positions != sorted(positions):
            start = min(positions)
            if start + len(slots) > len(words):
                continue
            target = range(start, start + len(slots))
        else:
            continue
        for slot, position in zip(slots, target):
            mapping[slot] = (line_id, position)


def _same_word(a: str, b: str) -> bool:
    """Two folded words, ignoring a quote mark that opens or closes a phrase.

    A line the score sets as ``Love is patient`` is printed in the layout inside
    quotation marks - ``'Love is patient'`` - and the opening mark travels with
    the first word. The mark is punctuation around the phrase, not part of the
    word that is sung, so it must not stop the two from matching.
    """
    return a.strip("'") == b.strip("'") and bool(a.strip("'"))


def _fill_bracketed_gaps(mapping, words_by_line, used) -> None:
    """Fill notes whose place in the layout line is not in doubt.

    Where the notes either side of a gap read from the same layout line and the
    positions they left unread are exactly as many as the notes with nothing on
    them, there is only one way the two can line up. The words themselves may
    differ - a translator writing 'I' where the score sings 'I'll' - but the
    ordering settles it, so the syllables are laid in rather than left out.
    """
    index = 0
    while index < len(mapping):
        if mapping[index] is not None:
            index += 1
            continue
        start = index
        while index < len(mapping) and mapping[index] is None:
            index += 1
        if start == 0 or index >= len(mapping):
            continue
        before, after = mapping[start - 1], mapping[index]
        if before is None or after is None or before[0] != after[0]:
            continue
        line_id, first = before
        last = after[1]
        span = last - first - 1
        if span != index - start or span <= 0:
            continue
        if any((line_id, first + step) in used for step in range(1, span + 1)):
            continue
        if last > len(words_by_line.get(line_id, [])):
            continue
        for offset, slot in enumerate(range(start, index), start=1):
            mapping[slot] = (line_id, first + offset)
            used.add((line_id, first + offset))


MAX_JOINED_BOXES = 3


def _recover_split_words(mapping, anchors, words_by_line, used) -> dict[int, list[int]]:
    """Recover a note whose word the layout breaks into more boxes than the score.

    The score may set a word on a single note - 'OK.' - where the layout gives it
    a box per syllable, 'O-' and 'K.'. Nothing matches either box on its own, so
    the note is left empty. Where consecutive unread boxes spell the word the
    note sings, they belong to it together, and their syllables are sung on that
    one note.

    Returns {note index: [positions]} for the notes filled this way.
    """
    joins: dict[int, list[int]] = {}
    for index, entry in enumerate(mapping):
        if entry is not None:
            continue
        wanted = fold(anchors[index]).strip("'")
        if not wanted:
            continue
        neighbours = []
        for step in (-1, 1):
            probe = index + step
            while 0 <= probe < len(mapping):
                if mapping[probe] is not None:
                    neighbours.append(mapping[probe])
                    break
                probe += step
        found = None
        for line_id, _ in neighbours:
            words = words_by_line.get(line_id, [])
            for start in range(len(words)):
                if (line_id, start) in used:
                    continue
                joined = ""
                for count in range(MAX_JOINED_BOXES):
                    position = start + count
                    if position >= len(words) or (line_id, position) in used:
                        break
                    joined += words[position].strip("'")
                    if count and joined == wanted:
                        found = (line_id, list(range(start, position + 1)))
                        break
                if found:
                    break
            if found:
                break
        if found:
            line_id, positions = found
            mapping[index] = (line_id, positions[0])
            for position in positions:
                used.add((line_id, position))
            joins[index] = positions
    return joins


def repair_word_starts(mapping, translation) -> dict[int, str]:
    """Never begin a note on the tail of a word.

    A harmony part often enters a bar after the lead, so its first note falls on
    the *second* syllable of a word. In English that is harmless — "will not let
    my hands drop down" still reads. In a language where the phrase opens
    "Jeho-vá", starting the part on "vá" is nonsense. Where the syllables before
    it are not sung by this voice anywhere, they are folded onto its first note,
    exactly as a translator does by hand.

    Returns {anchor index: replacement text}.
    """
    fixes: dict[int, str] = {}
    # Where else this voice is already singing, so a syllable that has a note of
    # its own is never also folded onto another one. Without this a syllable the
    # ordering recovered can be sung twice: once where it belongs, and again on
    # the front of the word it starts.
    sung = {entry for entry in mapping if entry is not None}
    for index, entry in enumerate(mapping):
        if entry is None:
            continue
        line_id, position = entry
        if position == 0:
            continue
        if index > 0 and mapping[index - 1] == (line_id, position - 1):
            continue  # the syllable before it is sung, on the previous note
        words = translation.get(line_id)
        if not words or position >= len(words):
            continue
        head: list[str] = []
        back = position - 1
        while (
            back >= 0
            and len(head) < MAX_FOLDED_SYLLABLES
            and (line_id, back) not in sung
            and words[back].rstrip().endswith(("-", "‐", "‑"))
        ):
            head.insert(0, words[back].rstrip())  # keep the hyphen: 'Jeho-vá' reads better
            back -= 1
        # Only worth doing if it actually completes the word back to its start.
        if head and (back < 0 or not words[back].rstrip().endswith(("-", "‐", "‑"))):
            fixes[index] = "".join(head) + words[position]
    return fixes


def _fill_held_notes(line, slice_, english_lines, translation) -> list[tuple]:
    """Put the translation's extra syllables onto the notes the English holds.

    Where the English layout has a blank box, the translator has written a real
    syllable in the box opposite. There is no English syllable printed on that
    note, so the only way to place it is the note itself: the engraving is read
    for notes carrying no lyric, and the syllable goes on the one that falls
    between the syllables either side of the blank.
    """
    if not line.held_notes:
        return []
    by_id = {item.id: item for item in english_lines}
    out: list[tuple] = []
    taken: set[float] = set()
    for offset, entry in enumerate(slice_):
        if entry is None:
            continue
        english = by_id.get(entry[0])
        words = translation.get(entry[0])
        if english is None or words is None:
            continue
        # Blank boxes sitting immediately after the box on this note.
        index = entry[1] + 1
        free = [x for x in line.held_after(offset) if x not in taken]
        while index < len(english.tokens) and english.tokens[index] == BLANK_BOX and free:
            if index < len(words):
                syllable = (words[index] or "").strip()
                if syllable and syllable != BLANK_BOX:
                    out.append((free[0], syllable))
                    taken.add(free[0])
            free = free[1:]
            index += 1
    return out


def align_voice_by_text(
    voice, score_lines, english_lines, translation, section_map, timeline=None
) -> VoicePlan:
    """Build this voice's plan from a word-for-word alignment against the English layout.

    ``translation`` maps an English layout line id to its translated syllables.
    """
    mapping = map_voice_to_layout(voice, score_lines, english_lines, section_map, timeline)
    joins = recover_transposed(mapping, score_lines, english_lines)
    fixes = repair_word_starts(mapping, translation)
    # A word the layout splits across several boxes is sung on one note, so its
    # syllables are set on that note together.
    for index, positions in joins.items():
        entry = mapping[index]
        if entry is None:
            continue
        words = translation.get(entry[0])
        if not words:
            continue
        pieces = [words[p] for p in positions if p < len(words)]
        if pieces:
            fixes[index] = "".join(pieces)

    assignments: list[Assignment] = []
    cursor = 0
    covered = 0
    notes_total = 0
    for line in score_lines:
        need = line.note_count
        slice_ = mapping[cursor : cursor + need]
        base = cursor
        cursor += need
        notes_total += need

        tokens: list[str] = []
        used: list[int] = []
        short = 0
        for offset, entry in enumerate(slice_):
            if entry is None:
                tokens.append("")
                continue
            line_id, index = entry
            words = translation.get(line_id)
            if words is None:
                tokens.append("")
                short += 1
                continue
            if index < len(words):
                tokens.append(fixes.get(base + offset, words[index]))
                covered += 1
            else:
                tokens.append("")
                short += 1
            if line_id not in used:
                used.append(line_id)

        held = _fill_held_notes(line, slice_, english_lines, translation)

        blanks = sum(1 for t in tokens if not t)
        if blanks == 0:
            status, note = "ok", ""
        elif blanks == need:
            status = "unmatched"
            note = (
                "This voice's words were not found in the English layout, so there is nothing "
                "to place here."
            )
        else:
            status = "partial"
            english_gap = sum(1 for e in slice_ if e is None)
            if short and not english_gap:
                note = (
                    "The English line and its translation have a different number of syllables, "
                    "so some notes are still empty."
                )
            else:
                note = "Some notes here could not be matched to a line in the English layout."

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

    matched = sum(1 for a in assignments if a.status == "ok")
    return VoicePlan(
        voice=voice,
        assignments=assignments,
        matched=matched,
        total=len(assignments),
        cost=0.0,
        covered=covered,
        notes_total=notes_total,
    )


def prepare_layout(english_lines, translation, section_map, score_doc):
    """Put the layout in performance order and extend the translation to the repeats.

    Every caller must run this once before aligning, and pass the list it returns
    to both ``reference_timeline`` and ``align_voice_by_text``: the timeline's
    positions are indexes into this exact list. It is safe to call twice.
    """
    order = [name for *_, name in score_doc.sections]
    lines = unroll_to_performance_order(english_lines, section_map, order)
    return lines, expand_translation(translation or {}, lines)


def reference_timeline(score_doc, english_lines, section_map, voices=None) -> dict:
    """Align the busiest voice on its own, and use it as the clock for the rest."""
    grouped = score_doc.lines_by_voice()
    targets = [v for v in (voices if voices is not None else score_doc.voices) if grouped.get(v)]
    if not targets or not english_lines:
        return {}
    reference = max(targets, key=lambda v: sum(line.note_count for line in grouped[v]))
    lines = grouped[reference]
    mapping = map_voice_to_layout(reference, lines, english_lines, section_map)
    return build_timeline(lines, mapping, english_lines)


def align_all_by_text(score_doc, english_lines, translation, section_map, voices=None):
    grouped = score_doc.lines_by_voice()
    targets = voices if voices is not None else score_doc.voices
    english_lines, translation = prepare_layout(
        english_lines, translation, section_map, score_doc
    )
    timeline = reference_timeline(score_doc, english_lines, section_map, voices)
    plans: dict[str, VoicePlan] = {}
    for voice in targets:
        lines = grouped.get(voice, [])
        if not lines:
            continue
        plans[voice] = align_voice_by_text(
            voice, lines, english_lines, translation, section_map, timeline
        )
    return plans
