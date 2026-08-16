import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import fitz
import streamlit as st

st.set_page_config(page_title="Multi-lingual Sheet Music Generator", page_icon="♪", layout="wide")
MUSIC_CHARS = set("œ˙ÓŒ‰™♩♪♫♬")


def normalize_spacing(text):
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s*-\s*", "-", text.strip())
    return re.sub(r"\s+", " ", text)


def lyric_tokens(text, keep_hyphens=False):
    output = []
    for word in normalize_spacing(text).split():
        parts = [part for part in word.split("-") if part]
        if keep_hyphens and len(parts) > 1:
            output.extend([part + "-" for part in parts[:-1]])
            output.append(parts[-1])
        else:
            output.extend(parts)
    return output


def compare_token(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'")
    return re.sub(r"[^a-z0-9]", "", text)


def extract_layout_pairs(pdf_bytes):
    """Pair each translated annotation with the complete English row above it."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pairs = []
    for page_number, page in enumerate(doc):
        grouped = {}
        for word in page.get_text("words"):
            x0, y0, x1, y1, text = word[:5]
            if any(char.isalpha() for char in text):
                key = round(y0, 1)
                grouped.setdefault(key, []).append((x0, text))
        text_lines = []
        for y, words in grouped.items():
            words.sort()
            text = normalize_spacing(" ".join(word for x, word in words))
            text_lines.append({"y": y, "text": text, "word_count": len(words)})

        annotations = []
        annot = page.first_annot
        while annot:
            content = (annot.info.get("content") or "").strip()
            if content and any(char.isalpha() for char in content):
                annotations.append({"y": annot.rect.y0, "x": annot.rect.x0,
                                    "text": normalize_spacing(content)})
            annot = annot.next
        annotations.sort(key=lambda item: (item["y"], item["x"]))

        for annotation in annotations:
            candidates = [line for line in text_lines
                          if 2 < annotation["y"] - line["y"] < 42
                          and not re.search(r"\bx2\b|\bch3\b", line["text"], re.I)]
            if not candidates:
                raise ValueError(f"Could not find the English layout line above annotation: {annotation['text']}")
            nearest_y = max(line["y"] for line in candidates)
            nearest = [line for line in candidates if nearest_y - line["y"] < 2.0]
            english = max(nearest, key=lambda line: line["word_count"])
            english_tokens = lyric_tokens(english["text"])
            target_tokens = lyric_tokens(annotation["text"], keep_hyphens=True)
            nearby_notes = [line["text"] for line in text_lines
                            if abs(line["y"] - english["y"]) < 3.0
                            and re.search(r"\bx\s*\d+|\bch\s*\d+|repeat", line["text"], re.I)]
            pairs.append({
                "page": page_number, "english": english["text"],
                "translation": annotation["text"], "english_tokens": english_tokens,
                "target_tokens": target_tokens,
                "counts_match": len(english_tokens) == len(target_tokens),
                "repeat_hint": bool(nearby_notes),
                "layout_notes": nearby_notes,
            })
    return pairs


def page_words(page):
    return [tuple(word[:5]) for word in page.get_text("words")]


def is_score_lyric(word, page_height):
    x0, y0, x1, y1, text = word
    if text == "-" or not text.strip() or x0 < 88 or y0 < 55 or y0 > page_height - 35:
        return False
    if text.isdigit() or any(char in MUSIC_CHARS for char in text):
        return False
    if text in {"Verse", "Chorus", "Lead", "M.", "Male", "Full", "Score"}:
        return False
    return any(char.isalpha() for char in text)


def extract_score_anchors(english_bytes, blank_bytes):
    english = fitz.open(stream=english_bytes, filetype="pdf")
    blank = fitz.open(stream=blank_bytes, filetype="pdf")
    if len(english) != len(blank):
        raise ValueError("The English and no-lyrics scores have different page counts.")
    anchors = []
    for page_number, page in enumerate(english):
        candidates = [word for word in page_words(page) if is_score_lyric(word, page.rect.height)]
        counts = {}
        for x0, y0, x1, y1, text in candidates:
            key = round(y0, 1)
            counts[key] = counts.get(key, 0) + 1
        baselines = {key for key, count in counts.items() if count >= 4}
        page_items = []
        for x0, y0, x1, y1, text in candidates:
            if round(y0, 1) in baselines:
                page_items.append({"page": page_number, "x0": x0, "x1": x1,
                                   "y0": y0, "source": text})
        page_items.sort(key=lambda item: (round(item["y0"], 1), item["x0"]))
        anchors.extend(page_items)
    return anchors


def line_match_score(score_tokens, layout_tokens):
    a = [compare_token(token) for token in score_tokens]
    b = [compare_token(token) for token in layout_tokens]
    exact = sum(left == right for left, right in zip(a, b)) / max(1, len(b))
    ratio = SequenceMatcher(None, a, b).ratio()
    return 0.7 * exact + 0.3 * ratio


def match_score_to_layout(anchors, pairs):
    """Segment the score into recognized layout lines, including a final partial refrain."""
    score = [anchor["source"] for anchor in anchors]
    n = len(score)
    dp = [-1.0] * (n + 1)
    back = [None] * (n + 1)
    dp[0] = 0.0
    for position in range(n):
        if dp[position] < 0:
            continue
        for pair_index, pair in enumerate(pairs):
            full_length = len(pair["english_tokens"])
            variants = [(pair["english_tokens"], pair["target_tokens"], False)]
            remaining = n - position
            if 2 <= remaining < full_length:
                variants.append((pair["english_tokens"][-remaining:],
                                 pair["target_tokens"][-remaining:], True))
            for english_variant, target_variant, partial in variants:
                length = len(english_variant)
                end = position + length
                if end > n or (partial and end != n):
                    continue
                confidence = line_match_score(score[position:end], english_variant)
                threshold = 0.82 if partial else 0.72
                if confidence < threshold:
                    continue
                context_bonus = 0.0
                if partial:
                    if pair.get("repeat_hint"):
                        context_bonus += 0.20
                    score_last = score[end - 1].strip()[-1:] if score[end - 1].strip() else ""
                    layout_last = pair["english_tokens"][-1].strip()[-1:] if pair["english_tokens"] else ""
                    if score_last in "!?" and score_last == layout_last:
                        context_bonus += 0.05
                value = dp[position] + confidence + context_bonus - (0.02 if partial else 0)
                if value > dp[end]:
                    dp[end] = value
                    back[end] = (position, pair_index, confidence, target_variant, partial)
    if back[n] is None:
        raise ValueError("The English lyrics in the score could not be matched completely to the English lines in the layout PDF.")

    matches = []
    cursor = n
    while cursor:
        start, pair_index, confidence, target_variant, partial = back[cursor]
        matches.append({"start": start, "end": cursor, "pair": pair_index,
                        "confidence": confidence, "target": target_variant,
                        "partial": partial})
        cursor = start
    matches.reverse()

    target = []
    diagnostics = []
    for match in matches:
        pair = pairs[match["pair"]]
        expected = match["end"] - match["start"]
        if len(match["target"]) != expected:
            raise ValueError(f"The translated line '{pair['translation']}' does not have the required syllable correspondence.")
        target.extend(match["target"])
        english_display = " ".join(pair["english_tokens"][-expected:]) if match["partial"] else pair["english"]
        translation_display = " ".join(match["target"])
        diagnostics.append({
            "english": english_display,
            "translation": translation_display if match["partial"] else pair["translation"],
            "syllables": expected, "confidence": match["confidence"],
            "partial": match["partial"],
            "layout_notes": pair.get("layout_notes", []),
        })
    if len(target) != len(anchors):
        raise ValueError("Final alignment failed. No PDF was generated.")
    return target, diagnostics


def available_width(anchors, index):
    current = anchors[index]
    same_row = [item for item in anchors
                if item["page"] == current["page"] and abs(item["y0"] - current["y0"]) < 0.6]
    same_row.sort(key=lambda item: item["x0"])
    position = same_row.index(current)
    left = 90 if position == 0 else (same_row[position - 1]["x1"] + current["x0"]) / 2
    right = 572 if position == len(same_row) - 1 else (current["x1"] + same_row[position + 1]["x0"]) / 2
    return max(7, right - left - 1), left, right


def create_score(blank_bytes, anchors, target, preferred_size, vertical_offset):
    if len(anchors) != len(target):
        raise ValueError("The final anchor and syllable counts differ.")
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    font_path = Path(__file__).with_name("DejaVuSans.ttf")
    font = fitz.Font(fontfile=str(font_path))
    for page in doc:
        page.insert_font(fontname="MatchedLyrics", fontfile=str(font_path))
    for index, (anchor, syllable) in enumerate(zip(anchors, target)):
        maximum, left, right = available_width(anchors, index)
        natural = font.text_length(syllable, fontsize=preferred_size)
        size = preferred_size if natural <= maximum else max(5.0, preferred_size * maximum / natural)
        width = font.text_length(syllable, fontsize=size)
        center = (anchor["x0"] + anchor["x1"]) / 2
        x = min(max(center - width / 2, left), right - width)
        doc[anchor["page"]].insert_text(
            (x, anchor["y0"] + vertical_offset), syllable,
            fontname="MatchedLyrics", fontsize=size, color=(0, 0, 0), overlay=True,
        )
    return doc.tobytes(garbage=4, deflate=True)


def render_page(pdf_bytes, page_number):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[page_number].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    return pix.tobytes("png")


st.title("Multi-lingual Sheet Music Generator")
st.caption("Matches English lyric lines in the score to the English lines in the layout PDF, then transfers the paired translation syllables.")

c1, c2, c3 = st.columns(3)
with c1:
    english_file = st.file_uploader("English score", type=["pdf"])
with c2:
    blank_file = st.file_uploader("Matching score without lyrics", type=["pdf"])
with c3:
    layout_file = st.file_uploader("Annotated syllable-layout PDF", type=["pdf"])

with st.expander("Optional placement adjustment"):
    a, b = st.columns(2)
    preferred_size = a.slider("Maximum lyric font size", 5.0, 10.0, 7.25, 0.25)
    vertical_offset = b.slider("Vertical baseline offset", 5.0, 10.0, 7.6, 0.1)

if english_file and blank_file and layout_file:
    try:
        pairs = extract_layout_pairs(layout_file.getvalue())
        anchors = extract_score_anchors(english_file.getvalue(), blank_file.getvalue())
        target, matches = match_score_to_layout(anchors, pairs)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Layout line pairs", len(pairs))
        m2.metric("Score lyric positions", len(anchors))
        m3.metric("Matched score lines", len(matches))
        m4.metric("Transferred syllables", len(target))

        unmatched_pairs = [pair for pair in pairs if not pair["counts_match"]]
        if unmatched_pairs:
            st.warning(f"{len(unmatched_pairs)} unused layout line pair(s) have unequal counts. Used lines still passed exact checks.")

        minimum_confidence = min(match["confidence"] for match in matches)
        if minimum_confidence < 0.82:
            st.warning("One or more English line matches have lower confidence. Review the match report before generating.")
        else:
            st.success("All score lines were matched to the layout, and every used line has an exact syllable count.")

        with st.expander("Review line-by-line matching", expanded=False):
            for number, match in enumerate(matches, 1):
                st.markdown(f"**{number}. {match['syllables']} syllables, {match['confidence']:.0%} English match**")
                st.write("English:", match["english"])
                st.write("Translation:", match["translation"])
                if match.get("partial"):
                    note = "; ".join(match.get("layout_notes", [])) or "terminal phrase match"
                    st.caption(f"Partial refrain selected using layout context: {note}")

        if st.button("Generate matched score", type="primary", use_container_width=True):
            st.session_state["finished_pdf"] = create_score(
                blank_file.getvalue(), anchors, target, preferred_size, vertical_offset
            )

        if "finished_pdf" in st.session_state:
            result = st.session_state["finished_pdf"]
            doc = fitz.open(stream=result, filetype="pdf")
            st.subheader("Preview")
            columns = st.columns(min(2, len(doc)))
            for page_number in range(len(doc)):
                columns[page_number % len(columns)].image(
                    render_page(result, page_number), caption=f"Page {page_number + 1}",
                    use_container_width=True,
                )
            st.download_button(
                "Download finished score", result, "matched_translation_score.pdf",
                "application/pdf", type="primary", use_container_width=True,
            )
    except Exception as error:
        st.error(str(error))
else:
    st.info("Upload the English score, no-lyrics score, and annotated syllable-layout PDF.")

st.divider()
st.caption("The app does not infer performance order from counts. It recognizes the English lyrics line by line and transfers only their paired translation lines.")
