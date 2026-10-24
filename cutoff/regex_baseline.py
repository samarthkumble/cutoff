"""The no-AI baseline: what a weekend of regexes gets you. Gemma has to beat this in the eval
table, or there's no reason to run a model at all."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from dateutil import parser as dateparser

from . import branches as br
from .grounding import ground, parse_date
from .schema import Email, Extraction, Field

DRIVE_WORDS = r"\b(register|registration|apply|eligib|campus (drive|recruitment|hiring)|hiring|internship|ctc|stipend|job description|jd)\b"
NOT_DRIVE_WORDS = r"\b(dress code|results? (of|for)|shortlist(ed)? (students|candidates)|congratulat)\b"


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _first(pattern: str, lines: list[str], flags=re.I) -> tuple[Optional[re.Match], Optional[str]]:
    for ln in lines:
        m = re.search(pattern, ln, flags)
        if m:
            return m, ln
    return None, None


def _deadline(lines: list[str], email_date: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    default = parse_date(email_date) if email_date else None
    default = (default or datetime.now()).replace(hour=23, minute=59, second=0, microsecond=0, tzinfo=None)
    for ln in lines:
        if not re.search(r"deadline|last date|register (by|before)|apply (by|before)|closes|due", ln, re.I):
            continue
        tail = re.split(r"deadline|last date|by|before|closes|due", ln, maxsplit=1, flags=re.I)[-1]
        if not re.search(r"\d", tail):
            continue
        try:
            dt = dateparser.parse(tail, fuzzy=True, dayfirst=True, default=default.replace(hour=0, minute=0))
        except (ValueError, OverflowError):
            continue
        if not re.search(r"\d\s*(am|pm)\b|\d:\d\d", tail, re.I):
            dt = dt.replace(hour=23, minute=59)
        return dt.strftime("%Y-%m-%dT%H:%M"), ln
    return None, None


def _branches(lines: list[str]) -> tuple[Optional[list[str]], Optional[str]]:
    for ln in lines:
        if not re.search(r"branch|stream|eligible|discipline|department", ln, re.I):
            continue
        n = br.normalize(ln)
        if re.search(r"\ball\b.*\b(branch|branches|stream|streams|disciplines?)\b", n):
            return ["All branches"], ln
        found: list[str] = []
        for code, aliases in br.ALIASES.items():
            for alias in sorted(aliases, key=len, reverse=True):
                if len(alias) <= 2:  # 'cs', 'it', 'is', 'me' are too common as plain words
                    continue
                if re.search(rf"\b{re.escape(alias)}\b", n):
                    found.append(alias.upper() if len(alias) <= 5 else alias)
                    break
        if found:
            return found, ln
    return None, None


def extract(email: Email) -> Extraction:
    text = email.full_text
    lines = _lines(email.body)
    x = Extraction(email_id=email.id, extractor="regex")
    x.is_placement_drive = bool(re.search(DRIVE_WORDS, text, re.I)) and not re.search(NOT_DRIVE_WORDS, email.subject, re.I)

    m = re.search(r"^(?:campus (?:drive|hiring|recruitment)|placement drive|internship drive|hiring)\s*[:\-|]\s*([A-Z][\w&.\- ]{1,40}?)(?:\s*[-|(:]|$)", email.subject, re.I)
    if m:
        x.fields["company"] = Field(m.group(1).strip(), email.subject)

    m, ln = _first(r"(?:role|position|designation|job title)\s*[:\-|]\s*(.+)", lines)
    if m:
        x.fields["role"] = Field(m.group(1).strip(" .|"), ln)

    m, ln = _first(r"(\d{1,2}(?:\.\d{1,2})?)(?!\s*%)\s*(?:and above\s*)?(?:cgpa|cgpi|cpi|gpa)|(?:cgpa|cgpi|cpi|gpa)[^0-9%\n]{0,25}(\d{1,2}(?:\.\d{1,2})?)(?![\d%])", lines)
    if m:
        x.fields["min_cgpa"] = Field(float(m.group(1) or m.group(2)), ln)

    m, ln = _first(r"(\d{2}(?:\.\d+)?)\s*%", [l for l in lines if re.search(r"10th|12th|sslc|puc|ug|degree|throughout|aggregate|marks", l, re.I)])
    if m:
        x.fields["min_percentage"] = Field(float(m.group(1)), ln)

    vals, ln = _branches(lines)
    if vals:
        x.fields["branches"] = Field(vals, ln)

    m, ln = _first(r"\b(20[2-3]\d)\b(?=[^\n]{0,20}(batch|pass ?-?outs?|graduat))|(?:batch|pass ?-?outs?|graduating)[^\n]{0,12}\b(20[2-3]\d)\b", lines)
    if m:
        years = sorted({int(y) for y in re.findall(r"\b(20[2-3]\d)\b", ln)})
        x.fields["batch_years"] = Field(years, ln)

    m, ln = _first(r"\bno\b[^\n]{0,20}backlogs?|(\d)\s*(?:active\s*)?backlogs?", lines)
    if m:
        x.fields["max_active_backlogs"] = Field(int(m.group(1)) if m.group(1) else 0, ln)

    val, ln = _deadline(lines, email.date)
    if val:
        x.fields["deadline"] = Field(val, ln)

    ground(x, email)
    x.meta = {"attempts": 1, "rejected_first": [n for n, f in x.fields.items() if f.unknown]}
    x.meta["rejected_final"] = x.meta["rejected_first"]
    return x
