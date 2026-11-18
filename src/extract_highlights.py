"""Extract all highlight-type annotations from annotated training PDFs.

Every highlight is treated as "yellow" (= needs replication), whatever its original
color. The original color is kept in a separate column.

Usage:
    .venv/bin/python -I src/extract_highlights.py <pdf_dir> <out_dir>
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

HIGHLIGHT_TYPES = {
    fitz.PDF_ANNOT_HIGHLIGHT: "highlight",
    fitz.PDF_ANNOT_UNDERLINE: "underline",
    fitz.PDF_ANNOT_SQUIGGLY: "squiggly",
    fitz.PDF_ANNOT_STRIKE_OUT: "strikeout",
}
OTHER_TYPES = {
    fitz.PDF_ANNOT_SQUARE: "square",
    fitz.PDF_ANNOT_CIRCLE: "circle",
    fitz.PDF_ANNOT_FREE_TEXT: "free_text",
    fitz.PDF_ANNOT_TEXT: "note",
    fitz.PDF_ANNOT_INK: "ink",
    fitz.PDF_ANNOT_STAMP: "stamp",
    fitz.PDF_ANNOT_LINE: "line",
    fitz.PDF_ANNOT_POPUP: "popup",
}


def hex_color(rgb):
    if not rgb:
        return ""
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def color_name(rgb):
    """Rough color family, only for summary statistics."""
    if not rgb:
        return "none"
    r, g, b = rgb
    if len(rgb) == 1:
        return "gray"
    if r > 0.8 and g > 0.8 and b < 0.5:
        return "yellow"
    if g > 0.6 and r < 0.6 and b < 0.6:
        return "green"
    if r > 0.8 and g < 0.5 and b < 0.5:
        return "red"
    if r > 0.8 and g < 0.7 and b > 0.6:
        return "pink"
    if b > 0.7 and r < 0.6:
        return "blue"
    if r > 0.8 and 0.4 < g < 0.8 and b < 0.4:
        return "orange"
    return "other"


def quads_of(annot):
    verts = annot.vertices or []
    quads = []
    for i in range(0, len(verts) - 3, 4):
        quads.append(fitz.Quad(verts[i:i + 4]).rect)
    return quads or [annot.rect]


def text_under(words, rect):
    """Words whose centre lies inside rect, in reading order."""
    hit = []
    for x0, y0, x1, y1, w, b, l, n in words:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
            hit.append((b, l, n, w))
    hit.sort()
    return " ".join(h[3] for h in hit)


DIGIT = re.compile(r"\d")
NUMBERLIKE = re.compile(r"[-−–]?\d[\d,]*\.?\d*%?|[-−]?\.\d+")
CAPTION = re.compile(r"^\s*(table|figure|fig\.)\s*[A-Z]?\d+", re.I)


def classify_shape(text):
    if CAPTION.match(text):
        return "caption"
    if NUMBERLIKE.search(text):
        return "has_number"
    return "text_only"


def main(pdf_dir, out_dir):
    pdf_dir, out_dir = Path(pdf_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    marks = []
    survey = {}
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        doc = fitz.open(pdf)
        types, colors = Counter(), Counter()
        for page in doc:
            words = page.get_text("words")
            for annot in page.annots() or []:
                t = annot.type[0]
                name = HIGHLIGHT_TYPES.get(t) or OTHER_TYPES.get(t) or f"type_{t}"
                types[name] += 1
                if t == fitz.PDF_ANNOT_INK:
                    stroke = annot.colors.get("stroke")
                    caps = [re.sub(r"\s+", " ", l.strip())[:90]
                            for l in page.get_text().split("\n")
                            if CAPTION.match(l)]
                    marks.append({
                        "paper": pdf.stem,
                        "page": page.number + 1,
                        "original_color": hex_color(stroke),
                        "color_family": color_name(stroke),
                        "treated_as": "yellow",
                        "bbox": " ".join(str(round(v, 1)) for v in annot.rect),
                        "captions_on_page": " || ".join(caps),
                        "comment": (annot.info.get("content") or "").strip(),
                    })
                    continue
                if t not in HIGHLIGHT_TYPES:
                    continue
                stroke = annot.colors.get("stroke")
                colors[color_name(stroke)] += 1
                quads = quads_of(annot)
                text = " ".join(text_under(words, q) for q in quads).strip()
                text = re.sub(r"\s+", " ", text)
                bbox = fitz.Rect(quads[0])
                for q in quads[1:]:
                    bbox |= q
                rows.append({
                    "paper": pdf.stem,
                    "page": page.number + 1,
                    "annot_type": name,
                    "original_color": hex_color(stroke),
                    "color_family": color_name(stroke),
                    "treated_as": "yellow",
                    "text": text,
                    "shape": classify_shape(text),
                    "bbox": [round(v, 1) for v in bbox],
                    "n_quads": len(quads),
                    "comment": (annot.info.get("content") or "").strip(),
                })
        survey[pdf.stem] = {
            "pages": doc.page_count,
            "annotation_types": dict(types),
            "highlight_color_families": dict(colors),
        }
        doc.close()

    with open(out_dir / "ground_truth_highlights.json", "w") as f:
        json.dump(rows, f, indent=1, ensure_ascii=False)
    with open(out_dir / "ground_truth_highlights.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["bbox"] = " ".join(map(str, r["bbox"]))
            w.writerow(r)
    with open(out_dir / "ground_truth_table_figure_marks.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(marks[0].keys()))
        w.writeheader()
        w.writerows(marks)
    print("ink marks (tables/figures):", len(marks))
    with open(out_dir / "survey.json", "w") as f:
        json.dump(survey, f, indent=1)

    print(json.dumps(survey, indent=1))
    by_paper = Counter((r["paper"], r["shape"]) for r in rows)
    print("\nhighlights per paper and shape:")
    for k, v in sorted(by_paper.items()):
        print(" ", k, v)
    print("\ntotal highlights:", len(rows))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
