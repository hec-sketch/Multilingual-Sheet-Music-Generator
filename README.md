# Multi-lingual Sheet Music Generator

Takes a translated syllable layout and writes it under the notes of an engraved vocal score.

You give it three PDFs:

| File | What it is |
| --- | --- |
| **English score** | The engraved score with the English lyrics under the notes |
| **Same score, no lyrics** | The identical engraving with the lyrics removed — this is the canvas |
| **Syllable layout** | The translator's document: the translated syllables, line by line |

It returns the no-lyrics score with the translated syllables placed on the notes.

---

## What it handles

**Full choral scores.** Each voice part is tracked separately — Male Lead, Female Harmony 1
and 2, Male Harmony 1–3, Ad Libs — across every system and page. Voices that sing different
words, enter late, drop out, or come back in a canon all get their own correct stream of
syllables. Lines that wrap from one system or page to the next are stitched back together.

**Layouts with no English in them.** Most tools need the English line and the translation
side by side. This one doesn't. It reads the section labels in your layout (`Ch1`, `1`,
`Pre-Ch 2`, `Ch3`), matches them to the section markers in the score (`Chorus 1`, `Verse 1`,
`Pre-Chorus 2`), and then works out line by line which layout lines each voice sings, using
syllable counts. Layouts that *do* pair English with a translated comment work too — the app
tries both readings and uses whichever actually matches.

**Two syllables on one note.** If you draw a box round some syllables and write
*"Cantar 2 sílabas en una"*, the app reads the box and joins those syllables onto one note.
Draw it once and it is applied to every repeat of that line. You can also do it by hand
anywhere: delete the space between two syllables and they share a note; add a space and they
split again.

**Two-column layouts.** Where a musical line is printed as a left and a right half on the
same row, the two halves are read as one line.

**Harmony-only lines.** A line tagged `Harmonies` is offered to the harmony parts and skipped
for the lead.

---

## Nothing is decided behind your back

Every step is a table you can edit before the PDF is made:

- **Layout lines** — correct any syllable, retag a line, change its section, or drop it.
- **Matching** — see each voice line by line: the English, how many notes it has, and the
  syllables going onto them. Type over anything. Leave a voice in English entirely if you want.
- **Generate** — mismatches between syllables and notes are listed plainly, and you can still
  produce the PDF. There is a CSV checking sheet for proofreading away from the app.

The app does not silently guess. When it cannot work something out it says so and lets you
fix it.

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
  score.py        Finds staves, groups them into systems, reads voice names from the
                  margin, locates section markers, and pulls out every syllable position
                  by its font signature
  layout.py       Reads the layout: rows, section labels, tags, two-column merging,
                  boxes that join syllables onto one note
  align.py        Decides which layout lines each voice sings — a shortest-path search
                  over section labels, syllable counts and tags
  render.py       Draws the syllables onto the no-lyrics score, shrinking anything that
                  would collide with its neighbours
fonts/            Unicode fonts covering the accented and modifier characters used by
                  languages such as Aymara (ʼ ñ ï ä á)
```

## Limits worth knowing

- Both scores must be the **same engraving** — the app checks this and warns you if the
  staves don't line up.
- The score must be a real engraved PDF, not a scan. A scanned image has no text to read.
- If your layout has no section labels, matching falls back to order and syllable count
  alone. It still works, but check the Matching tab more carefully.
- Freehand markings other than syllable-joining boxes aren't interpreted. Anything the app
  can't read, you can type in yourself.
