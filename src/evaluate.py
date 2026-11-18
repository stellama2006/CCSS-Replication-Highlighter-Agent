"""Compare the auto-generated checklists with the ground truth extracted from the
verified training PDFs.

Usage:
    .venv/bin/python -I src/evaluate.py <checklist_dir> <ground_truth_dir>

Matching rule: a predicted item and a ground-truth highlight match when they are on the
same page and their boxes overlap by at least 50% of the smaller box.
Caption and table/figure marks are matched at page level (see notes in the output).

Caveat: the ground truth only contains what the verifier highlighted. A prediction with
no match is NOT necessarily wrong (the verifier may have skipped it), so precision is
a lower bound and recall is the more reliable number.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path


def box(s):
    return [float(v) for v in s.split()]


def overlap(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / area if area > 0 else 0.0


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main(chk_dir, gt_dir):
    chk_dir, gt_dir = Path(chk_dir), Path(gt_dir)
    gt = read(gt_dir / "ground_truth_highlights.csv")
    marks = read(gt_dir / "ground_truth_table_figure_marks.csv")
    papers = sorted({r["paper"] for r in gt})
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    tot = defaultdict(int)
    for p in papers:
        pred = read(chk_dir / f"{p}_checklist.csv")
        by_page = defaultdict(list)
        for r in pred:
            by_page[int(r["page"])].append(r)
        g_rows = [r for r in gt if r["paper"] == p]

        def matched(g, regions=False):
            gb = box(g["bbox"])
            return any(overlap(box(r["bbox"]), gb) >= 0.5
                       for r in by_page[int(g["page"])]
                       if r["type"] in ("number", "ratio_phrase") or
                       (g["shape"] == "caption" and r["type"].endswith("_caption")) or
                       (regions and r["type"].endswith("_region")))

        region_only = 0
        hit = {"has_number": 0, "caption": 0, "text_only": 0}
        cnt = {"has_number": 0, "caption": 0, "text_only": 0}
        missed = []
        for g in g_rows:
            cnt[g["shape"]] += 1
            if matched(g):
                hit[g["shape"]] += 1
            elif matched(g, regions=True):
                hit[g["shape"]] += 1
                region_only += 1
            else:
                missed.append(g)

        numbers = [r for r in pred if r["type"] == "number"]
        gt_by_page = defaultdict(list)
        for g in g_rows:
            gt_by_page[int(g["page"])].append(box(g["bbox"]))
        prec_hit = sum(1 for r in numbers if any(
            overlap(box(r["bbox"]), gb) >= 0.5 for gb in gt_by_page[int(r["page"])]))
        high = [r for r in numbers if float(r["confidence"]) >= 0.6]
        high_hit = sum(1 for r in high if any(
            overlap(box(r["bbox"]), gb) >= 0.5 for gb in gt_by_page[int(r["page"])]))

        m_rows = [m for m in marks if m["paper"] == p]
        m_cap = [m for m in m_rows if m["captions_on_page"]]
        m_cap_hit = sum(1 for m in m_cap if any(
            r["type"].endswith(("_caption", "_region")) for r in by_page[int(m["page"])]))
        m_pages = {m["page"] for m in m_cap}
        m_pages_hit = {pg for pg in m_pages if any(
            r["type"].endswith(("_caption", "_region")) for r in by_page[int(pg)])}

        say(f"=== {p} ({len({r['page'] for r in pred})} pages with predictions)")
        for k in ("has_number", "caption", "text_only"):
            if cnt[k]:
                say(f"  recall, GT highlights of type {k:10s}: {hit[k]}/{cnt[k]} "
                    f"= {hit[k] / cnt[k]:.0%}")
        n_all, h_all = sum(cnt.values()), sum(hit.values())
        say(f"  recall, all GT text highlights        : {h_all}/{n_all} = {h_all / n_all:.0%}"
            f"  (of these, {region_only} are covered only by a table/figure region)")
        say(f"  GT table/figure marks on caption pages: pages {len(m_pages_hit)}/{len(m_pages)} "
            f"with a predicted table/figure item; {len(m_rows) - len(m_cap)} marks on body pages not evaluated")
        say(f"  precision (lower bound), numbers      : {prec_hit}/{len(numbers)} = "
            f"{prec_hit / max(1, len(numbers)):.0%}")
        say(f"  precision, confidence >= 0.6 only     : {high_hit}/{len(high)} = "
            f"{high_hit / max(1, len(high)):.0%}")
        say(f"  predicted numbers per page            : {len(numbers) / max(1, len({r['page'] for r in pred})):.1f}")
        if missed:
            say(f"  still missed GT highlights ({len(missed)}; first 12):")
            for g in missed[:12]:
                say(f"    p{g['page']:>3} [{g['shape']}] {g['text'][:80]}")
        tot["region_only"] += region_only
        for k in cnt:
            tot[k + "_n"] += cnt[k]
            tot[k + "_hit"] += hit[k]
        tot["prec_n"] += len(numbers)
        tot["prec_hit"] += prec_hit
        say()

    n_all = sum(tot[k + "_n"] for k in ("has_number", "caption", "text_only"))
    h_all = sum(tot[k + "_hit"] for k in ("has_number", "caption", "text_only"))
    say("=== ALL THREE PAPERS")
    say(f"  recall, all GT text highlights: {h_all}/{n_all} = {h_all / n_all:.0%}"
        f" (strict number-level: {h_all - tot['region_only']}/{n_all} = "
        f"{(h_all - tot['region_only']) / n_all:.0%})")
    say(f"  precision (lower bound), numbers: {tot['prec_hit']}/{tot['prec_n']} = "
        f"{tot['prec_hit'] / max(1, tot['prec_n']):.0%}")
    (chk_dir / "evaluation.txt").write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
