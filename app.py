"""Multi-lingual Sheet Music Generator.

Writes a translated syllable layout under the notes of an engraved vocal score.

Give it the English syllable layout as well as the translated one and the app
stops guessing: it matches the English layout to the English score word for word,
then puts the translated syllable that sits opposite each English syllable onto
that same note.

Everything it works out is shown back to you and can be corrected before the
PDF is made.
"""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import streamlit as st

from smgcore import align as aligner
from smgcore import layout as layout_mod
from smgcore import pairing as pairing_mod
from smgcore import render as render_mod
from smgcore import score as score_mod

st.set_page_config(page_title="Multi-lingual Sheet Music Generator", page_icon="♪", layout="wide")

STYLE_OPTIONS = [
    "All rows",
    "Only comment/annotation rows",
    "Only page text (ignore comments)",
]


# --------------------------------------------------------------------------- caching


@st.cache_data(show_spinner="Reading the score...")
def parse_score_cached(data: bytes):
    return score_mod.parse_score(data)


@st.cache_data(show_spinner="Reading a layout...")
def parse_layout_cached(data: bytes):
    return layout_mod.parse_layout(data)


@st.cache_data(show_spinner="Checking the two scores match...")
def geometry_cached(_score_doc, blank: bytes, key: str):
    return render_mod.check_geometry(_score_doc, blank)


@st.cache_data(show_spinner="Working out how each layout is written...")
def style_cached(_layout_doc, _score_doc, _english_lines, key: str):
    return aligner.choose_style(_layout_doc, _score_doc, STYLE_OPTIONS, _english_lines)


@st.cache_data(show_spinner="Pairing the English layout with the translation...")
def best_translation_style(_english_lines, _translated_doc, key: str):
    """Pick the reading of the translated layout that pairs best with the English one."""
    results = {}
    for style in STYLE_OPTIONS:
        lines = layout_mod.to_editable(_translated_doc, style)
        if not lines:
            results[style] = -1.0
            continue
        results[style] = pairing_mod.pair_layouts(_english_lines, lines).confidence
    return max(results, key=lambda s: results[s]), results


def digest(*chunks: bytes) -> str:
    hasher = hashlib.sha256()
    for chunk in chunks:
        hasher.update(chunk or b"")
    return hasher.hexdigest()[:16]


def reset_edits(token: str) -> None:
    if st.session_state.get("_files") != token:
        st.session_state["_files"] = token
        for key in ("layout_edits", "english_edits", "pair_overrides", "assign_edits"):
            st.session_state[key] = {}
        st.session_state["skip_voices"] = []
        st.session_state["dropped_layout"] = set()
        st.session_state.pop("result_pdf", None)


for key, default in [
    ("layout_edits", {}),
    ("english_edits", {}),
    ("pair_overrides", {}),
    ("assign_edits", {}),
    ("skip_voices", []),
    ("dropped_layout", set()),
]:
    st.session_state.setdefault(key, default)


# --------------------------------------------------------------------------- uploads

st.title("Multi-lingual Sheet Music Generator")
st.caption(
    "Give it the English layout as well as the translated one and it matches the words "
    "themselves, note for note, instead of counting syllables and hoping."
)

with st.sidebar:
    st.header("Your files")
    english_file = st.file_uploader("1. English score", type=["pdf"], key="english")
    blank_file = st.file_uploader("2. Same score, no lyrics", type=["pdf"], key="blank")
    layout_file = st.file_uploader("3. Translated syllable layout", type=["pdf"], key="layout")
    st.markdown("**4. English syllable layout** — strongly recommended")
    english_layout_file = st.file_uploader(
        "The same layout document before it was translated",
        type=["pdf"],
        key="english_layout",
        label_visibility="collapsed",
    )
    if english_layout_file is None:
        st.info(
            "Without it the app has to match by syllable count alone, which is a much weaker "
            "signal on a choral score."
        )

    st.divider()
    st.header("Placement")
    max_size = st.slider("Maximum text size", 4.0, 12.0, 7.25, 0.25)
    baseline = st.slider("Distance below the staff", 3.0, 14.0, 7.6, 0.1)
    font_choice = st.selectbox("Font", list(render_mod.BUNDLED_FONTS), index=0)

