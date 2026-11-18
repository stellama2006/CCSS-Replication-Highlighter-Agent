"""Rule-based first version of the replication highlighter (no LLM, no network).

Finds candidate in-text numbers, table captions/regions and figure captions/regions in
a manuscript PDF, highlights them in yellow, and writes a checklist (CSV + JSON).

Design choice: recall first. Anything that might need replication is highlighted;
uncertain items (e.g. small plain integers) are still highlighted but marked
needs_replication = "unclear" with low confidence in the checklist.

Usage:
    .venv/bin/python -I src/highlight_numbers.py <pdf or folder> <out_dir> [--strip-existing]

--strip-existing removes all annotations (in memory) before highlighting. Use it only
for the annotated training PDFs. The input file is never modified.
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

YELLOW = (1, 1, 0)

# ---------------------------------------------------------------- number parsing
LEAD = "()[]{}\"'“”‘’,;:*†‡§"
TRAIL = LEAD + "."
_NUM = (r"(?P<sign>[−–+-]?)\$?(?P<int>\d{1,3}(?:,\d{3})+|\d+)?"
        r"(?P<dec>\.\d+)?(?P<pct>%?)")
BARE = re.compile(r"^(?P<cmp>[<>≤≥=~±]?)" + _NUM + "$")
STAT = re.compile(r"^(?P<pre>[pPnNtFrR]|R2|df|SD|SE)(?P<cmp>[<>≤≥=])" + _NUM + "$")
RANGE = re.compile(r"^\$?\d[\d,]*(?:\.\d+)?%?[–−-]\$?\d[\d,]*(?:\.\d+)?%?$")
YEAR = re.compile(r"^(?:19|20)\d{2}$")

XREF_PREV = {
    "table", "tables", "figure", "figures", "fig", "fig.", "figs.", "section",
    "sec.", "appendix", "equation", "eq.", "eqs.", "chapter", "panel", "footnote",
    "note", "notes", "column", "columns", "model", "models", "hypothesis",
    "hypotheses", "wave", "study", "experiment",
}
PAGE_PREV = {"p.", "pp.", "page", "pages", "vol.", "no.", "issue"}
CAP_RE = re.compile(r"^(Table|Figure|Fig\.)\s*[A-Z]?\d+\s*(?:[:.\-–—]|$)", re.I)
RATIO_RE = re.compile(
    r"\b(?:half|twice|doubled?|tripled?|(?:two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+times)\b", re.I)
REF_HEADINGS = {"references", "bibliography", "works cited", "literature cited",
                "reference list"}
SECTION_NAMES = {"abstract", "introduction", "background", "literature review",
                 "theory", "hypotheses", "data", "methods", "method", "research design",
                 "data and methods", "empirical strategy", "results", "findings",
                 "analysis", "discussion", "conclusion", "conclusions", "appendix",
                 "supplementary materials"}
NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+")


def parse_token(raw):
    """Return (kind, key) if raw looks like a number, else None."""
    core = raw.lstrip(LEAD).rstrip(TRAIL)
    if not core or not any(c.isdigit() for c in core):
        return None
    if RANGE.match(core):
        parts = re.split(r"[–−-]", core.replace("$", ""), maxsplit=1)
        if all(YEAR.match(x) for x in parts):
            return "year_range", core
        return "range", core
    m = STAT.match(core)
    if m:
        return "pvalue" if m.group("pre") in ("p", "P") else "stat", core
    m = BARE.match(core)
    if not m or not (m.group("int") or m.group("dec")):
        return None
    if (m.group("int") and YEAR.match(m.group("int")) and not m.group("dec")
            and not m.group("pct") and not m.group("cmp") and not m.group("sign")):
        return "year", core  # kept as a candidate (recall first); low confidence later
    if m.group("pct"):
        return "percent", core
    if m.group("dec"):
        return "decimal", core
    return "integer", core


def int_value(core):
    digits = re.sub(r"[^\d]", "", core)
    return int(digits) if digits else 0


# ------------------------------------------------- four-digit number disambiguation
ABBREV = {"al.", "e.g.", "i.e.", "vs.", "no.", "fig.", "eq.", "cf.", "approx.", "etc.",
          "dr.", "mr.", "ms.", "inc.", "p.", "pp.", "sec.", "vol."}
SAMPLE_LEAD = re.compile(
    r"(?:\bn\s*=|\bsample(?:\s+size)?(?:\s+of|\s+was|\s+is)?|\btotal(?:\s+of)?|"
    r"\bnumber\s+of|\bcount\s+of|\bsize\s+of)\s*$", re.I)
UNITS = {"respondents", "participants", "observations", "firms", "households",
         "patients", "counties", "individuals", "schools", "students", "voters",
         "workers", "countries", "states", "districts", "cases", "subjects", "units",
         "tracts", "employees", "companies", "children", "women", "men", "people",
         "adults", "residents", "citizens", "villages", "cities", "municipalities",
         "legislators", "candidates", "speeches", "articles", "papers", "documents",
         "tweets", "posts", "records", "samples", "clusters", "groups", "teams"}
UNIT_WORDS = {"million", "billion", "thousand", "trillion", "tons", "tonnes", "kg",
              "percent", "%", "dollars", "euros", "usd", "miles", "hectares", "acres"}
YEAR_PREV = {"in", "since", "until", "through", "during", "before", "after", "year",
             "years", "period", "fy", "circa", "between", "from"}
YEAR_PREV2 = {("as", "of"), ("published", "in"), ("data", "from"), ("survey", "year"),
              ("fiscal", "year"), ("calendar", "year")}
YEAR_MODIFIER_NEXT = {"census", "election", "elections", "survey", "wave", "budget",
                      "fiscal", "baseline", "cohort", "edition", "version", "data",
                      "dataset", "study", "round"}
MEASURE = {"size", "effect", "coefficient", "estimate", "estimated", "mean", "median",
           "average", "total", "number", "count", "rate", "ratio", "sum", "score",
           "index", "value", "revenue", "income", "sales", "price", "population",
           "variance", "deviation"}


def _norm(t):
    return t.lower().strip(LEAD + ".?!")


def _yearlike(t):
    return bool(YEAR.match(t.lstrip(LEAD + "$").rstrip(TRAIL)))


def classify_year_like(tokens, i):
    """Decide whether the year-like token tokens[i] is a calendar year (exclude), a
    sample size / count / amount (include) or unclear. Returns (label, reason) with
    label in {"include", "exclude", "ambiguous"}.

    Conflict rule: a strong include cue and a strong exclude cue on the same number
    -> ambiguous. A strong include beats a weak exclude cue.
    """
    tok = tokens[i]
    core = tok.lstrip(LEAD).rstrip(TRAIL)
    # sentence window
    s = i
    while s > 0 and i - s < 25:
        p = tokens[s - 1]
        if p[-1:] in ".?!" and p.lower() not in ABBREV and not re.match(r"^[A-Z]\.$", p):
            break
        s -= 1
    e = i
    while e < len(tokens) - 1 and e - i < 25:
        if tokens[e][-1:] in ".?!" and tokens[e].lower() not in ABBREV:
            break
        e += 1
    p1 = _norm(tokens[i - 1]) if i - 1 >= s else ""
    p2 = _norm(tokens[i - 2]) if i - 2 >= s else ""
    n1 = _norm(tokens[i + 1]) if i + 1 <= e else ""
    sent = " ".join(tokens[s:e + 1])
    pos = len(" ".join(tokens[s:i])) + (1 if i > s else 0) + max(0, tok.find(core))

    strong_inc, strong_exc, weak_exc = [], [], []
    # --- include cues (sample size, amounts, counts with units)
    if re.search(r"[$€£]", tok[:2]) or p1 in {"$", "€", "£"}:
        strong_inc.append("currency amount")
    if n1 in UNIT_WORDS:
        strong_inc.append(f"unit word '{n1}' follows")
    if n1 in UNITS:
        strong_inc.append(f"counted noun '{n1}' follows")
    if SAMPLE_LEAD.search(" ".join(tokens[max(s, i - 4):i]).lower()):
        strong_inc.append("sample-size lead phrase before it")
    # --- exclude cues (calendar year)
    op = sent.rfind("(", 0, pos)
    if op != -1 and ")" not in sent[op:pos]:
        cl = sent.find(")", pos)
        seg = sent[op + 1: cl if cl != -1 else len(sent)]
        before = sent[:op].split()
        if re.search(r"\b[A-Z][a-z]{1,}", re.sub(r"\d", "", seg)):
            strong_exc.append("inside a citation parenthesis")
        elif re.fullmatch(r"\s*(?:19|20)\d{2}[a-z]?\s*", seg) and before and \
                (before[-1][:1].isupper() or before[-1].lower() == "al."):
            strong_exc.append("author-year citation 'Name (year)'")
    if RANGE.match(core) and all(YEAR.match(x) for x in re.split(r"[–−-]", core, maxsplit=1)):
        strong_exc.append("year range")
    if p1 in YEAR_PREV:
        strong_exc.append(f"preceded by '{p1}'")
    elif (p2, p1) in YEAR_PREV2:
        strong_exc.append(f"preceded by '{p2} {p1}'")
    if (p1 in {"and", "to"} and _yearlike(p2)) or \
            (i - 1 >= s and tokens[i - 1].endswith(",") and _yearlike(p1)):
        weak_exc.append("part of a list/range of years")
    if n1 in YEAR_MODIFIER_NEXT:
        weak_exc.append(f"year used as a modifier ('{core} {n1}')")

    if strong_inc and strong_exc:
        return "ambiguous", ("both readings plausible: include cues [" + "; ".join(strong_inc) +
                             "] vs year cues [" + "; ".join(strong_exc) + "]")
    if strong_inc:
        return "include", "; ".join(strong_inc)
    if strong_exc or weak_exc:
        return "exclude", "; ".join(strong_exc + weak_exc)
    ctx = [_norm(x) for k, x in enumerate(tokens[max(s, i - 6):min(e, i + 6) + 1])
           if max(s, i - 6) + k != i]
    if any(x in MEASURE for x in ctx) or (e - s + 1) <= 6:
        return "ambiguous", "calendar-year-like but no year cue and no sample-size marker " \
                            "(measurement word nearby or very short sentence)"
    return "exclude", "calendar-year range (1900-2099) with no sample-size/measurement context"


# ---------------------------------------------------------------- page analysis
def norm_heading(text):
    t = text.strip().lower().rstrip(".:")
    return NUMBERING.sub("", t) if NUMBERING.match(text.strip()) else t


def read_page(page):
    words = page.get_text("words")
    groups = defaultdict(list)
    for w in words:
        groups[(w[5], w[6])].append(w)
    lines = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda w: w[7])
        rect = pymupdf.Rect(ws[0][:4])
        for w in ws[1:]:
            rect |= pymupdf.Rect(w[:4])
        lines.append({"words": ws, "text": " ".join(w[4] for w in ws), "rect": rect})
    return lines


def find_reference_zone(pages):
    """Return (start_page, start_y, end_page) or None. end_page is exclusive."""
    start = None
    for pno, lines in enumerate(pages):
        if pno < 1:
            continue
        for ln in lines:
            if len(ln["words"]) <= 3 and norm_heading(ln["text"]) in REF_HEADINGS:
                start = (pno, ln["rect"].y0)
                break
        if start:
            break
    if not start:
        return None
    end = len(pages)
    for pno in range(start[0] + 1, len(pages)):
        for ln in pages[pno]:
            t = ln["text"].strip()
            if CAP_RE.match(t) or t.lower().startswith(("appendix", "supplementary")):
                end = pno
                break
        if end != len(pages):
            break
    return start[0], start[1], end


def in_ref_zone(zone, pno, y0):
    if not zone:
        return False
    sp, sy, ep = zone
    if pno == sp:
        return y0 >= sy
    return sp < pno < ep


def regions_for_page(page, has_table_cap, has_fig_cap):
    """Table / figure regions on a page that has a caption of that kind."""
    tables, figures = [], []
    if has_table_cap:
        try:
            tables = [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
        except Exception:
            tables = []
    if has_fig_cap:
        try:
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"])
                if r.width > 100 and r.height > 100:
                    figures.append(r)
        except Exception:
            pass
        try:
            for r in page.cluster_drawings():
                r = pymupdf.Rect(r)
                if r.width > 100 and r.height > 100:
                    figures.append(r)
        except Exception:
            pass
    return tables, figures


def inside(rect_list, w):
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rect_list)


def short(s, n=200):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- main routine
def process(pdf_path, out_dir, strip_existing):
    pdf_path, out_dir = Path(pdf_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    if strip_existing:
        for page in doc:
            for a in list(page.annots() or []):
                page.delete_annot(a)

    pages = [read_page(p) for p in doc]
    zone = find_reference_zone(pages)
    items = []
    no_text_pages = []
    section = "front_matter"

    for pno, (page, lines) in enumerate(zip(doc, pages)):
        if not lines:
            no_text_pages.append(pno + 1)
            continue
        H = page.rect.height
        caps = [(ln, CAP_RE.match(ln["text"].strip())) for ln in lines]
        caps = [(ln, m.group(1).lower()) for ln, m in caps if m]
        has_t = any(k == "table" for _, k in caps)
        has_f = any(k.startswith("fig") for _, k in caps)
        tables, figures = regions_for_page(page, has_t, has_f)
        skip_regions = tables + figures
        prev_word = ""
        flat = [w[4] for ln in lines for w in ln["words"]]
        offs, c = [], 0
        for ln in lines:
            offs.append(c)
            c += len(ln["words"])

        for li, ln in enumerate(lines):
            ws = ln["words"]
            line_text = ln["text"]
            in_ref = in_ref_zone(zone, pno, ln["rect"].y0)
            heading = norm_heading(line_text)
            if len(ws) <= 4 and heading in SECTION_NAMES:
                section = heading
            if in_ref:
                prev_word = ws[-1][4].lower()
                continue

            # captions -> table / figure items
            cap_kind = next((k for c, k in caps if c is ln), None)
            if cap_kind:
                kind = "table" if cap_kind == "table" else "figure"
                items.append({"page": pno + 1, "type": f"{kind}_caption",
                              "rect": pymupdf.Rect(ln["rect"]), "text": short(line_text, 120),
                              "context": short(line_text), "section": section,
                              "category": "table_or_figure", "confidence": 0.9,
                              "needs": True, "reason": f"{kind} caption", "key": ""})
                prev_word = ws[-1][4].lower()
                continue

            # numbers
            cands = []
            year_info = {}
            for i, w in enumerate(ws):
                tok = parse_token(w[4])
                prev = (ws[i - 1][4] if i > 0 else prev_word).lower().strip(LEAD + "")
                if not tok:
                    continue
                kind, core = tok
                if prev in XREF_PREV or prev in PAGE_PREV:
                    continue
                if skip_regions and inside(skip_regions, w):
                    continue
                if kind == "integer" and (re.search(r"[{}]", w[4]) or
                                          re.match(r"^\(?\d{1,2}\.?\)$", w[4])):
                    continue  # set notation like {0, 1} or enumerators like "2.)"
                top, bottom = w[1] < 0.06 * H, w[3] > 0.93 * H
                if (top or bottom) and kind in ("integer", "year"):
                    continue
                if i == 0 and kind == "integer":
                    nxt = ws[1][4] if len(ws) > 1 else ""
                    if re.match(r"^\d+(\.\d+)*\.?$", w[4]) and nxt[:1].isupper() \
                            and len(ws) <= 10:
                        continue  # section heading number
                    if w[4].endswith((".", ")")) and int_value(core) <= 20:
                        continue  # list marker
                if kind in ("year", "year_range"):
                    label, why = classify_year_like(flat, offs[li] + i)
                    if label == "exclude":
                        continue
                    year_info[i] = (label, why)
                    kind = "year_count" if label == "include" else "year"
                cands.append((i, kind, core))

            # group adjacent candidates, extend with "per cent" etc.
            groups, cur = [], []
            for c in cands:
                if cur and c[0] == cur[-1][0] + 1:
                    cur.append(c)
                else:
                    if cur:
                        groups.append(cur)
                    cur = [c]
            if cur:
                groups.append(cur)
            for g in groups:
                first, last = g[0][0], g[-1][0]
                tail = [w[4].lower().strip(LEAD + ".") for w in ws[last + 1:last + 3]]
                end = last
                if tail[:1] in (["percent"], ["%"]):
                    end = last + 1
                elif tail[:2] == ["per", "cent"]:
                    end = last + 2
                elif tail[:2] == ["percentage", "points"]:
                    end = last + 2
                rect = pymupdf.Rect(ws[first][:4])
                for w in ws[first + 1:end + 1]:
                    rect |= pymupdf.Rect(w[:4])
                kinds = {k for _, k, _ in g}
                strong = kinds & {"percent", "decimal", "pvalue", "stat", "range"}
                if end != last:
                    strong = strong or {"percent"}
                why = "; ".join(year_info[k][1] for k, _, _ in g if k in year_info)
                if kinds == {"year"}:
                    conf, cat = 0.3, "unclear_year"
                elif kinds == {"year_count"}:
                    conf, cat = 0.6, "sample_size_or_count"
                elif strong:
                    conf, cat = 0.8, "result_candidate"
                elif any(int_value(c) >= 100 or "," in c for _, _, c in g):
                    conf, cat = 0.6, "result_candidate"
                else:
                    conf, cat = 0.3, "unclear_small_integer"
                items.append({"page": pno + 1, "type": "number", "rect": rect,
                              "text": " ".join(w[4] for w in ws[first:end + 1]),
                              "context": short(line_text), "section": section,
                              "category": cat, "confidence": conf,
                              "needs": True if conf >= 0.6 else "unclear",
                              "notes": ("manual review: year vs count" if "year" in kinds else ""),
                              "reason": (("AMBIGUOUS: " if kinds == {"year"} else "") + why
                                         if why else
                                         "numeric token (" + ",".join(sorted(kinds)) + ")"),
                              "key": g[0][2] if (kinds - {"integer", "year"}) or conf >= 0.6 else ""})

            # ratio phrases ("half", "three times", ...)
            offsets, pos = [], 0
            for w in ws:
                offsets.append((pos, pos + len(w[4])))
                pos += len(w[4]) + 1
            for m in RATIO_RE.finditer(line_text):
                hit = [w for w, (a, b) in zip(ws, offsets) if a < m.end() and b > m.start()]
                if not hit:
                    continue
                rect = pymupdf.Rect(hit[0][:4])
                for w in hit[1:]:
                    rect |= pymupdf.Rect(w[:4])
                items.append({"page": pno + 1, "type": "ratio_phrase", "rect": rect,
                              "text": m.group(0), "context": short(line_text),
                              "section": section, "category": "unclear_ratio_phrase",
                              "confidence": 0.3, "needs": "unclear",
                              "reason": "result stated in words", "key": ""})
            prev_word = ws[-1][4].lower()

        for r in tables:
            items.append({"page": pno + 1, "type": "table_region", "rect": r,
                          "text": "(table body)", "context": "", "section": section,
                          "category": "table_or_figure", "confidence": 0.7, "needs": True,
                          "reason": "table detected near a Table caption", "key": ""})
        for r in figures:
            items.append({"page": pno + 1, "type": "figure_region", "rect": r,
                          "text": "(figure)", "context": "", "section": section,
                          "category": "table_or_figure", "confidence": 0.7, "needs": True,
                          "reason": "figure region near a Figure caption", "key": ""})

    # number ids, repeated-number links
    items.sort(key=lambda it: (it["page"], it["rect"].y0, it["rect"].x0))
    where = defaultdict(set)
    for it in items:
        if it["key"]:
            where[it["key"]].add(it["page"])
    for n, it in enumerate(items, 1):
        it["id"] = f"N{n:04d}"
        it["also_on_pages"] = ",".join(str(p) for p in sorted(where.get(it["key"], set())
                                                           - {it["page"]})) if it["key"] else ""

    # draw
    for it in items:
        page = doc[it["page"] - 1]
        if it["type"] in ("table_region", "figure_region"):
            a = page.add_rect_annot(it["rect"])
            a.set_colors(stroke=YELLOW)
            a.set_border(width=2)
        else:
            a = page.add_highlight_annot(quads=it["rect"])
            a.set_colors(stroke=YELLOW)
        a.set_info(content=it["id"])
        a.update()
    out_pdf = out_dir / f"{pdf_path.stem}_auto_highlighted.pdf"
    doc.save(out_pdf, garbage=3, deflate=True)

    rows = []
    for it in items:
        rows.append({
            "id": it["id"], "page": it["page"], "section": it["section"],
            "type": it["type"], "text": it["text"], "context": it["context"],
            "needs_replication": it["needs"], "category": it["category"],
            "reason": it["reason"], "confidence": it["confidence"],
            "also_on_pages": it["also_on_pages"],
            "bbox": " ".join(f"{v:.1f}" for v in it["rect"]),
            "reviewer_decision": "", "notes": it.get("notes", ""),
        })
    with open(out_dir / f"{pdf_path.stem}_checklist.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    summary = {
        "file": pdf_path.name, "pages": doc.page_count, "items": len(rows),
        "by_type": {t: sum(1 for r in rows if r["type"] == t) for t in sorted({r["type"] for r in rows})},
        "unclear_items": sum(1 for r in rows if r["needs_replication"] == "unclear"),
        "pages_without_text": no_text_pages,
        "reference_zone": None if not zone else {"start_page": zone[0] + 1, "end_page_exclusive": zone[2] + 1},
    }
    with open(out_dir / f"{pdf_path.stem}_checklist.json", "w") as f:
        json.dump({"summary": summary, "items": rows}, f, indent=1, ensure_ascii=False)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("out_dir")
    ap.add_argument("--strip-existing", action="store_true")
    a = ap.parse_args()
    src = Path(a.input)
    pdfs = sorted(src.glob("*.pdf")) if src.is_dir() else [src]
    for p in pdfs:
        process(p, a.out_dir, a.strip_existing)


if __name__ == "__main__":
    main()
