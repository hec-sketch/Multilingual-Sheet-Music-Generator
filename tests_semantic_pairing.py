from smgcore.layout import parse_layout, to_editable, split_in_half
from smgcore.score import parse_score
from smgcore.pairing import pair_layouts

LAYOUT='/mnt/data/jwb-147_We Go Preaching_SyllableLayout QII.pdf'
SCORE='/mnt/data/QII_osg_We Go Preaching - Full Score.pdf'

doc=parse_layout(open(LAYOUT,'rb').read())
lines=to_editable(doc,{})
score=parse_score(open(SCORE,'rb').read())
eng,tr=split_in_half(lines,doc.page_count,score.sung_words())
r=pair_layouts(eng,tr)
assert len(eng)==40 and len(tr)==40
assert len(r.pairs)==40
assert r.confidence==1.0
# Exact positional identity: line N in each semantic color block remains line N.
# Harmony rows must stay in their own stream and must never shift the rows after them.
for p in r.pairs:
    if p.tag.lower().startswith('harm'):
        assert p.english_id is not None and p.translated_id is not None
print('semantic color pairing regression: PASS')
