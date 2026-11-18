# CCSS Replication Highlighter Agent

An AI agent that reads a manuscript submitted to the American Journal of Political
Science (AJPS) and highlights, in yellow, the in-text numbers, figures, and tables
that a verifier would need to reproduce from the replication package.

Status: prototype / in development
Development test paper: Delgado-Mohatar & Alelú-Paz (2026), "When Algorithms Guard
Democracy", Social Sciences 15, 372 (21 pages).

Sources for the review rules below: the "Manuscript Review Guide" slides and the
"Finding in-text numbers efficiently" notes (both in this project's materials).

---

## 1. Overview

### Purpose
AJPS verification requires that results in the manuscript can be reproduced from the
authors' replication package. [Confirm exact AJPS policy wording with mentor.]
Finding every reportable result by hand is slow and error-prone. This agent produces a
first-pass highlighted PDF and a checklist so a human verifier can start from there.

The review guide says all in-text numbers, tables, figures, model assumptions, and
datasets should be highlighted before checking them, and that this pass must be done
even when the author supplies a highlighted manuscript, because authors may not have
highlighted everything.

### What it does
- Reads a manuscript PDF.
- Finds candidate items: in-text numbers, tables, figures.
- Classifies each as "needs replication" or "does not need replication".
- Highlights items that need replication in yellow.
- Outputs a checklist (CSV/JSON) with page, text, category, reason, confidence.

### What it does not do
- It does not decide that a result is correct. It only flags what to check.
- It does not replace the human verifier (human review is always the last step).
- In this version it does not compare numbers with the code output
  (see Section 5, the green/red verification stage).

### Errors the downstream verification looks for (context for the agent)
- Rounding errors (e.g. 0.366 reported as 0.36; 93.29 reported as 94).
- Inconsistent numbers (text/table value differs from the code output).
- Missing in-text numbers, tables, or figures (reported but not produced by the code).
- Incorrect p-value markings (significance stars that do not match the p-value).
- Typographical errors in the text.
The agent's highlights are the input to this check, so missing a number is worse than
highlighting one too many.

### Tools and APIs
| Component | Choice |
|---|---|
| Language | Python 3.x |
| PDF parsing and highlighting | PyMuPDF (`fitz`) |
| Table detection (optional) | PyMuPDF `find_tables`; pdfplumber as fallback |
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
- Check each page has extractable text. If a page has none (scanned), flag it in the
  checklist summary; no OCR in version 1 (OCR, e.g. Tesseract, is listed in Section 5).
- Keep the original PDF untouched; write highlights to a copy.

### 3.2 Extract text and coordinates
- `page.get_text("words")` returns every word with its bounding box
  (x0, y0, x1, y1, word, block, line, word_no).
- `page.get_text("dict")` for line/block structure and font size
  (used to detect captions and section headings, and footnotes).
- `page.get_images()` and `page.get_drawings()` to locate figures (bounding boxes).
- Store everything as a per-page list so later steps can map a matched string back
  to a rectangle.
- Record the section of each word (Abstract, Introduction, Methods, Results, ...),
  because most in-text numbers are in Results and numbers in the Abstract or
  Introduction are usually repeated there.
- Known issues to handle:
  - Numbers split across lines or by hyphenation.
  - Unicode minus ("−") vs hyphen ("-").
  - Percent signs and thousands separators separated from the digits.
  - Two-column layouts [check AJPS format].
  - Footnotes: they may contain computed values, so include them in the scan.

### 3.3 Candidate items: what needs replication and what does not

Overview: the agent first finds every candidate (3.3.1 and 3.3.2 define the types), then
decides which ones to highlight. Rule: when unsure, highlight and mark "unclear" with low
confidence (never silently skip). Status column: "v1" = already implemented in
`src/highlight_numbers.py` (rules only); "LLM" = needs the LLM stage (not built yet).

3.3.1 NEEDS replication

A. In-text numbers (types)

| # | Type | What it covers | Examples (from the training papers) | Detection | Status |
|---|---|---|---|---|---|
| 1 | Sample size / counts | N, n, number of respondents/observations/units, "X of Y" counts, counts with thousands separators | "366 of 2,283", "3,159", "# Respondents 4,002", "27 (40%)" | Regex finds each integer separately (with thousands separators, or written as "n=5" without spaces); "X of Y" is not yet grouped into one item and "n = 5" with spaces is not yet recognized; integers of 100 or more get higher confidence than small ones | v1 (partial) |
| 2 | Percentages and decimals | Percentages, proportions, means, coefficients, standard errors, correlations, effect sizes, reliability (alpha) | "85 per cent", "3.67%", "0.10", "(α = 0.65)", "0.3 to 0.7 standard deviation", "-0.026" | Regex: digits with "%", "percent", "per cent", "percentage points", or a decimal point; negative signs ("-", "−", "–") | v1 |
| 3 | p-values and significance levels | p-values, stars, "significant at the X% level", test statistics (t, F, chi-square, R-squared), confidence intervals | "p < .05", "significant at the 5 per cent level", "[0.078, 0.185]", "R2 0.06185" | Regex for p, t, F, R2 followed by a comparator and number; CI brackets; the phrase "significant at" [phrase rule not built yet] | v1 for numeric forms; LLM for the phrase forms |
| 4 | Ranges and intervals | A range of values or a CI written in text | "53-62", "0.3 to 0.7", "between 2 and 5 percentage points" | Regex for "a-b" and "a–b" ranges; "a to b" [not built yet] | v1 for hyphen/dash ranges |
| 5 | Derived and comparative numbers | Differences, gaps, change over time, growth, sums computed from results | "50.1 percentage point gap" (72.4 - 22.3), "by a further 13%" | Same as types 1-2; the verifier decides whether it is derived. Flag as derived when the sentence contains "gap", "difference", "increase", "decrease", "further" [rule not built yet] | v1 finds the number; derivation flag LLM |
| 6 | Results stated in words | Quantities or findings written without digits | "half", "one-third", "twice as", "three times more likely", "show significant negative effects", "no increased likelihood of ...", "not statistically distinguishable" | Rules catch "half / twice / doubled / tripled / N times" only; qualitative claims about significance or direction need the LLM | rules: partial v1; qualitative claims: LLM |

Notes on the types:
- Type 6 is the main gap of the rule version: in the training set the verifiers highlighted
  qualitative statements such as "show significant negative effects" and "not statistically
  distinguishable", which contain no digits. The rule version cannot find them.
- Type 6, small plain integers (under 100), and ambiguous four-digit numbers are always marked
  "unclear" with low confidence.
- Spelled-out numbers ("one-third", "ten percent", "seven respondents") [not built yet:
  number-word regex or LLM].
- Numbers grouped together (estimate, p-value, t-statistic, CI) are one item and normally
  appear together in the code output.
- Repeated numbers: a number in the Abstract or Introduction is usually repeated in Results;
  link them in the checklist (`also_on_pages`).
- Sample size: a count that comes from data processing (e.g. "366 of 2,283 respondents")
  needs replication; a count the authors chose in the design (e.g. "we invited 500 people")
  does not. [Rule to separate them: ]

B. Other items to highlight
- Tables of results (e.g. Table 1, Table 2 in the test paper): highlight the caption and box
  the table; check each number and the significance codes.
- Figures that display results (e.g. Figures 1-4 in the test paper): highlight the caption and
  box the figure.
- Numbers in the main text that come from a table in the Appendix/SI, together with that SI
  table (e.g. Table S2, S4 in the test paper): the SI table must also be produced by the
  code and match.
- Footnote values that are computed.
- Statements about the operationalization of variables and about tests of model
  assumptions (highlighted in a separate color, see 3.5). Example from the review
  guide: "We defined obese as BMI > 28". [Status: not built yet, LLM]
- References to datasets (search for "data" or "survey", usually in Methods)
  (separate color, see 3.5). [Status: not built yet]

3.3.2 Does NOT need replication

A. Numbers that look like results but are not (exclusion rules)

| # | Excluded item | Examples | How it is detected | Status |
|---|---|---|---|---|
| 1 | Reference list | numbers in the bibliography (volume, issue, pages, years) | Find the "References" / "Bibliography" heading; skip everything after it until an Appendix, Table, or Figure page | v1 |
| 2 | Calendar years and dates | "Levitsky and Ziblatt 2018", "(Maerz 2019)", "in 2019", "2015-2020", "25 March 2023" | Four-digit numbers are decided by context (see the rule table under this table): citation parentheses, "in/since/as of", ranges. Full date patterns [not built yet] | v1 (years); dates not built |
| 3 | Cross-references | "Table 3", "Figure 2", "Section 4", "Appendix B", "Equation (1)", "Model 2", "Column 3", "Panel A", "Footnote 5" | Skip a number that directly follows words like table, figure, fig., section, appendix, equation, model, column, panel, note, hypothesis, wave, study, experiment | v1 |
| 4 | Page numbers and running headers | page number at the page bottom, "p. 12", "pp. 123-145", "vol. 3, no. 2" | Skip plain integers in the top 6% or bottom 7% of the page; skip numbers after p., pp., page, vol., no., issue | v1 |
| 5 | Section / heading numbers | "3.1 Results", "2. Materials and Methods" | Skip a number at the start of a short line followed by a capitalized word | v1 |
| 6 | Enumerators and list markers | "1.)", "2.)", "(a)", "1." at the start of a list item | Skip integer markers like "1.)" or "2." at line start (value 20 or less) | v1 |
| 7 | Set notation and equation numbers | "{0, 1}", equation label "(3)" | Skip integers inside braces and bare "(n)" labels (limit: a count written as "(12)" is also skipped) | v1 |
| 8 | Footnote markers | superscript "1" after a word | Skipped when attached to a word (e.g. "level1."); a separate superscript token may still be highlighted | partial |
| 9 | DOIs, URLs, identifiers | "10.1017/...", "https://...", ORCID | Not matched by the number regex because they contain letters or slashes | v1 (by design of the regex) |
| 10 | Numbers inside direct quotations | numbers in a quoted speech or survey question | [Not built yet: detect quote marks / LLM] | not built |
| 11 | Table and figure internals | axis tick labels, numbers inside a table body | Skipped as separate items; the table or figure is highlighted as one object | v1 |

Four-digit numbers (1900-2099): year vs. sample size / other replicable number.
A number such as 2019 can be a calendar year or a real count, so the agent decides from
the context in the same sentence (function `classify_year_like` in `src/highlight_numbers.py`).

| Decision | Context cues |
|---|---|
| EXCLUDE as a year | Inside a citation parenthesis: "(Smith, 2019)", "(Smith et al., 2019; Jones, 2020)", or "Smith (2019)". Preceded by "in", "since", "until", "during", "before", "after", "from", "period", "year", "as of", "published in", "data from", "survey year". Part of a year range or list: "2015-2020", "between 2015 and 2020", "2015, 2016". Used as a modifier: "2020 census", "2019 survey". Calendar-year range with no sample-size or measurement cue nearby. |
| INCLUDE (needs replication) | Sample-size lead phrase before it: "n =", "N =", "sample of", "sample size of", "total of", "number of". A counted noun right after it: respondents, participants, observations, firms, households, patients, counties, and similar. A currency amount ("$2019 million") or a unit word after it (million, billion, tons, percent). |
| AMBIGUOUS -> highlighted as "unclear", notes = "manual review: year vs count" | An include cue and a year cue on the same number ("In 2019 firms increased hiring"). No cue either way, but a measurement word nearby or a very short sentence ("The effect size was 2019."). |

Conflict rule: a strong include cue beats a weak year cue (e.g. "As of 2019, 2019 patients ..."
-> the first 2019 is a year, the second is a count); a strong include cue against a strong year
cue is ambiguous.
Numbers inside a table of summary statistics are not decided one by one: the whole table is
highlighted as one object, so a count that looks like a year there is covered.
Result on the 3 training papers: the previous "keep every year" setting added 355 year-like
highlights with no match to a verifier highlight; the context rule removes 353 of them
(2 remain as ambiguous), no sample-size item of this type occurred in these papers, and recall is
unchanged. [Not yet tested on sentences with sample-size years such as "a sample of 2019 firms";
a small test file is planned: ]

B. Study design and other non-computed numbers
- Study design and specification choices (inputs, not results): scale definitions (0-10),
  fixed thresholds set by the authors (e.g. "risk threshold = 6"), number of indicators (11),
  number of models used (3), model names/versions, inclusion/exclusion criteria,
  "We gave 20% of participants a follow-up survey". [Status: LLM; rules cannot tell design
  numbers from result numbers]
- Analytical results taken from cited previous work (not computed by the authors). [LLM]
- Tables and figures that only describe methodology or other non-computational
  information; it is acceptable if the code produces no output for them. [LLM]
- Descriptive numbers that are not outputs of analysis
  (e.g. "approximately one year in term"). If the agent is not sure whether a number
  is computed or descriptive, it still highlights it and marks it low confidence
  (rule from the review guide: if unsure, highlight and check).

3.3.3 Unclear / needs a human decision
Default rule: when unsure, highlight and mark "unclear" with low confidence; never
silently skip.
- Parameters chosen by a procedure rather than by hand (e.g. k = 2 chosen by
  silhouette score): treat as computed, so replicate. [Confirm with mentor: ]
- Design numbers that appear in both the Methods section and the Results section:
  treat the design statement as not replicated; if the Results repeat it as a finding,
  replicate. [Confirm: ]
- Numbers reported in the Abstract: authors usually repeat them in Results. Highlight
  them and link each to its Results occurrence in the checklist (same value, same
  context). [If no Results occurrence is found, flag: ]
- Figures that are conceptual diagrams vs. results plots: mark as unclear when the
  caption does not say. [ ]
- Numbers from external data not in the replication package. [ ]
- Year-like numbers where the context cues in 3.3.2 disagree or are missing: highlighted as
  "unclear" and flagged for manual review. [Further cues to add: ]
- Spelled-out numbers and fractions ("one-third", "seven percent"). [ ]
- Summarized p-values in text (e.g. "p<0.05") that go with a figure showing an
  estimate and its confidence interval: valid if the two-sided 95% CI excludes 0,
  even if the code never prints the p-value. If unsure, flag for the author.
- Other: typographical errors in the text are flagged in the checklist as category
  "typo" (not highlighted yellow). [Other: ]

### 3.4 Classify candidates (including confidence)

Two-stage classification.

Stage 1: rules (cheap, deterministic)
- Regex to find numbers: integers, decimals, percentages, negatives, ranges,
  scientific notation, p-values ("p < .05"), and confidence intervals
  (e.g. "95% CI [0.12, 0.34]", "(0.12, 0.34)").
- Also find number words and ratio phrases: "half of", "twice", "three times",
  "doubled", "x% more likely".
- Exclude obvious non-replicable patterns (see 3.3.2): cross-references, page numbers,
  section numbers, enumerators, date formats (four-digit numbers are decided by context, see 3.3.2), DOI/URL strings, page markers, text inside the reference list
  (everything after the "References" heading).
- Find "Table N" / "Figure N" captions and the region they cover.
- Find sentences with "data", "survey", "defined as", "coded as", "assumption"
  to mark datasets, operationalization, and model assumptions.
- Link repeated numbers: if a number in the Abstract/Introduction appears again in
  Results, group them as one item with several locations.
- Find numbers that appear in the vicinity of a table (they may be values from that
  table even if not stated).

Stage 2: LLM classification
- Input per candidate: the number, its sentence, the section heading, and page number.
- Output (structured JSON):
  {
    "text": "...",
    "type": "in_text_number | table | figure | ci | operationalization | dataset | other",
    "needs_replication": true | false | "unclear",
    "category": "result | derived | design | citation | date | cited_prior_work | other",
    "reason": "...",
    "confidence": 0.0-1.0
  }
- Confidence handling:
  - confidence >= [threshold]: auto-label.
  - confidence < [threshold] or "unclear": still highlighted in yellow, but flagged
    for human review in the checklist.
- Prompt includes the 3.3 definitions and a few labelled examples from the training set.
- Settings: temperature [ ], max tokens [ ], retry policy [ ].
- Evaluate recall first: missing a replicable item is worse than flagging an extra one.

Confidence intervals specifically
- Treat each interval as one unit (both endpoints plus the level), not as separate numbers.
- Link to the estimate it belongs to if it appears in the same sentence or table row.
- Values reported together (estimate, p-value, Cohen's d, t-statistic, CI) are grouped
  as one item, because they are normally printed together in the code output.
- Intervals shown only in figures (error bars): highlight the figure and, in the
  checklist, note that a "p<0.05" statement near it can be checked by whether the
  two-sided 95% CI excludes 0.

### 3.5 Highlight in the PDF
Color scheme:
- Yellow: in-text numerical results, tables, and figures to be reproduced
  (this agent's main output).
- Green / red: used later by the human verifier (green = matches the code output,
  red = does not match or not found). The agent does not use these colors.

For each item with needs_replication = true (or "unclear"):
  - Find its rectangle(s) from step 3.2 (`fitz.Rect` for each word; merge words in the
    same line with `fitz.Rect.include_rect`).
  - `page.add_highlight_annot(rect)` and set the color to yellow, e.g.
    `annot.set_colors(stroke=(1, 1, 0))`, then `annot.update()`.
- Tables and figures: highlight the whole caption and draw a yellow rectangle
  (`page.add_rect_annot`) around the object.
- Uncertain items: highlighted yellow too, flagged in the checklist as low confidence
  (the review guide says to highlight when unsure).
- Add a short annotation note with item ID so it matches the checklist.
- Save: `doc.save("manuscript_highlighted.pdf")`.
- Check: re-open the output and confirm the number of highlights equals the number of
  checklist rows marked "replicate" or "unclear".

### 3.6 Output checklist
Columns:
| id | page | section | type | text | sentence/context | needs_replication | category | reason | confidence | linked_locations | reviewer_decision | notes |

- `linked_locations` lists other places where the same number appears
  (e.g. Abstract and Results).
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
- Follow the rules in 3.3 (including: results stated in words count; design numbers
  and numbers from cited work do not).
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
  (the paper's Table 1 vs Table 2 alignment values): the agent records which table or
  computation a number belongs to, and flags the item as "inconsistent definitions"
  for the verifier.
- A text claim that cannot be matched to a figure or table (e.g. "9 of 11 indicators";
  not yet confirmed by me against Figure 4): flag it for the verifier as "cannot be
  traced to a table/figure".
- Mismatch between a caption and the figure content (e.g. "five analysed profiles" with
  seven columns in Figure 4): flag as a possible typographical or consistency error.

---

## 5. Next steps

1. Link flagged items to the replication package
   - Parse the package (scripts, README, data folder, .txt log file, saved LaTeX tables).
   - For each flagged item, search the code and log for the output that produces it
     (e.g. `ggsave`, `write.csv`, `stargazer`, `cat()`/`print` of the same number,
     table export calls).
   - Search strategy (from the review notes):
     - Search the log for the most unique value first (a coefficient such as
       "-18.529" is less repeated than its p-value "0.03").
     - Match with tolerance for rounding: 13.45 may appear as 13.448.
     - Match values close to zero in both forms ("-0.1" and "-.1").
     - A percent such as "18.52%" may appear as 0.1852 (or equivalent).
     - A number sitting between Table 3 and Table 4 in the text may appear in the
       code output between the places where those two tables are produced.
     - Numbers with significance symbols in the output are likelier to be the ones
       discussed in the paper.
     - Check that the matching number comes from the analysis the text describes
       (read nearby code comments and neighboring variables); a match elsewhere in
       the log can be a coincidence.
     - Code comments can show where an in-text number is produced; look there first.
   - Tables: regression output usually has more variables than the manuscript reports;
     use row/column names to find the right ones (names rarely match exactly); each
     column of a multi-column table usually has its own sequential section in the log.
     Check significance codes too.
   - Output a mapping: manuscript item -> script file / line -> data file.
   - [Languages to support: R / Stata / Python; confirm which languages AJPS packages use]
2. Automated check (optional, later)
   - Run the code in a sandbox, compare regenerated numbers with the manuscript.
   - Mark matches green, mismatches red (with the value produced by the code in a
     comment), and not-found items red with a note to the author, following the
     review guide.
   - Rounding tolerance: accept a difference only if rounding the code value to the
     manuscript's number of decimals gives the manuscript value; flag everything
     else (e.g. 0.366 reported as 0.36). [Exact tolerance rule: ]
   - Check significance markings against p-values.
   - For figures, check that they are visually identical to the code output
     (this needs image comparison; a human check first).
3. Detect inconsistencies inside the manuscript (text vs table vs figure).
4. Support more formats: scanned PDFs (OCR), Word, LaTeX source.
5. Handle supplementary materials and appendices.
6. Reviewer interface: accept/reject each highlight, save feedback to improve prompts.
7. Fine-tune or select a model once enough labelled data exists. [ ]
8. Privacy/compliance review before any use on real submissions. [ ]
9. Datasets and citations check: confirm each dataset reference has a matching
   citation in the reference list and in the replication package README.

