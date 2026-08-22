"""Drive the real app on a single layout document holding both languages."""
import pathlib, sys
U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
FILES = {
    "english": U + "0fdce4c7-jwb141_By_Faith_Full_Score.pdf",
    "layout": U + "11bfad80-jwb141_By_Faith_SyllableLayout_2.pdf",
}
SHIM = """
import io as _io, streamlit as _st
_FILES = {files!r}
class _Up(_io.BytesIO):
    def __init__(self, path):
        super().__init__(open(path, 'rb').read()); self.name = path.split('/')[-1]
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
pathlib.Path("_gen_combined.py").write_text(src)
at = AppTest.from_file("_gen_combined.py", default_timeout=900)
at.run()
for e in at.exception:
    print("EXCEPTION:", e.value); sys.exit(1)
print("errors :", [e.value[:200] for e in at.error])
print("steps  :", [b.label.split("\n")[0] for b in at.button if "  " in b.label][:5])
print("metrics:", [(m.label, m.value) for m in at.metric])
print("warns  :", [w.value[:150] for w in at.warning])
at.session_state["active_step"] = 5
at.run()
[b for b in at.button if "Generate" in b.label][0].click().run()
for e in at.exception:
    print("EXCEPTION AFTER GENERATE:", e.value); sys.exit(1)
pdf = at.session_state["result_pdf"]
pathlib.Path("_combined_out.pdf").write_bytes(pdf)
print("PDF bytes:", len(pdf))
