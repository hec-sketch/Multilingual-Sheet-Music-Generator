# Multi-lingual Sheet Music Generator

Takes a translated syllable layout and writes it under the notes of an engraved
vocal score.

## The two files

| File | What it is |
| --- | --- |
| **1. English score** | The engraved score with the English lyrics under the notes |
| **2. Syllable layout** | The English syllable layout in full, followed by the translated one, in a single document |

It returns the score with the English lyrics removed and the translated syllables
set on the notes. The score without lyrics is made from file 1, so there is no
third file to supply.

---

## What the layout has to say — conventions

The app reads the layout literally. These are the things it takes the document at
its word about, so they are worth getting right at the source: every one of them
saves a correction later.

### One box, one note

A drawn cell is a note. Two syllables written in two boxes stay two notes even
when they are typed hard up against the border between them - `p'un-` `chay` with
no gap comes out of the PDF as the single word `p'un-chay`, and the app cuts it
back apart at the hyphen. A cell holding only `-`, or nothing at all, is still a
note — one the previous syllable is held across. Those cells must be drawn, not
left out, or every syllable after them lands a note early.

Two syllables written **inside one box, separated by a space** means "sing these
two on one note". That is the only thing a space inside a box means, so do not
use one for anything else.

### The hyphen decides whether syllables are joined

**A syllable is only ever joined to the one before it when the layout hyphenates
it.** This is the house rule, and the app implements exactly it.

So if the translated word *Jehová* is split across two boxes, write it
`Jeho-` `vá`. A voice that enters on the second box then correctly receives
`Jeho-vá` on its first note, because the hyphen says the word carries on. Write
`Ma` `ku` without a hyphen and a voice entering on `ku` receives `ku` alone,
because the layout has said they are two separate syllables.

Getting this wrong is not a small thing: it is the difference between a harmony
singer seeing the word they are singing and seeing a fragment of it. It is also
the single largest source of disagreement between the app's output and a
hand-made score — about thirty rows across the ten reference songs, every one of
them a document written before this rule was settled.

### Punctuation is read, so a repeat must be punctuated as it is engraved

Where a line is written out more than once and translated differently each time,
the full stop that ends the row is what tells the repeats apart: `trust in you.`
in the chorus against `trust in you!` at the end of the song. Write the stop the
score prints. A row that leaves it off is not held against the part it belongs
to, but it gives the app one less thing to go on.

### The two halves must be the same song, written twice

The English layout in full, then the translated one, same rows in the same order.
The halves are cut by page count. They do **not** have to break for a new page in
the same place — the app pairs them as two continuous streams — but they do have
to tell the song in the same order.

Where the English half heads a block `Ch2, 3` (one written block serving two
sections) and the translation writes Ch2 and Ch3 out separately, the halves no
longer hold the same rows. Prefer writing both halves the same way.

### Section labels, harmony, and everything that is not sung

- **Section labels** (`Ch1`, `1`, `Pre-Ch 2`, `Bridge`) go in the left margin and
  hold for the rows beneath them. A label written *below* the row it belongs to
  will be read as belonging to the rows after it.
- **Harmony-only rows** are marked either with a `(Harmonies)` label beside the
  row or with yellow cell fill. A harmony row is never given to a lead voice.
- **Notes to the singer** — `Cantar 2 sílabas en una`, `Only in Ch2`, review
  comments, tally marks — must sit **outside** the drawn cells. Anything whose
  centre falls inside a cell is read as a syllable.

### The document must be exported with a Unicode font

Word's default export can use WinAnsi (Latin-1) encoding, which physically cannot
store characters such as `ʉ ẽ ã ĩ ʼ`. When that happens the orthography is lost
before the app ever sees the file, and nothing downstream can recover it. One of
the ten reference layouts has this problem and cannot score above about 30%
because of it alone.

---

## What the app does with them

The layout is read once into locked lines — English syllable against translated
syllable, in the columns the translator wrote them in. Setting the score is then
not a matching problem between two documents: each voice's line of English is
read off the engraving, found among the locked lines, and its syllables swapped
for the translated ones opposite.

Things it handles that are easy to get wrong:

- **A doubling voice that sings a reduced line.** Where the score prints
  `faith I move a moun-tain.` against the lead's `faith I can move a moun-tain.`,
  each word the part sings takes the box locked to it and the box for `can` is
  passed over.
- **A phrase wrapping at a system break.** A one-note fragment cannot say which
  written row it opens, so the row is chosen partly by what the same voice sings
  next.
- **A chorus written out once per repeat**, with a line or two translated
  differently each time. A part tag only argues for a row that is in the right
  part of the song.
- **Blank and dash cells**, in either language, as real notes. Where the English
  half leaves a box blank and the translation writes a syllable in it, that
  syllable goes on the note the English holds its vowel across.

Nothing is decided behind your back: every step is an editable table before the
PDF is made, and Step 5 lists anything that will be left empty. On the generated
score itself, clicking a syllable opens a box on top of it — type over it where
it sits, Enter to keep, Escape to leave it. Notes the app was unsure about are
red on screen and turn green once you have settled them; the downloaded PDF is
always plain black.

---

## How it is tested

Two things, both reading the read-only corpus in `test-data/`:

```bash
python3 -m pytest tests_layout_regressions.py   # 29 tests, all passing
python3 corpus_baseline.py                      # the whole corpus, scored
```

`tests_layout_regressions.py` pins one reproduced defect each. Most of them fail at the `v1.0` tag, which is what makes them regression tests rather
than descriptions of current behaviour.

`corpus_baseline.py` replays the app's pipeline over every case with no human
edits and compares the generated PDF to the hand-made answer key, row by row.
Current standing, from `v1.0` to now:

| case | v1.0 | now |
| --- | --- | --- |
| More Than Sparrows / WY | 81 | **109 / 112** |
| Hands Drop Down / EMB-Diphthong | 83 | **108 / 112** |
| Hands Drop Down / KIM | 91 | **102 / 112** |
| Hands Drop Down / QUB | 81 | **100 / 112** |
| We Go Preaching / QII | 58 | **64 / 67** |
| We Go Preaching / WY | 57 | **60 / 67** |
| By Faith / QII | 31 | **45 / 52** |
| By Faith / QU | new | **45 / 52** |
| By Faith / WY | 36 | **45 / 52** |
| More Than Sparrows / EMB | 21 | **25 / 111** |
| Rise Again / QUB | new | **61 / 61** |
| Trust in you / QUB | new | **18 / 18** |

885 of 988 rows. Hands Drop Down / AP is withdrawn from scoring: its English
half heads one block `Ch2, 3` where the translation writes Ch2 and Ch3 out
separately, so twelve translated rows have nothing opposite them - a document
matter, not a defect. Rise Again and Trust in you are songs the fixes were never
tuned on: both were added after the work above and scored on first contact. The
song title is not counted: it appears in the answer keys and in neither half of
any layout, so no reading of the inputs can produce it.

`tests_byfaith_exact.py`, `tests_semantic_pairing.py` and
`tests_mixed_harmony.py` read from `/mnt/data/...`, which is not in this
repository. **They have never run** and should be repointed at `test-data/` or
removed.

---

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Both scores must be the same engraving, and the score must be a real engraved
PDF rather than a scan. The renderer uses an available Unicode system font; no
font binaries are bundled.
