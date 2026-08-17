"""Multi-lingual Sheet Music Generator.

Transfers a translated syllable layout onto an engraved vocal score.
Everything the matcher decides can be reviewed and corrected before the PDF is made.
"""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import streamlit as st

from smgcore import align as aligner
from smgcore import layout as layout_mod
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


@st.cache_data(show_spinner="Reading the layout...")
def parse_layout_cached(data: bytes):
    return layout_mod.parse_layout(data)


@st.cache_data(show_spinner="Checking the two scores match...")
def geometry_cached(_score_doc, blank: bytes, key: str):
    return render_mod.check_geometry(_score_doc, blank)


@st.cache_data(show_spinner="Working out where the translation is...")
def style_cached(_layout_doc, _score_doc, key: str):
    return aligner.choose_style(_layout_doc, _score_doc, STYLE_OPTIONS)


def digest(*chunks: bytes) -> str:
    hasher = hashlib.sha256()
    for chunk in chunks:
        hasher.update(chunk or b"")
    return hasher.hexdigest()[:16]


def reset_edits(token: str) -> None:
    if st.session_state.get("_files") != token:
        st.session_state["_files"] = token
        st.session_state["layout_edits"] = {}
        st.session_state["assign_edits"] = {}
        st.session_state["skip_voices"] = []
        st.session_state.pop("result_pdf", None)


# --------------------------------------------------------------------------- sidebar

st.title("Multi-lingual Sheet Music Generator")
st.caption(
    "Reads the section labels and syllable counts in your layout, works out which lines each "
    "voice sings, and writes them under the notes. Nothing is final until you say so."
)

with st.sidebar:
    st.header("1. Your three PDFs")
    english_file = st.file_uploader("English score", type=["pdf"], key="english")
    blank_file = st.file_uploader("Same score, no lyrics", type=["pdf"], key="blank")
    layout_file = st.file_uploader("Syllable layout (translation)", type=["pdf"], key="layout")

    st.divider()
    st.header("Placement")
    max_size = st.slider("Maximum text size", 4.0, 12.0, 7.25, 0.25)
    baseline = st.slider("Distance below the staff", 3.0, 14.0, 7.6, 0.1)
    font_choice = st.selectbox("Font", list(render_mod.BUNDLED_FONTS), index=0)

if not (english_file and blank_file and layout_file):
    st.info("Upload all three PDFs in the sidebar to begin.")
    st.markdown(
        """
        **What each file is**

        | File | What it is |
        | --- | --- |
        | English score | The engraved score with the English lyrics under the notes |
        | Same score, no lyrics | The identical engraving with the lyrics removed - this is the canvas |
        | Syllable layout | The translator's document: the translated syllables, laid out line by line |

        The layout may be translation-only or may show English with the translation added as
        comments. Both work. Section labels such as `Ch1`, `1`, `Pre-Ch 2` help a great deal.
        """
    )
    st.stop()

english_bytes = english_file.getvalue()
blank_bytes = blank_file.getvalue()
layout_bytes = layout_file.getvalue()
reset_edits(digest(english_bytes, blank_bytes, layout_bytes))

try:
    score_doc = parse_score_cached(english_bytes)
except Exception as error:  # noqa: BLE001
    st.error(f"The English score could not be read.\n\n{error}")
    st.stop()

try:
    layout_doc = parse_layout_cached(layout_bytes)
except Exception as error:  # noqa: BLE001
    st.error(f"The syllable layout could not be read.\n\n{error}")
    st.stop()

geometry_problems = geometry_cached(score_doc, blank_bytes, digest(english_bytes, blank_bytes))

tab_overview, tab_lines, tab_match, tab_make = st.tabs(
    ["Overview", "Layout lines", "Matching", "Generate"]
)

# --------------------------------------------------------------------------- overview

with tab_overview:
    left, right = st.columns(2)
    with left:
        st.subheader("The score")
        st.metric("Pages", score_doc.page_count)
        st.metric("Voice parts", len(score_doc.voices))
        st.metric("Syllable positions", len(score_doc.anchors))
        st.write("**Sections found**")
        if score_doc.sections:
            st.write(", ".join(name for _, _, _, name in score_doc.sections))
        else:
            st.write("_none_")
        st.write("**Voices found**")
        st.write(", ".join(score_doc.voices))
    with right:
        st.subheader("The layout")
        st.metric("Syllable lines", len(layout_doc.lyric_lines()))
        st.metric("Sections", len(layout_doc.sections))
        st.metric(
            "Lines written as comments",
            f"{layout_doc.annotation_share:.0%}",
        )
        st.write("**Sections found**")
        st.write(", ".join(layout_doc.sections) if layout_doc.sections else "_none_")

    for problem in geometry_problems:
        st.error(problem)
    for warning in score_doc.warnings + layout_doc.warnings:
        st.warning(warning)
    if not geometry_problems:
        st.success("The two scores are the same engraving, so syllables will land on the notes.")

# --------------------------------------------------------------------------- layout lines

