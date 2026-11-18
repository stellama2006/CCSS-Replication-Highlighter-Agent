# AJPS Replication Highlighter Agent

An AI agent that reads a manuscript submitted to the American Journal of Political
Science (AJPS) and highlights, in yellow, the in-text numbers, figures, and tables
that a verifier would need to reproduce from the replication package.

Status: prototype / in development
Development test paper: Delgado-Mohatar & Alelú-Paz (2026), "When Algorithms Guard
Democracy", Social Sciences 15, 372 (21 pages).

---

## 1. Overview

### Purpose
AJPS verification requires that results in the manuscript can be reproduced from the
authors' replication package. [Confirm exact AJPS policy wording with mentor.]
Finding every reportable result by hand is slow and error-prone. This agent produces a
first-pass highlighted PDF and a checklist so a human verifier can start from there.

### What it does
- Reads a manuscript PDF.
- Finds candidate items: in-text numbers, tables, figures.
- Classifies each as "needs replication" or "does not need replication".
- Highlights items that need replication in yellow.
- Outputs a checklist (CSV/JSON) with page, text, category, reason, confidence.

### What it does not do
- It does not decide that a result is correct. It only flags what to check.
- It does not replace the human verifier (human review is always the last step).

### Tools and APIs
| Component | Choice |
|---|---|
| Language | Python 3.x |
| PDF parsing and highlighting | PyMuPDF (`fitz`) |
| Table detection (optional) | pdfplumber [or PyMuPDF `find_tables`] |
| Rule-based filtering | Python `re` |
| LLM classifier | [model / provider TBD] via [API TBD] |
| Output | `pandas` -> CSV, `json` |

Confidentiality note: submitted manuscripts are unpublished. Do not send full text to an
external LLM API until it is confirmed this is allowed.
[Allowed provider / local model / approved institutional endpoint: ]

---

## 2. Pipeline

PDF -> extract text and coordinates -> find candidates and classify
    -> highlight in yellow on the PDF -> output checklist

Input : manuscript.pdf
Output: manuscript_highlighted.pdf, checklist.csv, checklist.json

---

## 3. Steps: tools and implementation

### 3.1 PDF
- Input is a text-based (born-digital) PDF.
- Open with `fitz.open(path)`.
- Check each page has extractable text. If a page has none (scanned), flag it and
  [decide: run OCR (e.g. Tesseract) or reject].
- Keep the original PDF untouched; write highlights to a copy.

### 3.2 Extract text and coordinates
- `page.get_text("words")` returns every word with its bounding box
  (x0, y0, x1, y1, word, block, line, word_no).
- `page.get_text("dict")` for line/block structure and font size
  (used to detect captions and section headings).
- `page.get_images()` and `page.get_drawings()` to locate figures (bounding boxes).
- Store everything as a per-page list so later steps can map a matched string back
  to a rectangle.
- Known issues to handle:
  - Numbers split across lines or by hyphenation.
  - Unicode minus ("−") vs hyphen ("-").
  - Percent signs and thousands separators separated from the digits.
  - Two-column layouts [check AJPS format].

### 3.3 Candidate items: what needs replication and what does not

3.3.1 NEEDS replication
- In-text numbers produced by the authors' own analysis, e.g.
  coefficients, standard errors, p-values, confidence intervals, sample sizes
  that come from data processing, percentages, model fit statistics.
  Example from the test paper: "first two components captured 93.2% of cumulative
  variance", "silhouette ~ 0.687", "99.1% alignment, 80.7% intensity".
- Derived numbers computed from results.
  Example: "50.1 percentage point gap" (72.4 - 22.3).
- Tables of results (e.g. Table 1, Table 2 in the test paper).
- Figures that display results (e.g. Figures 1-4 in the test paper).
- Numbers in supplementary tables that the main text relies on
  (e.g. Table S2, S4). [Does the verifier need these too? ]

3.3.2 Does NOT need replication
- Citation years and reference-list numbers (e.g. "Levitsky and Ziblatt 2018").
- Dates of events or speeches, access dates.
- Page numbers, volume/issue numbers, DOIs, affiliation superscripts, section numbers.
- Numbers inside direct quotations.
- Study design and specification choices (inputs, not results):
  scale definitions (0-10), fixed thresholds set by the authors (e.g. "risk threshold = 6"),
  number of indicators (11), number of models used (3), model names/versions,
  inclusion/exclusion criteria.
- Descriptive text numbers that are not outputs of analysis
  (e.g. "approximately one year in term"). [Confirm treatment: ]

3.3.3 Unclear / needs a human decision (leave blank, resolve with mentor)
- Parameters chosen by a procedure rather than by hand (e.g. k = 2 chosen by
  silhouette score). [Replicate? ]
- Design numbers that appear in both the Methods section and the Results section. [ ]
- Numbers reported in the Abstract only. [ ]
- Figures that are conceptual diagrams vs. results plots. [ ]
- Numbers from external data not in the replication package. [ ]
- [Other: ]

### 3.4 Classify candidates (including confidence)

Two-stage classification.

Stage 1: rules (cheap, deterministic)
- Regex to find numbers: integers, decimals, percentages, negatives, ranges,
  scientific notation, p-values ("p < .05"), and confidence intervals
  (e.g. "95% CI [0.12, 0.34]", "(0.12, 0.34)").