if not (english_file and blank_file and layout_file):
    st.info("Upload your files in the sidebar to begin. The first three are required.")
    st.markdown(
        """
        | File | What it is |
        | --- | --- |
        | 1. English score | The engraved score with the English lyrics under the notes |
        | 2. Same score, no lyrics | The identical engraving with the lyrics removed — this is the canvas |
        | 3. Translated syllable layout | The translator's document: the translated syllables, line by line |
        | 4. English syllable layout | The *same* document before translation, still in English |

        **Why the fourth file matters so much.** The translated layout on its own tells the app
        nothing about *where* in the music each line belongs — it has to work that out from
        section labels and syllable counts, and on a score where six voices sing overlapping
        words that is a hard guess.

        The English layout removes the guessing. The app matches its lines to the English lyrics
        already printed in the score, word for word, so it knows exactly which notes each layout
        line covers. The translated line sitting opposite then drops straight onto those notes.
        """
    )
    st.stop()

english_bytes = english_file.getvalue()
blank_bytes = blank_file.getvalue()
layout_bytes = layout_file.getvalue()
english_layout_bytes = english_layout_file.getvalue() if english_layout_file else b""
reset_edits(digest(english_bytes, blank_bytes, layout_bytes, english_layout_bytes))

try:
    score_doc = parse_score_cached(english_bytes)
except Exception as error:  # noqa: BLE001
    st.error(f"The English score could not be read.\n\n{error}")
    st.stop()

try:
    layout_doc = parse_layout_cached(layout_bytes)
except Exception as error:  # noqa: BLE001
    st.error(f"The translated syllable layout could not be read.\n\n{error}")
    st.stop()

english_layout_doc = None
if english_layout_bytes:
    try:
        english_layout_doc = parse_layout_cached(english_layout_bytes)
    except Exception as error:  # noqa: BLE001
        st.error(
            "The English syllable layout could not be read, so the app has fallen back to "
            f"matching by syllable count.\n\n{error}"
        )

use_text_matching = english_layout_doc is not None
geometry_problems = geometry_cached(score_doc, blank_bytes, digest(english_bytes, blank_bytes))

score_sections = [name for _, _, _, name in score_doc.sections]

tab_names = ["Overview", "Layout lines"]
if use_text_matching:
    tab_names.append("English ↔ translation")
tab_names += ["Matching", "Generate"]
tabs = st.tabs(tab_names)
tab_overview, tab_lines = tabs[0], tabs[1]
tab_pairs = tabs[2] if use_text_matching else None
tab_match, tab_make = tabs[-2], tabs[-1]

# --------------------------------------------------------------------------- overview

with tab_overview:
    if use_text_matching:
        st.success(
            "**Word-for-word matching is on.** The English layout will be matched against the "
            "lyrics already printed in the score, so the app knows which notes every line covers "
            "rather than inferring it from syllable counts."
        )
    else:
        st.warning(
            "**Matching by syllable count.** No English syllable layout was uploaded, so the app "
            "has to work out where each translated line belongs from section labels and counts "
            "alone. Add the English layout in the sidebar for a far more reliable result."
        )

    columns = st.columns(3 if use_text_matching else 2)
    with columns[0]:
        st.subheader("The score")
        st.metric("Pages", score_doc.page_count)
        st.metric("Voice parts", len(score_doc.voices))
        st.metric("Syllable positions", len(score_doc.anchors))
        st.write("**Sections**")
        st.write(", ".join(score_sections) if score_sections else "_none found_")
        st.write("**Voices**")
        st.write(", ".join(score_doc.voices))
    with columns[1]:
        st.subheader("The translation")
        st.metric("Syllable lines", len(layout_doc.lyric_lines()))
        st.metric("Sections", len(layout_doc.sections))
        st.write("**Sections**")
        st.write(", ".join(layout_doc.sections) if layout_doc.sections else "_none found_")
    if use_text_matching:
        with columns[2]:
            st.subheader("The English layout")
            st.metric("Syllable lines", len(english_layout_doc.lyric_lines()))
            st.metric("Sections", len(english_layout_doc.sections))
            st.write("**Sections**")
            st.write(
                ", ".join(english_layout_doc.sections)
                if english_layout_doc.sections
                else "_none found_"
            )

    for problem in geometry_problems:
        st.error(problem)
    warnings = list(score_doc.warnings) + list(layout_doc.warnings)
    if english_layout_doc:
        warnings += [f"English layout: {w}" for w in english_layout_doc.warnings]
    for warning in warnings:
        st.warning(warning)
    if not geometry_problems:
        st.caption("The two scores are the same engraving, so syllables will land on the notes.")

