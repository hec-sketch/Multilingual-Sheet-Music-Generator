# Multi-lingual Sheet Music Generator

Takes a translated syllable layout and writes it under the notes of an engraved vocal score.

## The four files

| File | What it is | |
| --- | --- | --- |
| **1. English score** | The engraved score with the English lyrics under the notes | required |
| **2. Same score, no lyrics** | The identical engraving with the lyrics removed — this is the canvas | required |
| **3. Translated syllable layout** | The translator's document: the translated syllables, line by line | required |
| **4. English syllable layout** | The *same* layout document before it was translated, still in English | strongly recommended |

It returns the no-lyrics score with the translated syllables placed on the notes.

---

## Why the fourth file changes everything

Given only the translated layout, the app has no way to know *where* each line belongs. It has
to infer it from section labels and syllable counts. On a six-part choral score where the voices
sing overlapping words, enter late, drop out and come back in canon, that is a hard guess.

Give it the English layout as well and there is nothing left to guess. The English layout is
matched **word for word** against the English lyrics already printed in the score, so the app
knows exactly which notes each layout line covers. The translated line sitting opposite it then
drops straight onto those notes.

On the test piece — *Do Not Let Your Hands Drop Down*, nine voice parts, 1,034 syllable
positions — the two approaches differ on **60% of the notes**. With the English layout, every
one of the 1,034 notes was filled correctly and every voice matched cleanly with nothing left
for a human to fix.

The app still works with three files. It just tells you plainly that it is guessing.

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
                  an exact note-by-note mapping. Without one: the older search over section
                  labels and syllable counts
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
