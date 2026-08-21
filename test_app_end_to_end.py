"""Run the real app, press Generate, and score the PDF it produces against the answer key.

This exists because a previous version scored 100% when the alignment engine was
driven directly from a test script, but 96% through the app itself: the app was
not passing the reference timeline. Testing the library is not testing the app.
"""

import pathlib
import sys

import compare_pdf_to_key as compare

U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
FILES = {
    "english": U + "69deabc6-jwb143_DoNotLetYourHandsDropDown_Full_Score.pdf",
    "blank": U + "702ac9c0-jwb143_DoNotLetYourHandsDropDown_No_LyricsFull_Score.pdf",
    "layout": U + "1c671057-jwb143_SyllableLayoutaymara.pdf",
    "english_layout": U + "b6fa29ed-jwb143_Do_Not_Let_Your_Hands_Drop_Down_SyllableLayout.pdf",
}

SHIM = """
import io as _io, streamlit as _st
_FILES = {files!r}
class _Up(_io.BytesIO):
    def __init__(self, path):
        super().__init__(open(path, 'rb').read())
        self.name = path.split('/')[-1]
_orig_uploader = _st.file_uploader
def _fake_uploader(*a, **k):
    _orig_uploader(*a, **k)
    key = k.get('key')
    key = ''.join(c for c in (key or '') if not c.isdigit())
    return _Up(_FILES[key]) if key in _FILES else None
_st.file_uploader = _fake_uploader
"""


def run(with_english: bool):
    from streamlit.testing.v1 import AppTest

    files = dict(FILES)
    if not with_english:
        files.pop("english_layout")
    source = pathlib.Path("app.py").read_text()
    source = source.replace(
        "import streamlit as st", "import streamlit as st\n" + SHIM.format(files=files), 1
    )
    target = f"_generated_{'four' if with_english else 'three'}_file_app.py"
    pathlib.Path(target).write_text(source)

    label = "FOUR FILES (word matching)" if with_english else "THREE FILES (count fallback)"
    print("=" * 70)
    print(label)
    at = AppTest.from_file(target, default_timeout=900)
    at.run()
    for exception in at.exception:
        print("EXCEPTION:", exception.value)
        return None
    print("errors  :", [e.value[:140] for e in at.error])
    print("metrics :", [(m.label, m.value) for m in at.metric if "Notes" in m.label or "paired" in m.label])

    at.session_state["active_step"] = 5
    at.run()
    [b for b in at.button if "Generate" in b.label][0].click().run()
    for exception in at.exception:
        print("EXCEPTION AFTER GENERATE:", exception.value)
        return None

    pdf = at.session_state["result_pdf"]
    out = f"_app_output_{'four' if with_english else 'three'}.pdf"
    pathlib.Path(out).write_bytes(pdf)
    return out


for with_english in (True,):  # the English layout is now a mandatory upload; no fallback mode
    produced = run(with_english)
    if not produced:
        sys.exit(1)
    key = compare.read(compare.KEY, ["Arial"])
    ours = compare.read(produced, ["LiberationSerif", "DejaVu"], geometry_from=compare.ENGLISH)
    entries = sorted(set(list(key) + list(ours)))
    good = sum(
        1
        for entry in entries
        if compare.flat(ours.get(entry, "")) == compare.flat(key.get(entry, ""))
    )
    print(f"APP OUTPUT vs answer key: {good}/{len(entries)} staff lines  ({good / len(entries):.1%})")
    for entry in entries:
        if compare.flat(ours.get(entry, "")) != compare.flat(key.get(entry, "")):
            print(f"   page {entry[0] + 1} staff {entry[1]}")
            print(f"      app: {' '.join(ours.get(entry, '').split())[:120]}")
            print(f"      key: {' '.join(key.get(entry, '').split())[:120]}")
