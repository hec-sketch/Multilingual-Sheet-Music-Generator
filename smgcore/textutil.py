"""Shared text helpers for lyric and syllable handling."""

from __future__ import annotations

import re
import unicodedata

# Characters produced by music engraving fonts that must never be treated as lyrics.
MUSIC_CHARS = set("œ˙ÓŒ‰™♩♪♫♬∑¢°&?#bwUnjJ|")

# Dash-like characters that all mean "syllable continues".
DASHES = "-‐‑‒–—―−"

# Apostrophe-like characters. Aymara uses U+02BC and U+2019 interchangeably in practice.
APOSTROPHES = "’ʼʾ'´`"

_WS = re.compile(r"\s+")


def normalize_spacing(text: str) -> str:
    """Collapse whitespace and tighten hyphens so 'ju -  paw' becomes 'ju-paw'."""
    if not text:
        return ""
    for dash in DASHES[1:]:
        text = text.replace(dash, "-")
    text = _WS.sub(" ", text.strip())
    # Tighten spaces around hyphens: "ja - si" -> "ja-si"
    text = re.sub(r"\s*-\s*", "-", text)
    return text.strip()


def syllable_tokens(text: str) -> list[str]:
    """Split a lyric line into syllable tokens, keeping trailing hyphens as written.

    'Jeho- va Dio- sajj' -> ['Jeho-', 'va', 'Dio-', 'sajj']
    A token is one note's worth of text. Joining two syllables with no space
    (or with an explicit '~') means they share a single note.
    """
    if not text:
        return []
    out: list[str] = []
    for word in normalize_spacing(text).split(" "):
        if not word:
            continue
        parts = [p for p in word.split("-") if p != ""]
        if not parts:
            continue
        if len(parts) == 1:
            out.append(parts[0])
            continue
        # Re-attach the hyphens that signal "syllable continues".
        pieces = [p + "-" for p in parts[:-1]]
        pieces.append(parts[-1])
        # A word ending with '-' keeps the trailing hyphen on the last piece.
        if word.endswith("-"):
            pieces[-1] = pieces[-1] + "-"
        out.extend(pieces)
    return out


def join_tokens(tokens) -> str:
    """Render tokens back into an editable single-line string."""
    return " ".join(tokens)


def fold(text: str) -> str:
    """Aggressively normalize a token for comparison across languages/encodings."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for ap in APOSTROPHES:
        text = text.replace(ap, "'")
    return re.sub(r"[^a-z0-9']", "", text)


def count_syllables(text: str) -> int:
    return len(syllable_tokens(text))


def is_wordlike(text: str) -> bool:
    """True when the token carries actual letters rather than engraving glyphs."""
    stripped = text.strip()
    if not stripped:
        return False
    if all(ch in MUSIC_CHARS or ch in DASHES for ch in stripped):
        return False
    return any(ch.isalpha() for ch in stripped)


def looks_like_lyric(text: str) -> bool:
    """Filter obvious non-lyric text that shares the lyric font."""
    stripped = text.strip()
    if not stripped or not is_wordlike(stripped):
        return False
    if re.fullmatch(r"[\d\W_]+", stripped):
        return False
    return True