# --------------------------------------------------------------------------- layout lines

english_lines: list = []

with tab_lines:
    if use_text_matching:
        english_style, _ = style_cached(
            english_layout_doc, score_doc, None, digest(english_bytes, english_layout_bytes)
        )
        english_lines = layout_mod.to_editable(
            english_layout_doc, english_style, st.session_state["english_edits"]
        )
        style, style_scores = best_translation_style(
            english_lines, layout_doc, digest(english_layout_bytes, layout_bytes)
        )
        st.caption(
            "Reading of the translated layout chosen by pairing each option against the English "
            "layout: " + " | ".join(f"{k}: {v:.0%}" for k, v in style_scores.items())
        )
    else:
        style, style_scores = style_cached(
            layout_doc, score_doc, None, digest(english_bytes, layout_bytes)
        )

    st.subheader("Check the translated lines")
    st.caption(
        "One box per note. To sing two syllables on one note, delete the space between them. "
        "To split them again, add a space. Untick a row to leave it out entirely."
    )

    style = st.radio(
        "Where is the translation in this layout?",
        STYLE_OPTIONS,
        index=STYLE_OPTIONS.index(style),
        horizontal=True,
    )

    editable = layout_mod.to_editable(layout_doc, style, st.session_state["layout_edits"])
    if not editable:
        st.error("That choice leaves no lines. Pick a different option above.")
        st.stop()

    frame = pd.DataFrame(
        [
            {
                "Use": line.id not in st.session_state["dropped_layout"],
                "Page": line.page + 1,
                "Section": line.section,
                "Tag": line.tag,
                "Notes": line.note_count,
                "Syllables": line.text,
                "Box applied": "yes" if line.inferred_join else "",
                "_id": line.id,
            }
            for line in editable
        ]
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        use_container_width=True,
        height=420,
        column_config={
            "Use": st.column_config.CheckboxColumn(width="small"),
            "Page": st.column_config.NumberColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small"),
            "Tag": st.column_config.TextColumn(width="small"),
            "Notes": st.column_config.NumberColumn(width="small", disabled=True),
            "Syllables": st.column_config.TextColumn(width="large"),
            "Box applied": st.column_config.TextColumn(width="small", disabled=True),
            "_id": None,
        },
        key="layout_editor",
    )

    changed = 0
    for _, row in edited.iterrows():
        original = next(l for l in editable if l.id == row["_id"])
        if row["Syllables"] != original.text:
            st.session_state["layout_edits"][int(row["_id"])] = row["Syllables"]
            changed += 1
    st.session_state["dropped_layout"] = {
        int(r["_id"]) for _, r in edited.iterrows() if not r["Use"]
    }
    if changed:
        st.info(f"{changed} line(s) edited. The matching will use your version.")

    if use_text_matching:
        with st.expander("The English layout, as the app read it", expanded=False):
            st.caption(
                "Edit a line here only if the app misread the original English document."
            )
            english_frame = pd.DataFrame(
                [
                    {
                        "Page": line.page + 1,
                        "Section": line.section,
                        "Tag": line.tag,
                        "Notes": line.note_count,
                        "Syllables": line.text,
                        "_id": line.id,
                    }
                    for line in english_lines
                ]
            )
            edited_english = st.data_editor(
                english_frame,
                hide_index=True,
                use_container_width=True,
                height=320,
                column_config={
                    "Page": st.column_config.NumberColumn(width="small", disabled=True),
                    "Section": st.column_config.TextColumn(width="small"),
                    "Tag": st.column_config.TextColumn(width="small"),
                    "Notes": st.column_config.NumberColumn(width="small", disabled=True),
                    "Syllables": st.column_config.TextColumn(width="large"),
                    "_id": None,
                },
                key="english_editor",
            )
            for _, row in edited_english.iterrows():
                original = next(l for l in english_lines if l.id == row["_id"])
                if row["Syllables"] != original.text:
                    st.session_state["english_edits"][int(row["_id"])] = row["Syllables"]

