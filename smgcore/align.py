"""Match layout lines to score lines, voice by voice.

The layout carries no English, so alignment is driven by three signals:

* section labels on both sides (``Ch1`` <-> ``Chorus 1``)
* exact syllable counts, after applying the "two syllables, one note" boxes
* per-line tags (``Harmonies``) that say which voices sing a line

The search is a shortest-path over "which layout lines does this voice sing, in
order", so a voice may skip lines other voices sing, and a musical line may wrap
across systems and pages. Every result stays editable afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INFINITY = float("inf")


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

    @property
    def complete(self) -> bool:
        return self.matched == self.total


# --------------------------------------------------------------------------- sections


def normalize_section(name: str) -> str:
    text = (name or "").strip().lower()
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return f"verse{text}"
    text = re.sub(r"[\.\-_]+", " ", text)
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
    score_by_key = {}
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


# --------------------------------------------------------------------------- costs


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


# --------------------------------------------------------------------------- search


def align_voice(voice, score_lines, layout_lines, section_map, allow_partial=True) -> VoicePlan:
    """Choose which layout lines this voice sings, and spread them over its notes."""
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
            # Skip this layout line: another voice sings it.
            value = base + skip_cost(j, i)
            if value < nxt[i]:
                nxt[i] = value
                back[j + 1][i] = (i, 0, 0)
            # Sing it in full.
            length = counts[j]
            end = i + length
            if length and end <= total:
                value = base + use_cost(j, i)
                if value < nxt[end]:
                    nxt[end] = value
                    back[j + 1][end] = (i, length, 0)
            # Sing only part of it - a last resort for layouts that do not add up.
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
        # Nothing reached the end; take the furthest position we could fill.
        best_i = max((i for i in range(total + 1) if dp[count][i] < INFINITY), default=0)
    else:
        best_i = total

    # Walk the path back into a flat token stream.
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
    return VoicePlan(voice, assignments, matched, len(assignments), dp[count][best_i])


def choose_style(layout_doc, score_doc, styles: list[str]) -> tuple[str, dict]:
    """Work out where the translation lives by trying each reading and seeing which aligns.

    A layout may be translation-only, or English with the translation added as comments.
    Guessing from the proportion of comments is unreliable, so we test instead: the reading
    that lets the busiest voice match cleanly is the right one.
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
        plan = align_voice(probe, probe_lines, lines, mapping, allow_partial=False)
        covered = sum(1 for a in plan.assignments if a.status == "ok")
        scores[style] = covered / max(1, plan.total)

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
