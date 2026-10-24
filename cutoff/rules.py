"""Plain, testable rules: grounded fields + your profile -> a verdict.

The one rule that matters: a field we could not verify is UNKNOWN, and UNKNOWN can push a
drive into CHECK but never into NOT_ELIGIBLE. Missing a drive you could have applied to is
the expensive mistake; opening one extra email is the cheap one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import re

import yaml

from .branches import branch_status
from .grounding import norm, parse_date
from .schema import Extraction

ELIGIBLE, NOT_ELIGIBLE, CHECK, NOT_A_DRIVE, CLOSED = "ELIGIBLE", "NOT_ELIGIBLE", "CHECK", "NOT_A_DRIVE", "CLOSED"


@dataclass
class Profile:
    cgpa: float
    branch: str
    batch: int
    active_backlogs: int = 0
    tenth_pct: Optional[float] = None
    twelfth_pct: Optional[float] = None
    ug_pct: Optional[float] = None
    allied_includes_me: bool = True
    cse_includes_me: bool = False  # does "CSE" / "Computer Science & Engineering" include you?
    cs_includes_me: bool = False  # does plain "CS" / "Computer Science" include you?
    # Senders that only email you jobs you're already eligible for (e.g. a placement portal that
    # filters by your CGPA/branch). A drive from them with no criteria in the email is ELIGIBLE;
    # from anyone else it's CHECK, because the criteria are probably in an attachment or link.
    prefiltered_senders: list = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Verdict:
    email_id: str
    verdict: str
    reasons: list[str] = field(default_factory=list)  # why NOT_ELIGIBLE / why CHECK
    passed: list[str] = field(default_factory=list)  # criteria you clear, for the digest
    deadline: Optional[datetime] = None


_TENTH = r"\b(10th|10 th|x th|class x|sslc|ssc|secondary|matric\w*)\b"
_TWELFTH = r"\b(12th|12 th|xii|class xii|puc|pu|hsc|intermediate|diploma|higher secondary)\b"
_UG = r"\b(ug|graduation|graduate|degree|b ?e|b ?tech|engineering|cgpa|aggregate in (b|graduation))\b"
_ALL = r"\b(throughout|all academics?|across|each|every|all (the )?(levels|exams|semesters))\b"


def pct_scope(evidence: Optional[str]) -> list[str]:
    """Which of your marks a percentage cutoff applies to, from the sentence that states it.
    When the sentence doesn't say, assume all three: a wrong 'all' only sends a drive to CHECK."""
    t = re.sub(r"[./]", " ", (evidence or "").lower())
    t = re.sub(r"\s+", " ", t)
    if re.search(_ALL, t):
        return ["10th", "12th", "UG"]
    scope = [k for k, pat in (("10th", _TENTH), ("12th", _TWELFTH), ("UG", _UG)) if re.search(pat, t)]
    return scope or ["10th", "12th", "UG"]


def _cgpa_alternative(evidence: Optional[str]) -> bool:
    t = (evidence or "").lower()
    return bool(re.search(r"(%\s*/\s*\d+(\.\d+)?\s*(cgpa|cgpi|cpi|gpa))|((or|/)\s*(an\s+)?(equivalent\s+)?(cgpa|cgpi|cpi))", t))


