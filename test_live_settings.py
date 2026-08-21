"""Type settings must apply without pressing Generate again, and without crashing."""
import pathlib, sys
U = "/root/.claude/uploads/b9bec0c9-1936-58a6-bb58-8c0df0b786d4/"
FILES = {"english": U+"0fdce4c7-jwb141_By_Faith_Full_Score.pdf",
         "blank": U+"47c6c538-jwb141_By_Faith_No_LyricsFull_Score.pdf",
         "layout": U+"11bfad80-jwb141_By_Faith_SyllableLayout_2.pdf"}
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
pathlib.Path("_gen_live.py").write_text(src)

at = AppTest.from_file("_gen_live.py", default_timeout=900)
at.run()
if at.exception: print("EXC on load:", at.exception[0].value); sys.exit(1)
at.session_state["active_step"] = 5
at.run()
[b for b in at.button if "Generate the score" in b.label][0].click().run()
if at.exception: print("EXC on generate:", at.exception[0].value); sys.exit(1)
first = at.session_state["result_pdf"]
print(f"generated: {len(first)} bytes, preview page = {at.session_state['preview_page']}")

# move to page 3, then change each setting in turn
at.session_state["preview_page"] = 3
at.run()
for label, key, value in [("type size", "max_size", 10.0),
                          ("spacing", "baseline", 11.0),
                          ("font", "font_choice", "Sans")]:
    at.session_state[key] = value
    at.run()
    if at.exception:
        print(f"EXC after changing {label}:", at.exception[0].value); sys.exit(1)
    pdf = at.session_state["result_pdf"]
    page = at.session_state["preview_page"]
    where = at.session_state["active_step"]
    print(f"after {label:10} -> {len(pdf):>7} bytes | changed: {pdf != first} | "
          f"still on step {where}, preview page {page} | errors: {len(at.error)}")
    if page != 3:
        print("   !! preview page was reset"); sys.exit(1)
    if where != 5:
        print("   !! the open step was reset"); sys.exit(1)
    first = pdf
print("no Generate press was needed after the first one")
