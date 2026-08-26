"""Regression tests for harmony pairing and lead exclusion."""
from types import SimpleNamespace
from smgcore.pairing import pair_layouts

class Tok:
    def __init__(self, text, x0=0, x1=10):
        self.text=text; self.x0=x0; self.x1=x1


def line(i, text, tag="", section="Bridge"):
    toks=[Tok(t,i*12,(i+1)*12-2) for i,t in enumerate(text.split())] if text else []
    return SimpleNamespace(id=i, text=text, tag=tag, section=section,
                           tokens=toks, note_count=len(toks), page=1, xs=[t.x0 for t in toks])


def main():
    # Case 1: a one-box harmony line is visibly yellow/harmony on only one half.
    # It pairs with the one-box English counterpart, but cannot shift following rows.
    eng=[
        line(0,"One"),
        line(1,"Four five"),
        line(2,"Six seven eight"),
    ]
    tr=[
        line(10,"a-b-c",tag="Harmonies"),
        line(11,"d-e"),
        line(12,"f-g-h"),
    ]
    result=pair_layouts(eng,tr)
    assert result.pairs[0].english_id == 0 and result.pairs[0].translated_id == 10
    assert result.pairs[0].tag == 'Harmonies'

    # Case 2: an extra harmony row with no plausible English counterpart stays
    # translation-only and must not shift the following ordinary lead rows.
    eng=[
        line(0,"How could I doubt your saving hand?"),
        line(1,"You've always seen me through."),
        line(2,"They strengthen my faith in you."),
    ]
    tr=[
        line(10,"ta-noujain",tag="Harmonies"),
        line(11,"Pu't te'e reein ma'in ta-ya"),
        line(12,"Pi'i-taa-la na a-na-ka-na"),
    ]
    result=pair_layouts(eng,tr)
    assert result.pairs[0].english_id is None and result.pairs[0].translated_id == 10
    assert result.pairs[1].english_id == 0 and result.pairs[1].translated_id == 11
    assert result.pairs[2].english_id == 1 and result.pairs[2].translated_id == 12
    print('harmony pairing regression: PASS')

if __name__ == '__main__':
    main()