def _check_criteria(x: Extraction, p: Profile) -> tuple[list[str], list[str], list[str]]:
    fails, unknowns, passes = [], [], []

    def unknown(name: str, label: str):
        f = x.get(name)
        unknowns.append(f"{label}: couldn't verify ({f.reason})")

    f = x.get("min_cgpa")
    if f.unknown:
        unknown("min_cgpa", "CGPA cutoff")
    elif f.stated:
        (passes if p.cgpa >= f.value else fails).append(f"CGPA {p.cgpa} vs cutoff {f.value:g}")

    f = x.get("min_percentage")
    if f.unknown:
        unknown("min_percentage", "percentage cutoff")
    elif f.stated:
        scope = pct_scope(f.evidence)
        marks = {"10th": p.tenth_pct, "12th": p.twelfth_pct, "UG": p.ug_pct}
        cg = x.get("min_cgpa")
        # "70%/7.0 CGPA" or "60% (or equivalent CGPA)": for your degree, the CGPA counts instead.
        if "UG" in scope and cg.stated and p.cgpa >= cg.value and _cgpa_alternative(f.evidence):
            scope = [k for k in scope if k != "UG"]
            passes.append(f"degree: CGPA {p.cgpa} clears {cg.value:g} (the email allows CGPA instead of %)")
        known = {k: marks[k] for k in scope if marks[k] is not None}
        below = [f"{k} {v:g}%" for k, v in known.items() if v < f.value]
        if below:
            fails.append(f"{', '.join(below)} vs {f.value:g}% cutoff")
        elif len(known) < len(scope):
            missing = [k for k in scope if k not in known]
            unknowns.append(f"{f.value:g}% cutoff, and your {', '.join(missing)} % isn't in your profile")
        elif scope:
            passes.append(f"{', '.join(scope)} marks above {f.value:g}%")

    f = x.get("branches")
    if f.unknown:
        unknown("branches", "branches")
    elif f.stated:
        status, why = branch_status(f.value, p.branch, p.allied_includes_me, p.cse_includes_me, p.cs_includes_me)
        {"PASS": passes, "FAIL": fails, "UNKNOWN": unknowns}[status].append(why)

    f = x.get("batch_years")
    if f.unknown:
        unknown("batch_years", "batch")
    elif f.stated:
        years = sorted(f.value)
        (passes if p.batch in years else fails).append(f"batch {p.batch} vs {'/'.join(map(str, years))}")

    f = x.get("max_active_backlogs")
    if f.unknown:
        unknown("max_active_backlogs", "backlogs")
    elif f.stated:
        (passes if p.active_backlogs <= f.value else fails).append(
            f"{p.active_backlogs} active backlogs vs max {f.value}")

    return fails, unknowns, passes


CRITERIA = ("min_cgpa", "min_percentage", "branches", "batch_years", "max_active_backlogs")

# "Eligibility criteria ... are mentioned in the above attachment" / "eligible programmes are in each JD":
# the email itself says it isn't the whole story, so it can never be a clean ELIGIBLE.
_ELSEWHERE = [
    r"(eligibility|eligible (programmes|programs|branches|degrees|streams|courses))[^.]{0,120}"
    r"(attach|brochure|job descriptions?|\bjds?\b|pdf)",
    r"(attach\w*|brochure|job descriptions?)[^.]{0,60}eligibility criteria",
]


def criteria_elsewhere(body: str) -> bool:
    # Read the prose only: the "[Attachments: a.pdf, b.xlsx]" list is file names, not a sentence.
    t = norm(re.sub(r"\[Attachments?:[^\]]*\]", " ", body or ""))
    return any(re.search(p, t) for p in _ELSEWHERE)


COMPETITION = r"\b(hackathon|techthon|ideathon|codeathon|competition|contest|olympiad|webinar|cup \d{4}|hackquest|mockvita|codevita)\b"


