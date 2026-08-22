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
from smgcore import blankscore as blank_mod
from smgcore import layout as layout_mod
from smgcore import lyricsdoc as lyrics_mod
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

# --------------------------------------------------------------------------- appearance
#
# One palette, used everywhere. Muted and low-contrast by design, so that the
# only strong colour on the page is the one marking something that needs
# attention. Every control that opens — a menu, a picker, an upload box — is
# given a visible border and a tinted field, because the default Streamlit
# styling leaves them almost invisible against a white page.

INK = "#2F3D4B"        # deep navy: body text, and the mark for anything outstanding
SLATE = "#5A6B78"      # slate blue: controls and headings
SAGE = "#A9B39C"       # sage: borders and rules
MOSS = "#DDE4D5"       # soft green: panels for work that is complete
SAND = "#EADFCC"       # cream: the sidebar
TAN = "#DFC7B0"        # warm tan: panels for work still outstanding
PAPER = "#F7F7F2"      # page tint, very slightly green

THEME = f"""
<style>
  html, body, [class*="css"] {{ color: {INK}; }}
  .stApp {{ background: {PAPER}; }}
  section[data-testid="stSidebar"] {{
      background: {SAND}; border-right: 1px solid {SAGE};
      box-shadow: 2px 0 12px rgba(47,61,75,.06);
  }}
  h1 {{
      font-weight: 700; letter-spacing: -.02em; color: {INK};
      border-bottom: 2px solid {SLATE}; padding-bottom: .55rem; margin-bottom: .4rem;
  }}
  h2, h3 {{ font-weight: 600; letter-spacing: -.01em; color: {INK}; }}
  [data-testid="stWidgetLabel"] p {{
      font-weight: 600 !important; color: {INK} !important; font-size: .9rem !important;
  }}
  hr {{ border-color: {SAGE}; opacity: .55; }}

  /* --- Status banners: one component, used everywhere a verdict or a notice is
         shown, so every banner in the app looks and behaves identically. --- */
  .smg-banner {{
      border: 1px solid {SAGE}; border-radius: 8px; padding: 12px 16px;
      margin-bottom: 14px; color: {INK}; box-shadow: 0 1px 3px rgba(47,61,75,.06);
  }}
  .smg-banner strong {{ color: {INK}; }}
  .smg-banner--ok {{ background: {MOSS}; border-left: 5px solid {SAGE}; }}
  .smg-banner--attn {{ background: {TAN}; border-left: 5px solid {INK}; }}
  .smg-banner--info {{ background: {MOSS}; }}
  .smg-icon {{ display: inline-block; width: 1.1em; text-align: center; margin-right: 4px; }}

  /* --- Controls that open. The default styling is a faint underline that is easy
         to miss, so every one gets a solid field, a border and a boxed chevron. --- */
  .react-aria-ComboBox > div[role="group"],
  [data-testid="stMultiSelect"] div[data-baseweb="select"] > div,
  [data-testid="stNumberInput"] > div,
  [data-testid="stTextInput"] > div {{
      background: #FFFFFF !important;
      border: 1.5px solid {SLATE} !important;
      border-radius: 8px !important;
      min-height: 44px !important;
      box-shadow: 0 1px 2px rgba(47,61,75,.07);
  }}
  .react-aria-ComboBox > div[role="group"]:hover,
  [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover {{
      border-color: {INK} !important;
  }}
  .react-aria-ComboBox > div[role="group"]:focus-within {{
      border-color: {INK} !important; box-shadow: 0 0 0 3px rgba(90,107,120,.18);
  }}
  /* the chevron: boxed, tinted, unmistakably a menu */
  .react-aria-ComboBox > div[role="group"] > button {{
      background: {MOSS} !important; border-left: 1.5px solid {SLATE} !important;
      border-radius: 0 6px 6px 0 !important; width: 42px !important;
      margin: 0 !important; height: 100% !important;
  }}
  .react-aria-ComboBox > div[role="group"] > button svg {{
      fill: {INK} !important; width: 20px !important; height: 20px !important;
  }}
  .react-aria-ComboBox input[role="combobox"] {{
      font-weight: 500; color: {INK} !important; padding-left: 12px !important;
  }}

  /* --- Sliders. Locked to the palette explicitly rather than left to inherit
         Streamlit's theme, so a slider never shows an off-palette accent colour
         regardless of which theme a browser or a hosting platform applies. --- */
  [data-testid="stSlider"] [data-orientation="horizontal"][style] > div:first-child {{
      background: {SAGE} !important; height: 4px !important; border-radius: 4px !important;
  }}
  [data-testid="stSlider"] [data-orientation="horizontal"] > div[style*="position: absolute"] {{
      background: {SLATE} !important; border: 2px solid #FFFFFF !important;
      box-shadow: 0 1px 3px rgba(47,61,75,.35) !important;
  }}
  [data-testid="stSlider"] [data-testid="stSliderThumbValue"] p {{
      color: {SLATE} !important; font-weight: 700 !important;
  }}
  input[type="range"], input[type="checkbox"], input[type="radio"] {{ accent-color: {SLATE}; }}

  /* --- Upload boxes read as drop targets. --- */
  [data-testid="stFileUploaderDropzone"] {{
      background: #FFFFFF; border: 1.5px dashed {SLATE};
      border-radius: 10px; padding: 14px 16px;
      box-shadow: 0 1px 2px rgba(47,61,75,.05);
      transition: background .12s ease, border-color .12s ease;
  }}
  [data-testid="stFileUploaderDropzone"]:hover {{ background: {PAPER}; border-color: {INK}; }}

  /* --- The five steps. Each one is a button holding its own state, so the step on
         screen is remembered and a change made anywhere else does not move it. --- */
  [class*="st-key-step_btn_"] button {{
      width: 100%; justify-content: flex-start !important; align-items: flex-start !important;
      flex-direction: column !important; text-align: left; line-height: 1.3;
      border: 1px solid {SAGE} !important; border-left: 4px solid {SLATE} !important;
      border-radius: 8px !important; padding: 10px 12px 11px 13px !important;
      min-height: 68px; height: auto !important; font-weight: 600;
      transition: background .12s ease, box-shadow .12s ease, transform .08s ease;
  }}
  [class*="st-key-step_btn_"] button p {{ margin: 0 !important; font-size: .9rem; }}
  [class*="st-key-step_btn_"] button p + p {{
      margin-top: 5px !important; font-size: .8rem; font-weight: 500; opacity: .82;
  }}
  /* the label is centred by default; these two put it against the left edge */
  [class*="st-key-step_btn_"] button > div,
  [class*="st-key-step_btn_"] button > div > span {{
      width: 100% !important; justify-content: flex-start !important;
  }}
  [class*="st-key-step_btn_"] [data-testid="stMarkdownContainer"] {{
      width: 100%; text-align: left;
  }}
  [class*="st-key-step_btn_"] [data-testid="stBaseButton-secondary"] {{
      background: #FFFFFF; color: {INK} !important;
  }}
  [class*="st-key-step_btn_"] [data-testid="stBaseButton-secondary"]:hover {{
      background: {MOSS}; box-shadow: 0 2px 6px rgba(47,61,75,.1); transform: translateY(-1px);
  }}
  [class*="st-key-step_btn_"] [data-testid="stBaseButton-primary"] {{
      background: {SLATE} !important; border-color: {SLATE} !important;
      border-left-color: {INK} !important; box-shadow: 0 2px 8px rgba(47,61,75,.18);
  }}
  [class*="st-key-step_btn_"] [data-testid="stBaseButton-primary"] * {{ color: #FFFFFF !important; }}

  /* --- Buttons --- */
  [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {{
      border-radius: 8px; font-weight: 600; min-height: 44px;
      border: 1.5px solid {SLATE};
      transition: background .12s ease, box-shadow .12s ease, transform .08s ease, border-color .12s ease;
  }}
  [data-testid="stBaseButton-primary"] {{
      background: {SLATE}; border-color: {SLATE}; color: #FFFFFF;
      box-shadow: 0 2px 6px rgba(47,61,75,.18);
  }}
  [data-testid="stBaseButton-primary"]:hover {{
      background: {INK}; border-color: {INK};
      transform: translateY(-1px); box-shadow: 0 4px 12px rgba(47,61,75,.24);
  }}
  [data-testid="stBaseButton-secondary"]:hover {{
      background: {MOSS}; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(47,61,75,.1);
  }}
  [data-testid="stBaseButton-primary"]:active, [data-testid="stBaseButton-secondary"]:active {{
      transform: translateY(0); box-shadow: none;
  }}

  /* --- Tables and panels --- */
  [data-testid="stDataFrame"], [data-testid="stDataEditor"] {{
      border: 1px solid {SAGE}; border-radius: 10px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(47,61,75,.06);
  }}
  [data-testid="stExpander"] {{
      border: 1px solid {SAGE}; border-radius: 10px; background: #FFFFFF;
      box-shadow: 0 1px 2px rgba(47,61,75,.05);
  }}
  [data-testid="stExpander"] summary {{ font-weight: 600; color: {INK}; }}
  hr {{ border-color: {SAGE}; }}
  [data-testid="stMetricValue"] {{ color: {INK}; font-weight: 700; }}
  [data-testid="stMetricLabel"] p {{ color: {SLATE} !important; font-weight: 600 !important; }}
  [data-testid="stSliderTickBarMin"], [data-testid="stSliderTickBarMax"] {{ color: {SLATE}; }}
</style>
"""
st.markdown(THEME, unsafe_allow_html=True)


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


