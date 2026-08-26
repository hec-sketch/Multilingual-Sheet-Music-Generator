from smgcore import layout, pairing, lock
from smgcore.score import parse_score

LAYOUT = '/mnt/data/jwb-147_We Go Preaching_SyllableLayout QII.pdf'
SCORE = '/mnt/data/QII_osg_We Go Preaching - Full Score.pdf'

doc = layout.parse_layout(open(LAYOUT, 'rb').read())
rows = layout.to_editable(doc, {})
score = parse_score(open(SCORE, 'rb').read())
eng, tr = layout.split_in_half(rows, doc.page_count, score.sung_words())
pr = pairing.pair_layouts(eng, tr)
assert len(eng) == len(tr) == 40
assert len(pr.pairs) == 40
assert pr.confidence == 1.0

# Bridge row 1 is intentionally mixed: two blue Lead boxes, followed by five yellow Harmony boxes.
mixed = next(line for line in eng if line.section == 'Br' and line.page == 3)
assert mixed.token_classes[:2] == ['prechorus', 'prechorus']
assert all(x == 'harmony' for x in mixed.token_classes[2:])
assert mixed.color_class == 'mixed'

eng2 = layout.inherit_pair_tags(eng, pr.pairs, tr)
translation = pairing.translation_map(pr.pairs, tr, {}, eng2)
lck = lock.build_lock(eng2, translation)
plans = lock.plan_voices(score, lck, score.voices)

lead = plans['Female Lead 1']
lead_bridge = [a for a in lead.assignments if a.section == 'Bridge']
assert lead_bridge[0].tokens[:3] == ['Mun-do', 'Jeho-', 'vá']
assert lead_bridge[0].tokens[3:] == ['Mai-', 'pi']

harm = plans['Female Harm 1']
harm_bridge = [a for a in harm.assignments if a.section == 'Bridge']
assert harm_bridge[0].tokens[:5] == ['en-', 'te-', 'ro-', 'pi-', 'mi']

# No lead assignment may ever consume a yellow Harmony token from a mixed row.
for assignment in lead.assignments:
    for line_id in assignment.layout_line_ids:
        line = next(x for x in eng2 if x.id == line_id)
        if 'harmony' in line.token_classes:
            # Verify the actual chosen words are from non-Harmony positions.
            for token in assignment.tokens:
                assert token not in {translation[line_id][i] for i,c in enumerate(line.token_classes) if c == 'harmony'}

print('mixed Harmony/Lead routing regression: PASS')
