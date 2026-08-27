"""Multi-lingual Sheet Music Generator.

Writes a translated syllable layout under the notes of an engraved vocal score.

The interface is five numbered steps. Each step answers one question, says
whether anything needs attention, and lists exactly which rows to look at
before showing the full editable table.
"""

from __future__ import annotations

import hashlib
import io
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import os
import base64

from smgcore import blankscore as blank_mod
from smgcore import layout as layout_mod
from smgcore import lock as lock_mod
from smgcore import pairing as pairing_mod
from smgcore import render as render_mod
from smgcore import score as score_mod
from smgcore import spelling as spelling_mod

st.set_page_config(page_title="Multi-lingual Sheet Music Generator", page_icon="♪", layout="wide")

ICONS = {"ok": "✓", "warn": "!", "err": "✕", "todo": "○", "adjust": "↔"}

COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "clickable_preview")
clickable_preview_component = components.declare_component("smg_clickable_preview", path=COMPONENT_DIR)

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


@st.cache_data(show_spinner="Setting the syllables...", max_entries=3)
def render_cached(_score_doc, blank: bytes, _placements, _held, _nudges, size, offset, font, key):
    """Draw the score. Cached on the type settings so a slider change re-renders once.

    Only the settings and the file identity vary between calls, so moving a slider
    produces a new render and moving it back returns the previous one from cache.
    """
    settings = render_mod.RenderSettings(
        max_size=size, baseline_offset=offset, font_choice=font
    )
    layout: list[dict] = []
    notes: list[str] = []
    pdf = render_mod.render(_score_doc, blank, _placements, settings, _held, _nudges,
                            layout_out=layout, warnings_out=notes)
    return pdf, layout, notes


@st.cache_data(show_spinner="Marking what needs a look...", max_entries=3)
def proof_cached(_score_doc, blank: bytes, _placements, _held, _nudges, _marks,
                 size, offset, font, key):
    """The same score, coloured for proofing. Never the copy that is downloaded.

    Returns the PDF and the list of where every syllable was actually drawn, so
    the click targets on the preview are taken from the drawing itself rather
    than guessed from the anchors - which is what left held-note syllables with
    no box to click and, now that crowded syllables are moved apart, would put
    every other box slightly beside its syllable.
    """
    settings = render_mod.RenderSettings(
        max_size=size, baseline_offset=offset, font_choice=font
    )
    layout: list[dict] = []
    pdf = render_mod.render(_score_doc, blank, _placements, settings, _held, _nudges,
                            _marks, layout_out=layout)
    return pdf, layout


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
STATE_PREFIXES = ("voice_editor_", "grid_editor_", "solo_")
STATE_KEYS = (
    "layout_edits",
    "english_edits",
    "pair_overrides",
    "assign_edits",
    "held_edits",
    "skip_voices",
    "dropped_layout",
    "layout_editor",
    "english_editor",
    "pair_editor",
    "review_voice",
    "grid_line",
    "result_pdf",
    "has_generated",
    "preview_page",
    "active_step",
    "spelling_map",
    "spelling_editor",
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
        ("held_edits", {}),
        ("nudge_edits", {}),
        ("preview_edit_text", {}),
        ("preview_flags", {}),
        ("preview_seq", None),
        ("preview_selected", None),
        ("skip_voices", []),
        ("dropped_layout", set()),
        ("upload_round", 0),
        ("active_step", 1),
        ("spelling_map", ""),
    ]:
        st.session_state.setdefault(key, default)


seed_state()




def selected_preview_value(result_pdf: bytes, page_number: int, hotspots: list[dict],
                           selected_key: str | None = None, baseline: float = 5.6):
    """Render the final PDF page as a clickable image and return the clicked hotspot.

    The component is deliberately used only in Step 5: the image shown is the actual
    generated page, and the clickable hotspots are computed from the same score anchors
    used by the renderer. A click therefore selects the exact syllable the user sees.
    """
    import pymupdf as fitz
    doc = fitz.open(stream=result_pdf, filetype="pdf")
    page = doc[page_number]
    zoom = 1.65
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    png_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
    value = clickable_preview_component(
        image=png_b64,
        width=pix.width,
        height=pix.height,
        hotspots=hotspots,
        zoom=zoom,
        baseline=float(baseline),
        selected=selected_key,
        key=f"clickable_preview_{page_number}",
        default=None,
    )
    return value

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
    # The scores are engraved with their lyrics in Times at around 9pt, so that
    # is the size the notes are spaced for. Setting the translation larger than
    # the English it replaces - or in a wider face - is what forced crowded
    # lines to shrink to fit, which is why the type used to come out at a
    # different size on every other line. Start from the score's own size.
    score_size = None
    if st.session_state.get("score_lyric_size"):
        score_size = float(st.session_state["score_lyric_size"])
    st.session_state.setdefault("max_size", round(score_size or 11.0, 2))
    max_size = st.slider("Maximum type size", 4.0, 12.0, step=0.25, key="max_size")
    if score_size:
        st.caption(f"The score sets its own lyrics at {score_size:g}pt.")
    baseline = st.slider("Distance below staff", 3.0, 14.0, 5.6, 0.1, key="baseline")
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