# Rebuild the working line lists from the edits above.
working_lines = [
    line
    for line in layout_mod.to_editable(layout_doc, style, st.session_state["layout_edits"])
    if line.id not in st.session_state["dropped_layout"]
]
edit_lookup = {int(r["_id"]): r for _, r in edited.iterrows()}
for line in working_lines:
    row = edit_lookup.get(line.id)
    if row is not None:
        line.section = str(row["Section"] or "")
        line.tag = str(row["Tag"] or "")

if use_text_matching:
    english_lines = layout_mod.to_editable(
        english_layout_doc, english_style, st.session_state["english_edits"]
    )

layout_sections: list[str] = []
for line in working_lines:
    if line.section and line.section not in layout_sections:
        layout_sections.append(line.section)

# --------------------------------------------------------------------------- pairing

pairs: list = []
translation: dict[int, list[str]] = {}

if use_text_matching:
    with tab_pairs:
        st.subheader("English line by English line, what the translation says")
        result = pairing_mod.pair_layouts(english_lines, working_lines)
        pairs = result.pairs

        left, right = st.columns([1, 3])
        with left:
            st.metric("Lines paired cleanly", f"{result.confidence:.0%}")
        with right:
            for note in result.notes:
                st.warning(note)
            if not result.notes:
                st.success(
                    "Every English line has a translation opposite it with the same number of "
                    "syllables."
                )

        pair_frame = pd.DataFrame(
            [
                {
                    "Section": pair.section,
                    "Tag": pair.tag,
                    "English": pair.english_text or "—",
                    "Notes": pair.english_count,
                    "Translation": (
                        st.session_state["pair_overrides"].get(
                            pair.english_id, pair.translated_text
                        )
                        if pair.english_id is not None
                        else pair.translated_text
                    ),
                    "Given": pair.translated_count,
                    "Status": {
                        "ok": "",
                        "count": "syllable counts differ",
                        "english-only": "no translation",
                        "translation-only": "no English",
                    }[pair.status],
                    "_eid": -1 if pair.english_id is None else pair.english_id,
                }
                for pair in pairs
            ]
        )
        edited_pairs = st.data_editor(
            pair_frame,
            hide_index=True,
            use_container_width=True,
            height=460,
            column_config={
                "Section": st.column_config.TextColumn(width="small", disabled=True),
                "Tag": st.column_config.TextColumn(width="small", disabled=True),
                "English": st.column_config.TextColumn(width="large", disabled=True),
                "Notes": st.column_config.NumberColumn(width="small", disabled=True),
                "Translation": st.column_config.TextColumn(width="large"),
                "Given": st.column_config.NumberColumn(width="small", disabled=True),
                "Status": st.column_config.TextColumn(width="medium", disabled=True),
                "_eid": None,
            },
            key="pair_editor",
        )
        for _, row in edited_pairs.iterrows():
            eid = int(row["_eid"])
            if eid < 0:
                continue
            original = next((p for p in pairs if p.english_id == eid), None)
            if original is not None and row["Translation"] != original.translated_text:
                st.session_state["pair_overrides"][eid] = row["Translation"]

        st.caption(
            "Type over anything in the Translation column to correct it. Where the two counts "
            "differ, join two syllables by deleting the space between them or split one by "
            "adding a space."
        )

    translation = pairing_mod.translation_map(
        pairs, working_lines, st.session_state["pair_overrides"]
    )

