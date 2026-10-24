"""The lazy payoff: one short message instead of a morning of opening emails."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from .rules import same_company, CHECK, CLOSED, ELIGIBLE, NOT_A_DRIVE, NOT_ELIGIBLE, Verdict
from .schema import Email, Extraction


def _when(dt: datetime | None) -> str:
    return f"apply by {dt:%a %d %b, %H:%M}" if dt else "no deadline in the email"


def _name(x: Extraction, e: Email) -> str:
    company = x.get("company").value if x.get("company").stated else None
    role = x.get("role").value if x.get("role").stated else None
    if not company:
        return e.subject[:70] or e.id
    return f"{company} — {role}" if role else company


def _link(e: Email) -> list[str]:
    return [f"      ↳ {e.apply_url}"] if e.apply_url else []


def _reason_kind(reason: str) -> str:
    r = reason.lower()
    for key, label in (("cgpa", "CGPA cutoff"), ("%", "% cutoff"), ("not in", "branch"), ("excludes", "branch"),
                       ("batch", "batch"), ("backlog", "backlogs")):
        if key in r:
            return label
    return "other"


def render(emails: list[Email], extractions: dict[str, Extraction], verdicts: list[Verdict],
           now: datetime | None = None) -> str:
    now = now or datetime.now()
    by_id = {e.id: e for e in emails}
    groups: dict[str, list[Verdict]] = {}
    for v in verdicts:
        groups.setdefault(v.verdict, []).append(v)
    key = lambda v: v.deadline.replace(tzinfo=None) if v.deadline else datetime.max  # noqa: E731

    def dedupe(vs: list[Verdict]) -> list[tuple[Verdict, int, Email]]:
        """Placement cells send the same drive 2-3 times (Superset notice, reminder, the forwarded
        company email). Show it once: the earliest deadline, plus the first Apply link any of them has."""
        groups: list[list] = []  # [representative verdict, count, email whose link we show]
        for v in sorted(vs, key=key):
            e, x = by_id[v.email_id], extractions[v.email_id]
            company = x.get("company").value if x.get("company").stated else None
            for g in groups:
                ge, gx = by_id[g[0].email_id], extractions[g[0].email_id]
                gc = gx.get("company").value if gx.get("company").stated else None
                same = (company and gc and same_company(company, gc)) or \
                       (e.apply_url and e.apply_url == ge.apply_url) or _name(x, e).lower() == _name(gx, ge).lower()
                if same:
                    g[1] += 1
                    if not g[2].apply_url and e.apply_url:
                        g[2] = e
                    break
            else:
                groups.append([v, 1, e])
        return [(v, n, le) for v, n, le in groups]

    yes, check = dedupe(groups.get(ELIGIBLE, [])), dedupe(groups.get(CHECK, []))
    times = lambda n: f" (×{n} emails)" if n > 1 else ""  # noqa: E731
    lines = [f"Cutoff · {now:%a %d %b}", ""]
    if yes:
        lines.append(f"{len(yes)} {'drive' if len(yes) == 1 else 'drives'} you can apply to (from {len(verdicts)} emails):")
        for v, n, le in yes:
            lines.append(f"  ✓ {_name(extractions[v.email_id], by_id[v.email_id])} · {_when(v.deadline)}{times(n)}")
            lines += _link(le)
    else:
        lines.append(f"Nothing to apply to in {len(verdicts)} emails.")
    if check:
        lines += ["", f"{len(check)} {'needs' if len(check) == 1 else 'need'} a look (I couldn't verify something):"]
        for v, n, le in check:
            when = f" · {_when(v.deadline)}" if v.deadline else ""
            lines.append(f"  ? {_name(extractions[v.email_id], by_id[v.email_id])}{when}{times(n)}")
            lines.append(f"      {v.reasons[0]}")
            lines += _link(le)

    no, closed, other = groups.get(NOT_ELIGIBLE, []), groups.get(CLOSED, []), groups.get(NOT_A_DRIVE, [])
    if no or closed or other:
        kinds = Counter(_reason_kind(v.reasons[0]) for v in no)
        parts = []
        if no:
            parts.append(f"{len(no)} not eligible (" + ", ".join(f"{k} ×{c}" for k, c in kinds.most_common()) + ")")
        if closed:
            parts.append(f"{len(closed)} already closed")
        if other:
            parts.append(f"{len(other)} not {'a drive' if len(other) == 1 else 'drives'}")
        lines += ["", "Skipped: " + "; ".join(parts) + "."]
    return "\n".join(lines)


def render_details(emails: list[Email], extractions: dict[str, Extraction], verdicts: list[Verdict]) -> str:
    """Why each email got its verdict, for when you don't trust the digest (yet)."""
    by_id = {e.id: e for e in emails}
    out = []
    for v in verdicts:
        x, e = extractions[v.email_id], by_id[v.email_id]
        out.append(f"## {v.verdict} · {e.subject}  ({e.id})")
        for r in v.reasons:
            out.append(f"- because: {r}")
        for p in v.passed:
            out.append(f"- clears: {p}")
        for name, f in x.fields.items():
            if f.stated or f.unknown:
                tag = "✗ unverified" if f.unknown else "✓"
                out.append(f"  - {tag} {name} = {f.value!r}" + (f"  ← “{f.evidence}”" if f.evidence else "")
                           + (f"  ({f.reason})" if f.reason else ""))
        out.append("")
    return "\n".join(out)