# The size the score sets its own lyrics at is the size the notes are spaced
# for, so it is where the type settings start. Recorded here because the
# sidebar is drawn before this point on the next run.
if score_doc.lyric_font:
    st.session_state["score_lyric_size"] = float(score_doc.lyric_font[1])

# File 2 is always a grid: the English syllable layout in full, followed by the
# translated one underneath, page for page, same boxes in the same order.
try:
    layout_doc = parse_layout_cached(layout_bytes)
except Exception as error:  # noqa: BLE001
    stop_with_a_way_out("File 2, the syllable layout, could not be read.", str(error))

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

# File 2 is cut in half by page count: the English syllable layout in full,
# then the translated one underneath, same boxes in the same order. Pairing is
# purely positional - box N of a translated row against box N of the English
# row above it - never guessed at from the words.
all_rows = layout_mod.to_editable(
    layout_doc,
    {**st.session_state["english_edits"], **st.session_state["layout_edits"]},
)
english_lines, editable_lines = layout_mod.split_in_half(
    all_rows, layout_doc.page_count, score_words
)
combined_document = bool(english_lines)
if not combined_document:
    stop_with_a_way_out(
        "File 2 doesn't look like a two-half layout.",
        "It should hold the English syllable layout in full, followed immediately "
        "underneath by the translated one, the same boxes in the same order "
        "(so the same number of pages of each). Only one language was found.",
    )

# Some layouts are typed with a tool that cannot produce the language's own
# letters - Acrobat's typewriter under WinAnsiEncoding has no `ʉ` in it, so the
# translation comes through as `bu` for `bʉ` and `jen` for `jẽ`. The characters
# are not in the file in any form, so they cannot be recovered and must not be
# guessed at; the correspondence is supplied once, on Step 3, and applied here
# before anything else reads the words.
spelling_map = spelling_mod.parse(st.session_state.get("spelling_map", ""))
spelling_mod.apply_to_lines(editable_lines, spelling_map)

working_lines = [
    line for line in editable_lines if line.id not in st.session_state["dropped_layout"]
]

# Box N of a translated row is paired with box N of the English row directly
# above it - a straight positional read of the grid, not a guess.
pair_result = pairing_mod.pair_layouts(english_lines, working_lines)
pairs = pair_result.pairs
# Harmony-only rows may be identified on the translated side (yellow cells or a
# nearby “(Harmonies)” marker). Carry that semantic tag back to the paired English
# row before the voice-selection stage, so those words can never be fed to a lead.
english_lines = layout_mod.inherit_pair_tags(english_lines, pairs, working_lines)
translation = pairing_mod.translation_map(
    pairs, working_lines, st.session_state["pair_overrides"], english_lines
)

skip = st.session_state["skip_voices"]
active_voices = [v for v in score_doc.voices if v not in skip]

# Now that every English layout line has its translated syllables locked to it,
# each voice's own line of the score is found among the English layout lines
# (matching the words, in the order that voice sings them) and its syllables
# are swapped for the translated ones sitting opposite.
lock = lock_mod.build_lock(english_lines, translation)
plans = lock_mod.plan_voices(score_doc, lock, active_voices)

# Which voices sing each line of the layout, so a syllable corrected once in
# Step 3 is corrected for every voice singing it.
voices_by_layout_line: dict[int, set] = {}
for voice_name, voice_plan in plans.items():
    for assignment in voice_plan.assignments:
        for line_id in assignment.layout_line_ids:
            voices_by_layout_line.setdefault(line_id, set()).add(voice_name)