@st.cache_data(show_spinner="Making the no-lyrics score...", max_entries=4)
def blank_from_score_cached(_score_doc, score_bytes: bytes, key: str):
    return blank_mod.strip_lyrics(score_bytes, _score_doc)


@st.cache_data(show_spinner="Reading the lyrics sheet...", max_entries=4)
def lyrics_cached(data: bytes):
    return lyrics_mod.parse_lyrics_document(data)


@st.cache_data(show_spinner="Matching the lyrics to the score...", max_entries=4)
def from_lyrics_cached(_score_doc, _lyrics_doc, key: str):
    return lyrics_mod.build_from_lyrics(_score_doc, _lyrics_doc)


@st.cache_data(show_spinner="Setting the syllables...", max_entries=3)
def render_cached(_score_doc, blank: bytes, _placements, _held, size, offset, font, key):
    """Draw the score. Cached on the type settings so a slider change re-renders once.

    Only the settings and the file identity vary between calls, so moving a slider
    produces a new render and moving it back returns the previous one from cache.
    """
    settings = render_mod.RenderSettings(
        max_size=size, baseline_offset=offset, font_choice=font
    )
    return render_mod.render(_score_doc, blank, _placements, settings, _held)


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
    "has_generated",
    "preview_page",
    "active_step",
)
# Kept while the piece is being worked on and cleared only by "Start a new project".
SESSION_KEYS = ("reference_audio", "max_size", "baseline", "font_choice")


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
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)
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
        ("active_step", 1),
    ]:
        st.session_state.setdefault(key, default)


