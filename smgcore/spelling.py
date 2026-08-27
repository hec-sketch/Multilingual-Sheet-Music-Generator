"""Correct the spelling of translated syllables the layout typed plainly.

Some layouts are typed with a tool that cannot produce the language's own
letters. More Than Sparrows / EMB is the clear case: its translation was typed
into the PDF with Acrobat's typewriter in Courier New under WinAnsiEncoding, an
encoding with no `ʉ` in it at all, so the translator typed `dau` for `daʉ`, `bu`
for `bʉ` and a trailing `n` for the nasal tilde - `jen` for `jẽ`. The characters
are not in the file in any form; there is no richer layer to read and nothing
for the app to recover.

What the app must not do is guess them back. `u` is `ʉ` in `bu` and stays `u` in
`ju ru`, so no letter-for-letter rule is true, and inventing orthography in a
song people will sing from is not a small mistake.

What it can do is take the correspondence from whoever knows it, once, and apply
it everywhere. Measured against the finished EMB score: across 39 rows and 351
syllables, 44 distinct plain spellings, **one** plain spelling ever stands for
two different proper ones, and that one is only a capital letter at the start of
a line. So a flat table of `as typed -> as it reads`, with no context and no
cleverness, is enough to correct the whole document - which is why this module is
a dict and not an algorithm.

A map is per language, not per song, so one written for Emberá corrects every
Emberá layout that follows.
"""

from __future__ import annotations

import os
import unicodedata

# A map written for a language is worth keeping, so the ones already written
# live beside the app and are offered by name. Adding a language is a matter of
# putting a .txt here, the way adding a script is a matter of putting a .ttf in
# 'fonts' - neither needs this file changed.
MAP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spelling")


def available() -> dict[str, str]:
    """The maps that ship with the app, by the name each one calls itself."""
    out: dict[str, str] = {}
    if not os.path.isdir(MAP_DIR):
        return out
    for filename in sorted(os.listdir(MAP_DIR)):
        if not filename.lower().endswith(".txt"):
            continue
        path = os.path.join(MAP_DIR, filename)
        out[_title(path) or os.path.splitext(filename)[0]] = path
    return out


def _title(path: str) -> str:
    """A map's own name, taken from its opening comment line."""
    try:
        with open(path, encoding="utf-8") as handle:
            first = handle.readline().strip()
    except OSError:
        return ""
    if not first.startswith("#"):
        return ""
    name = first.lstrip("#").strip()
    for tail in (" spelling map", " map"):
        if name.lower().endswith(tail):
            name = name[: -len(tail)]
            break
    return name.strip()


def load(name: str) -> str:
    """The text of a shipped map, ready to be shown and edited before it is used."""
    path = available().get(name)
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def parse(text: str) -> dict[str, str]:
    """Read a spelling map written as one `typed -> reads` correspondence a line.

    Blank lines and lines opening with `#` are ignored, so a map can carry a note
    saying where it came from. `->`, `=` and a tab all separate the two sides,
    because a map gets written by hand and pasted in.

    A rule may name more than one syllable, and then it matches only where those
    syllables are sung one after another. That is what settles a spelling the
    layout writes the same way in two places and means differently: Emberá `u` is
    `ʉ̃` in `maʉ̃-ʉ̃-rʉ` and plain `u` in `tai u-no-ta`, which no rule about the
    word `u` on its own can tell apart, but `mau u -> maʉ̃ ʉ̃` can.

    Both sides of such a rule must name the same number of syllables. A syllable
    is a note, so a rule that could add or drop one could move the whole song, and
    one that tries is dropped rather than obeyed.
    """
    mapping: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for separator in ("->", "→", "\t", "="):
            if separator in line:
                typed, _, reads = line.partition(separator)
                break
        else:
            continue
        typed, reads = typed.strip(), reads.strip()
        if not typed:
            continue
        if len(typed.split()) != len(reads.split()):
            continue  # would change the note count - see the docstring
        mapping[" ".join(_key(word) for word in typed.split())] = (
            unicodedata.normalize("NFC", reads)
        )
    return mapping


