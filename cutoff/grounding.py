"""Check every extracted value against the email it came from.

A small model is a fluent but careless reader. The two failures that matter most here:
  * "60% throughout" read as a CGPA cutoff of 6.0 (or 60)
  * a deadline or cutoff that appears nowhere in the email
So a value only reaches the rules if (1) its evidence sentence really is in the email and
(2) the value is visibly in that sentence, in the right form. Anything else becomes UNKNOWN,
and UNKNOWN can never make you "not eligible".
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from dateutil import parser as dateparser

from . import branches as br
from .schema import Email, Extraction, Field

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
CGPA_WORDS = r"cgpa|cgpi|cpi|gpa|sgpa|pointer|cumulative|/\s*10|out of 10|10[- ]point"
BACKLOG_WORDS = r"backlog|arrear|atkt|\bkt\b|re-?appear|supplementary|pending (paper|subject|exam)"


BATCH_WORDS = r"batch|graduat|pass ?-?out|passing|year of|yop|class of|hiring|recruit|fresher"


def norm(text: str | None) -> str:
    if not text:
        return ""
    t = re.sub(r"(?m)^[ \t]*(?:>[ \t]?)+", " ", text)  # quoted replies: "> related fields"
    t = unicodedata.normalize("NFKC", t).lower()
    t = t.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                                   "–": "-", "—": "-", "−": "-", "•": " "}))
    t = re.sub(r"[*_|]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_date(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        pass
    try:
        return dateparser.parse(str(value), dayfirst=True)
    except (ValueError, OverflowError, TypeError):
        return None


def evidence_in_email(evidence: str | None, email_text: str) -> bool:
    ev = norm(evidence).strip(" .,:;-")
    return bool(ev) and ev in norm(email_text)


def _numbers(evidence: str) -> list[tuple[float, bool]]:
    """(number, is_percentage) for every number in the evidence."""
    out = []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(?![\d])\s*(%|percent|per cent|marks)?", norm(evidence)):
        out.append((float(m.group(1)), bool(m.group(2))))
    return out


def _check(name: str, value, evidence: str | None, email: Email) -> str | None:
    """Return None if the value is supported, else a short reason (fed back to the model on retry)."""
    text = email.full_text + "\n" + email.sender
    ev = norm(evidence)

    if name in ("company", "role"):
        if not isinstance(value, str) or not value.strip():
            return "empty"
        return None if norm(value) in norm(text) else f'"{value}" does not appear in the email'

    if not evidence_in_email(evidence, text):
        return "the evidence is not copied word-for-word from the email"
    prose = re.sub(r"\[Attachments?:[^\]]*\]", " ", text)
    if not evidence_in_email(evidence, prose):
        return "that is a file name from the attachment list, not the email's text"

    if name == "min_cgpa":
        if not isinstance(value, (int, float)) or not (0 < value <= 10):
            return f"{value} is not a CGPA on a 10-point scale"
        hits = [(n, pct) for n, pct in _numbers(evidence) if abs(n - value) < 1e-6]
        if not hits:
            if any(pct and abs(n - value * 10) < 1e-6 for n, pct in _numbers(evidence)):
                return f"the email says {value * 10:g}%, a percentage, not a CGPA of {value:g}"
            return f"{value} is not written in the evidence"
        if all(pct for _, pct in hits):
            return f"{value} is a percentage in the email, not a CGPA"
        if not re.search(CGPA_WORDS, ev):
            return "the evidence does not mention CGPA"
        return None

    if name == "min_percentage":
        if not isinstance(value, (int, float)) or not (30 <= value <= 100):
            return f"{value} is not a percentage cutoff"
        if any(abs(n - value) < 1e-6 and pct for n, pct in _numbers(evidence)):
            return None
        return f"{value}% is not written in the evidence"

    if name == "branches":
        if not isinstance(value, list) or not value:
            return "empty branch list"
        missing = []
        for item in value:
            ni = br.normalize(item)
            if ni in ("all", "all branches") and re.search(r"\ball\b|\bany\b", br.normalize(evidence)):
                continue
            if ni and ni in br.normalize(evidence):
                continue
            code = br.code_for(item)
            if code and any(re.search(rf"\b{re.escape(a)}\b", br.normalize(evidence)) for a in br.ALIASES[code]):
                continue
            missing.append(item)
        return None if not missing else "not in the evidence: " + ", ".join(f'"{m}"' for m in missing)

    if name == "batch_years":
        if not isinstance(value, list) or not value:
            return "empty batch list"
        bad = [y for y in value if not (isinstance(y, int) and 2020 <= y <= 2035)
               or not (str(y) in ev or f"'{y % 100}" in ev)]
        if bad:
            return f"years not in the evidence: {bad}"
        if not re.search(BATCH_WORDS, ev):
            return "the evidence doesn't say these are graduation years (a start date is not a batch)"
        return None

    if name == "max_active_backlogs":
        if not isinstance(value, int) or not (0 <= value <= 10):
            return f"{value} is not a backlog count"
        if not re.search(BACKLOG_WORDS, ev):
            return "the evidence does not mention backlogs"
        if value == 0:
            ok = re.search(r"\b(no|nil|zero|0|none|not have any|without any|without)\b", ev)
        else:
            ok = re.search(rf"(?<!\d){value}(?!\d)", ev)
        return None if ok else f"{value} backlogs is not what the evidence says"

    if name == "deadline":
        dt = parse_date(value)
        if dt is None:
            return f'"{value}" is not a date'
        day, month = dt.day, dt.month
        sent = parse_date(email.date)
        if re.search(r"\b(today|tonight)\b", ev) and sent and sent.date() == dt.date():
            return None  # "register by today 7 PM", and today is the day the email was sent
        if not re.search(rf"(?<!\d)0?{day}(st|nd|rd|th)?(?!\d)", ev):
            return f"day {day} is not in the evidence"
        month_named = MONTHS[month - 1] in ev
        month_numeric = re.search(rf"(?<!\d)0?{day}\s*[/.\-]\s*0?{month}(?!\d)", ev) or \
            re.search(rf"\b{dt.year}\s*-\s*0?{month}\s*-\s*0?{day}\b", ev)
        return None if (month_named or month_numeric) else f"month {month} is not in the evidence"

    return None


TOPIC = {
    "min_cgpa": CGPA_WORDS,
    "min_percentage": r"%|percent|marks",
    "branches": r"branch|stream|discipline|cse|ise|ece|eee|csbs|allied|circuit",
    # narrower than BATCH_WORDS: "FY27 Campus hiring" is about hiring, not a graduation year
    "batch_years": r"batch|graduat|pass ?-?out|passing|year of|class of",
    "max_active_backlogs": BACKLOG_WORDS,
    "deadline": r"\d",
}


def mentions_topic(name: str, evidence: str | None) -> bool:
    """Does this sentence talk about the field at all? Used to tell 'the email doesn't state it'
    apart from 'the email states it and we couldn't read it'."""
    pattern = TOPIC.get(name)
    return bool(pattern and evidence and re.search(pattern, norm(evidence)))


def ground(extraction: Extraction, email: Email) -> list[str]:
    """Mark unsupported fields as rejected. Returns the names that were rejected."""
    rejected = []
    for name, f in extraction.fields.items():
        if f.value in (None, [], ""):
            f.value, f.status, f.reason = None, "ok", None
            continue
        reason = _check(name, f.value, f.evidence, email)
        if reason:
            f.status, f.reason = "rejected", reason
            rejected.append(name)
        else:
            f.status, f.reason = "ok", None
    return rejected


def reset(field: Field) -> None:
    field.value, field.evidence, field.status, field.reason = None, None, "ok", None
