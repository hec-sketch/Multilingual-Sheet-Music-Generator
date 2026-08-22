"""Work from a plain lyrics sheet instead of a box-grid syllable layout.

The normal input is a layout document: a grid with one box per note, which says
exactly which syllable sits on which note. Some translators do not produce one.
What arrives instead is an ordinary page of words, hyphenated at the syllable
breaks and grouped under section headings:

    Chorus 1
    Via-hi vi nji vui-la lio-va
    Ye-ho-va ali na-nge;

That is enough to work from, because the hyphens give the syllable breaks and the
score itself supplies everything the grid would have: which notes exist, what the
English sings on them, and where each phrase ends.

Two things are therefore derived here rather than read from a file:

* the English phrase lines, cut out of the score's own lyrics at its punctuation;
* the correspondence between those phrases and the translator's lines.

Neither is guessed silently. Every line the two disagree about is returned with a
note on it, and the interface shows those lines first so they can be corrected by
hand before anything is drawn.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .align import normalize_section
from .layout import ENGLISH_ROW, EditableLine
from .textutil import fold

# --------------------------------------------------------------------------- reading

# Section headings, in the languages these documents are usually written in. A
# heading is a short line that is nothing but one of these words and a number -
# including the bare abbreviations ('Ch', 'Br') a translator's own worksheet
# often uses in place of the score's full names ('Chorus', 'Bridge').
SECTION_WORD = (
    r"(?:chorus|choru|chor|ch|coro|estribillo|refrain|refr[aã]o|refrein|kor|"
    r"verse|verso|vers|estrofa|strofa|couplet|"
    r"pre[\s\-]*chorus|pre[\s\-]*ch|pre[\s\-]*coro|prechorus|"
    r"bridge|br|puente|ponte|br[uü]cke|pont|"
    r"intro|outro|ending|final|tag|coda|interlude|instrumental)"
)
# Not \b: a number may follow the section word with no space ('Ch1'), and \b
# does not separate two word characters, which a letter and a digit both are.
HEADING_START = re.compile(rf"^\s*{SECTION_WORD}(?![^\W\d_])", re.I)
# What may follow the section word on a heading row: numbers, commas between
# them, and a trailing note in parentheses - 'Ch2, 3' or 'Ch2, 3 (only in ch2)'.
HEADING_TAIL = re.compile(r"^[\s,]*(?:\d+[\s,]*)*[:.]?\s*(?:\([^)]*\))?\s*$")


def heading_labels(row: str) -> list[str] | None:
    """The section name(s) a heading row stands for, or None if it is not one.

    A row naming more than one number - 'Ch2, 3' - is the same written block
    sung at two places in the score, so it stands for both section names, not
    one heading with a stray extra number.
    """
    match = HEADING_START.match(row)
    tail = row[match.end():] if match else ""
    if not match or not HEADING_TAIL.match(tail):
        return None
    word = match.group(0).strip()
    # A parenthesised remark ('(only in ch2)') may itself contain a number; only
    # the numbers naming the heading, before any such remark, are wanted.
    numbers = re.findall(r"\d+", tail.split("(", 1)[0])
    return [f"{word}{n}" for n in numbers] if numbers else [word]


# Split a word into syllables at any kind of hyphen the document might use.
HYPHENS = re.compile(r"[-‐‑‒–—−]")
EDGE_PUNCT = re.compile(r"^[^\w'’]+|[^\w'’]+$", re.UNICODE)


@dataclass
class ProseLine:
    """One written line of the translation, split into syllables at its hyphens."""

    id: int
    section: str
    text: str
    tokens: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tokens)


@dataclass
class LyricsDoc:
    lines: list[ProseLine]
    sections: list[str]
    warnings: list[str] = field(default_factory=list)


def syllables_of(line: str) -> list[str]:
    """The syllables of one written line, one entry per note the translator intends."""
    out: list[str] = []
    for word in line.split():
        cleaned = EDGE_PUNCT.sub("", word)
        if not cleaned:
            continue
        for piece in HYPHENS.split(cleaned):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


NUMERAL_ROW = re.compile(r"^\d+$")


def _visual_rows(page) -> list[tuple[float, str]]:
    """One page's text, grouped by printed row rather than by however many text
    runs the page happens to store.

    Some documents place every word as its own positioned run - fully justified
    text with no shared run to hold them together - which makes a naive read of
    the page's text lines return one word per line. Clustering by y instead of
    trusting the document's own line breaks reconstructs the row a human reads,
    whether it was stored as one run or forty. Returns (y, text) so a caller can
    tell a running header, pinned to the same slot on every page, from a chorus
    that legitimately repeats wherever it falls in that page's own flow.
    """
    pieces: list[tuple[float, float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                pieces.append((line["bbox"][1], line["bbox"][0], text))
    pieces.sort(key=lambda p: (p[0], p[1]))

    rows: list[list[tuple[float, str]]] = []
    row_ys: list[float] = []
    for y0, x0, text in pieces:
        if rows and abs(row_ys[-1] - y0) <= 1.0:
            rows[-1].append((x0, text))
        else:
            rows.append([(x0, text)])
            row_ys.append(y0)
    out = []
    for y, row in zip(row_ys, rows):
        joined = re.sub(r"\s+", " ", " ".join(t for _, t in sorted(row))).strip()
        if joined:
            out.append((y, joined))
    return out


def _page_rows(data: bytes) -> list[list[str]]:
    """Every printed row of the document, one list per page, whether it is a PDF
    or plain text (as a single page).

    A row repeated at the same height on every page - a running title, a job
    code pinned to the header slot - is front matter rather than lyrics, and is
    dropped. A chorus that is simply sung more than once is not: it repeats at
    whatever height it happens to fall on each page, not the same one every
    time, so it survives this test even though its text repeats too. A row that
    is nothing but a number is also dropped - a cue or measure reference some
    translator worksheets print beside each block, not a syllable to place.
    """
    if data[:5] != b"%PDF-":
        raw = data.decode("utf-8", errors="replace")
        return [[line.strip() for line in raw.splitlines() if line.strip()]]

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    per_page = [_visual_rows(page) for page in doc]

    counts: Counter = Counter()
    for rows in per_page:
        for y, text in set(rows):
            counts[(round(y), text)] += 1
    n_pages = len(doc)
    repeated = {key for key, n in counts.items() if n_pages > 1 and n >= max(2, n_pages - 1)}

    out: list[list[str]] = []
    for rows in per_page:
        page_out = [
            text
            for y, text in rows
            if (round(y), text) not in repeated and not NUMERAL_ROW.match(text)
        ]
        out.append(page_out)
    return out


def _parse_rows(rows: list[str]) -> LyricsDoc:
    """Read one language's worth of printed rows into sections of syllable lines.

    The first section is not always headed - a sheet often opens straight into
    Verse 1 and only labels the sections after it - so collecting starts at the
    first row that looks like a lyric (it has more than one syllable hyphen) if
    that comes before any heading does. A heading naming more than one number
    ('Ch2, 3') is registered under every number it names, since that is one
    written block sung at two places in the score rather than one heading with a
    stray number (see ``heading_labels``).
    """
    lines: list[ProseLine] = []
    sections: list[str] = []
    warnings: list[str] = []
    current: list[str] = [""]
    seen_content = False
    next_id = 0
    pending = ""

    def flush() -> None:
        nonlocal pending, next_id
        text = pending.strip()
        pending = ""
        tokens = syllables_of(text)
        if not tokens:
            return
        for label in current:
            lines.append(ProseLine(next_id, label, text, list(tokens)))
            next_id += 1

    for row in rows:
        labels = heading_labels(row)
        if labels is not None:
            flush()
            current = labels
            seen_content = True
            for label in labels:
                if label not in sections:
                    sections.append(label)
            continue
        if not seen_content and len(HYPHENS.findall(row)) < 2:
            # A stray hyphen (a title, a compound word) does not make this a lyric
            # line - a written line breaks several words at their syllables, so it
            # takes more than one hyphen to tell the front matter from the lyrics.
            continue
        seen_content = True
        flush()
        pending = row
    flush()

    if not lines:
        warnings.append(
            "No lyric lines were found. The document needs a section heading such as "
            "'Chorus 1' or 'Verse 1' above each block of lines."
        )
    if lines and not any(HYPHENS.search(line.text) for line in lines):
        warnings.append(
            "No hyphens were found anywhere in the document. Syllables are read from the "
            "hyphens, so without them every word will be treated as a single note."
        )
    return LyricsDoc(lines=lines, sections=sections, warnings=warnings)


def parse_lyrics_document(data: bytes, english_vocabulary=None) -> LyricsDoc:
    """Read a plain lyrics sheet, keeping only the translation if it holds both.

    Some translators send their English source pages and their finished
    translation as one file, one after the other, each headed with its own copy
    of the same section names. Splitting has to happen before the section
    headings are read, not after: read straight through, a heading from the
    English half would otherwise stay in force into the translated half that
    follows it, mislabelling everything up to its own first heading. Each page
    is classified by whether most of its words are already sung in the score
    (the same test ``layout.split_by_language`` uses for a two-language grid),
    and only runs of pages that are NOT English are parsed - each on its own, so
    a heading never leaks from one language into the other.

    With no vocabulary to check against, or where no real split is found, the
    document is parsed as a single language, exactly as ``_parse_rows`` does.
    """
    pages = _page_rows(data)
    whole = [row for page in pages for row in page]
    if not english_vocabulary or len(pages) < 2:
        return _parse_rows(whole)

    labels: list[str] = []
    last = "other"
    for page in pages:
        words = [fold(w).strip("-") for row in page for w in syllables_of(row)]
        words = [w for w in words if w]
        if words:
            share = sum(1 for w in words if w in english_vocabulary) / len(words)
            last = "english" if share >= ENGLISH_ROW else "other"
        labels.append(last)

    if len(set(labels)) < 2:
        return _parse_rows(whole)

    segments: list[tuple[str, list[str]]] = []
    for label, page in zip(labels, pages):
        if segments and segments[-1][0] == label:
            segments[-1][1].extend(page)
        else:
            segments.append((label, list(page)))

    lines: list[ProseLine] = []
    sections: list[str] = []
    warnings: list[str] = []
    next_id = 0
    for label, rows in segments:
        if label != "other":
            continue
        doc = _parse_rows(rows)
        for line in doc.lines:
            lines.append(ProseLine(next_id, line.section, line.text, line.tokens))
            next_id += 1
        sections.extend(name for name in doc.sections if name not in sections)
        warnings.extend(doc.warnings)

    return LyricsDoc(lines=lines, sections=sections, warnings=warnings) if lines else _parse_rows(whole)


INNER_HYPHEN = re.compile(r"\w[-‐‑‒–]\w")


def lyrics_doc_from_lines(lines) -> LyricsDoc:
    """Treat already-parsed layout rows as the written lines of a sheet.

    Used when file 2 holds a grid of the translation but no English to match it
    against. The rows are one-per-note and therefore better than prose, but where
    each row belongs still has to be worked out from the score, which is exactly
    what the lyrics-sheet pairing does.
    """
    return LyricsDoc(
        lines=[
            ProseLine(id=line.id, section=line.section, text=line.text, tokens=list(line.tokens))
            for line in lines
        ],
        sections=list(dict.fromkeys(line.section for line in lines if line.section)),
        warnings=[],
    )


def strip_english_lines(lyrics: LyricsDoc, english_vocabulary) -> LyricsDoc:
    """Drop any lines that are themselves English, when the sheet holds both.

    Some translators send their English source pages and their finished
    translation as one file, one after the other, each under its own copy of the
    same section headings. Every word of the English half is already printed in
    the score, so a line is English when most of its words are - the same test
    ``layout.split_by_language`` uses to tell the two halves of a two-language
    grid apart. Only the translated half is kept; the English score already
    supplies the English side of the pairing.

    Returns ``lyrics`` unchanged if the document turns out to be only one
    language - nothing would be left otherwise.
    """
    if not english_vocabulary:
        return lyrics
    kept: list[ProseLine] = []
    dropped = 0
    for line in lyrics.lines:
        words = [fold(token).strip("-") for token in line.tokens]
        words = [word for word in words if word]
        if not words:
            kept.append(line)
            continue
        share = sum(1 for word in words if word in english_vocabulary) / len(words)
        if share >= ENGLISH_ROW:
            dropped += 1
        else:
            kept.append(line)
    if not dropped or not kept:
        return lyrics
    sections = list(dict.fromkeys(line.section for line in kept if line.section))
    return LyricsDoc(lines=kept, sections=sections, warnings=list(lyrics.warnings))


def prefer_lyrics_sheet(lyrics: LyricsDoc, layout_doc, score_doc) -> bool:
    """Whether file 3 is a plain lyrics sheet rather than a box-grid layout.

    A lyrics sheet is recognised by what it is rather than by what it lacks. Two
    things mark one out: section headings spelled in words, and hyphens inside
    words, because that is how a syllable break is written in running text. A grid
    writes each syllable in its own box, so its hyphens sit at the ends of pieces
    rather than between two letters.

    A grid is preferred wherever a usable one can be read, since it says which
    syllable belongs on which note instead of leaving that to be worked out.
    """
    from .layout import split_by_language, to_editable

    if len(lyrics.lines) < 4 or len(lyrics.sections) < 2:
        return False
    hyphenated = sum(1 for line in lyrics.lines if INNER_HYPHEN.search(line.text))
    if hyphenated < max(2, 0.4 * len(lyrics.lines)):
        return False
    if layout_doc is None:
        return True

    try:
        lines = to_editable(layout_doc)
    except Exception:  # noqa: BLE001
        return True

    # A grid holding both languages is a better source than the same file read as
    # prose. One stray English-looking row - a title, a heading - is not that.
    english, translated = split_by_language(lines, score_doc.sung_words())
    return not (len(english) >= 4 and len(translated) >= 4)


# --------------------------------------------------------------------------- English from the score

PHRASE_END = re.compile(r"[.;:,!?’”\")]\s*$")


def _ends_phrase(token: str) -> bool:
    return bool(PHRASE_END.search(token.strip()))


def reference_voice(score_doc) -> str:
    """The voice carrying the most notes: the one whose words cover the whole piece."""
    grouped = score_doc.lines_by_voice()
    candidates = [v for v in score_doc.voices if grouped.get(v)]
    if not candidates:
        return ""
    return max(candidates, key=lambda v: sum(line.note_count for line in grouped[v]))


def english_phrases(score_doc, voice: str | None = None) -> list[tuple[str, list[str]]]:
    """Cut a voice's English into phrases: [(section, [syllable, ...]), ...].

    The engraver's punctuation marks the ends of sung lines, which is the same
    place a translator breaks their written lines, so the two can be put side by
    side. Sections are kept apart so a phrase never spans two of them. With no
    voice named, the one carrying the most notes is used.
    """
    grouped = score_doc.lines_by_voice()
    voice = voice or reference_voice(score_doc)
    if not voice or not grouped.get(voice):
        return []

    out: list[tuple[str, list[str]]] = []
    buffer: list[str] = []
    current = None
    for line in grouped[voice]:
        if line.section != current:
            if buffer:
                out.append((current or "", buffer))
                buffer = []
            current = line.section
        for anchor in line.anchors:
            buffer.append(anchor.text)
            if _ends_phrase(anchor.text):
                out.append((current or "", buffer))
                buffer = []
    if buffer:
        out.append((current or "", buffer))
    return out


# --------------------------------------------------------------------------- pairing

SPLIT_PENALTY = 1.0   # cutting one sung phrase across two written lines
MERGE_PENALTY = 1.0   # running two sung phrases into one written line
DROP_PROSE = 4.0      # a written line with no phrase to sing it
DROP_PHRASE = 4.0     # a sung phrase with no written line, before the block runs out
MAX_MERGE = 5         # a translation often writes one long sentence for a whole
                       # run of short sung phrases (verse/pre-chorus repeats cut
                       # into many score phrases); allow folding up to this many
                       # consecutive phrases into a single written line, each
                       # extra phrase paying one more MERGE_PENALTY


def _align_block(phrases: list[list[str]], prose: list[ProseLine]):
    """Match one section's sung phrases to its written lines.

    Returns [(tokens, prose_line_or_None), ...] in performance order. A written
    line longer or shorter than the phrase it lands on is still matched: the
    difference is what the interface reports, not a reason to refuse the pairing.
    """
    rows, cols = len(phrases), len(prose)
    if rows == 0:
        return []
    if cols == 0:
        return [(tokens, None) for tokens in phrases]

    INF = float("inf")
    best = [[INF] * (cols + 1) for _ in range(rows + 1)]
    move = [[None] * (cols + 1) for _ in range(rows + 1)]
    best[rows][cols] = 0.0

    def size(index: int) -> int:
        return len(phrases[index])

    for i in range(rows, -1, -1):
        for j in range(cols, -1, -1):
            if i == rows and j == cols:
                continue
            options = []
            if i < rows and j < cols:
                options.append(
                    (abs(size(i) - prose[j].count) + best[i + 1][j + 1], ("match", i, j))
                )
            if i < rows and j + 1 < cols:
                pair = prose[j].count + prose[j + 1].count
                options.append(
                    (abs(size(i) - pair) + SPLIT_PENALTY + best[i + 1][j + 2], ("split", i, j))
                )
            if j < cols:
                for k in range(2, MAX_MERGE + 1):
                    if i + k > rows:
                        break
                    run = sum(size(i + n) for n in range(k))
                    options.append(
                        (
                            abs(run - prose[j].count) + (k - 1) * MERGE_PENALTY + best[i + k][j + 1],
                            ("merge", i, j, k),
                        )
                    )
            if i < rows:
                # Free once the written block is spent: the rest are repeats.
                cost = 0.0 if j == cols else DROP_PHRASE
                options.append((cost + best[i + 1][j], ("phrase", i, j)))
            if j < cols:
                options.append((DROP_PROSE + best[i][j + 1], ("prose", i, j)))
            if options:
                best[i][j], move[i][j] = min(options, key=lambda item: item[0])

    out: list[tuple[list[str], ProseLine | None]] = []
    i = j = 0
    while i < rows or j < cols:
        step = move[i][j]
        if step is None:
            break
        kind = step[0]
        if kind == "match":
            out.append((phrases[i], prose[j]))
            i, j = i + 1, j + 1
        elif kind == "split":
            first = prose[j].count
            out.append((phrases[i][:first], prose[j]))
            out.append((phrases[i][first:], prose[j + 1]))
            i, j = i + 1, j + 2
        elif kind == "merge":
            k = step[3]
            merged: list[str] = []
            for n in range(k):
                merged.extend(phrases[i + n])
            out.append((merged, prose[j]))
            i, j = i + k, j + 1
        elif kind == "phrase":
            out.append((phrases[i], None))
            i += 1
        else:
            out.append(([], prose[j]))
            j += 1
    return [(tokens, line) for tokens, line in out if tokens]


def _key(tokens: list[str]) -> str:
    return " ".join(fold(t).strip("-") for t in tokens if fold(t).strip("-"))


def _reuse(tokens: list[str], seen: dict[str, ProseLine]):
    """Recover the written line(s) for a phrase the score is singing again.

    A repeated phrase is looked up whole first. Failing that it is read from the
    left in the longest pieces that were seen before, because the first time the
    score sang it the phrase may have been cut to fit two written lines, and the
    repeat has to be cut the same way to stay on the same notes.
    """
    whole = seen.get(_key(tokens))
    if whole is not None:
        return [(tokens, whole)]

    out: list[tuple[list[str], ProseLine | None]] = []
    start = 0
    while start < len(tokens):
        for end in range(len(tokens), start, -1):
            found = seen.get(_key(tokens[start:end]))
            if found is not None:
                out.append((tokens[start:end], found))
                start = end
                break
        else:
            return [(tokens, None)]  # nothing recognisable; report it as it stands
    return out


def _add_harmony_surplus(score_doc, english_lines, translation, seen, next_id):
    """Add the repeats only the harmony parts sing, so their notes have words too.

    Counted per section and per phrase: if a voice sings a phrase four times and
    the lines built so far hold it twice, two more are added. They are marked as
    harmony lines and placed at the end of their section, which is enough for the
    matcher to find them - it works from the words, not from where the line sits.
    """
    from collections import Counter

    have: dict[str, Counter] = {}
    for line in english_lines:
        have.setdefault(line.section, Counter())[_key(line.tokens)] += 1

    want: dict[str, Counter] = {}
    shapes: dict[str, list[str]] = {}
    reference = reference_voice(score_doc)
    for voice in score_doc.voices:
        if voice == reference:
            continue
        per_section: dict[str, Counter] = {}
        for section, tokens in english_phrases(score_doc, voice):
            key = _key(tokens)
            shapes.setdefault(key, list(tokens))
            per_section.setdefault(section, Counter())[key] += 1
        for section, counts in per_section.items():
            target = want.setdefault(section, Counter())
            for key, number in counts.items():
                target[key] = max(target[key], number)

    # What each known phrase looks like, and the syllables sitting on it.
    known: dict[str, tuple[list[str], list[str]]] = {}
    for line in english_lines:
        words = translation.get(line.id)
        if words:
            known.setdefault(_key(line.tokens), (list(line.tokens), list(words)))

    extras: dict[str, list] = {}
    for section, counts in want.items():
        held = have.get(section, Counter())
        for key, number in counts.items():
            if not key:
                continue
            found = _borrow(shapes.get(key, key.split()), known)
            if found is None:
                continue  # a phrase the sheet never wrote; reported elsewhere
            for _ in range(number - held.get(key, 0)):
                extras.setdefault(section, []).append(found)

    if not extras:
        return english_lines, translation, next_id

    out: list[EditableLine] = []
    for index, line in enumerate(english_lines):
        out.append(line)
        last_of_section = (
            index + 1 == len(english_lines) or english_lines[index + 1].section != line.section
        )
        if not last_of_section:
            continue
        for tokens, words in extras.pop(line.section, []):
            out.append(
                EditableLine(
                    id=next_id, page=0, section=line.section, tag="Harmonies",
                    tokens=list(tokens), xs=[],
                )
            )
            translation[next_id] = list(words)
            next_id += 1
    return out, translation, next_id


def _part_of(wanted: list[str], known: dict[str, tuple[list[str], list[str]]]):
    """One phrase, matched whole or as the head or tail of a longer known phrase.

    A harmony part often enters late on a line the lead sings whole, so it sings
    'will not let my hands drop down' where the lead sings 'I will not let my
    hands drop down'. That is the same line without its pick-up, so it takes the
    same syllables without their first one. Trimming is only done where the phrase
    and its translation are the same length, since otherwise which syllable to
    drop is a musical decision rather than an arithmetical one.
    """
    exact = known.get(_key(wanted))
    if exact is not None:
        return exact
    folded = _key(wanted).split()
    if not folded:
        return None
    for tokens, words in known.values():
        if len(tokens) != len(words) or len(folded) >= len(tokens):
            continue
        whole = _key(tokens).split()
        if whole[-len(folded):] == folded:
            return tokens[-len(folded):], words[-len(folded):]
        if whole[: len(folded)] == folded:
            return tokens[: len(folded)], words[: len(folded)]
    return None


def _borrow(wanted: list[str], known: dict[str, tuple[list[str], list[str]]]):
    """The words for a phrase a harmony sings, borrowed from the same words elsewhere.

    Matched whole where possible. Failing that the phrase is read from the left in
    the longest known pieces, because a harmony line often runs the tail of one
    sung line straight into the whole of the next with no punctuation between them.
    """
    direct = _part_of(wanted, known)
    if direct is not None:
        return direct

    tokens: list[str] = []
    words: list[str] = []
    start = 0
    while start < len(wanted):
        for end in range(len(wanted), start, -1):
            piece = _part_of(wanted[start:end], known)
            if piece is not None:
                tokens.extend(piece[0])
                words.extend(piece[1])
                start = end
                break
        else:
            return None
    return (tokens, words) if tokens else None


def build_from_lyrics(score_doc, lyrics: LyricsDoc):
    """Pair the score's English with a plain lyrics sheet.

    Returns (english_lines, translation, notes):

    * ``english_lines`` are ``EditableLine``s cut from the score, one per sung
      phrase, in performance order - the same shape the box-grid reader produces.
    * ``translation`` maps each of those line ids to the translated syllables.
    * ``notes`` lists, per line, anything the user should look at.
    """
    phrases = english_phrases(score_doc)
    if not phrases:
        return [], {}, ["The score's English lyrics could not be read, so there is nothing to pair."]

    by_section: dict[str, list[ProseLine]] = {}
    for line in lyrics.lines:
        by_section.setdefault(normalize_section(line.section), []).append(line)

    # Group the sung phrases by section, keeping performance order.
    blocks: list[tuple[str, list[list[str]]]] = []
    for section, tokens in phrases:
        if blocks and blocks[-1][0] == section:
            blocks[-1][1].append(tokens)
        else:
            blocks.append((section, [tokens]))

    # A section label is printed above the bar its music starts in, but a line
    # often begins with a pick-up note before that bar. The engraver leaves that
    # note under the previous label, which strands it: 'I' at the end of one block
    # and 'will not let my hands drop down.' at the start of the next. A trailing
    # phrase that carries no closing punctuation is really the opening of the
    # block after it, so it is moved there before anything is matched.
    for index in range(len(blocks) - 1):
        tail = blocks[index][1]
        if len(tail) > 1 and tail and not _ends_phrase(tail[-1][-1]):
            blocks[index + 1][1].insert(0, tail.pop())

    english_lines: list[EditableLine] = []
    translation: dict[int, list[str]] = {}
    notes: list[str] = []
    seen: dict[str, ProseLine] = {}
    used_sections: set[str] = set()
    next_id = 0

    for index, (section, block) in enumerate(blocks):
        key = normalize_section(section)
        prose = by_section.get(key, [])
        if not prose and index == 0:
            # A sheet often opens straight into its first section without
            # labelling it - the first heading printed is really the second
            # section's. Content collected before any heading was kept under ""
            # and belongs here, since this is the section the score starts on.
            prose = by_section.get("", [])
        if prose:
            used_sections.add(key)
        for tokens, line in _align_block(block, prose):
            if line is None:
                # No written line left: a phrase the score repeats and the sheet
                # wrote once. Reuse whatever was matched to it the first time. The
                # repeat may span several earlier phrases, because the first time
                # round it was cut to fit two written lines, so it is taken apart
                # the same way rather than matched whole.
                pieces = _reuse(tokens, seen)
            else:
                seen.setdefault(_key(tokens), line)
                pieces = [(tokens, line)]

            for piece, source in pieces:
                english_lines.append(
                    EditableLine(
                        id=next_id, page=0, section=section, tag="",
                        tokens=list(piece), xs=[],
                    )
                )
                if source is not None:
                    translation[next_id] = list(source.tokens)
                    if source.count != len(piece):
                        notes.append(
                            f"{section}: “{' '.join(piece)}” has {len(piece)} note(s) but "
                            f"“{source.text.strip()}” has {source.count} syllable(s)."
                        )
                else:
                    notes.append(
                        f"{section}: “{' '.join(piece)}” has no line opposite it in the "
                        "lyrics sheet."
                    )
                next_id += 1

    # A harmony part often sings a phrase more times than the lead does - an extra
    # 'I will not let my hands drop down' behind the melody. The lead's phrases
    # alone cannot cover those notes, so any surplus a voice sings is added to its
    # section, carrying the translation already worked out for that same phrase.
    english_lines, translation, next_id = _add_harmony_surplus(
        score_doc, english_lines, translation, seen, next_id
    )

    missing = [
        name
        for name in lyrics.sections
        if normalize_section(name) not in used_sections
    ]
    if missing:
        notes.append(
            "These headings in the lyrics sheet match no section in the score: "
            + ", ".join(missing)
            + ". Rename them to match the score's own labels."
        )
    return english_lines, translation, notes
