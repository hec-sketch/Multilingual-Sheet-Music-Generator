"""Pair the English half of a syllable layout with its translated half.

The document is written the same way every time: the English layout in full,
then the same layout again underneath with the translated words in the same
boxes. So pairing is not an alignment problem. Pages are taken in order, rows
are read off together, and the nth box of the translated row is set against the
nth box of the English one - by the column it sits in, so a box the translator
left empty is seen as the empty column it is rather than shifting everything
after it along by one.

Whatever does not line up is reported rather than guessed at, and every line is
shown back and can be repaired by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .layout import BLANK_BOX


@dataclass
class Pair:
    """One row of the English-to-translation correspondence."""

    english_id: int | None
    translated_id: int | None
    english_text: str = ""
    translated_text: str = ""
    section: str = ""
    tag: str = ""
    english_count: int = 0
    translated_count: int = 0
    status: str = "ok"  # ok | count | english-only | translation-only

    @property
    def paired(self) -> bool:
        return self.english_id is not None and self.translated_id is not None

    @property
    def counts_agree(self) -> bool:
        return self.paired and self.english_count == self.translated_count


@dataclass
class PairingResult:
    pairs: list[Pair]
    confidence: float
    notes: list[str]

    def translation_for(self) -> dict[int, list[str]]:
        """{english layout line id: translated syllables} for the paired rows only."""
        return {}


def _is_harmony_row(line) -> bool:
    """Whether the layout explicitly says this row is harmony-only.

    Color/marker classification is already performed by the layout parser, so
    pairing must respect it as a semantic constraint. A yellow harmony row is
    allowed to exist only on the harmony side of the finished score; it must
    never be paired to an ordinary lead row just because it occupies the next
    visual row.
    """
    return "harmon" in (getattr(line, "tag", "") or "").lower()


def _row_xs(line):
    xs = list(getattr(line, "xs", None) or [])
    if not xs:
        tokens = getattr(line, "tokens", None) or []
        xs = [getattr(t, "x0", 0.0) for t in tokens]
    return xs


def _normalized(xs):
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    span = max(hi - lo, 1.0)
    return [(x - lo) / span for x in xs]


def _row_geometry(a, b) -> float:
    """Similarity of the *box columns*, not the printed word widths."""
    ax, bx = _row_xs(a), _row_xs(b)
    if not ax or not bx:
        return 0.0
    na, nb = _normalized(ax), _normalized(bx)
    if len(na) == len(nb):
        err = sum(abs(x - y) for x, y in zip(na, nb)) / max(1, len(na))
        return max(0.0, 1.0 - min(err / 0.12, 1.0))
    # Different token counts can still be the same written row. Compare endpoints
    # and density; this is intentionally tolerant because joined syllable boxes and
    # held-note blanks make the raw counts differ between languages.
    count_ratio = min(len(na), len(nb)) / max(len(na), len(nb))
    endpoint = 1.0 - min(abs(na[0] - nb[0]) + abs(na[-1] - nb[-1]), 1.0)
    return 0.55 * count_ratio + 0.45 * endpoint


# A row marked harmony on one half only may still be that row on the other, but
# only when the two grids agree this closely. Below it, the row is an extra one.
HARMONY_RESCUE_GEOMETRY = 0.88

# How much more it costs to leave the *first* row of a half unpaired than the
# last. Both halves open on the song's first line, so a gap at the top is the
# less likely reading. Deliberately small: it only decides between two readings
# the grid itself cannot tell apart, and must never outweigh what the grid does
# say (a pairing scores around 17). Raising it to 2.0 loses seven rows on
# jwb-143 / Aymara.
EARLY_GAP = 1.5


def _may_pair(a, b) -> bool:
    """Whether these two rows are allowed to be the same row of the song.

    A harmony-only row is the one hard constraint. A translator writes harmony
    rows the English half does not spell out, and they are short - a two-syllable
    answering phrase against a whole sung line. Nothing about the grid says they
    belong together, so a harmony row must never be handed to a lead: it would
    both put the wrong words on the lead's notes and push every row after it out
    of step.

    The one exception is a row whose harmony colour survived in only one of the
    two exports. There the grids still agree cell for cell, so the geometry says
    what the lost colour would have.
    """
    if _is_harmony_row(a) == _is_harmony_row(b):
        return True
    return _row_geometry(a, b) >= HARMONY_RESCUE_GEOMETRY


def _pair_score(a, b) -> float:
    """Score two rows for correspondence using structure and color/metadata."""
    geom = _row_geometry(a, b)
    score = 8.0 * geom
    ac, bc = a.note_count, b.note_count
    if ac and bc:
        score += 2.5 * (min(ac, bc) / max(ac, bc))
    else:
        score -= 4.0
    if _is_harmony_row(a) == _is_harmony_row(b):
        score += 6.0
    else:
        # Only reachable when the geometry rescued the pairing above.
        score += 0.5
    sa = (a.section or "").strip().lower()
    sb = (b.section or "").strip().lower()
    if sa and sb and sa == sb:
        score += 2.5
    elif sa and sb:
        score -= 1.5
    return score


def _pair_page_rows(above, below):
    """Monotone DP over rows, with explicit harmony-aware insert/delete states."""
    n, m = len(above), len(below)
    NEG = -10**9
    gap = -5.5

    def gap_cost(index: int, count: int) -> float:
        """What it costs to leave the row at ``index`` with nothing opposite it.

        Both halves are the same song and both start at its first line, so a row
        near the top of one half almost certainly has a counterpart near the top
        of the other. A half that runs out of rows runs out at the end. Leaving an
        opening row unpaired is therefore treated as the more suspect reading, and
        the aligner only does it when the rows genuinely cannot go together.
        """
        return gap - EARLY_GAP * (1.0 - index / max(count - 1, 1))

    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    move = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur == NEG:
                continue
            if i < n and j < m and _may_pair(above[i], below[j]):
                val = cur + _pair_score(above[i], below[j])
                if val > dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = val
                    move[i + 1][j + 1] = ("pair", i, j)
            if i < n:
                val = cur + gap_cost(i, n)
                if val > dp[i + 1][j]:
                    dp[i + 1][j] = val
                    move[i + 1][j] = ("english-only", i, j)
            if j < m:
                val = cur + gap_cost(j, m)
                if val > dp[i][j + 1]:
                    dp[i][j + 1] = val
                    move[i][j + 1] = ("translation-only", i, j)
    out = []
    i, j = n, m
    while i or j:
        mv = move[i][j]
        if mv is None:
            break
        kind, pi, pj = mv
        out.append((kind, pi if kind != "translation-only" else None,
                    pj if kind != "english-only" else None))
        i, j = pi, pj
    out.reverse()
    return out


def _semantic_class(line) -> str:
    """Semantic routing class used before any word/count matching.

    Explicit section/tag metadata is preferred over color when both exist.  Color
    then fills in rows whose textual section marker is missing.  Harmony remains a
    hard semantic class regardless of section label.
    """
    color = (getattr(line, "color_class", "") or "").lower()
    if color == "harmony" or _is_harmony_row(line):
        return "harmony"

    section = (getattr(line, "section", "") or "").strip().lower()
    if "bridge" in section or section in {"br", "bridge"}:
        return "bridge"
    if "pre-ch" in section or "prechor" in section:
        return "prechorus"
    if re.search(r"(^|[^a-z])ch(?:orus)?\s*\d|^ch\d", section):
        return "chorus"
    if "verse" in section or re.fullmatch(r"\d+", section):
        return "verse"

    if color in {"prechorus", "chorus"}:
        return color
    return "neutral"

# Rows used to be bucketed by section/colour and then zipped inside each
# bucket, which is how a bridge row came to be paired with the song's opening
# line: one bucket having an extra row shifted only that bucket. Pairing is
# monotone again (``_pair_page_rows``), and the semantics that mattered - a
# harmony row never going to a lead - are enforced there as a constraint.


def pair_layouts(english_lines, translated_lines) -> PairingResult:
    notes: list[str] = []
    if not english_lines or not translated_lines:
        return PairingResult([], 0.0, ["One of the two halves had no syllable lines in it."])

    english_pages = _by_page(english_lines)
    translated_pages = _by_page(translated_lines)
    pairs: list[Pair] = []
    ragged = 0
    harmony_unpaired = 0

    if len(english_pages) != len(translated_pages):
        notes.append(
            f"The English half is {len(english_pages)} page(s) and the translated half is "
            f"{len(translated_pages)}."
        )
        english_pages = [[line for page in english_pages for line in page]]
        translated_pages = [[line for page in translated_pages for line in page]]

    for above, below in zip(english_pages, translated_pages):
        for kind, ai, bj in _pair_page_rows(above, below):
            a = above[ai] if ai is not None else None
            b = below[bj] if bj is not None else None
            if kind == "pair":
                harmony = _semantic_class(a) == "harmony" or _semantic_class(b) == "harmony"
                pairs.append(Pair(
                    english_id=a.id, translated_id=b.id,
                    english_text=a.text, translated_text=b.text,
                    section=a.section or b.section,
                    tag="Harmonies" if harmony else (a.tag or b.tag),
                    english_count=a.note_count, translated_count=b.note_count,
                    status=_count_status(a, b),
                ))
            elif kind == "english-only":
                ragged += 1
                pairs.append(Pair(
                    english_id=a.id, translated_id=None,
                    english_text=a.text, section=a.section, tag=a.tag or ("Harmonies" if _semantic_class(a)=="harmony" else ""),
                    english_count=a.note_count, status="english-only",
                ))
            else:
                ragged += 1
                if _semantic_class(b) == "harmony":
                    harmony_unpaired += 1
                pairs.append(Pair(
                    english_id=None, translated_id=b.id,
                    translated_text=b.text, section=b.section,
                    tag=b.tag or ("Harmonies" if _semantic_class(b)=="harmony" else ""),
                    translated_count=b.note_count, status="translation-only",
                ))

    if ragged:
        notes.append(
            f"{ragged} line(s) have nothing opposite them, so the two halves do not hold the same number of rows."
        )
    if harmony_unpaired:
        notes.append(
            f"{harmony_unpaired} harmony-only row(s) were kept separate. Yellow/Harmonies rows never shift ordinary/Lead rows."
        )
    gaps = [p for p in pairs if p.status == "count"]
    if gaps:
        notes.append(f"{len(gaps)} line(s) have a different number of syllable columns in the two languages.")
    clean = sum(1 for p in pairs if p.status == "ok")
    return PairingResult(pairs=pairs, confidence=clean / max(1, len(pairs)), notes=notes)



def align_by_column(english, translated) -> list[str]:
    """Map translation boxes to the English note columns by physical position.

    The syllable layout is a positional key: each printed cell corresponds to a
    note column.  Counts alone are unsafe because one language may use more or
    fewer cells than the other.  This mapper therefore uses the normalized x
    position of every translated cell and assigns it to the nearest English
    column.  Multiple translated cells may legitimately land on one English
    note; they are kept together rather than dropped.
    """
    ex, tx = english.xs, translated.xs
    if not ex or not tx or len(ex) != len(english.tokens) or len(tx) != len(translated.tokens):
        return list(translated.tokens)

    def normalize(xs):
        lo = xs[0]
        hi = xs[-1]
        span = max(hi - lo, 1.0)
        return [(x - lo) / span for x in xs]

    epos = normalize(ex)
    tpos = normalize(tx)
    # English blank boxes still represent a real note column, so keep a slot for
    # them. Rendering may later use the held-note machinery for those columns.
    out = [""] * len(english.tokens)

    # Dynamic programming over monotonic box order.  This handles missing cells,
    # inserted cells, and two translated cells close together without letting a
    # later match jump backward.
    n, m = len(epos), len(tpos)
    INF = 10**9
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    move = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    miss_penalty = 0.18
    extra_penalty = 0.22

    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur >= INF:
                continue
            if i < n and cur + miss_penalty < dp[i + 1][j]:
                dp[i + 1][j] = cur + miss_penalty
                move[i + 1][j] = ("miss", i, j)
            if j < m and cur + extra_penalty < dp[i][j + 1]:
                dp[i][j + 1] = cur + extra_penalty
                move[i][j + 1] = ("extra", i, j)
            if i < n and j < m:
                cost = abs(epos[i] - tpos[j])
                if cost < 0.34 and cur + cost < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cur + cost
                    move[i + 1][j + 1] = ("pair", i, j)

    # If the strict path is unavailable because the two rows have very different
    # box counts, fall back to nearest-column assignment rather than dropping
    # translation cells altogether.
    if dp[n][m] >= INF:
        for j, pos in enumerate(tpos):
            i = min(range(n), key=lambda k: abs(epos[k] - pos))
            token = translated.tokens[j]
            out[i] = f"{out[i]} {token}".strip()
        return out

    matches: list[tuple[int, int]] = []
    i, j = n, m
    while i or j:
        step = move[i][j]
        if step is None:
            break
        kind, pi, pj = step
        if kind == "pair":
            matches.append((pi, pj))
        i, j = pi, pj
    matches.reverse()
    for ei, tj in matches:
        token = translated.tokens[tj]
        out[ei] = f"{out[ei]} {token}".strip() if out[ei] else token

    # Cells skipped as 'extra' by the DP are assigned to the nearest English
    # column. This is crucial for preserving a multi-syllable translated word
    # when the English has fewer syllable cells at that point.
    paired_t = {tj for _, tj in matches}
    for tj, token in enumerate(translated.tokens):
        if tj in paired_t:
            continue
        ei = min(range(n), key=lambda k: abs(epos[k] - tpos[tj]))
        out[ei] = f"{out[ei]} {token}".strip() if out[ei] else token
    return out


def _count_status(english, translated) -> str:
    """Whether a difference in the two syllable counts is a problem.

    It is not a problem when the English line has blank boxes: those are notes the
    English holds a syllable across, and a translator is free to leave them empty
    too. Only a difference the blanks cannot account for needs a human.
    """
    if english.note_count == translated.note_count:
        return "ok"
    blanks = sum(1 for token in english.tokens if token == BLANK_BOX)
    if blanks and english.note_count - blanks == translated.note_count:
        return "ok"
    return "count"


def _by_page(lines) -> list[list]:
    """The lines grouped into the pages they were written on, in order."""
    pages: dict[int, list] = {}
    for line in lines:
        pages.setdefault(line.page, []).append(line)
    return [pages[page] for page in sorted(pages)]


def translation_map(
    pairs: list[Pair], translated_lines, overrides=None, english_lines=None
) -> dict[int, list[str]]:
    """{english layout line id: translated syllable tokens}, honouring user edits."""
    overrides = overrides or {}
    by_id = {line.id: line for line in translated_lines}
    english_by_id = {line.id: line for line in (english_lines or [])}
    out: dict[int, list[str]] = {}
    for pair in pairs:
        if pair.english_id is None:
            continue
        if pair.english_id in overrides:
            # A list is one entry per note, so a note may hold two syllables with a
            # space between them. A plain string is still split on spaces.
            value = overrides[pair.english_id]
            out[pair.english_id] = (
                [str(token) for token in value]
                if isinstance(value, (list, tuple))
                else str(value).split()
            )
            continue
        if pair.translated_id is None:
            continue
        line = by_id.get(pair.translated_id)
        if line is None:
            continue
        english = english_by_id.get(pair.english_id)
        # The layout boxes are positional data, not merely a count.  Whenever
        # both rows carry those x-positions, use them even when the token counts
        # happen to match.  A count-only copy can silently put a translated token
        # on the wrong note when the translator left a box empty, used a different
        # number of syllables, or grouped text differently.
        if english is not None and english.xs and line.xs:
            out[pair.english_id] = align_by_column(english, line)
        elif english is not None and len(english.tokens) != len(line.tokens):
            out[pair.english_id] = align_by_column(english, line)
        else:
            out[pair.english_id] = list(line.tokens)
    return out
