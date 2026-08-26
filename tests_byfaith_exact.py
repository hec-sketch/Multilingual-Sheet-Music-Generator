from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from smgcore.layout import parse_layout, split_in_half, to_editable
from smgcore.pairing import pair_layouts

LAYOUT = Path('/mnt/data/jwb-141_By Faith_SyllableLayout-3 (1).pdf')

doc = parse_layout(LAYOUT.read_bytes())
rows = to_editable(doc)
half=doc.page_count//2
eng=[x for x in rows if x.page < half]
tr=[x for x in rows if x.page >= half]
# split_in_half needs score vocabulary only as a sanity check; bypass it for exact
# row-count verification when running this regression standalone.
assert len(eng) == 23, len(eng)
assert len(tr) == 23, len(tr)
res = pair_layouts(eng, tr)
# Exact Harmony pairing must exist in the Bridge rows.
harmony = [p for p in res.pairs if (p.tag or '').lower().startswith('harm')]
assert len(harmony) == 1, [(p.status,p.english_text,p.translated_text,p.tag) for p in harmony]
p = harmony[0]
assert p.english_text == 'By faith', p
assert p.translated_text == 'ta- noujain', p
assert p.status == 'ok', p
assert not any(p.status == 'translation-only' and p.translated_text == 'ta- noujain' for p in res.pairs)

# Every paired row must preserve the physical box count, including explicit '-'
# hold boxes. A dash is a structural note column, not a missing box.
assert all(p.status == 'ok' and p.english_count == p.translated_count for p in res.pairs), [(p.english_text,p.translated_text,p.english_count,p.translated_count,p.status) for p in res.pairs if p.status != 'ok' or p.english_count != p.translated_count]
print('PASS: exact By Faith layout = 23 English + 23 translated; all physical box counts agree, including blank/dash boxes; Harmony By faith <-> ta- noujain paired; no orphan Harmony row.')
# The four ordinary Bridge rows are a distinct Bridge stream even though their boxes are blue.
bridge_pairs = [p for p in res.pairs if p.section.lower() == 'bridge' and p.tag.lower() != 'harmonies']
assert len(bridge_pairs) == 4, [(p.section,p.tag,p.english_text,p.translated_text) for p in bridge_pairs]
assert all(p.status == 'ok' for p in bridge_pairs)
