"""Multi-lingual Sheet Music Generator.

Writes a translated syllable layout under the notes of an engraved vocal score.

The interface is five numbered steps. Each step answers one question, says
whether anything needs attention, and lists exactly which rows to look at
before showing the full editable table.
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

ICONS = {"ok": "✓", "warn": "!", "err": "✕", "todo": "○"}
COLOURS = {"ok": "green", "warn": "orange", "err": "red", "todo": "gray"}


# --------------------------------------------------------------------------- caching


@st.cache_data(show_spinner="Reading the score...", max_entries=4)
def parse_score_cached(data: bytes):
    return score_mod.parse_score(data)


@st.cache_data(show_spinner="Reading a layout...", max_entries=8)
def parse_layout_cached(data: bytes):
    return layout_mod.parse_layout(data)


@st.cache_data(show_spinner="Checking the two scores match...", max_entries=4)
def geometry_cached(_score_doc, blank: bytes, key: str):
    return render_mod.check_geometry(_score_doc, blank)


@st.cache_data(show_spinner="Working out how the layout is written...", max_entries=4)
def style_cached(_layout_doc, _score_doc, _english_lines, key: str):
    return aligner.choose_style(_layout_doc, _score_doc, STYLE_OPTIONS, _english_lines)


@st.cache_data(show_spinner="Pairing the English layout with the translation...", max_entries=4)
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


# Everything the app keeps between reruns, apart from the four uploaders. The
# tables and pickers all carry a key so their contents survive a rerun, which is
# exactly what must NOT survive a change of piece: a half-finished edit to row 40
# of a 52-line layout means nothing to a 23-line one, and a voice picked from the
# last score may not exist in this one.
STATE_PREFIXES = ("secmap_", "voice_editor_", "grid_editor_", "solo_")
STATE_KEYS = (
    "layout_edits",
    "english_edits",
    "pair_overrides",
    "assign_edits",
    "skip_voices",
    "dropped_layout",
    "layout_style",
    "layout_editor",
    "english_editor",
    "pair_editor",
    "review_voice",
    "grid_line",
    "result_pdf",
)


def clear_work() -> None:
    """Forget everything worked out about the piece currently loaded."""
    for key in list(st.session_state.keys()):
        if key in STATE_KEYS or key.startswith(STATE_PREFIXES):
            del st.session_state[key]


def start_another_project() -> None:
    """The button: drop the files, the corrections, and everything made from them.

    Emptying an upload box is the one thing clearing session state cannot do — the
    browser keeps showing the file. Counting the uploaders up instead gives them
    new keys, so Streamlit builds them fresh and they come back empty.
    """
    clear_work()
    st.session_state.pop("_files", None)
    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
    st.cache_data.clear()


def reset_edits(token: str) -> None:
    """A new set of files means every correction from the last one is stale."""
    if st.session_state.get("_files") != token:
        st.session_state["_files"] = token
        clear_work()


def seed_state() -> None:
    """Put back the empty containers the rest of the script reads. Runs after any reset."""
    for key, default in [
        ("layout_edits", {}),
        ("english_edits", {}),
        ("pair_overrides", {}),
        ("assign_edits", {}),
        ("skip_voices", []),
        ("dropped_layout", set()),
        ("upload_round", 0),
    ]:
        st.session_state.setdefault(key, default)


seed_state()


# --------------------------------------------------------------------------- shared UI


def step_header(number: int, title: str, question: str) -> None:
    """Every step opens the same way: what it is, and the one question it answers."""
    st.subheader(f"Step {number} · {title}")
    st.markdown(f"**{question}**")


def verdict(count: int, clean: str, todo: str) -> None:
    """One banner per step: either nothing to do, or exactly what to do."""
    if count:
        st.warning(f"**{count} to check.**  {todo}")
    else:
        st.success(f"**Nothing to fix here.**  {clean}")


def show_boxes(text: str) -> str:
    """A blank box is a note with no syllable on it. Show it as one, not as a dash."""
    return " ".join("▫" if token == layout_mod.BLANK_BOX else token for token in text.split())


def attention_table(rows: list[dict], caption: str) -> None:
    """The specific rows to look at, before the full table below."""
    if not rows:
        return
    st.markdown(f"**{caption}**")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


# --------------------------------------------------------------------------- files

st.title("Multi-lingual Sheet Music Generator")

with st.sidebar:
    st.header("Your files")
    round_ = st.session_state["upload_round"]
    english_file = st.file_uploader("1. English score", type=["pdf"], key=f"english{round_}")
    blank_file = st.file_uploader("2. Same score, no lyrics", type=["pdf"], key=f"blank{round_}")
    layout_file = st.file_uploader("3. Syllable layout", type=["pdf"], key=f"layout{round_}")
    english_layout_file = st.file_uploader(
        "4. English syllable layout — only if it is a separate file",
        type=["pdf"],
        key=f"english_layout{round_}",
    )

    st.divider()
    st.header("How the syllables look")
    max_size = st.slider("Maximum text size", 4.0, 12.0, 7.25, 0.25)
    baseline = st.slider("Distance below the staff", 3.0, 14.0, 7.6, 0.1)
    font_choice = st.selectbox("Font", list(render_mod.BUNDLED_FONTS), index=0)

    st.divider()
    st.button(
        "Start another project",
        on_click=start_another_project,
        width='stretch',
        help="Clears the files, every correction you have made, and the finished PDF, "
        "and takes you back to an empty upload screen.",
    )

uploaded = {
    "1. English score": (english_file, True, "The engraving with the English lyrics under the notes"),
    "2. Same score, no lyrics": (blank_file, True, "The identical engraving with the lyrics removed"),
    "3. Syllable layout": (layout_file, True, "The translator's document"),
    "4. English syllable layout": (
        english_layout_file,
        False,
        "Only needed if the English is not already in file 3",
    ),
}

if not all(handle for handle, required, _ in uploaded.values() if required):
    st.info("Upload the first three files in the sidebar to begin.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": ICONS["ok"] if handle else (ICONS["todo"] if required else ""),
                    "File": name,
                    "What it is": description,
                    "Status": (
                        "uploaded"
                        if handle
                        else ("still needed" if required else "only if needed")
                    ),
                }
                for name, (handle, required, description) in uploaded.items()
            ]
        ),
        hide_index=True,
        width='stretch',
    )
    st.stop()

english_bytes = english_file.getvalue()
blank_bytes = blank_file.getvalue()
layout_bytes = layout_file.getvalue()
english_layout_bytes = english_layout_file.getvalue() if english_layout_file else b""
reset_edits(digest(english_bytes, blank_bytes, layout_bytes, english_layout_bytes))
seed_state()


def stop_with_a_way_out(title: str, detail: str) -> None:
    """Never leave the page dead. Anything unexpected still offers a fresh start."""
    st.error(f"**{title}**\n\n{detail}")
    st.button(
        "Start another project",
        key=f"restart_{abs(hash(title)) % 10000}",
        on_click=start_another_project,
        type="primary",
    )
    st.stop()

try:
    score_doc = parse_score_cached(english_bytes)
except Exception as error:  # noqa: BLE001
    stop_with_a_way_out("File 1, the English score, could not be read.", str(error))

try:
    layout_doc = parse_layout_cached(layout_bytes)
except Exception as error:  # noqa: BLE001
    stop_with_a_way_out("File 3, the syllable layout, could not be read.", str(error))

geometry_problems = geometry_cached(score_doc, blank_bytes, digest(english_bytes, blank_bytes))
score_sections = [name for _, _, _, name in score_doc.sections]
score_words = score_doc.sung_words()


# --------------------------------------------------------------------------- the pipeline
#
# Everything is worked out before anything is drawn, so each tab can be labelled
# with what it found. Corrections made in the tables live in session state, so
# they are already folded in by the time this runs on the next interaction.

# Some translators keep the English and the translation in one file, some in two.
# Every English syllable is already printed in the score, so which rows are which
# is read off the file rather than asked about.
one_document = bool(
    layout_mod.split_by_language(layout_mod.to_editable(layout_doc), score_words)[0]
)

if one_document:
    english_layout_doc = None
    style_scores = {}
    style = st.session_state.get("layout_style", "All rows")
    combined = layout_mod.to_editable(
        layout_doc,
        style,
        {**st.session_state["english_edits"], **st.session_state["layout_edits"]},
    )
    english_lines, editable_lines = layout_mod.split_by_language(combined, score_words)
else:
    if not english_layout_file:
        st.error(
            "**The English syllable layout is missing.** File 3 holds only one language, so "
            "the app has nothing to match against the English in your score. Upload the "
            "English layout as file 4, or upload a file 3 that contains both languages."
        )
        st.stop()
    try:
        english_layout_doc = parse_layout_cached(english_layout_bytes)
    except Exception as error:  # noqa: BLE001
        stop_with_a_way_out("File 4, the English syllable layout, could not be read.", str(error))
    english_style, _ = style_cached(
        english_layout_doc, score_doc, None, digest(english_bytes, english_layout_bytes)
    )
    english_lines = layout_mod.to_editable(
        english_layout_doc, english_style, st.session_state["english_edits"]
    )
    suggested_style, style_scores = best_translation_style(
        english_lines, layout_doc, digest(english_layout_bytes, layout_bytes)
    )
    style = st.session_state.get("layout_style", suggested_style)
    editable_lines = layout_mod.to_editable(layout_doc, style, st.session_state["layout_edits"])

working_lines = [
    line for line in editable_lines if line.id not in st.session_state["dropped_layout"]
]

pair_result = pairing_mod.pair_layouts(english_lines, working_lines)
pairs = pair_result.pairs
translation = pairing_mod.translation_map(
    pairs, working_lines, st.session_state["pair_overrides"], english_lines
)

ordered_sections: list[str] = []
for line in english_lines:
    if line.section and line.section not in ordered_sections:
        ordered_sections.append(line.section)

default_map = aligner.build_section_map(ordered_sections, score_sections)
section_map: dict[str, frozenset] = {}
for label in ordered_sections:
    guess = [n for n in score_sections if n in aligner.section_set(default_map.get(label))]
    chosen = st.session_state.get(f"secmap_{label}", guess)
    if chosen:
        section_map[label] = frozenset(chosen)

unmapped_sections = [label for label in ordered_sections if label not in section_map]

skip = st.session_state["skip_voices"]
active_voices = [v for v in score_doc.voices if v not in skip]
grouped = score_doc.lines_by_voice()

# A section the layout writes once may be sung several times, so the layout is first
# repeated into the order the score actually sings it. The timeline's positions are
# indexes into that list, so both steps below must be given the same one.
aligned_lines, aligned_translation = aligner.prepare_layout(
    english_lines, translation, section_map, score_doc
)
timeline = aligner.reference_timeline(score_doc, aligned_lines, section_map)

# A repeated chorus is a copy with its own id; corrections belong to the line the
# translator actually wrote, so map every copy back to it.
repeat_origin = {
    line.id: (line.repeat_of if line.repeat_of is not None else line.id)
    for line in aligned_lines
}

plans: dict[str, aligner.VoicePlan] = {}
for voice_name in active_voices:
    voice_lines = grouped.get(voice_name, [])
    if voice_lines:
        plans[voice_name] = aligner.align_voice_by_text(
            voice_name, voice_lines, aligned_lines, aligned_translation, section_map, timeline
        )


# Which voices sing each line of the layout. Two voices land on the same layout
# line when the alignment gave them the same words, whether they sing them together
# or a bar apart — so a syllable corrected once is corrected for all of them.
voices_by_layout_line: dict[int, set] = {}
for voice_name, voice_plan in plans.items():
    for assignment in voice_plan.assignments:
        for line_id in assignment.layout_line_ids:
            origin = repeat_origin.get(line_id, line_id)
            voices_by_layout_line.setdefault(origin, set()).add(voice_name)


def edited_tokens(voice_name: str, assignment) -> list[str]:
    """The syllables for one score line, including anything typed over them."""
    override = st.session_state["assign_edits"].get(f"{voice_name}||{assignment.score_line_id}")
    return override.split() if override is not None else list(assignment.tokens)


# --------------------------------------------------------------------------- what needs doing

layout_trouble = [
    line for line in working_lines if not line.tokens or line.note_count == 0
]
pair_trouble = [pair for pair in pairs if pair.status != "ok"]
mismatched_lines = []
empty_notes = 0
for voice_name, voice_plan in plans.items():
    for assignment in voice_plan.assignments:
        tokens = edited_tokens(voice_name, assignment)
        need = len(assignment.tokens)
        empty_notes += sum(1 for index in range(need) if index >= len(tokens) or not tokens[index])
        if len(tokens) != need:
            mismatched_lines.append((voice_name, assignment, len(tokens), need))

score_state = "err" if geometry_problems else ("warn" if score_doc.warnings else "ok")
layout_state = "warn" if layout_trouble else "ok"
pair_state = "warn" if pair_trouble else "ok"
notes_state = "warn" if (mismatched_lines or empty_notes or unmapped_sections) else "ok"
ready_state = "ok" if st.session_state.get("result_pdf") else "todo"

steps = [
    ("Score", score_state),
    ("Syllables", layout_state),
    ("Translation", pair_state),
    ("Notes", notes_state),
    ("PDF", ready_state),
]
tabs = st.tabs([f"{ICONS[state]}  {i} · {name}" for i, (name, state) in enumerate(steps, 1)])
tab_score, tab_lines, tab_pairs, tab_match, tab_make = tabs


# --------------------------------------------------------------------------- 1 · Score

with tab_score:
    step_header(1, "Score", "Did the app read your two score PDFs correctly?")
    verdict(
        len(geometry_problems),
        "The two scores are the same engraving, so every syllable will land on a note.",
        "The scores below do not line up. Syllables cannot be placed until this is fixed.",
    )
    for problem in geometry_problems:
        st.error(problem)

    left, middle, right = st.columns(3)
    with left:
        st.markdown("**The score**")
        st.metric("Pages", score_doc.page_count)
        st.metric("Voice parts", len(score_doc.voices))
        st.metric("Notes to fill", len(score_doc.anchors))
    with middle:
        st.markdown("**Read from the score**")
        st.write("Sections")
        st.write(", ".join(score_sections) if score_sections else "_none found_")
        st.write("Voices")
        st.write(", ".join(score_doc.voices))
    with right:
        st.markdown("**Read from the layout**")
        st.metric("English lines", len(english_lines))
        st.metric("Translated lines", len(working_lines))
        st.write(
            "Both languages were found in file 3."
            if one_document
            else "English from file 4, translation from file 3."
        )

    warnings = list(score_doc.warnings)
    warnings += [f"Layout: {w}" for w in layout_doc.warnings]
    if english_layout_doc is not None:
        warnings += [f"English layout: {w}" for w in english_layout_doc.warnings]
    if warnings:
        st.markdown("**Worth knowing**")
        for warning in warnings:
            st.warning(warning)


# --------------------------------------------------------------------------- 2 · Syllables

with tab_lines:
    step_header(2, "Syllables", "Did the app read the translator's document correctly?")
    verdict(
        len(layout_trouble),
        "Every line in the translated layout was read as a row of syllables.",
        "These lines came through empty. Type the syllables in, or untick Use to leave them out.",
    )
    attention_table(
        [{"Page": line.page + 1, "Section": line.section, "Line": line.text or "(empty)"}
         for line in layout_trouble],
        "Lines to look at",
    )

    st.markdown("---")
    st.markdown(
        "**One box per note.** Delete the space between two syllables to sing them on one "
        "note; add a space to split them again. Untick **Use** to leave a line out."
    )

    frame = pd.DataFrame(
        [
            {
                "Use": line.id not in st.session_state["dropped_layout"],
                "Page": line.page + 1,
                "Section": line.section,
                "Tag": line.tag,
                "Notes": line.note_count,
                "Syllables": line.text,
                "Blank": sum(1 for tk in line.tokens if tk == layout_mod.BLANK_BOX) or "",
                "Joined": "yes" if line.inferred_join else "",
                "_id": line.id,
            }
            for line in editable_lines
        ]
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width='stretch',
        height=420,
        column_config={
            "Use": st.column_config.CheckboxColumn(width="small"),
            "Page": st.column_config.NumberColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small"),
            "Tag": st.column_config.TextColumn(width="small"),
            "Notes": st.column_config.NumberColumn(
                width="small", disabled=True, help="How many notes these syllables cover"
            ),
            "Syllables": st.column_config.TextColumn(width="large"),
            "Blank": st.column_config.TextColumn(
                width="small",
                disabled=True,
                help="Boxes with only a dash in them: notes where a syllable is held rather "
                "than a new one sung. Written as - in the Syllables column.",
            ),
            "Joined": st.column_config.TextColumn(
                width="small", disabled=True, help="A box in the PDF joined syllables onto one note"
            ),
            "_id": None,
        },
        key="layout_editor",
    )

    changed = 0
    for _, row in edited.iterrows():
        original = next(l for l in editable_lines if l.id == row["_id"])
        if row["Syllables"] != original.text:
            st.session_state["layout_edits"][int(row["_id"])] = row["Syllables"]
            changed += 1
        original.section = str(row["Section"] or "")
        original.tag = str(row["Tag"] or "")
    st.session_state["dropped_layout"] = {
        int(r["_id"]) for _, r in edited.iterrows() if not r["Use"]
    }
    if changed:
        st.info(f"{changed} line(s) edited. Everything after this step uses your version.")

    with st.expander("Advanced — where the translation sits in the document", expanded=False):
        st.write(
            "The app tried each way of reading the file and kept whichever matched the English "
            "best. Change it only if the table above is reading the wrong text."
        )
        if style_scores:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Reading": name,
                            "Match": f"{value:.0%}" if value >= 0 else "no lines found",
                        }
                        for name, value in style_scores.items()
                    ]
                ),
                hide_index=True,
                width='stretch',
            )
        st.radio(
            "Reading to use",
            STYLE_OPTIONS,
            index=STYLE_OPTIONS.index(style),
            key="layout_style",
            horizontal=True,
        )

    with st.expander("Advanced — the English layout, as the app read it", expanded=False):
        st.write("Edit a line here only if the app misread the original English document.")
        english_frame = pd.DataFrame(
            [
                {
                    "Page": line.page + 1,
                    "Section": line.section,
                    "Tag": line.tag,
                    "Notes": line.note_count,
                    "Syllables": line.text,
                    "Blank": sum(1 for tk in line.tokens if tk == layout_mod.BLANK_BOX) or "",
                    "_id": line.id,
                }
                for line in english_lines
            ]
        )
        edited_english = st.data_editor(
            english_frame,
            hide_index=True,
            width='stretch',
            height=320,
            column_config={
                "Page": st.column_config.NumberColumn(width="small", disabled=True),
                "Section": st.column_config.TextColumn(width="small"),
                "Tag": st.column_config.TextColumn(width="small"),
                "Notes": st.column_config.NumberColumn(width="small", disabled=True),
                "Syllables": st.column_config.TextColumn(width="large"),
                "Blank": st.column_config.TextColumn(width="small", disabled=True),
                "_id": None,
            },
            key="english_editor",
        )
        for _, row in edited_english.iterrows():
            original = next(l for l in english_lines if l.id == row["_id"])
            if row["Syllables"] != original.text:
                st.session_state["english_edits"][int(row["_id"])] = row["Syllables"]


# --------------------------------------------------------------------------- 3 · Translation

STATUS_TEXT = {
    "ok": "",
    "count": "counts differ",
    "english-only": "no translation",
    "translation-only": "no English line",
}

with tab_pairs:
    step_header(3, "Translation", "Is each English line sitting beside the right translation?")
    verdict(
        len(pair_trouble),
        "Every English line has a translation opposite it with the same number of syllables.",
        "Check the rows below. Where the counts differ, join two syllables by deleting the "
        "space between them, or split one by adding a space.",
    )
    attention_table(
        [
            {
                "Section": pair.section,
                "English": show_boxes(pair.english_text) or "—",
                "Notes": pair.english_count,
                "Translation": show_boxes(pair.translated_text) or "—",
                "Syllables": pair.translated_count,
                "Issue": STATUS_TEXT[pair.status],
            }
            for pair in pair_trouble
        ],
        "Lines to look at",
    )
    for note in pair_result.notes:
        st.info(note)

    st.markdown("---")
    st.markdown(
        f"**{pair_result.confidence:.0%} of lines paired cleanly.** "
        "Type over anything in the **Translation** column to correct it."
    )

    pair_frame = pd.DataFrame(
        [
            {
                "": ICONS["ok"] if pair.status == "ok" else ICONS["warn"],
                "Section": pair.section,
                "Tag": pair.tag,
                "English": show_boxes(pair.english_text) or "—",
                "Notes": pair.english_count,
                "Translation": (
                    st.session_state["pair_overrides"].get(pair.english_id, pair.translated_text)
                    if pair.english_id is not None
                    else pair.translated_text
                ),
                "Syllables": pair.translated_count,
                "_eid": -1 if pair.english_id is None else pair.english_id,
            }
            for pair in pairs
        ]
    )
    edited_pairs = st.data_editor(
        pair_frame,
        hide_index=True,
        width='stretch',
        height=460,
        column_config={
            "": st.column_config.TextColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small", disabled=True),
            "Tag": st.column_config.TextColumn(width="small", disabled=True),
            "English": st.column_config.TextColumn(width="large", disabled=True),
            "Notes": st.column_config.NumberColumn(
                width="small", disabled=True, help="Notes this line has in the score"
            ),
            "Translation": st.column_config.TextColumn(width="large"),
            "Syllables": st.column_config.NumberColumn(
                width="small", disabled=True, help="Syllables the translator wrote"
            ),
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

    # ------------------------------------------------------------------ one line, note by note
    st.markdown("---")
    st.markdown("**Work on one line, note by note**")
    st.write(
        "One row per note, with the English word that sits on it. Type the syllable straight "
        "into the box. Two syllables in one box are sung on that one note; an empty box leaves "
        "the note held. A change here applies to **every voice that sings this line**."
    )

    english_by_id = {line.id: line for line in english_lines}
    choices = [
        pair.english_id
        for pair in pairs
        if pair.english_id is not None and pair.english_id in english_by_id
    ]
    if choices:
        def line_label(eid: int) -> str:
            line = english_by_id[eid]
            singers = len(voices_by_layout_line.get(eid, ()))
            mark = "" if any(p.english_id == eid and p.status == "ok" for p in pairs) else "  !"
            voices = "1 voice" if singers == 1 else f"{singers} voices"
            return f"[{line.section or '—'}]  {show_boxes(line.text)[:52]}   ({voices}){mark}"

        picked = st.selectbox(
            "Line to work on", choices, format_func=line_label, key="grid_line"
        )
        english_line = english_by_id[picked]
        current = translation.get(picked, [])
        singers = sorted(voices_by_layout_line.get(picked, ()))

        st.caption(
            ("Sung by " + ", ".join(singers)) if singers
            else "No voice is singing this line at the moment."
        )

        grid = pd.DataFrame(
            [
                {
                    "Note": index + 1,
                    "English": (
                        "▫ held" if token == layout_mod.BLANK_BOX else token
                    ),
                    "Syllable": (current[index] if index < len(current) else ""),
                }
                for index, token in enumerate(english_line.tokens)
            ]
        )
        edited_grid = st.data_editor(
            grid,
            hide_index=True,
            width='stretch',
            height=min(420, 60 + 35 * len(grid)),
            column_config={
                "Note": st.column_config.NumberColumn(width="small", disabled=True),
                "English": st.column_config.TextColumn(
                    width="medium", disabled=True, help="The English sung on this note"
                ),
                "Syllable": st.column_config.TextColumn(
                    width="medium", help="Leave empty to sing nothing new on this note"
                ),
            },
            key=f"grid_editor_{picked}",
        )
        typed = [str(v or "").strip() for v in edited_grid["Syllable"].tolist()]
        if typed != [str(t) for t in current]:
            st.session_state["pair_overrides"][picked] = typed
            st.info(
                "Saved. "
                + (f"Applied to {len(singers)} voice(s): " + ", ".join(singers) if singers
                   else "No voice sings this line yet.")
            )


# --------------------------------------------------------------------------- 4 · Notes

with tab_match:
    step_header(4, "Notes", "Is every note getting the syllable it should?")
    verdict(
        len(mismatched_lines) + len(unmapped_sections) + (1 if empty_notes else 0),
        "Every note has a syllable on it and every section is accounted for.",
        f"{empty_notes} note(s) would be left empty. Pick the voice below and type the "
        "syllables in — you can still make the PDF either way.",
    )

    if unmapped_sections:
        st.error(
            "**Not every section is placed:** "
            + ", ".join(f"`{name}`" for name in unmapped_sections)
            + ". Open *Which part of the score each section is sung in* below and tick where "
            "each one belongs."
        )

    attention_table(
        [
            {
                "Voice": voice_name,
                "Page": assignment.page + 1,
                "Section": assignment.section,
                "English in the score": assignment.english,
                "Notes": need,
                "Syllables given": given,
            }
            for voice_name, assignment, given, need in mismatched_lines[:25]
        ],
        "Lines to look at",
    )
    if len(mismatched_lines) > 25:
        st.caption(f"...and {len(mismatched_lines) - 25} more.")

    st.markdown("---")

    with st.expander(
        "Which part of the score each section is sung in",
        expanded=bool(unmapped_sections),
    ):
        st.write(
            "A section written once in the layout can be sung several times in the score — one "
            "**Ch** block covering Chorus 1, 2 and 3. Tick every place it is sung."
        )
        if ordered_sections:
            grid = st.columns(min(3, len(ordered_sections)))
            for index, label in enumerate(ordered_sections):
                with grid[index % len(grid)]:
                    key = f"secmap_{label}"
                    extra = (
                        {}
                        if key in st.session_state
                        else {
                            "default": [
                                n
                                for n in score_sections
                                if n in aligner.section_set(default_map.get(label))
                            ]
                        }
                    )
                    st.multiselect(label, score_sections, key=key, **extra)

    st.multiselect(
        "Voices to leave in English",
        score_doc.voices,
        key="skip_voices",
        help="Anything selected here is skipped entirely and keeps no lyrics.",
    )

    if not plans:
        st.error("Every voice has been left in English. Untick one above to continue.")
        st.stop()

    st.markdown("**Every voice at a glance**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": ICONS["ok"] if plan.covered == plan.notes_total else ICONS["warn"],
                    "Voice": plan.voice,
                    "Lines": plan.total,
                    "Notes filled": f"{plan.covered} / {plan.notes_total}",
                    "Coverage": round(plan.coverage * 100),
                }
                for plan in plans.values()
            ]
        ),
        hide_index=True,
        width='stretch',
        column_config={
            "": st.column_config.TextColumn(width="small"),
            "Coverage": st.column_config.ProgressColumn(
                "Coverage", min_value=0, max_value=100, format="%d%%"
            ),
        },
    )

    st.markdown("---")
    voice = st.selectbox("Voice to review line by line", list(plans), key="review_voice")
    plan = plans[voice]
    st.markdown(
        "**The English printed in the score, and the syllables going onto those same notes.**"
    )
    solo = st.checkbox(
        f"Give {voice} different words from the other voices",
        key=f"solo_{voice}",
        help="Off: correct the words on Step 3 and every voice singing that line follows. "
        "On: whatever you type here applies to this voice only.",
    )
    if not solo:
        st.caption(
            "To change a syllable, use **Step 3** — the correction is made once and every "
            "voice singing that line picks it up. Tick the box above only if this voice "
            "genuinely sings something different."
        )

    rows = []
    for assignment in plan.assignments:
        key = f"{voice}||{assignment.score_line_id}"
        override = st.session_state["assign_edits"].get(key)
        text = override if override is not None else " ".join(assignment.tokens)
        rows.append(
            {
                "": ICONS["ok"] if len(text.split()) == len(assignment.tokens) else ICONS["warn"],
                "Page": assignment.page + 1,
                "Section": assignment.section,
                "English in the score": assignment.english,
                "Notes": len(assignment.tokens),
                "Syllables": text,
                "On held notes": assignment.held_text,
                "Sung by": len(
                    set().union(*(voices_by_layout_line.get(repeat_origin.get(i, i), set())
                                  for i in assignment.layout_line_ids))
                ) if assignment.layout_line_ids else 1,
                "_key": key,
            }
        )
    edited_voice = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        width='stretch',
        height=430,
        column_config={
            "": st.column_config.TextColumn(width="small", disabled=True),
            "Page": st.column_config.NumberColumn(width="small", disabled=True),
            "Section": st.column_config.TextColumn(width="small", disabled=True),
            "English in the score": st.column_config.TextColumn(width="large", disabled=True),
            "Notes": st.column_config.NumberColumn(
                width="small", disabled=True, help="Notes on this line of the score"
            ),
            "Syllables": st.column_config.TextColumn(width="large", disabled=not solo),
            "On held notes": st.column_config.TextColumn(
                width="medium",
                disabled=True,
                help="Extra syllables the translation sings on notes where the English holds "
                "one syllable across several. They are placed on those notes in the PDF.",
            ),
            "Sung by": st.column_config.NumberColumn(
                width="small",
                disabled=True,
                help="How many voices sing these words. Correcting them on Step 3 changes "
                "them for all of them at once.",
            ),
            "_key": None,
        },
        key=f"voice_editor_{voice}",
    )
    if solo:
        for _, row in edited_voice.iterrows():
            st.session_state["assign_edits"][row["_key"]] = row["Syllables"]
    else:
        for assignment in plan.assignments:
            st.session_state["assign_edits"].pop(f"{voice}||{assignment.score_line_id}", None)

    unresolved = [a.note for a in plan.assignments if a.note]
    if unresolved:
        with st.expander(f"What the app could not work out for {voice}", expanded=False):
            for text in dict.fromkeys(unresolved):
                st.write("- " + text)


# --------------------------------------------------------------------------- 5 · PDF

with tab_make:
    step_header(5, "PDF", "Make the finished score.")

    placements: dict[int, list[str]] = {}
    held_notes: dict[int, list[tuple]] = {}
    issues: list[dict] = []
    for voice_name, voice_plan in plans.items():
        for assignment in voice_plan.assignments:
            tokens = edited_tokens(voice_name, assignment)
            need = len(assignment.tokens)
            if len(tokens) > need:
                issues.append(
                    {
                        "Voice": voice_name,
                        "Page": assignment.page + 1,
                        "Notes": need,
                        "Syllables given": len(tokens),
                        "What happens": f"the last {len(tokens) - need} will be left off",
                    }
                )
                tokens = tokens[:need]
            elif len(tokens) < need:
                issues.append(
                    {
                        "Voice": voice_name,
                        "Page": assignment.page + 1,
                        "Notes": need,
                        "Syllables given": len(tokens),
                        "What happens": f"{need - len(tokens)} note(s) will be left empty",
                    }
                )
                tokens = tokens + [""] * (need - len(tokens))
            placements[assignment.score_line_id] = tokens
            if assignment.held:
                held_notes[assignment.score_line_id] = list(assignment.held)

    extra = sum(len(v) for v in held_notes.values())
    blank = sum(1 for tokens in placements.values() for token in tokens if not token)
    total = sum(len(tokens) for tokens in placements.values()) + extra

    left, right = st.columns([1, 2])
    with left:
        st.metric("Notes that will carry a syllable", f"{total - blank} of {total}")
    with right:
        verdict(
            len(issues),
            "Every note will carry a syllable.",
            "These lines will still be written, with the gaps shown below. "
            "Go back to Step 4 to fill them, or carry on.",
        )

    attention_table(issues[:15], "What will not be filled")
    if len(issues) > 15:
        st.caption(f"...and {len(issues) - 15} more.")

    st.markdown("---")
    if st.button("Generate the score", type="primary", width='stretch'):
        try:
            settings = render_mod.RenderSettings(
                max_size=max_size, baseline_offset=baseline, font_choice=font_choice
            )
            st.session_state["result_pdf"] = render_mod.render(
                score_doc, blank_bytes, placements, settings, held_notes
            )
        except Exception as error:  # noqa: BLE001
            st.error(f"**The PDF could not be made.**\n\n{error}")

    if st.session_state.get("result_pdf"):
        result = st.session_state["result_pdf"]
        st.success("**Your score is ready.**")

        first, second = st.columns(2)
        with first:
            st.download_button(
                "Download the finished score (PDF)",
                result,
                "translated_score.pdf",
                "application/pdf",
                type="primary",
                width='stretch',
            )
        with second:
            report = io.StringIO()
            report.write("voice,page,section,english,syllables\n")
            for voice_name, voice_plan in plans.items():
                for assignment in voice_plan.assignments:
                    text = " ".join(edited_tokens(voice_name, assignment))
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
                width='stretch',
            )

        st.markdown("**Preview**")
        import pymupdf as fitz

        pages = fitz.open(stream=result, filetype="pdf").page_count
        page_pick = st.number_input("Page", 1, pages, 1)
        st.image(render_mod.page_image(result, int(page_pick) - 1, 2.0), width='stretch')

        st.divider()
        st.markdown("**Finished with this one?**")
        st.button(
            "Start another project",
            key="restart_after_download",
            on_click=start_another_project,
            type="primary",
            width='stretch',
            help="Download anything you want to keep first — this clears the files, your "
            "corrections and the PDF.",
        )
