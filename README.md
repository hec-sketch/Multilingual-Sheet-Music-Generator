# Multi-lingual Sheet Music Generator

Takes a translated syllable layout and writes it under the notes of an engraved vocal score.

## The files

| File | What it is | |
| --- | --- | --- |
| **1. English score** | The engraved score with the English lyrics under the notes | required |
| **2. Same score, no lyrics** | The identical engraving with the lyrics removed — this is the canvas | made from file 1 if you do not have it |
| **3. Syllable layout or lyrics sheet** | The translator's document, line by line | required |
| **4. English syllable layout** | The same layout before it was translated | only if it is a separate file |

It returns the no-lyrics score with the translated syllables placed on the notes.

**The least you can supply is files 1 and 3.** Everything else is worked out from those two.

**One file or two.** Some translators keep the English layout and the translated one in a single
document — facing halves, or one under the other. Others keep them as two files. Either works,
and you are not asked which: every English syllable is already printed in your score, so the app
reads each row and knows whether it is English or the translation. Rows come out at around 100%
against the score when they are English and under a third when they are not, so it is not a
close call. Step 1 tells you which it found.

Where the two languages sit in one document, the translator has already lined each translated
line up with its English one, so the app does not have to work that out: pairing is exact rather
than a sequence match.

### When file 2 is missing

The no-lyrics score is the canvas the syllables are drawn onto. If you were never sent one, the
app makes it: it deletes the lyric text from file 1 and leaves everything else — staves, notes,
slurs, dynamics, section labels — exactly as engraved. It knows which text to delete because it
is the same text it already turned into syllable slots.

Scored against the same hand-made Aymara score, the derived canvas gives **112 / 112 (100%)** —
the same as being handed the engraver's own no-lyrics export. Use theirs when you have it; you
lose nothing when you don't.

### When file 3 is a plain lyrics sheet

Not every translator produces a grid with one box per note. Some send an ordinary page of words,
hyphenated at the syllable breaks, under section headings:

```
Chorus 1
Via-hi vi nji vui-la lio-va
Ye-ho-va ali na-nge;
```

That is enough. The app recognises the format on sight — headings spelled out in words, hyphens
inside words — and switches to it without being told. It then takes from the score what the grid
would have supplied: the English is cut into lines at the engraver's own punctuation, and those
lines are matched to the translator's written lines by section, order and syllable count.

A grid is still the better input where one exists, because it states which syllable belongs on
which note instead of leaving that to be worked out. The app prefers a grid whenever it can read
one, and only reads a sheet as prose when there is no grid to read.

Where a written line and its sung phrase disagree on how many syllables there are — usually a
word the translator left un-hyphenated — the line is matched anyway and listed in Step 3 for you
to correct. Nothing is invented to make the counts agree.

---

### Checked against a hand-made score

The PDF the app produces was compared, staff line by staff line, against the Aymara score a
person had already produced by hand for the same piece:

| | Staff lines matching the human version |
| --- | --- |
| **All four files** | **112 / 112 (100%)** |

Across nine voice parts, nine pages and 1,034 notes — including the places where a part enters
mid-word, where two syllables share a note, and where the same English line has two different
translations depending on which repeat it is.

The comparison is run against the app's own output, not against the matching engine called
directly. An earlier version scored 100% when the engine was driven from a test script and 96%
through the app, because the app was not passing the engine everything it needed.

---

## What it handles

**Full choral scores.** Each voice part is tracked separately — Male Lead, Female Harmony 1
and 2, Male Harmony 1–3, Ad Libs — across every system and page. Voices that sing different
words, enter late, drop out, or come back in a canon all get their own correct stream of
syllables. Lines that wrap from one system or page to the next are stitched back together.

**Layouts that do not line up perfectly.** The English and translated layouts are aligned as
sequences, not zipped together, so a line the translator merged, split, added or left out is
found and reported rather than silently shifting everything after it.

**Two syllables on one note.** Draw a box round some syllables and write *"Cantar 2 sílabas en
una"*; the app reads the box and joins those syllables onto one note. Draw it once and it is
applied to every repeat of that line. You can also do it by hand anywhere: delete the space
between two syllables and they share a note; add a space and they split again.

**Empty boxes, in either language.** The layout is a grid of one box per note, and a box holding
only a dash is a note the English sings no new syllable on — it holds the one before. The app
treats those as real boxes:

- **English blank, translation filled.** There is no English syllable on that note to hang the
  translation from, so the app reads the *engraving* instead: it finds the notes carrying no
  lyric and puts the syllable on the right one.
- **English filled, translation blank.** The note is simply left empty, as the translator
  intended. Which box is empty is worked out from the column it sits in, not from the counts —
  the two documents share the same grid, so a column with nothing opposite it is the answer.

A difference in the two syllable counts is only reported when the blanks cannot account for it.

**Template documents.** A layout whose later pages are the blank template the translator worked
from is read correctly — the empty pages are recognised and ignored.

**Harmony-only lines.** A line tagged `Harmonies` is offered to the harmony parts and skipped
for the lead.

---

## Nothing is decided behind your back

Every step is a table you can edit before the PDF is made:

- **Layout lines** — correct any syllable, retag a line, change its section, or drop it. The
  English layout is shown too, in case the app misread the original document.
- **English ↔ translation** — every English line beside the translation the app paired with it,
  with both syllable counts. Type over anything.
- **Matching** — each voice line by line: the English printed in the score, how many notes it
  has, and the syllables going onto them. Type over anything. Leave a voice in English entirely
  if you want.
- **Generate** — mismatches between syllables and notes are listed plainly, and you can still
  produce the PDF. There is a CSV checking sheet for proofreading away from the app.

When the app cannot work something out it says so and lets you fix it.

---

## Running it

### On Streamlit Community Cloud (shareable link, free)

1. Push this folder to a **public** GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. **Create app** → pick the repository → main file path `app.py` → **Deploy**.

You get a URL like `https://your-app-name.streamlit.app` to share with anyone.

### On your own computer

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501.

---

## How it works

```
app.py            The interface: upload, review, edit, generate
smgcore/
  score.py        Finds staves, groups them into systems using the brackets down their
                  left edge, reads voice names from the margin, locates section markers,
                  and pulls out every syllable position by its font signature
  layout.py       Reads a layout document: rows, section labels, tags, two-column merging,
                  boxes that join syllables onto one note. The body column and the margin
                  are measured across the whole file, so sparse template pages read correctly
  pairing.py      Aligns the English layout with the translated one — a sequence alignment
                  over section labels, tags, page geometry and syllable counts
  align.py        Two engines. With an English layout: a semi-global Needleman-Wunsch
                  alignment of each voice's printed lyrics against the English layout, giving
                  an exact note-by-note mapping. The busiest voice is aligned first and used
                  as a clock, so a part that repeats a line the lead has already sung is
                  placed by *when* it sings, not just by what the words say. Words are never
                  left starting on a syllable fragment. Without an English layout: the older
                  search over section labels and syllable counts
  render.py       Draws the syllables onto the no-lyrics score, shrinking anything that
                  would collide with its neighbours
fonts/            Unicode fonts covering the accented and modifier characters used by
                  languages such as Aymara (ʼ ñ ï ä á)
```

## Limits worth knowing

- Both scores must be the **same engraving** — the app checks this and warns you if the
  staves don't line up.
- The score must be a real engraved PDF, not a scan. A scanned image has no text to read.
- The English syllable layout has to be the same document the translator worked from. A
  different English edition with different line breaks will still work, but less well.
- Freehand markings other than syllable-joining boxes aren't interpreted. Anything the app
  can't read, you can type in yourself.
