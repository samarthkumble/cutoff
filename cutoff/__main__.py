"""cutoff: a local Gemma reads your placement emails so you only open the ones you can apply to.

  python -m cutoff doctor                        is Ollama + the model ready?
  python -m cutoff run  --inbox data/inbox       morning digest for new emails
  python -m cutoff eval --inbox data/inbox --labels data/labels.csv
  python -m cutoff prefill --inbox data/inbox --out data/labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from . import digest, evaluate, gemma, regex_baseline
from .emails import load_folder
from .grounding import parse_date
from .rules import Profile, decide_all


def _extractor(args):
    if args.extractor == "regex":
        return regex_baseline.extract, "regex"
    model = args.model or gemma.DEFAULT_MODEL
    think = {"on": True, "off": False, "default": None}[args.think]

    def run(email):
        return gemma.extract(email, model=model, think=think, use_cache=not args.no_cache)
    return run, f"gemma-{model}-{gemma.PROMPT_VERSION}" + ("-think" if think else "")


def _profile(path: str) -> Profile:
    p = Path(path)
    if not p.exists():
        sys.exit(f"No profile at {p}. Copy profile.example.yaml to profile.yaml and fill it in.")
    return Profile.load(p)


def _progress(i: int, n: int, label: str) -> None:
    print(f"\r  [{i}/{n}] {label[:60]:<60}", end="", file=sys.stderr, flush=True)


def cmd_doctor(args) -> None:
    print(gemma.doctor(args.model or gemma.DEFAULT_MODEL))


def cmd_run(args) -> None:
    profile = _profile(args.profile)
    emails = load_folder(args.inbox)
    if not emails:
        sys.exit(f"No .eml / .mbox / .txt files in {args.inbox}")
    extract, name = _extractor(args)
    now = parse_date(args.now) if args.now else datetime.now()
    extractions = {}
    for i, e in enumerate(emails, 1):
        _progress(i, len(emails), e.subject)
        extractions[e.id] = extract(e)
    print(file=sys.stderr)
    verdicts = decide_all([(e, extractions[e.id]) for e in emails], profile, now)
    text = digest.render(emails, extractions, verdicts, now)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n\n---\n\n" + digest.render_details(emails, extractions, verdicts), encoding="utf-8")
        print(f"\n(details written to {args.out})", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps([extractions[v.email_id].to_dict() | {"verdict": v.verdict, "reasons": v.reasons}
                                               for v in verdicts], indent=1, default=str), encoding="utf-8")


def cmd_eval(args) -> None:
    profile = _profile(args.profile)
    emails = load_folder(args.inbox)
    labels = evaluate.load_labels(args.labels)
    extract, name = _extractor(args)
    n = len(emails)
    count = {"i": 0}

    def tracked(e):
        count["i"] += 1
        _progress(count["i"], n, e.subject)
        return extract(e)

    result = evaluate.evaluate(emails, labels, tracked, profile, name)
    print(file=sys.stderr)
    md = evaluate.save(result, args.out_dir)
    print(evaluate.to_markdown(result).split("## Every mistake")[0])
    print(f"Full report with every mistake: {md}")


def cmd_prefill(args) -> None:
    """Draft labels from the REGEX baseline (not Gemma, so the labels aren't biased toward the
    model being tested). Check and correct every row by hand before running eval."""
    emails = load_folder(args.inbox)
    out = Path(args.out)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists. Use --force to overwrite (you'd lose your corrections).")
    cols = ["id", "is_drive", "company", "role", "min_cgpa", "min_percentage", "branches", "batch_years",
            "max_active_backlogs", "deadline", "eligible", "checked", "subject", "notes"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for e in emails:
            x = regex_baseline.extract(e)
            v = lambda n: x.get(n).value if x.get(n).stated else None  # noqa: E731
            w.writerow({
                "id": e.id, "is_drive": "Y" if x.is_placement_drive else "N",
                "company": v("company") or "", "role": v("role") or "",
                "min_cgpa": v("min_cgpa") or "", "min_percentage": v("min_percentage") or "",
                "branches": ";".join(v("branches") or []), "batch_years": ";".join(map(str, v("batch_years") or [])),
                "max_active_backlogs": "" if v("max_active_backlogs") is None else v("max_active_backlogs"),
                "deadline": (v("deadline") or "")[:16], "eligible": "?", "checked": "",
                "subject": e.subject, "notes": "",
            })
    print(f"Wrote {len(emails)} draft rows to {out}. Open it, fix every cell, set eligible to Y/N "
          f"and checked to Y. Blank = the email doesn't say.")


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; the digest uses ✓
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cutoff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, needs_profile=True):
        p.add_argument("--inbox", default="data/inbox", help="folder of .eml / .mbox / .txt")
        p.add_argument("--extractor", choices=["gemma", "regex"], default="gemma")
        p.add_argument("--model", help=f"Ollama model (default {gemma.DEFAULT_MODEL})")
        p.add_argument("--think", choices=["off", "on", "default"], default="off")
        p.add_argument("--no-cache", action="store_true")
        if needs_profile:
            p.add_argument("--profile", default="profile.yaml")

    p = sub.add_parser("doctor")
    p.add_argument("--model")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("run")
    common(p)
    p.add_argument("--now", help="pretend it's this time (ISO), for demos")
    p.add_argument("--out", default="digest.md")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("eval")
    common(p)
    p.add_argument("--labels", default="data/labels.csv")
    p.add_argument("--out-dir", default="eval")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("prefill")
    p.add_argument("--inbox", default="data/inbox")
    p.add_argument("--out", default="data/labels.csv")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_prefill)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except gemma.OllamaError as e:
        sys.exit(f"\n{e}")


if __name__ == "__main__":
    main()