# --------------------------------------------------------------------------- matching

with tab_match:
    st.subheader("Where every syllable is going")

    source_sections = (
        [line.section for line in english_lines if line.section]
        if use_text_matching
        else layout_sections
    )
    ordered_sections: list[str] = []
    for name in source_sections:
        if name not in ordered_sections:
            ordered_sections.append(name)

    with st.expander("How sections line up", expanded=False):
        default_map = aligner.build_section_map(ordered_sections, score_sections)
        choices = ["(ignore)"] + score_sections
        section_map: dict[str, str] = {}
        if ordered_sections:
            grid = st.columns(min(4, len(ordered_sections)))
            for index, label in enumerate(ordered_sections):
                with grid[index % len(grid)]:
                    guess = default_map.get(label)
                    section_map[label] = st.selectbox(
                        label,
                        choices,
                        index=choices.index(guess) if guess in choices else 0,
                        key=f"secmap_{label}",
                    )
        section_map = {k: v for k, v in section_map.items() if v != "(ignore)"}

    skip = st.multiselect(
        "Voices to leave in English",
        score_doc.voices,
        default=st.session_state["skip_voices"],
        help="Anything selected here is skipped entirely and keeps no lyrics.",
    )
    st.session_state["skip_voices"] = skip
    active_voices = [v for v in score_doc.voices if v not in skip]

    grouped = score_doc.lines_by_voice()
    plans: dict[str, aligner.VoicePlan] = {}
    for voice in active_voices:
        lines = grouped.get(voice, [])
        if not lines:
            continue
        if use_text_matching:
            plans[voice] = aligner.align_voice_by_text(
                voice, lines, english_lines, translation, section_map
            )
        else:
            plans[voice] = aligner.align_voice(voice, lines, working_lines, section_map)

    if not plans:
        st.error("Every voice has been left in English. Untick one above to continue.")
        st.stop()

    summary = pd.DataFrame(
        [
            {
                "Voice": plan.voice,
                "Lines": plan.total,
                "Clean": plan.matched,
                "Needs a look": plan.total - plan.matched,
                "Notes filled": f"{plan.covered}/{plan.notes_total}",
                "Coverage": plan.coverage,
            }
            for plan in plans.values()
        ]
    )
    st.dataframe(
        summary,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Coverage": st.column_config.ProgressColumn(
                "Coverage", min_value=0.0, max_value=1.0, format="%.0f%%"
            )
        },
    )

    trouble = int(summary["Needs a look"].sum())
    empty = sum(plan.notes_total - plan.covered for plan in plans.values())
    if trouble:
        st.warning(
            f"{trouble} line(s) are not completely matched and {empty} note(s) would be left "
            "empty. Open the voice below and type the syllables in — you can still generate the "
            "PDF either way."
        )
    else:
        st.success("Every note has a syllable on it. Have a look through, then go to Generate.")

    voice = st.selectbox("Voice to review", list(plans), key="review_voice")
    plan = plans[voice]

    rows = []
    for assignment in plan.assignments:
        key = f"{voice}||{assignment.score_line_id}"
        override = st.session_state["assign_edits"].get(key)
        text = override if override is not None else " ".join(assignment.tokens)
        rows.append(
            {
                "Page": assignment.page + 1,
                "Section": assignment.section,
                "English in the score": assignment.english,
                "Notes": len(assignment.tokens),
                "Syllables": text,
                "Status": assignment.status,
                "_key": key,
            }
        )
    edited_voice = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        height=430,
        column_config={
            "Page": st.column_config.NumberColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small", disabled=True),
            "English in the score": st.column_config.TextColumn(width="large", disabled=True),
            "Notes": st.column_config.NumberColumn(width="small", disabled=True),
            "Syllables": st.column_config.TextColumn(width="large"),
            "Status": st.column_config.TextColumn(width="small", disabled=True),
            "_key": None,
        },
        key=f"voice_editor_{voice}",
    )
    for _, row in edited_voice.iterrows():
        st.session_state["assign_edits"][row["_key"]] = row["Syllables"]

    problems = [
        (row["Page"], len(str(row["Syllables"]).split()), row["Notes"])
        for _, row in edited_voice.iterrows()
        if len(str(row["Syllables"]).split()) != row["Notes"]
    ]
    if problems:
        st.error(
            f"{len(problems)} line(s) have the wrong number of syllables for their notes. "
            "Join two by deleting the space between them, or split one by adding a space."
        )
        st.dataframe(
            pd.DataFrame(problems, columns=["Page", "Syllables given", "Notes available"]),
            hide_index=True,
        )

    notes_for_lines = [a.note for a in plan.assignments if a.note]
    if notes_for_lines:
        with st.expander(f"What the app could not work out for {voice}", expanded=False):
            for text in dict.fromkeys(notes_for_lines):
                st.write("- " + text)