- Exclude obvious non-replicable patterns: years in parentheses next to author names,
  date formats, DOI/URL strings, page markers, text inside the reference list
  (everything after the "References" heading).
- Find "Table N" / "Figure N" captions and the region they cover.

Stage 2: LLM classification
- Input per candidate: the number, its sentence, the section heading, and page number.
- Output (structured JSON):
  {
    "text": "...",
    "type": "in_text_number | table | figure | ci | other",
    "needs_replication": true | false | "unclear",
    "category": "result | derived | design | citation | date | other",
    "reason": "...",
    "confidence": 0.0-1.0
  }
- Confidence handling:
  - confidence >= [threshold]: auto-label.
  - confidence < [threshold] or "unclear": mark for human review (separate color or
    flag in the checklist).
- Prompt includes the 3.3 definitions and a few labelled examples from the training set.
- Settings: temperature [ ], max tokens [ ], retry policy [ ].
- Evaluate recall first: missing a replicable item is worse than flagging an extra one.

Confidence intervals specifically
- Treat each interval as one unit (both endpoints plus the level), not as separate numbers.
- Link to the estimate it belongs to if it appears in the same sentence or table row.
- [Handling of intervals shown only in figures (error bars): ]

### 3.5 Highlight in the PDF
- For each item with needs_replication = true:
  - Find its rectangle(s) from step 3.2 (`fitz.Rect` for each word; merge words in the
    same line with `fitz.Rect.include_rect`).
  - `page.add_highlight_annot(rect)` and set the color to yellow, e.g.
    `annot.set_colors(stroke=(1, 1, 0))`, then `annot.update()`.
- Tables and figures: highlight the whole caption and draw a yellow rectangle
  (`page.add_rect_annot`) around the object.
- Uncertain items: [different color? e.g. orange; or no highlight, checklist only]
- Add a short annotation note with item ID so it matches the checklist.
- Save: `doc.save("manuscript_highlighted.pdf")`.
- Check: re-open the output and confirm the number of highlights equals the number of
  checklist rows marked "replicate".

### 3.6 Output checklist
Columns:
| id | page | type | text | sentence/context | needs_replication | category | reason | confidence | reviewer_decision | notes |

- `reviewer_decision` and `notes` are left blank for the human verifier.
- Formats: `checklist.csv` (for spreadsheets) and `checklist.json` (for later steps).
- Also write a short summary: counts by type, number of low-confidence items,
  pages with no extractable text.
- Log: model name/version, prompt version, run date. [Store where? ]

---

## 4. Training / development set and test results

Note: the LLM is used with prompting and examples, not trained from scratch. "Training set"
here means the labelled examples used to write prompts and tune rules.

### 4.1 Data
- Development paper: Delgado-Mohatar & Alelú-Paz (2026), 21 pages (manually labelled).
- [Additional AJPS manuscripts with real verifier annotations: ]
- [Number of papers / items: ]
- Split: [development / held-out test]

### 4.2 Annotation guideline
- Label each number/table/figure as: replicate / not replicate / unclear.
- Record category and a one-line reason.
- Two annotators if possible; report agreement. [ ]

### 4.3 Metrics
- Recall of replicable items (primary).
- Precision of highlighted items.
- Highlights per page (to catch over-flagging).
- Table/figure detection rate.
- Agreement between LLM runs (stability).

### 4.4 Results
| Version | Recall | Precision | Notes |
|---|---|---|---|
| Rules only | [ ] | [ ] | |
| Rules + LLM | [ ] | [ ] | |
| [Other] | [ ] | [ ] | |

Error analysis: [common misses and false positives ]

### 4.5 Known hard cases seen in the development paper
- The same concept reported with different calculations in different places
  (the paper's Table 1 vs Table 2 alignment values), so the agent should record which
  computation a number refers to. [ ]
- A text claim that cannot be matched to a figure or table (e.g. "9 of 11 indicators")
  should be flagged for the verifier. [ ]
- Mismatch between a caption and the figure content. [ ]

---

## 5. Next steps

1. Link flagged items to the replication package
   - Parse the package (scripts, README, data folder).
   - For each flagged item, search the code for the output that produces it
     (e.g. `ggsave`, `write.csv`, `stargazer`, `cat()`/`print` of the same number,
     table export calls).
   - Output a mapping: manuscript item -> script file / line -> data file.
   - [Languages to support: R / Stata / Python ]
2. Automated check (optional, later)
   - Run the code in a sandbox, compare regenerated numbers with the manuscript.
   - [Tolerance rules for rounding: ]
3. Detect inconsistencies inside the manuscript (text vs table vs figure).
4. Support more formats: scanned PDFs (OCR), Word, LaTeX source.
5. Handle supplementary materials and appendices.
6. Reviewer interface: accept/reject each highlight, save feedback to improve prompts.
7. Fine-tune or select a model once enough labelled data exists. [ ]
8. Privacy/compliance review before any use on real submissions. [ ]

---

## Open questions for mentor
1. Exact AJPS definition of "needs replication" (in-text numbers, appendix, SI?).
2. Is the use of external LLM APIs allowed for unpublished manuscripts?
3. Can we get 1-2 manuscripts already annotated by verifiers?
4. Preferred highlight convention (yellow only, or extra colors for uncertain items)?
5. Preferred checklist format for the verification team?
