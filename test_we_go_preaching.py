"""We Go Preaching (jwb147): a harder lyrics-sheet case.

The layout file mixes English and Wayuunaiki prose in one PDF, uses
heading abbreviations ("Ch1", "Br", "Ch2, 3"), and its written lines are
often longer/fewer than the score's own phrase-cuts (long translated
sentences covering several short sung phrases).
"""
import pathlib, sys

U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
FILES = {
    "english": U + "3a755121-jwb147_We_Go_Preaching_Full_Score.pdf",
    "layout": U + "3c72f668-jwb147_We_Go_Preaching_WY.pdf",
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
pathlib.Path("_gen_wegopreaching.py").write_text(src)

at = AppTest.from_file("_gen_wegopreaching.py", default_timeout=900)
at.run()
if at.exception:
    print("EXCEPTION on load:", at.exception[0].value); sys.exit(1)
print("errors :", [e.value[:160] for e in at.error])
print("metrics:", [(m.label, m.value) for m in at.metric])
print("warns  :", [w.value[:160] for w in at.warning])

at.session_state["active_step"] = 5
at.run()
if at.exception:
    print("EXCEPTION on step 5:", at.exception[0].value); sys.exit(1)
print("step5  :", [(m.label, m.value) for m in at.metric])

[b for b in at.button if "Generate the score" in b.label][0].click().run()
if at.exception:
    print("EXCEPTION on generate:", at.exception[0].value); sys.exit(1)
pdf = at.session_state["result_pdf"]
pathlib.Path("_wegopreaching_out.pdf").write_bytes(pdf)
print("PDF bytes:", len(pdf))

for step in (1, 2, 3, 4):
    at.session_state["active_step"] = step
    at.run()
    if at.exception:
        print(f"EXCEPTION on step {step}:", at.exception[0].value); sys.exit(1)
    print(f"step {step} ok, errors: {len(at.error)}")
print("all steps rendered")