with tab_lines:
    st.subheader("Check the translated lines")
    st.caption(
        "One box per note. To sing two syllables on one note, delete the space between them. "
        "To split them again, add a space. Untick a row to leave it out entirely."
    )

    best_style, style_scores = style_cached(
        layout_doc, score_doc, digest(english_bytes, layout_bytes)
    )
    style = st.radio(
        "Where is the translation in this layout?",
        STYLE_OPTIONS,
        index=STYLE_OPTIONS.index(best_style),
        horizontal=True,
        help=(
            "Chosen by trying each reading and seeing which one matches the score. "
            + " | ".join(f"{name}: {value:.0%}" for name, value in style_scores.items())
        ),
    )

    editable = layout_mod.to_editable(layout_doc, style, st.session_state["layout_edits"])
    if not editable:
        st.error("That choice leaves no lines. Pick a different option above.")
        st.stop()

    frame = pd.DataFrame(
        [
            {
                "Use": True,
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
        height=430,
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
    dropped = {int(r["_id"]) for _, r in edited.iterrows() if not r["Use"]}
    st.session_state["dropped_layout"] = dropped
    if changed:
        st.info(f"{changed} line(s) edited. The matching will use your version.")

# Rebuild the working line list from the edits above.
working_lines = [
    line
    for line in layout_mod.to_editable(layout_doc, style, st.session_state["layout_edits"])
    if line.id not in st.session_state.get("dropped_layout", set())
]
# Let the user's Section/Tag edits flow through.
edit_lookup = {int(r["_id"]): r for _, r in edited.iterrows()}
for line in working_lines:
    row = edit_lookup.get(line.id)
    if row is not None:
        line.section = str(row["Section"] or "")
        line.tag = str(row["Tag"] or "")

layout_sections = []
for line in working_lines:
    if line.section and line.section not in layout_sections:
        layout_sections.append(line.section)
score_sections = [name for _, _, _, name in score_doc.sections]

# --------------------------------------------------------------------------- matching

with tab_match:
    st.subheader("Match the layout to the score")

    with st.expander("How sections line up", expanded=False):
        default_map = aligner.build_section_map(layout_sections, score_sections)
        choices = ["(ignore)"] + score_sections
        section_map: dict[str, str] = {}
        columns = st.columns(min(4, max(1, len(layout_sections))))
        for index, label in enumerate(layout_sections):
            with columns[index % len(columns)]:
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
        default=st.session_state.get("skip_voices", []),
        help="Anything selected here is skipped entirely and keeps no lyrics.",
    )
    st.session_state["skip_voices"] = skip
    active_voices = [v for v in score_doc.voices if v not in skip]

    grouped = score_doc.lines_by_voice()
    plans = {}
    for voice in active_voices:
        lines = grouped.get(voice, [])
        if lines:
            plans[voice] = aligner.align_voice(voice, lines, working_lines, section_map)

    summary = pd.DataFrame(
        [
            {
                "Voice": plan.voice,
                "Lines": plan.total,
                "Matched": plan.matched,
                "Needs a look": plan.total - plan.matched,
            }
            for plan in plans.values()
        ]
    )
    st.dataframe(summary, hide_index=True, use_container_width=True)

    trouble = int(summary["Needs a look"].sum()) if not summary.empty else 0
    if trouble:
        st.warning(
            f"{trouble} line(s) could not be matched automatically. Open the voice below and type "
            "the syllables in - you can still generate the PDF either way."
        )
    else:
        st.success("Every line matched. Have a look through, then go to Generate.")

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
                "English": assignment.english,
                "Notes": len(assignment.tokens),
                "Syllables": text,
                "Status": assignment.status,
                "_key": key,
            }
        )
    voice_frame = pd.DataFrame(rows)
    edited_voice = st.data_editor(
        voice_frame,
        hide_index=True,
        use_container_width=True,
        height=430,
        column_config={
            "Page": st.column_config.NumberColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small", disabled=True),
            "English": st.column_config.TextColumn(width="large", disabled=True),
            "Notes": st.column_config.NumberColumn(width="small", disabled=True),
            "Syllables": st.column_config.TextColumn(width="large"),
            "Status": st.column_config.TextColumn(width="small", disabled=True),
            "_key": None,
        },
        key=f"voice_editor_{voice}",
    )
    for _, row in edited_voice.iterrows():
        st.session_state["assign_edits"][row["_key"]] = row["Syllables"]

    mismatches = [
        (row["Page"], len(str(row["Syllables"]).split()), row["Notes"])
        for _, row in edited_voice.iterrows()
        if len(str(row["Syllables"]).split()) != row["Notes"]
    ]
    if mismatches:
        st.error(
            f"{len(mismatches)} line(s) have the wrong number of syllables for their notes. "
            "Join two syllables by deleting the space between them, or split one by adding a space."
        )
        st.dataframe(
            pd.DataFrame(mismatches, columns=["Page", "Syllables given", "Notes available"]),
            hide_index=True,
        )

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
                    f"{need} notes - the extra ones were left off."
                )
                tokens = tokens[:need]
            elif len(tokens) < need:
                issues.append(
                    f"{voice_name}, page {assignment.page + 1}: {len(tokens)} syllables for "
                    f"{need} notes - those notes were left empty."
                )
                tokens = tokens + [""] * (need - len(tokens))
            placements[assignment.score_line_id] = tokens

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