seed_state()


# --------------------------------------------------------------------------- shared UI


STEP_TITLES = [
    "Source files",
    "Syllable layout",
    "Translation",
    "Note assignment",
    "Output",
]


def step_header(number: int, instruction: str) -> None:
    """Every step opens identically: its number and name, then what to do in it."""
    st.subheader(f"Step {number} of 5 · {STEP_TITLES[number - 1]}")
    st.markdown(
        f'<div style="border-left:3px solid {SLATE};padding:2px 0 2px 12px;'
        f'margin:-4px 0 14px 0;color:{INK};font-size:1.02rem;">{instruction}</div>',
        unsafe_allow_html=True,
    )


def verdict(count: int, clean: str, todo: str) -> None:
    """One status line per step: either the step is complete, or it names the work."""
    if count:
        st.markdown(
            f'<div class="smg-banner smg-banner--attn">'
            f'<span class="smg-icon">{ICONS["warn"]}</span>'
            f'<strong>{count} item(s) to check.</strong> {todo}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="smg-banner smg-banner--ok">'
            f'<span class="smg-icon">{ICONS["ok"]}</span>'
            f'<strong>Nothing to change here.</strong> {clean}</div>',
            unsafe_allow_html=True,
        )


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
    st.header("Source files")
    round_ = st.session_state["upload_round"]
    english_file = st.file_uploader("1 · English score", type=["pdf"], key=f"english{round_}")
    layout_file = st.file_uploader(
        "2 · Syllable layout (English followed by translation)",
        type=["pdf", "txt"],
        key=f"layout{round_}",
    )

    st.divider()
    st.header("Reference audio")
    st.caption("(Optional) Upload an audio to listen to a reference as needed.")
    audio_file = st.file_uploader(
        "Audio file",
        type=["mp3", "wav", "m4a", "ogg", "flac"],
        key=f"reference_audio{round_}",
        label_visibility="collapsed",
    )
    if audio_file is not None:
        st.session_state["reference_audio"] = {
            "data": audio_file.getvalue(),
            "name": audio_file.name,
        }
    reference = st.session_state.get("reference_audio")
    if reference:
        st.audio(reference["data"])
        st.caption(reference["name"])

    st.divider()
    st.header("Type settings")
    max_size = st.slider("Maximum type size", 4.0, 12.0, 7.25, 0.25, key="max_size")
    baseline = st.slider("Distance below staff", 3.0, 14.0, 7.6, 0.1, key="baseline")
    font_choice = st.selectbox(
        "Font", list(render_mod.BUNDLED_FONTS), index=0, key="font_choice"
    )

    st.divider()
    st.button(
        "Start a new project",
        on_click=start_another_project,
        width='stretch',
        help="Removes the files, the corrections and the generated score.",
    )

