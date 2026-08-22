"""File 2 holding the translation but no English.

The English lines are cut from the score instead. It works, and the app says so,
but it is measurably worse than a document holding both languages - which is the
whole reason Step 1 puts a notice on it.
"""
import pathlib, sys
import compare_pdf_to_key as compare

U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
FILES = {
    "english": U + "69deabc6-jwb143_DoNotLetYourHandsDropDown_Full_Score.pdf",
    "layout": U + "1c671057-jwb143_SyllableLayoutaymara.pdf",
}
SHIM = """
import io as _io, streamlit as _st
_FILES = {files!r}
class _Up(_io.BytesIO):
    def __init__(self, path):
        super().__init__(open(path,'rb').read()); self.name = path.split('/')[-1]
_orig = _st.file_uploader
def _fake(*a, **k):
    _orig(*a, **k); key = k.get('key')
    key = ''.join(c for c in (key or '') if not c.isdigit())
    return _Up(_FILES[key]) if key in _FILES else None
_st.file_uploader = _fake
"""
from streamlit.testing.v1 import AppTest

src = pathlib.Path("app.py").read_text().replace(
    "import streamlit as st", "import streamlit as st\n" + SHIM.format(files=FILES), 1)
pathlib.Path("_gen_tronly.py").write_text(src)
at = AppTest.from_file("_gen_tronly.py", default_timeout=900)
at.run()
if at.exception:
    print("EXC:", at.exception[0].value); sys.exit(1)
print("errors :", [e.value[:120] for e in at.error])
print("metrics:", [(m.label, m.value) for m in at.metric])

at.session_state["active_step"] = 5
at.run()
[b for b in at.button if "Generate the score" in b.label][0].click().run()
if at.exception:
    print("EXC gen:", at.exception[0].value); sys.exit(1)
out = "_tronly_out.pdf"
pathlib.Path(out).write_bytes(at.session_state["result_pdf"])
key = compare.read(compare.KEY, ["Arial"])
ours = compare.read(out, ["LiberationSerif", "DejaVu"], geometry_from=compare.ENGLISH)
entries = sorted(set(list(key) + list(ours)))
good = sum(1 for e in entries
           if compare.flat(ours.get(e, "")) == compare.flat(key.get(e, "")))
print(f"TRANSLATION-ONLY file 2 vs answer key: {good}/{len(entries)} ({good/len(entries):.1%})")
print("(the combined document scores 112/112 on the same piece)")