# --------------------------------------------------------------------------- generate

with tab_make:
    st.subheader("Make the PDF")

    placements: dict[int, list[str]] = {}
    issues: list[str] = []
    for voice_name, voice_plan in plans.items():
        for assignment in voice_plan.assignments:
            key = f"{voice_name}||{assignment.score_line_id}"
            text = st.session_state["assign_edits"].get(key)
            tokens = text.split() if text is not None else list(assignment.tokens)
            need = len(assignment.tokens)
            if len(tokens) > need:
                issues.append(
                    f"{voice_name}, page {assignment.page + 1}: {len(tokens)} syllables for "
                    f"{need} notes — the extra ones were left off."
                )
                tokens = tokens[:need]
            elif len(tokens) < need:
                issues.append(
                    f"{voice_name}, page {assignment.page + 1}: {len(tokens)} syllables for "
                    f"{need} notes — those notes were left empty."
                )
                tokens = tokens + [""] * (need - len(tokens))
            placements[assignment.score_line_id] = tokens

    blank_notes = sum(1 for tokens in placements.values() for t in tokens if not t)
    total_notes = sum(len(tokens) for tokens in placements.values())
    st.metric(
        "Notes that will carry a syllable",
        f"{total_notes - blank_notes} of {total_notes}",
    )

    for issue in issues[:12]:
        st.warning(issue)
    if len(issues) > 12:
        st.caption(f"...and {len(issues) - 12} more.")

    if st.button("Generate the score", type="primary", use_container_width=True):
        try:
            settings = render_mod.RenderSettings(
                max_size=max_size, baseline_offset=baseline, font_choice=font_choice
            )
            st.session_state["result_pdf"] = render_mod.render(
                score_doc, blank_bytes, placements, settings
            )
        except Exception as error:  # noqa: BLE001
            st.error(f"The PDF could not be made.\n\n{error}")

    if st.session_state.get("result_pdf"):
        result = st.session_state["result_pdf"]
        st.download_button(
            "Download the finished score",
            result,
            "translated_score.pdf",
            "application/pdf",
            type="primary",
            use_container_width=True,
        )

        report = io.StringIO()
        report.write("voice,page,section,english,syllables\n")
        for voice_name, voice_plan in plans.items():
            for assignment in voice_plan.assignments:
                key = f"{voice_name}||{assignment.score_line_id}"
                text = st.session_state["assign_edits"].get(key, " ".join(assignment.tokens))
                english = assignment.english.replace('"', "'")
                report.write(
                    f'"{voice_name}",{assignment.page + 1},"{assignment.section}",'
                    f'"{english}","{text}"\n'
                )
        st.download_button(
            "Download a checking sheet (CSV)",
            report.getvalue().encode("utf-8"),
            "alignment_report.csv",
            "text/csv",
            use_container_width=True,
        )

        st.write("**Preview**")
        import pymupdf as fitz

        total = fitz.open(stream=result, filetype="pdf").page_count
        page_pick = st.number_input("Page", 1, total, 1)
        st.image(render_mod.page_image(result, int(page_pick) - 1, 2.0), use_container_width=True)