def _phrases(mapping: dict[str, str]) -> list[tuple[list[str], list[str]]]:
    """The rules naming more than one syllable, longest first."""
    out = [
        (typed.split(), reads.split())
        for typed, reads in mapping.items()
        if " " in typed
    ]
    out.sort(key=lambda rule: -len(rule[0]))
    return out


def unparse(mapping: dict[str, str]) -> str:
    return "\n".join(f"{typed} -> {reads}" for typed, reads in sorted(mapping.items()))


def _key(word: str) -> str:
    """What two spellings of the same syllable have in common.

    Matching ignores case, so a syllable at the head of a line is corrected like
    any other, and ignores the punctuation the layout hangs off a syllable - the
    comma in `rea,` and the quotes around `«i` are not part of the word.
    """
    return unicodedata.normalize("NFC", word).strip().lower()


def _restore_case(replacement: str, original: str) -> str:
    """Keep the capital the layout used; the map only says how a word is spelt."""
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _edges(word: str) -> tuple[int, int]:
    """Where the word itself starts and ends inside what is printed on the note."""
    head = 0
    while head < len(word) and not unicodedata.normalize("NFD", word[head])[0].isalnum():
        head += 1
    tail = len(word)
    while tail > head and not unicodedata.normalize("NFD", word[tail - 1])[0].isalnum():
        tail -= 1
    return head, tail


def _core(word: str) -> str:
    """The word without the punctuation the layout printed around it."""
    head, tail = _edges(word)
    return word[head:tail]


def _rebuild(word: str, reads: str) -> str:
    """Put a corrected word back inside the punctuation that surrounded it."""
    head, tail = _edges(word)
    if tail <= head:
        return word
    return word[:head] + _restore_case(reads, word[head:tail]) + word[tail:]


def correct_word(word: str, mapping: dict[str, str]) -> str:
    """Correct one word, leaving the punctuation printed around it alone."""
    if not mapping or not word:
        return word
    core = _core(word)
    if not core:
        return word
    reads = mapping.get(_key(core))
    if reads is None:
        return word
    return _rebuild(word, reads)


def correct_cell(cell: str, mapping: dict[str, str]) -> str:
    """Correct a layout cell, which may hold more than one word on one note."""
    if not mapping or not cell.strip():
        return cell
    return " ".join(correct_word(word, mapping) for word in cell.split(" "))


def apply_to_lines(lines, mapping: dict[str, str]):
    """Correct every cell of the translated half, in place.

    Corrected here, before pairing, so that the same words are what the app pairs,
    what Step 3 shows for checking, what the score is set in and what the
    proofing sheet lists. Nothing downstream needs to know a map exists.

    Syllable counts cannot change - a cell is a note - so pairing and alignment
    are untouched by this and a layout with no map is byte-for-byte the document
    it was.
    """
    if not mapping:
        return lines
    phrases = _phrases(mapping)
    for line in lines:
        tokens = list(line.tokens)
        at = 0
        while at < len(tokens):
            for typed, reads in phrases:
                span = tokens[at:at + len(typed)]
                if len(span) < len(typed):
                    continue
                if [_key(_core(cell)) for cell in span] != typed:
                    continue
                for step, word in enumerate(reads):
                    tokens[at + step] = _rebuild(span[step], word)
                at += len(typed) - 1
                break
            else:
                tokens[at] = correct_cell(tokens[at], mapping)
            at += 1
        line.tokens = tokens
    return lines


def suggestions(lines) -> list[str]:
    """Every distinct word in the translated half, for a map to be written against."""
    seen: dict[str, None] = {}
    for line in lines:
        for token in line.tokens:
            for word in token.split():
                core = _key(word).strip("«».,;:!?\"'()-_")
                if core:
                    seen.setdefault(core, None)
    return sorted(seen)