def decide(x: Extraction, p: Profile, now: Optional[datetime] = None, sender: str = "", body: str = "",
           subject: str = "") -> Verdict:
    deadline = None
    d = x.get("deadline")
    if d.stated:
        deadline = parse_date(d.value)

    if not x.is_placement_drive:
        return Verdict(x.email_id, NOT_A_DRIVE, ["not a drive announcement"], deadline=deadline)
    if re.search(COMPETITION, subject.lower()):
        # Gemma reads "PPO for winners" as a job. The subject line says what the email really is.
        return Verdict(x.email_id, NOT_A_DRIVE, ["a competition / event, not a hiring drive"], deadline=deadline)

    fails, unknowns, passes = _check_criteria(x, p)
    if fails and x.roles_differ:
        # "Growth Intern: open to all. AEO Associate: 2026 batch only." Failing one role's rule
        # doesn't rule you out of the email, so this is a CHECK, not a NO.
        return Verdict(x.email_id, CHECK, ["several roles with different criteria; you miss one role's: "
                                           + "; ".join(fails)], passes, deadline)
    if fails:
        return Verdict(x.email_id, NOT_ELIGIBLE, fails, passes, deadline)

    if now and deadline:
        # Placement emails are IST and rarely say so; compare wall-clock times.
        if deadline.replace(tzinfo=None) < now.replace(tzinfo=None):
            return Verdict(x.email_id, CLOSED, [f"deadline passed ({deadline:%d %b %H:%M})"], passes, deadline)

    if unknowns:
        return Verdict(x.email_id, CHECK, unknowns, passes, deadline)
    if not any(x.get(c).stated for c in CRITERIA):
        s = sender.lower()
        hit = next((d for d in p.prefiltered_senders if d and d.lower() in s), None)
        if hit:
            return Verdict(x.email_id, ELIGIBLE, [], [f"{hit} only sends drives you're eligible for"], deadline)
        if "joinsuperset" in s:
            why = "eligibility isn't in the email; Superset shows it when you open the job"
        else:
            why = "no eligibility criteria in the email (check the JD link or attachment)"
        return Verdict(x.email_id, CHECK, [why], passes, deadline)
    if criteria_elsewhere(body):
        return Verdict(x.email_id, CHECK, ["you clear what the email states, but it says more criteria are in the "
                                           "attachment / JD"], passes, deadline)
    return Verdict(x.email_id, ELIGIBLE, [], passes, deadline)


# --- Same drive, several emails -------------------------------------------------------------
# Superset's "Open for application" notices never carry criteria, but the placement office
# usually forwards the company's own email with them. Same company -> borrow that verdict.

_SUFFIX = (r"\b(pvt|private|ltd|limited|llp|inc|india|technologies|technology|solutions|services|"
           r"systems|global|group|co|company|corp|corporation|the)\b")


def company_key(name) -> tuple[str, ...]:
    t = re.sub(r"'s\b", "", str(name or "").lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return tuple(re.sub(_SUFFIX, " ", t).split())


def same_company(a, b) -> bool:
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return False
    n = min(len(ka), len(kb))
    return ka[:n] == kb[:n]


def _has_criteria(x: Extraction) -> bool:
    return any(x.get(c).stated for c in CRITERIA)


def decide_all(pairs: list, p: Profile, now: Optional[datetime] = None) -> list[Verdict]:
    """decide() for every (email, extraction), then fill criteria-less drives from another email
    about the same company. Borrowing can turn CHECK into ELIGIBLE, never into NOT_ELIGIBLE:
    the other email might be about a different role, so a 'no' only becomes a note."""
    verdicts = [decide(x, p, now, sender=e.sender, body=e.body, subject=e.subject) for e, x in pairs]
    donors = [(e, x) for e, x in pairs if x.is_placement_drive and _has_criteria(x) and x.get("company").stated]
    for i, (e, x) in enumerate(pairs):
        v = verdicts[i]
        if v.verdict != CHECK or _has_criteria(x) or not x.get("company").stated:
            continue
        found = [(de, dx) for de, dx in donors if de.id != e.id and same_company(dx.get("company").value, x.get("company").value)]
        if not found:
            continue
        judged = [(de, decide(dx, p, sender=de.sender, body=de.body, subject=de.subject)) for de, dx in found]
        ok = [(de, dv) for de, dv in judged if dv.verdict == ELIGIBLE]
        if ok:
            de, dv = ok[0]
            verdicts[i] = Verdict(x.email_id, ELIGIBLE, [], [f"criteria from the other {x.get('company').value} email "
                                                           f"(\"{de.subject[:70]}\")"] + dv.passed, v.deadline)
            continue
        de, dv = judged[0]
        note = "; ".join(dv.reasons) or dv.verdict
        verdicts[i] = Verdict(x.email_id, CHECK, [f"another {x.get('company').value} email (\"{de.subject[:60]}\") says: {note}"],
                              v.passed, v.deadline)
    return verdicts
