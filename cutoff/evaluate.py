"""Score an extractor against hand labels. Writes the tables that go straight into the write-up.

labels.csv, one row per email (blank cell = the email does not state it):
  id, is_drive (Y/N), company, role, min_cgpa, min_percentage, branches (CSE;ISE / ALL / CS-ALLIED),
  batch_years (2026;2027), max_active_backlogs, deadline (YYYY-MM-DD[THH:MM]), eligible (Y/N/?), notes

Per field, every email lands in exactly one bucket:
  correct   matches the label (including "both say not stated")
  wrong     a different value         <- can wrongly rule you out
  invented  a value the email doesn't state  <- can wrongly rule you out
  missed    the email states it, the extractor returned nothing
  unknown   the extractor answered but grounding couldn't verify it (shown as CHECK, never as a no)
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import branches as br
from .grounding import norm, parse_date
from .rules import CHECK, ELIGIBLE, NOT_A_DRIVE, NOT_ELIGIBLE, Profile, decide_all
from .schema import FIELDS, Email, Extraction

BUCKETS = ["correct", "wrong", "invented", "missed", "unknown"]


def _split(cell: str) -> list[str]:
    return [p.strip() for p in cell.replace(",", ";").split(";") if p.strip()]


def parse_label(row: dict) -> dict:
    def num(k):
        v = (row.get(k) or "").strip()
        return float(v) if v else None

    return {
        "is_drive": (row.get("is_drive") or "Y").strip().upper() != "N",
        "company": (row.get("company") or "").strip() or None,
        "role": (row.get("role") or "").strip() or None,
        "min_cgpa": num("min_cgpa"),
        "min_percentage": num("min_percentage"),
        "branches": _split(row.get("branches") or "") or None,
        "batch_years": [int(y) for y in _split(row.get("batch_years") or "")] or None,
        "max_active_backlogs": int(num("max_active_backlogs")) if num("max_active_backlogs") is not None else None,
        "deadline": (row.get("deadline") or "").strip() or None,
        "eligible": (row.get("eligible") or "?").strip().upper()[:1] or "?",
    }


def load_labels(path: str | Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["id"].strip(): parse_label(r) for r in csv.DictReader(fh) if (r.get("id") or "").strip()}


def _clean_company(s: str) -> str:
    s = norm(s)
    for junk in (" private limited", " pvt ltd", " pvt. ltd.", " limited", " ltd", " inc", " india", ","):
        s = s.replace(junk, "")
    return s.strip(" .")


def same(name: str, truth, pred) -> bool:
    if name in ("company", "role"):
        a, b = _clean_company(truth), _clean_company(pred)
        return bool(a) and bool(b) and (a in b or b in a)
    if name in ("min_cgpa", "min_percentage", "max_active_backlogs"):
        return abs(float(truth) - float(pred)) < 0.01
    if name == "branches":
        return br.canonical_set(truth) == br.canonical_set(pred)
    if name == "batch_years":
        return set(truth) == set(pred)
    if name == "deadline":
        a, b = parse_date(truth), parse_date(pred)
        return bool(a and b) and a.date() == b.date()
    return truth == pred


def bucket(name: str, truth, x: Extraction) -> str:
    f = x.get(name)
    if f.unknown:
        return "unknown"
    pred = f.value if f.stated else None
    if name == "branches" and pred is not None and not br.canonical_set(pred):
        pred = None  # ["BE/B.Tech"] names a degree, not a branch: same as not stated
    if truth is None:
        return "correct" if pred is None else "invented"
    if pred is None:
        return "missed"
    return "correct" if same(name, truth, pred) else "wrong"


def evaluate(emails: list[Email], labels: dict[str, dict], extract: Callable[[Email], Extraction],
             profile: Profile, name: str) -> dict:
    per_field = {f: Counter() for f in FIELDS}
    details, verdicts = [], Counter()
    drive_correct = drive_total = 0
    wall, attempts = [], []
    unlabeled = []

    labelled = [e for e in emails if e.id in labels]
    unlabeled.extend(e.id for e in emails if e.id not in labels)
    xs = {e.id: extract(e) for e in labelled}
    # no 'now': judge eligibility, not whether it has closed
    vs = {v.email_id: v for v in decide_all([(e, xs[e.id]) for e in labelled], profile)}
    for email in labelled:
        truth = labels[email.id]
        x = xs[email.id]
        if x.meta.get("wall_ms") is not None:
            wall.append(x.meta["wall_ms"])
        attempts.append(x.meta.get("attempts", 1))

        drive_total += 1
        drive_correct += int(x.is_placement_drive == truth["is_drive"])
        row = {"id": email.id, "subject": email.subject, "fields": {}, "rejected": {}}

        if truth["is_drive"]:
            for f in FIELDS:
                b = bucket(f, truth[f], x)
                per_field[f][b] += 1
                if b != "correct":
                    row["fields"][f] = {"bucket": b, "truth": truth[f], "pred": x.get(f).value,
                                        "evidence": x.get(f).evidence, "reason": x.get(f).reason}
            if truth["eligible"] in ("Y", "N"):
                v = vs[x.email_id]
                verdicts[(truth["eligible"], v.verdict)] += 1
                row["verdict"] = {"truth": truth["eligible"], "pred": v.verdict, "reasons": v.reasons}
        reasons = x.meta.get("rejected_reasons", {})
        for f in x.meta.get("rejected_first", []):
            row["rejected"][f] = reasons.get(f) or x.get(f).reason or "rejected"
        details.append(row)

    n_drives = sum(1 for e in emails if e.id in labels and labels[e.id]["is_drive"])
    truth_yes = sum(c for (t, _), c in verdicts.items() if t == "Y")
    truth_no = sum(c for (t, _), c in verdicts.items() if t == "N")
    missed_eligible = sum(c for (t, p), c in verdicts.items() if t == "Y" and p in (NOT_ELIGIBLE, NOT_A_DRIVE))
    false_alarm = sum(c for (t, p), c in verdicts.items() if t == "N" and p == ELIGIBLE)
    to_open = sum(c for (_, p), c in verdicts.items() if p in (ELIGIBLE, CHECK))
    judged = truth_yes + truth_no

    return {
        "name": name,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "emails": len(details),
        "drives": n_drives,
        "unlabeled": unlabeled,
        "is_drive_correct": drive_correct,
        "fields": {f: dict(per_field[f]) for f in FIELDS},
        "verdicts": {f"{t}->{p}": c for (t, p), c in sorted(verdicts.items())},
        "judged": judged,
        "truly_eligible": truth_yes,
        "missed_eligible": missed_eligible,
        "false_alarm": false_alarm,
        "check": sum(c for (_, p), c in verdicts.items() if p == CHECK),
        "to_open": to_open,
        "median_ms": statistics.median(wall) if wall else None,
        "retries": sum(1 for a in attempts if a > 1),
        "details": details,
    }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "–"


def to_markdown(r: dict) -> str:
    out = [f"# Eval: {r['name']}", "", f"Run {r['run_at']} · {r['emails']} emails ({r['drives']} drives) · "
           f"is-it-a-drive correct {_pct(r['is_drive_correct'], r['emails'])}"]
    if r["median_ms"] is not None:
        out[-1] += f" · median {r['median_ms'] / 1000:.1f}s per email · {r['retries']} needed a retry"
    if r["unlabeled"]:
        out.append(f"\n⚠️ {len(r['unlabeled'])} emails have no label and were skipped: {', '.join(r['unlabeled'][:10])}")

    out += ["", "## Verdicts (what actually matters)", "",
            "| | count |", "|---|---|",
            f"| Drives you could really apply to | {r['truly_eligible']} |",
            f"| **Missed: told 'not eligible' but you were** | **{r['missed_eligible']}** |",
            f"| False alarm: told 'eligible' but you weren't | {r['false_alarm']} |",
            f"| Sent to CHECK (open it yourself) | {r['check']} |",
            f"| Emails you'd still open (eligible + check) | {r['to_open']} of {r['judged']} |", ""]

    out += ["## Fields", "", "| Field | Correct | Wrong | Invented | Missed | Unknown (CHECK) |", "|---|---|---|---|---|---|"]
    for f, c in r["fields"].items():
        total = sum(c.values())
        out.append(f"| {f} | {_pct(c.get('correct', 0), total)} | {c.get('wrong', 0)} | {c.get('invented', 0)} | "
                   f"{c.get('missed', 0)} | {c.get('unknown', 0)} |")

    out += ["", "## Every mistake, with the evidence", ""]
    for d in r["details"]:
        if not d["fields"] and not d["rejected"] and not (d.get("verdict") and d["verdict"]["truth"] == "Y" and d["verdict"]["pred"] != ELIGIBLE):
            continue
        out.append(f"### {d['subject'] or d['id']}  (`{d['id']}`)")
        if d.get("verdict"):
            v = d["verdict"]
            out.append(f"- verdict: truth **{'eligible' if v['truth'] == 'Y' else 'not eligible'}**, said **{v['pred']}**"
                       + (f" ({'; '.join(v['reasons'])})" if v["reasons"] else ""))
        for f, info in d["fields"].items():
            out.append(f"- `{f}` **{info['bucket']}**: label {json.dumps(info['truth'])}, got {json.dumps(info['pred'])}"
                       + (f", evidence: “{info['evidence']}”" if info["evidence"] else "")
                       + (f" — {info['reason']}" if info["reason"] else ""))
        for f, why in d["rejected"].items():
            if f not in d["fields"]:
                out.append(f"- `{f}` rejected on the first try ({why}); fixed by the retry")
        out.append("")
    return "\n".join(out)


def save(r: dict, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = r["name"].replace(":", "_").replace("/", "_")
    (out_dir / f"{slug}.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    md = out_dir / f"{slug}.md"
    md.write_text(to_markdown(r), encoding="utf-8")
    hist = out_dir / "history.csv"
    new = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["run_at", "name", "emails", "missed_eligible", "false_alarm", "check", "to_open", "judged",
                        *[f"{f}_correct" for f in FIELDS], "median_s", "retries"])
        w.writerow([r["run_at"], r["name"], r["emails"], r["missed_eligible"], r["false_alarm"], r["check"],
                    r["to_open"], r["judged"],
                    *[r["fields"][f].get("correct", 0) for f in FIELDS],
                    f"{r['median_ms'] / 1000:.1f}" if r["median_ms"] is not None else "", r["retries"]])
    return md