uploaded = {
    "1 · English score": (
        english_file,
        True,
        "Engraved score with the English lyrics set under the notes",
    ),
    "2 · Syllable layout": (
        layout_file,
        True,
        "The English layout followed by the translated one, in a single document",
    ),
}

if not all(handle for handle, required, _ in uploaded.values() if required):
    st.markdown(
        f'<div class="smg-banner smg-banner--info">'
        f'<strong>To begin, upload both files using the panel on the left.</strong> '
        f'File 2 should hold the English syllable layout followed by the translated one. '
        f'The score without lyrics is made from file 1, so it is not needed.</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": ICONS["ok"] if handle else (ICONS["todo"] if required else ""),
                    "File": name,
                    "Description": description,
                    "Status": (
                        "Received"
                        if handle
                        else ("Required" if required else "Optional")
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
layout_bytes = layout_file.getvalue()
reset_edits(digest(english_bytes, layout_bytes))
seed_state()


def stop_with_a_way_out(title: str, detail: str) -> None:
    """Never leave the page dead. The sidebar is drawn first, so its reset button is
    still on screen — say so rather than adding a second one beside it."""
    st.markdown(
        f'<div class="smg-banner smg-banner--attn">'
        f'<span class="smg-icon">{ICONS["err"]}</span><strong>{title}</strong>'
        f'<div style="margin-top:6px;">{detail}</div>'
        f'<div style="margin-top:10px;">Supply a different file, or use '
        f'<strong>Start a new project</strong> in the panel on the left.</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()

try:
    score_doc = parse_score_cached(english_bytes)
except Exception as error:  # noqa: BLE001
    stop_with_a_way_out("File 1, the English score, could not be read.", str(error))

# File 2 is either a grid with one box per note, or an ordinary page of words
# hyphenated at the syllables. Which one it is, is read off the document: a lyrics
# sheet is the one written in section headings and running prose.
lyrics_doc = lyrics_cached(layout_bytes)
try:
    layout_doc = parse_layout_cached(layout_bytes)
except Exception:  # noqa: BLE001
    layout_doc = None
lyrics_mode = lyrics_mod.prefer_lyrics_sheet(lyrics_doc, layout_doc, score_doc)
if layout_doc is None and not lyrics_mode:
    stop_with_a_way_out(
        "File 2 could not be read.",
        "It was read neither as a syllable layout nor as a lyrics sheet.",
    )

# The score without lyrics is the copy the syllables are drawn onto. It is made
# here by deleting the lyric text from file 1, which leaves the staves, notes and
# everything else exactly as engraved.
blank_bytes, _ = blank_from_score_cached(score_doc, english_bytes, digest(english_bytes))

geometry_problems = geometry_cached(score_doc, blank_bytes, digest(english_bytes, blank_bytes))
score_sections = [name for _, _, _, name in score_doc.sections]
score_words = score_doc.sung_words()


# --------------------------------------------------------------------------- the pipeline
#
# Everything is worked out before anything is drawn, so each tab can be labelled
# with what it found. Corrections made in the tables live in session state, so
# they are already folded in by the time this runs on the next interaction.

# File 2 should hold both languages: the English layout followed by the translated
# one. Every English syllable is already printed in the score, so which rows are
# which is read off the file rather than asked about.
#
# Three shapes are handled, in descending order of how much the document itself
# settles. Only the first needs nothing worked out.
#
#   both       - English and translation in one grid. The translator has already
#                put each pair of lines together, so pairing is exact.
#   grid only  - a grid of the translation with no English beside it. The English
#                lines are cut from the score instead, and matched by section,
#                order and syllable count.
#   sheet      - ordinary prose, hyphenated at the syllables. Same as above, but
#                the boxes have to be worked out from the hyphens too.
style = "All rows"
combined_document = False

if lyrics_mode:
    english_lines, derived_translation, derived_notes = from_lyrics_cached(
        score_doc, lyrics_doc, digest(english_bytes, layout_bytes)
    )
    editable_lines = [
        layout_mod.EditableLine(
            id=line.id, page=0, section=line.section, tag="",
            tokens=list(line.tokens), xs=[],
        )
        for line in lyrics_doc.lines
    ]
else:
    style = st.session_state.get("layout_style", "All rows")
    all_rows = layout_mod.to_editable(
        layout_doc,
        style,
        {**st.session_state["english_edits"], **st.session_state["layout_edits"]},
    )
    english_lines, editable_lines = layout_mod.split_by_language(all_rows, score_words)
    combined_document = bool(english_lines)
    if not combined_document:
        # One language only. The grid still says how many notes each line covers,
        # which is better than prose, but where each line belongs has to come from
        # the score - so it goes through the same matching a lyrics sheet does.
        editable_lines = all_rows
        english_lines, derived_translation, derived_notes = from_lyrics_cached(
            score_doc,
            lyrics_mod.lyrics_doc_from_lines(all_rows),
            digest(english_bytes, layout_bytes, style.encode()),
        )

working_lines = [
    line for line in editable_lines if line.id not in st.session_state["dropped_layout"]
]

if not combined_document:
    # The English line and its syllables were matched when the document was read,
    # so the correspondence is already known. It is turned into the same rows the
    # combined path produces, and corrections made in Step 3 lie over the top.
    translation = {
        line_id: list(tokens) for line_id, tokens in derived_translation.items()
    }
    for line_id, value in st.session_state["pair_overrides"].items():
        translation[line_id] = (
            [str(token) for token in value]
            if isinstance(value, (list, tuple))
            else str(value).split()
        )
    pairs = [
        pairing_mod.Pair(
            english_id=line.id,
            translated_id=line.id if line.id in derived_translation else None,
            english_text=line.text,
            translated_text=" ".join(translation.get(line.id, [])),
            section=line.section,
            tag=line.tag,
            english_count=line.note_count,
            translated_count=len(translation.get(line.id, [])),
            status=(
                "ok"
                if len(translation.get(line.id, [])) == line.note_count
                else ("english-only" if not translation.get(line.id) else "count")
            ),
        )
        for line in english_lines
    ]
    clean = sum(1 for pair in pairs if pair.status == "ok")
    pair_result = pairing_mod.PairingResult(
        pairs=pairs,
        confidence=clean / max(1, len(pairs)),
        # Only what the table above does not already say: a line whose counts
        # disagree is listed there row by row, so repeating it here is noise.
        notes=(list(lyrics_doc.warnings) if lyrics_mode else [])
        + [note for note in derived_notes if note.startswith("These headings")],
    )
else:
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
ready_state = "ok" if st.session_state.get("has_generated") else "todo"

states = [score_state, layout_state, pair_state, notes_state, ready_state]
outstanding = [
    index for index, state in enumerate(states, start=1) if state in ("warn", "err")
]

st.markdown(
    f'<p style="color:{SLATE};margin:-4px 0 12px 0;font-size:1.03rem;">'
    "Work through the five steps below. Each one shows whether anything needs your "
    "attention, and the finished score is produced in Step 5.</p>",
    unsafe_allow_html=True,
)
if outstanding:
    st.markdown(
        f'<div class="smg-banner smg-banner--attn">'
        f'<span class="smg-icon">{ICONS["warn"]}</span>'
        f'<strong>Needs your attention: '
        f'{"Step " + ", ".join(str(n) for n in outstanding)}.</strong> '
        "Open each of those steps and check the items listed there.</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div class="smg-banner smg-banner--ok">'
        f'<span class="smg-icon">{ICONS["ok"]}</span>'
        "<strong>Nothing needs attention.</strong> Look through the steps if you wish, "
        "then generate the score in Step 5.</div>",
        unsafe_allow_html=True,
    )

LABELS = {"ok": "Ready", "warn": "Needs a check", "err": "Problem found", "todo": "Not started"}


def go_to_step(number: int) -> None:
    st.session_state["active_step"] = number


# The step bar is five buttons rather than tabs. A tab strip forgets which tab was
# open whenever the script reruns, so moving a slider sent the page back to Step 1.
# The step being viewed is held in session state instead, where nothing else touches it.
step_columns = st.columns(5, gap="small")
for index, state in enumerate(states, start=1):
    with step_columns[index - 1]:
        st.button(
            f"{index}  {STEP_TITLES[index - 1]}\n\n{ICONS[state]} {LABELS[state]}",
            key=f"step_btn_{index}",
            on_click=go_to_step,
            args=(index,),
            type="primary" if st.session_state["active_step"] == index else "secondary",
            width='stretch',
        )

step = int(st.session_state.get("active_step", 1))
st.write("")


def next_step_button(number: int, label: str) -> None:
    """The way forward from the bottom of a step, so the bar is not the only route."""
    st.markdown("---")
    st.button(
        label,
        key=f"next_from_{number}",
        on_click=go_to_step,
        args=(number + 1,),
        type="primary",
    )


# --------------------------------------------------------------------------- 1 · Score

if step == 1:
    step_header(1, "Check that both score files were read correctly.")
    verdict(
        len(geometry_problems),
        "The two score files match, so every syllable will land on the right note.",
        "The two files are not the same engraving. Upload a matching pair to continue.",
    )
    for problem in geometry_problems:
        st.error(problem)

    left, middle, right = st.columns(3)
    with left:
        st.markdown("**Score**")
        st.metric("Pages", score_doc.page_count)
        st.metric("Voice parts", len(score_doc.voices))
        st.metric("Notes to fill", len(score_doc.anchors))
    with middle:
        st.markdown("**Detected in the score**")
        st.write("Sections")
        st.write(", ".join(score_sections) if score_sections else "_none found_")
        st.write("Voices")
        st.write(", ".join(score_doc.voices))
    with right:
        st.markdown("**Detected in the layout**")
        st.metric("English lines", len(english_lines))
        st.metric("Translated lines", len(working_lines))
        if combined_document:
            st.write("Both languages were found in file 2, so each line is paired with its own.")
        elif lyrics_mode:
            st.write(
                "File 2 was read as a lyrics sheet. It holds no English, so the English was "
                "taken from the score and cut into lines at its punctuation."
            )
        else:
            st.write(
                "File 2 holds only one language. The English was taken from the score and "
                "cut into lines at its punctuation."
            )
        st.write("The score without lyrics was made from file 1.")

    if not combined_document:
        st.markdown(
            f'<div class="smg-banner smg-banner--attn">'
            f'<span class="smg-icon">{ICONS["warn"]}</span>'
            f'<strong>File 2 contains only the translation.</strong> Where each line belongs '
            f'had to be worked out from the score rather than read from the document. A file '
            f'holding the English layout followed by the translated one is matched exactly '
            f'and gives a better result.</div>',
            unsafe_allow_html=True,
        )

    warnings = list(score_doc.warnings)
    if layout_doc is not None and not lyrics_mode:
        warnings += [f"Layout: {w}" for w in layout_doc.warnings]
    if warnings:
        st.markdown("**Notices**")
        for warning in warnings:
            st.warning(warning)

    next_step_button(1, "Continue to Step 2 · Syllable layout")


# --------------------------------------------------------------------------- 2 · Syllables

if step == 2:
    step_header(2, "Check the syllables read from the translator's document.")
    verdict(
        len(layout_trouble),
        "Every line of the layout was read as a row of syllables.",
        "The lines below were read as empty. Enter the syllables, or clear Use to omit the line.",
    )
    attention_table(
        [{"Page": line.page + 1, "Section": line.section, "Line": line.text or "(empty)"}
         for line in layout_trouble],
        "Lines to check",
    )

    st.markdown("---")
    if lyrics_mode:
        st.markdown(
            "**One syllable per note.** The lines below were split at the hyphens in the "
            "document. Add a hyphen to split a word further; remove the space between two "
            "syllables to sing them both on one note."
        )
    else:
        st.markdown(
            "**One box per note.** Remove the space between two syllables to set them on a "
            "single note; add a space to separate them again. Clear **Use** to omit a line "
            "entirely."
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
        st.info(f"{changed} line(s) edited. All later steps use the edited text.")

    if not lyrics_mode:
      with st.expander("Advanced · Where the translation sits within the document", expanded=False):
        st.write(
            "The app has already chosen how to read the document. Change this only if the "
            "table above shows the wrong text."
        )
        st.radio(
            "Reading to use",
            STYLE_OPTIONS,
            index=STYLE_OPTIONS.index(style),
            key="layout_style",
            horizontal=True,
        )

    if combined_document:
      with st.expander("Advanced · The English layout as it was read", expanded=False):
        st.write("Change a line here only if the English was read from the document incorrectly.")
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

    next_step_button(2, "Continue to Step 3 · Translation")


# --------------------------------------------------------------------------- 3 · Translation

STATUS_TEXT = {
    "ok": "",
    "count": "counts differ",
    "english-only": "no translation",
    "translation-only": "no English line",
}

if step == 3:
    step_header(3, "Check that each English line is matched with the right translation.")
    verdict(
        len(pair_trouble),
        "Every English line is matched to a translation with the same number of syllables.",
        "Check the rows below. Where the counts differ, put two syllables on one note by "
        "removing the space between them, or split them by adding a space.",
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
        "Lines to check",
    )
    for note in pair_result.notes:
        st.info(note)

    st.markdown("---")
    st.markdown(
        f"**{pair_result.confidence:.0%} of lines matched.** "
        "Type over any entry in the **Translation** column to correct it."
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
    st.markdown("**Edit a single line, note by note**")
    st.write(
        "One row per note, showing the English word set on it. Enter the syllable directly into "
        "the box. Two syllables in one box are sung on that note; an empty box leaves the note "
        "held. An edit here applies to **every voice that sings this line**."
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
            "Select a line to edit", choices, format_func=line_label, key="grid_line"
        )
        english_line = english_by_id[picked]
        current = translation.get(picked, [])
        singers = sorted(voices_by_layout_line.get(picked, ()))

        st.caption(
            ("Sung by: " + ", ".join(singers)) if singers
            else "No voice is currently assigned to this line."
        )

        grid = pd.DataFrame(
            [
                {
                    "Note": index + 1,
                    "English": (
                        "▫  held note" if token == layout_mod.BLANK_BOX else token
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
                    width="medium", disabled=True, help="The English word set on this note"
                ),
                "Syllable": st.column_config.TextColumn(
                    width="medium", help="Leave empty to hold the previous syllable across this note"
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
                   else "No voice is currently assigned to this line.")
            )

    next_step_button(3, "Continue to Step 4 · Note assignment")


# --------------------------------------------------------------------------- 4 · Notes

if step == 4:
    step_header(4, "Check the syllables set on each note, one voice at a time.")
    verdict(
        len(mismatched_lines) + len(unmapped_sections) + (1 if empty_notes else 0),
        "Every note carries a syllable and every section is accounted for.",
        f"{empty_notes} note(s) would be left empty. Select the voice below and enter the "
        "syllables. The PDF can be generated either way.",
    )

    if unmapped_sections:
        st.error(
            "**Unplaced sections:** "
            + ", ".join(f"`{name}`" for name in unmapped_sections)
            + ". Open *Section placement* below and select where each one is sung."
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
        "Lines to check",
    )
    if len(mismatched_lines) > 25:
        st.caption(f"...and {len(mismatched_lines) - 25} more.")

    st.markdown("---")

    with st.expander("Section placement", expanded=bool(unmapped_sections)):
        st.write(
            "A section written once in the layout may be sung several times in the score — a "
            "single **Ch** block covering Chorus 1, 2 and 3. Select every place it is sung."
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
        help="A voice selected here keeps its English and receives no syllables.",
    )

    if not plans:
        st.error("Every voice has been left in English. Untick one above to continue.")
        st.stop()

    st.markdown("**Summary by voice**")
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
    voice = st.selectbox("Select a voice to review", list(plans), key="review_voice")
    plan = plans[voice]
    st.markdown(
        "**The English printed in the score, beside the syllables set on the same notes.**"
    )
    solo = st.checkbox(
        f"Assign different words to {voice} only",
        key=f"solo_{voice}",
        help="Leave this off to correct the words once in Step 3 for every voice that sings "
        "them. Turn it on only where this voice sings something different.",
    )
    if not solo:
        st.caption(
            "To change a syllable, go to **Step 3**. The correction is made once and applies "
            "to every voice singing that line. Use the option above only where this voice sings "
            "different words."
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
        with st.expander(f"Unresolved items for {voice}", expanded=False):
            for text in dict.fromkeys(unresolved):
                st.write("- " + text)

    next_step_button(4, "Continue to Step 5 · Output")


# --------------------------------------------------------------------------- 5 · PDF

if step == 5:
    step_header(5, "Generate the score and download it.")

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
                        "Result": f"the final {len(tokens) - need} will be omitted",
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
                        "Result": f"{need - len(tokens)} note(s) will remain empty",
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
        st.metric("Notes carrying a syllable", f"{total - blank} of {total}")
    with right:
        verdict(
            len(issues),
            "Every note will carry a syllable.",
            "The score can still be generated, with those notes left empty. Go back to Step 4 "
            "to fill them in first if you prefer.",
        )

    attention_table(issues[:15], "Notes that will remain empty")
    if len(issues) > 15:
        st.caption(f"...and {len(issues) - 15} more.")

    st.markdown("---")
    st.button(
        "Generate the score",
        type="primary",
        width='stretch',
        key="generate",
        on_click=lambda: st.session_state.update(has_generated=True),
    )

    result = None
    if st.session_state.get("has_generated"):
        # Rendering is cached on the type settings, so moving a slider re-renders and the
        # preview follows immediately. Generate does not have to be pressed again.
        try:
            result = render_cached(
                score_doc,
                blank_bytes,
                placements,
                held_notes,
                max_size,
                baseline,
                font_choice,
                digest(english_bytes, layout_bytes),
            )
        except Exception as error:  # noqa: BLE001
            st.markdown(
                f'<div class="smg-banner smg-banner--attn">'
                f'<span class="smg-icon">{ICONS["err"]}</span>'
                f'<strong>The score could not be generated.</strong> {error}</div>',
                unsafe_allow_html=True,
            )
        st.session_state["result_pdf"] = result

    if result:
        st.markdown(
            f'<div class="smg-banner smg-banner--ok">'
            f'<span class="smg-icon">{ICONS["ok"]}</span>'
            f'<strong>The score is ready to download.</strong> Type size, spacing and font '
            f'can be adjusted in the panel on the left.</div>',
            unsafe_allow_html=True,
        )

        first, second = st.columns(2)
        with first:
            st.download_button(
                "Download the score (PDF)",
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
                "Download the proofing sheet (CSV)",
                report.getvalue().encode("utf-8"),
                "alignment_report.csv",
                "text/csv",
                width='stretch',
            )

        st.markdown("**Preview**")
        import pymupdf as fitz

        pages = fitz.open(stream=result, filetype="pdf").page_count
        # Keep the page the user was looking at when a setting changes, rather than
        # dropping them back to page 1. A shorter score clamps it into range.
        if st.session_state.get("preview_page", 1) > pages:
            st.session_state["preview_page"] = pages
        st.session_state.setdefault("preview_page", 1)
        page_pick = st.number_input(
            f"Preview page (1 to {pages})", 1, pages, key="preview_page"
        )
        st.image(render_mod.page_image(result, int(page_pick) - 1, 2.0), width='stretch')
