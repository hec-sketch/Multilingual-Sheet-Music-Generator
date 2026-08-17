"""Work out which words each voice sings, and where each syllable goes.

Two engines live here.

**Text alignment** (``align_voice_by_text``) is used when an English syllable
layout has been supplied. Every syllable in the English score is compared against
every syllable in the English layout, and the two are aligned end to end. Because
the words themselves are being matched, the result is not a guess: the app knows
that *this* note carries *that* layout syllable, so the translated syllable
paired with it lands exactly there. Repeats, late entries, dropouts, canons and
lines that wrap across systems and pages all fall out of the alignment for free.

**Count alignment** (``align_voice``) is the fallback for when no English layout
is available. It has nothing to compare words against, so it works from section
labels, syllable counts and per-line tags. It is far more approximate, which is
why supplying the English layout is worth the extra upload.

Both produce the same ``VoicePlan``, and everything they decide stays editable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textutil import fold

INFINITY = float("inf")

# Text-alignment scoring. Positive numbers are rewards, negative are penalties.
SAME_WORD = 2.0
NEAR_WORD = 1.0
WRONG_WORD = -1.5
SECTION_AGREES = 0.4
SECTION_DISAGREES = -1.2
WRONG_VOICE_FOR_TAG = -0.6
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

    @property
    def note_count(self) -> int:
        return len(self.tokens)


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
    text = re.sub(r"\bpre\s*ch(orus|oro)?\b", "prechorus", text)
    text = re.sub(r"\bch(orus|oro)?\b", "chorus", text)
    text = re.sub(r"\bv(erse|s)?\b", "verse", text)
    text = re.sub(r"\bpuente\b", "bridge", text)
    digits = re.findall(r"\d+", text)
    stem = re.sub(r"[^a-z]", "", text)
    return stem + (digits[-1] if digits else "")


def build_section_map(layout_sections: list[str], score_sections: list[str]) -> dict[str, str]:
    """Map layout section labels onto score section names."""
    mapping: dict[str, str] = {}
    score_by_key: dict[str, str] = {}
    for name in score_sections:
        score_by_key.setdefault(normalize_section(name), name)

    unresolved = []
    for label in layout_sections:
        key = normalize_section(label)
        if key in score_by_key:
            mapping[label] = score_by_key[key]
        else:
            unresolved.append(label)

    # Fall back to order when the two documents use different wording.
    if unresolved and len(layout_sections) == len(score_sections):
        for label, name in zip(layout_sections, score_sections):
            mapping.setdefault(label, name)
        for label in unresolved:
            index = layout_sections.index(label)
            mapping[label] = score_sections[index]
    return mapping


# --------------------------------------------------------------------------- voices


def _is_lead(voice: str) -> bool:
    lowered = voice.lower()
    return "lead" in lowered or "solo" in lowered or "melody" in lowered


def _tag_fits(tag: str, voice: str) -> bool:
    if not tag:
        return True
    lowered = tag.lower()
    voice_lower = voice.lower()
    if "harmon" in lowered or "armon" in lowered:
        return not _is_lead(voice)
    if "lead" in lowered or "solo" in lowered:
        return _is_lead(voice)
    if "ad lib" in lowered:
        return "ad lib" in voice_lower
    return True


# --------------------------------------------------------------------------- text alignment


def _word_score(a: str, b: str) -> float:
    if not a or not b:
        return WRONG_WORD
    if a == b:
        return SAME_WORD
    if min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)):
        return NEAR_WORD
    return WRONG_WORD


def map_voice_to_layout(voice, score_lines, english_lines, section_map) -> list[tuple | None]:
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

    left = [(fold(a.text), a.section) for a in anchors]
    right: list[tuple] = []
    for line in english_lines:
        section = section_map.get(line.section, line.section)
        fits = _tag_fits(line.tag, voice)
        for index, token in enumerate(line.tokens):
            right.append((fold(token), section, fits, line.id, index))

    rows, cols = len(left), len(right)
    previous = [0.0] * (cols + 1)  # free start: the layout may begin anywhere
    moves = [[0] * (cols + 1) for _ in range(rows + 1)]

    for i in range(1, rows + 1):
        word, section = left[i - 1]
        current = [0.0] * (cols + 1)
        current[0] = previous[0] + NOTE_WITH_NO_WORD
        moves[i][0] = 1
        for j in range(1, cols + 1):
            other, other_section, fits, _, _ = right[j - 1]
            score = _word_score(word, other)
            if section and other_section:
                score += SECTION_AGREES if section == other_section else SECTION_DISAGREES
            if not fits:
                score += WRONG_VOICE_FOR_TAG
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


def align_voice_by_text(
    voice, score_lines, english_lines, translation, section_map
) -> VoicePlan:
    """Build this voice's plan from a word-for-word alignment against the English layout.

    ``translation`` maps an English layout line id to its translated syllables.
    """
    mapping = map_voice_to_layout(voice, score_lines, english_lines, section_map)
    by_id = {line.id: line for line in english_lines}

    assignments: list[Assignment] = []
    cursor = 0
    covered = 0
    notes_total = 0
    for line in score_lines:
        need = line.note_count
        slice_ = mapping[cursor : cursor + need]
        cursor += need
        notes_total += need

        tokens: list[str] = []
        used: list[int] = []
        short = 0
        for entry in slice_:
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
                tokens.append(words[index])
                covered += 1
            else:
                tokens.append("")
                short += 1
            if line_id not in used:
                used.append(line_id)

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


def align_all_by_text(score_doc, english_lines, translation, section_map, voices=None):
    grouped = score_doc.lines_by_voice()
    targets = voices if voices is not None else score_doc.voices
    plans: dict[str, VoicePlan] = {}
    for voice in targets:
        lines = grouped.get(voice, [])
        if not lines:
            continue
        plans[voice] = align_voice_by_text(
            voice, lines, english_lines, translation, section_map
        )
    return plans


# --------------------------------------------------------------------------- count alignment


def align_voice(voice, score_lines, layout_lines, section_map, allow_partial=True) -> VoicePlan:
    """Fallback for when there is no English layout: choose lines by count and section."""
    slots = [anchor for line in score_lines for anchor in line.anchors]
    total = len(slots)
    if total == 0:
        return VoicePlan(voice, [], 0, 0, 0.0)

    slot_sections = [a.section for a in slots]
    counts = [line.note_count for line in layout_lines]
    line_sections = [section_map.get(line.section, line.section) for line in layout_lines]
    lead = _is_lead(voice)

    def section_at(index: int) -> str:
        if index >= total:
            return slot_sections[-1] if slot_sections else ""
        return slot_sections[index]

    def use_cost(j: int, i: int) -> float:
        cost = 0.0
        here = section_at(i)
        if line_sections[j] and here and line_sections[j] != here:
            cost += 9.0
        if not _tag_fits(layout_lines[j].tag, voice):
            cost += 3.5
        return cost

    def skip_cost(j: int, i: int) -> float:
        here = section_at(i)
        if not _tag_fits(layout_lines[j].tag, voice):
            return 0.05
        if line_sections[j] and here and line_sections[j] != here:
            return 0.1
        return 1.1 if lead else 0.5

    count = len(layout_lines)
    dp = [[INFINITY] * (total + 1) for _ in range(count + 1)]
    back: list[list[tuple | None]] = [[None] * (total + 1) for _ in range(count + 1)]
    dp[0][0] = 0.0

    for j in range(count):
        row, nxt = dp[j], dp[j + 1]
        for i in range(total + 1):
            base = row[i]
            if base == INFINITY:
                continue
            value = base + skip_cost(j, i)
            if value < nxt[i]:
                nxt[i] = value
                back[j + 1][i] = (i, 0, 0)
            length = counts[j]
            end = i + length
            if length and end <= total:
                value = base + use_cost(j, i)
                if value < nxt[end]:
                    nxt[end] = value
                    back[j + 1][end] = (i, length, 0)
            if allow_partial and length > 1:
                penalty = 6.0 + use_cost(j, i)
                for take in range(1, length):
                    end = i + take
                    if end > total:
                        break
                    for offset in (0, length - take):
                        value = base + penalty + (length - take) * 0.4
                        if value < dp[j + 1][end]:
                            dp[j + 1][end] = value
                            back[j + 1][end] = (i, take, offset)

    if dp[count][total] == INFINITY:
        best_i = max((i for i in range(total + 1) if dp[count][i] < INFINITY), default=0)
    else:
        best_i = total

    chosen: list[tuple] = []
    i = best_i
    for j in range(count, 0, -1):
        step = back[j][i]
        if step is None:
            continue
        previous, take, offset = step
        if take:
            chosen.append((j - 1, take, offset))
        i = previous
    chosen.reverse()

    stream: list[str] = []
    provenance: list[int] = []
    partial_lines: set[int] = set()
    for index, take, offset in chosen:
        tokens = layout_lines[index].merged_tokens()
        piece = tokens[offset : offset + take]
        if take != len(tokens):
            partial_lines.add(layout_lines[index].id)
        stream.extend(piece)
        provenance.extend([layout_lines[index].id] * len(piece))

    assignments: list[Assignment] = []
    cursor = 0
    for line in score_lines:
        need = line.note_count
        tokens = stream[cursor : cursor + need]
        ids = sorted(set(provenance[cursor : cursor + need]))
        cursor += len(tokens)
        if len(tokens) < need:
            status = "unmatched" if not tokens else "partial"
            tokens = tokens + [""] * (need - len(tokens))
            note = "No layout syllables were left for this line."
        elif any(i in partial_lines for i in ids):
            status = "partial"
            note = "Only part of a layout line was used here."
        else:
            status = "ok"
            note = ""
        assignments.append(
            Assignment(
                score_line_id=line.id,
                voice=voice,
                page=line.page,
                section=line.section,
                english=line.text,
                tokens=tokens,
                layout_line_ids=ids,
                status=status,
                note=note,
            )
        )

    matched = sum(1 for a in assignments if a.status == "ok")
    filled = sum(1 for a in assignments for t in a.tokens if t)
    return VoicePlan(
        voice=voice,
        assignments=assignments,
        matched=matched,
        total=len(assignments),
        cost=dp[count][best_i],
        covered=filled,
        notes_total=total,
    )


def choose_style(layout_doc, score_doc, styles: list[str], english_lines=None) -> tuple[str, dict]:
    """Work out where the text lives in a layout by trying each reading and testing it.

    A layout may be plain text, or text with the useful part added as comments.
    Guessing from the proportion of comments is unreliable, so we test instead.
    When an English layout is available the test is a word-for-word one; otherwise
    it falls back to seeing which reading lets the busiest voice match cleanly.
    """
    from .layout import to_editable

    grouped = score_doc.lines_by_voice()
    if not grouped:
        return styles[0], {}
    probe = max(grouped, key=lambda v: sum(line.note_count for line in grouped[v]))
    probe_lines = grouped[probe]
    score_sections = [name for _, _, _, name in score_doc.sections]

    scores: dict[str, float] = {}
    for style in styles:
        lines = to_editable(layout_doc, style)
        if not lines:
            scores[style] = -1.0
            continue
        sections: list[str] = []
        for line in lines:
            if line.section and line.section not in sections:
                sections.append(line.section)
        mapping = build_section_map(sections, score_sections)
        if english_lines is None:
            # Reading being tested is itself the text we match against the score.
            found = map_voice_to_layout(probe, probe_lines, lines, mapping)
            scores[style] = sum(1 for entry in found if entry) / max(1, len(found))
        else:
            plan = align_voice(probe, probe_lines, lines, mapping, allow_partial=False)
            scores[style] = sum(1 for a in plan.assignments if a.status == "ok") / max(
                1, plan.total
            )

    best = max(scores, key=lambda s: scores[s])
    return best, scores


def align_all(score_doc, layout_doc, section_map=None, voices=None) -> dict[str, VoicePlan]:
    layout_lines = layout_doc.lyric_lines()
    score_sections = [name for _, _, _, name in score_doc.sections]
    if section_map is None:
        section_map = build_section_map(layout_doc.sections, score_sections)

    grouped = score_doc.lines_by_voice()
    targets = voices if voices is not None else score_doc.voices
    plans: dict[str, VoicePlan] = {}
    for voice in targets:
        lines = grouped.get(voice, [])
        if not lines:
            continue
        plans[voice] = align_voice(voice, lines, layout_lines, section_map)
    return plans