def edited_tokens(voice_name: str, assignment) -> list[str]:
    """The syllables for one score line, including anything typed over them."""
    override = st.session_state["assign_edits"].get(f"{voice_name}||{assignment.score_line_id}")
    return override.split() if override is not None else list(assignment.tokens)


def edited_held(voice_name: str, assignment) -> list[tuple]:
    """Syllables sitting on held notes, including anything typed over them.

    These have no English syllable underneath and so are not part of the line's
    token list; they are stored against their own position in the held run.
    """
    held = list(assignment.held or [])
    edits = st.session_state.get("held_edits", {})
    out = []
    for n, (x, text) in enumerate(held):
        override = edits.get(f"{voice_name}||{assignment.score_line_id}||held{n}")
        out.append((x, override if override is not None else text))
    return out


def edited_nudges(voice_name: str, assignment, count: int) -> list[float]:
    """Hand-entered horizontal offsets (points) for one score line, one per note.

    Defaults to no offset. A value that doesn't parse as a number is treated as 0
    rather than raising, since this is free-typed text.
    """
    text = st.session_state["nudge_edits"].get(f"{voice_name}||{assignment.score_line_id}", "")
    values: list[float] = []
    for piece in text.split():
        try:
            values.append(float(piece))
        except ValueError:
            values.append(0.0)
    values = values[:count] + [0.0] * (count - len(values))
    return values


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
notes_state = "warn" if (mismatched_lines or empty_notes) else "ok"
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
        st.write("Both languages were found in file 2, so each box is paired with its own.")
        st.write("The score without lyrics was made from file 1.")

    warnings = list(score_doc.warnings) + [f"Layout: {w}" for w in layout_doc.warnings]
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

    # Spelling. Some layouts are typed with a tool that cannot produce the
    # language's own letters, so the translation arrives in a plain spelling -
    # `bu` for `bʉ`, `jen` for `jẽ`. Those characters are not in the file in any
    # form and the app must not invent them; the correspondence is given here
    # once and applied to every cell in the score.
    with st.expander("Spelling — if the layout was typed without the language's letters"):
        st.markdown(
            "Some layouts are typed with a tool that cannot produce every letter "
            "the language uses, so a syllable arrives spelt plainly. Write the "
            "correspondence here, one a line, as **`as typed -> as it reads`**:"
        )
        st.code("bu -> bʉ\nkrin -> krĩ\nun -> ũ", language=None)
        st.text_area(
            "Spelling corrections",
            key="spelling_editor",
            value=st.session_state.get("spelling_map", ""),
            height=160,
            label_visibility="collapsed",
            help="Matching ignores capitals and any punctuation printed around a "
                 "syllable. A map is per language, so one written for a language "
                 "corrects every layout in it that follows.",
        )
        left, right = st.columns([1, 2])
        if left.button("Apply spelling", key="apply_spelling"):
            st.session_state["spelling_map"] = st.session_state.get("spelling_editor", "")
            st.rerun()
        if spelling_map:
            right.markdown(
                f"**{len(spelling_map)} correction(s) in use.** "
                "The score, the table below and the checking sheet all show the "
                "corrected spelling."
            )

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
    explicit_blank_boxes = sum(
        1 for line in english_lines for token in line.tokens if token == layout_mod.BLANK_BOX
    )
    if explicit_blank_boxes:
        st.info(
            f"The layout contains {explicit_blank_boxes} explicit blank/dash box(es). "
            "These are real note columns and are counted on both language halves; they are "
            "not deleted just because no word was printed inside the box."
        )
    verdict(
        len(mismatched_lines) + (1 if empty_notes else 0),
        "Every note carries a syllable.",
        f"{empty_notes} note(s) would be left empty. Select the voice below and enter the "
        "syllables. The PDF can be generated either way.",
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
                "Nudge (pt)": st.session_state["nudge_edits"].get(key, ""),
                "On held notes": assignment.held_text,
                "Sung by": len(
                    set().union(*(voices_by_layout_line.get(i, set())
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
            "Nudge (pt)": st.column_config.TextColumn(
                width="medium",
                help="Fine-tune a syllable's position if the automatic placement looks off. "
                "One number per note, space-separated (e.g. '-2 0 1.5') — negative moves left, "
                "positive moves right. Leave blank to keep the automatic placement.",
            ),
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
    for _, row in edited_voice.iterrows():
        if str(row["Nudge (pt)"]).strip():
            st.session_state["nudge_edits"][row["_key"]] = row["Nudge (pt)"]
        else:
            st.session_state["nudge_edits"].pop(row["_key"], None)

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
    nudges: dict[int, list[float]] = {}
    issues: list[dict] = []
    for voice_name, voice_plan in plans.items():
        for assignment in voice_plan.assignments:
            tokens = edited_tokens(voice_name, assignment)
            need = len(assignment.tokens)
            line_nudges = edited_nudges(voice_name, assignment, need)
            if any(line_nudges):
                nudges[assignment.score_line_id] = line_nudges
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
                held_notes[assignment.score_line_id] = edited_held(voice_name, assignment)

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
    result_layout: list[dict] = []
    font_notes: list[str] = []
    if st.session_state.get("has_generated"):
        # Rendering is cached on the type settings, so moving a slider re-renders and the
        # preview follows immediately. Generate does not have to be pressed again.
        try:
            result, result_layout, font_notes = render_cached(
                score_doc,
                blank_bytes,
                placements,
                held_notes,
                nudges,
                max_size,
                baseline,
                font_choice,
                digest(
                    english_bytes,
                    layout_bytes,
                    json.dumps(placements, sort_keys=True).encode(),
                    json.dumps(held_notes, sort_keys=True).encode(),
                    json.dumps(nudges, sort_keys=True).encode(),
                ),
            )
        except Exception as error:  # noqa: BLE001
            st.markdown(
                f'<div class="smg-banner smg-banner--attn">'
                f'<span class="smg-icon">{ICONS["err"]}</span>'
                f'<strong>The score could not be generated.</strong> {error}</div>',
                unsafe_allow_html=True,
            )
        # A language written in a script no available font can draw would
        # otherwise print as gaps, with nothing on the page to say why.
        for note in font_notes:
            st.warning(note)
        st.session_state["result_pdf"] = result

    # The proofing copy: the same score with every note the app was unsure about
    # in red, and everything settled since in green. A note left deliberately as
    # it stands keeps its red, so "I chose this" never looks like "I never saw
    # this". This copy is only ever shown on screen; the download above is drawn
    # plain black.
    proof = result
    proof_layout = result_layout
    if result:
        unsettled_rows = {
            pair.english_id for pair in pairs
            if pair.status != "ok" and pair.english_id is not None
        }
        # Which notes have been settled by hand. A syllable on a held note is
        # keyed "...||held3" rather than by a note index, because it has no
        # English syllable under it to index by - and the attention marks are a
        # list per English syllable, so those keys have no place here and must
        # not be read as numbers.
        resolved_notes = set()
        for key, state in st.session_state["preview_flags"].items():
            if state != "resolved" or key.count("||") != 2:
                continue
            _, line_id, note = key.split("||")
            if note.isdigit():
                resolved_notes.add((int(line_id), int(note)))
        marks: dict[int, list[str]] = {}
        for voice_name, voice_plan in plans.items():
            for assignment in voice_plan.assignments:
                state = lock_mod.attention_marks(
                    assignment,
                    placements.get(assignment.score_line_id, []),
                    resolved_notes,
                    unsettled_rows,
                )
                if any(state):
                    marks[assignment.score_line_id] = state
        if marks:
            try:
                proof, proof_layout = proof_cached(
                    score_doc, blank_bytes, placements, held_notes, nudges, marks,
                    max_size, baseline, font_choice,
                    digest(
                        english_bytes, layout_bytes,
                        json.dumps(placements, sort_keys=True).encode(),
                        json.dumps(held_notes, sort_keys=True).encode(),
                        json.dumps(nudges, sort_keys=True).encode(),
                        json.dumps({str(k): v for k, v in marks.items()}, sort_keys=True).encode(),
                    ),
                )
            except Exception:  # noqa: BLE001
                # Proofing colour is a convenience, never a blocker.
                proof, proof_layout = result, result_layout

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

        st.markdown("**Preview — click a syllable to adjust it directly on the generated score**")
        import pymupdf as fitz

        pages = fitz.open(stream=result, filetype="pdf").page_count
        if st.session_state.get("preview_page", 1) > pages:
            st.session_state["preview_page"] = pages
        st.session_state.setdefault("preview_page", 1)
        page_pick = st.number_input(
            f"Preview page (1 to {pages})", 1, pages, key="preview_page"
        )

        # Every hit area comes straight from where the renderer actually drew the
        # syllable. Working back from the anchors instead - as this used to - was
        # wrong twice over: a syllable on a held note has no anchor at all, so it
        # got no box and could not be clicked, and a syllable moved aside to
        # clear its neighbour would have had its box left behind on the note.
        line_voice = {}
        for voice_name, voice_plan in plans.items():
            for assignment in voice_plan.assignments:
                line_voice[assignment.score_line_id] = voice_name

        visible_hotspots = []
        for drawn in proof_layout:
            if drawn["page"] + 1 != int(page_pick):
                continue
            if drawn["text"] == layout_mod.BLANK_BOX:
                continue
            voice_name = line_voice.get(drawn["line_id"])
            if voice_name is None:
                continue
            # An empty note carries no text to click on, and it is the one most
            # in need of a person. It gets a hotspot of its own.
            label = str(drawn["text"]).strip() or "(no syllable)"
            slot, index = drawn["slot"], drawn["index"]
            key = (f"{voice_name}||{drawn['line_id']}||{index}" if slot == "english"
                   else f"{voice_name}||{drawn['line_id']}||held{index}")
            visible_hotspots.append({
                "key": key,
                # The layout already carries the baseline the syllable sits on,
                # so the component must not add its own offset a second time.
                "x": float(drawn["x"]),
                "y": float(drawn["y"]),
                "size": float(drawn["size"]),
                "label": label,
                "voice": voice_name,
                "line_id": drawn["line_id"],
                "index": index,
                "slot": slot,
            })

        # The hotspots already carry the baseline each syllable was drawn on, so
        # the component is told to add nothing further.
        selected = selected_preview_value(proof, int(page_pick) - 1, visible_hotspots,
                                           st.session_state.get("preview_selected"),
                                           0.0)
        # Corrections typed onto the score itself. The component sends one of
        # these each time a syllable is committed, nudged, or deliberately left
        # as it stands; `seq` counts them, so the same correction made twice is
        # applied once and a rerun does not replay the last one.
        if selected and selected.get("key"):
            st.session_state["preview_selected"] = selected["key"]
            action = selected.get("action")
            seq = selected.get("seq")
            if action in ("edit", "nudge", "keep") and seq != st.session_state.get("preview_seq"):
                st.session_state["preview_seq"] = seq
                try:
                    voice_name = selected["voice"]
                    line_id = int(selected["line_id"])
                    index = int(selected["index"])
                    slot = selected.get("slot") or "english"
                    assignment = next(a for a in plans[voice_name].assignments
                                      if a.score_line_id == line_id)
                    row = f"{voice_name}||{line_id}"
                    if slot == "held":
                        # A syllable on a held note is stored against its place
                        # in the held run, not against a token index, because it
                        # has no English syllable underneath it to index by.
                        if action == "edit":
                            st.session_state.setdefault("held_edits", {})
                            st.session_state["held_edits"][f"{row}||held{index}"] = (
                                selected.get("text", "").strip()
                            )
                    elif action == "edit":
                        values = edited_tokens(voice_name, assignment)
                        while len(values) <= index:
                            values.append("")
                        values[index] = selected.get("text", "").strip()
                        st.session_state["assign_edits"][row] = " ".join(values)
                    elif action == "nudge":
                        values = edited_nudges(voice_name, assignment, len(assignment.tokens))
                        values[index] += float(selected.get("delta") or 0.0)
                        st.session_state["nudge_edits"][row] = " ".join(map(str, values))
                    # "keep" settles the note without changing it: the person has
                    # looked at it and is happy, which is exactly what the green
                    # is for. Every action marks it settled.
                    st.session_state["preview_flags"][selected["key"]] = "resolved"
                    st.rerun()
                except (KeyError, StopIteration, ValueError, TypeError):
                    pass

        st.caption(
            "**Red** is a note the app was unsure about — a gap it could not fill, or a "
            "line it only partly recognised. Click any syllable to type over it where it "
            "sits: Enter keeps what you typed, Escape leaves it alone, and the small bar "
            "underneath moves it sideways or marks it read. A note you have settled turns "
            "**green**; one you have not looked at keeps its red, so you can always tell "
            "what you have been through from what you have not. The colour is only here on "
            "screen — the downloaded score is plain black."
        )
