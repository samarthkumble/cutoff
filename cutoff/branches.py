"""Turn the ways colleges write branch names into codes, and decide whether a branch list includes you.

The hard cases are the honest part of this project:
  "CS and allied branches"  -> includes CSBS? (configurable, default yes)
  "Circuit branches"        -> colleges disagree, so we say CHECK instead of guessing
  "CS" / "Computer Science" -> the CS family; whether CSBS counts is your call (cs_includes_me)
  "CSE" / "Computer Science & Engineering" -> that one department; separate switch (cse_includes_me)
At BMSCE, Superset let CSBS apply where the email said "CS" (IBM, KPMG) but not where it spelled
out "Computer Science & Engineering" (Tejas), which is why these are two codes.
"""
from __future__ import annotations

import re

ALIASES: dict[str, list[str]] = {
    "CS": ["cs", "computer science", "comp sci", "computing", "computers"],
    "CSE": ["cse", "computer science and engineering", "computer science engineering",
            "computer engineering", "computer science engg"],
    "ISE": ["ise", "is", "information science", "information science and engineering"],
    "IT": ["it", "information technology"],
    "CSBS": ["csbs", "cs and bs", "cs bs", "computer science and business systems",
             "computer science and business system", "cse business systems", "cs business systems",
             "cse bs", "business systems"],
    "AIML": ["aiml", "ai ml", "ai and ml", "artificial intelligence and machine learning",
             "cse aiml", "cse ai ml", "cse ai and ml", "ai"],
    "AIDS": ["aids", "ai ds", "ai and ds", "artificial intelligence and data science"],
    "DS": ["ds", "data science", "cse data science", "cse ds"],
    "IOT": ["iot", "cse iot", "internet of things"],
    "CY": ["cyber security", "cybersecurity", "cse cyber security", "cse cy"],
    "ECE": ["ece", "ec", "electronics and communication", "electronics and communications",
            "electronics and communication engineering", "electronics"],
    "EEE": ["eee", "ee", "electrical", "electrical and electronics",
            "electrical and electronics engineering", "electrical engineering"],
    "ETE": ["ete", "tce", "telecommunication", "electronics and telecommunication",
            "telecommunication engineering"],
    "EIE": ["eie", "ei", "electronics and instrumentation", "instrumentation", "instrumentation technology"],
    "MD": ["md", "medical electronics"],
    "BM": ["bm", "bme", "biomedical", "biomedical engineering", "bio medical"],
    "PE": ["production", "production engineering"],
    "ME": ["me", "mech", "mechanical", "mechanical engineering"],
    "CV": ["cv", "civil", "civil engineering"],
    "CH": ["chem", "chemical", "chemical engineering"],
    "BT": ["bt", "biotech", "biotechnology"],
    "AE": ["ae", "aero", "aerospace", "aeronautical"],
    "IEM": ["iem", "industrial engineering and management", "industrial engineering", "industrial"],
    "MCA": ["mca"],
    "MBA": ["mba"],
    "MTECH": ["mtech", "m tech", "me mtech"],
    "MSC": ["msc", "m sc"],
}

_ALIAS_TO_CODE = {alias: code for code, aliases in ALIASES.items() for alias in aliases}

# Degree names and glue words. "M.E" (dotted, so "m e") is a degree; "ME" alone stays Mechanical.
# "M/Tech" splits into "M" and "Tech", so those count as filler on their own too.
FILLER = (r"\b(b ?e|b ?tech|be btech|m e|m|tech|engineering|engg|branch(es)?|stream(s)?|dept|department|"
          r"students?|only|of|the|in|degree|programs?)\b")

ALL_PATTERNS = [
    r"\ball\b.*\b(branch|branches|stream|streams|discipline|disciplines|engineering|engg|b ?e|b ?tech|departments?)\b",
    r"\bany\b.*\b(branch|stream|discipline)\b",
    r"^all$",
    r"^open to all$",
]

# Families that a college might or might not count you in. The profile decides.
ALLIED_PATTERNS = [
    r"\b(cs|cse|computer( science)?)\b.*\b(allied|related)\b",
    r"\b(allied|related)\b.*\b(cs|cse|computer( science)?)\b",
    r"\bcs ?it\b.*\b(allied|related)\b",
]
AMBIGUOUS_PATTERNS = [r"\bcircuit\b", r"\bcore\b", r"\bnon ?cs\b", r"\btech(nical)? branches\b", r"\ballied\b", r"\brelated\b"]


def normalize(text: str) -> str:
    t = text.lower().replace("&", " and ").replace("+", " and ")
    t = re.sub(r"[().\-_'\"]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _split(item: str) -> list[str]:
    # "BTECH (Circuit Branches- CS, IT, ECE)" -> BTECH | Circuit Branches | CS | IT | ECE.
    # A dash only splits when spaced ("Branches- CS"), so "AI-ML" stays whole.
    parts = [p.strip() for p in re.split(r"[/,;|()\[\]:]| or |\s+-\s*|-\s+", item) if p.strip()]
    out: list[str] = []
    for p in parts:
        # "Aeronautical and Aerospace" is two branches; "Electronics and Communication" is one.
        pieces = [q.strip() for q in re.split(r"\s+(?:and|&)\s+", p) if q.strip()]
        if len(pieces) > 1 and not code_for(p) and all(code_for(q) for q in pieces):
            out.extend(pieces)
        else:
            out.append(p)
    return [re.sub(r"^(?:and|or|&)\s+", "", p) for p in out]  # "and related fields" -> "related fields"


def code_for(item: str) -> str | None:
    """Exact alias lookup, then again with filler words removed. Never substring-matches,
    so 'CSE (AIML)' is AIML, not CSE."""
    n = normalize(item)
    if n in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[n]
    stripped = re.sub(r"\s+", " ", re.sub(FILLER, " ", n)).strip()
    if stripped in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[stripped]
    return None


def is_all(item: str) -> bool:
    n = normalize(item)
    return any(re.search(p, n) for p in ALL_PATTERNS) and not any(re.search(p, n) for p in (r"\bcircuit\b", r"\bexcept\b", r"\bexcluding\b"))


def classify_item(item: str) -> tuple[str, str | None]:
    """Returns (kind, code). kind: 'all' | 'allied' | 'ambiguous' | 'branch' | 'unknown'."""
    if is_all(item):
        return "all", None
    n = normalize(item)
    if not re.sub(r"\s+", "", re.sub(FILLER, " ", n)):
        return "filler", None  # "B.E.", "BTech", "Engineering" on their own say nothing
    if any(re.search(p, n) for p in ALLIED_PATTERNS):
        return "allied", None
    code = code_for(item)
    if code:
        return "branch", code
    if any(re.search(p, n) for p in AMBIGUOUS_PATTERNS):
        return "ambiguous", None
    return "unknown", None


def _parts(raw: str) -> list[str]:
    """Split 'CSE/ISE, ECE' into items, but keep whole phrases like 'CS/IT and allied' together."""
    n = normalize(raw)
    if is_all(raw) or any(re.search(p, n) for p in ALLIED_PATTERNS) or re.search(r"\bnon\b", n):
        return [raw]  # "Non CS/IT" is one exclusion, not "Non CS" plus a listed "IT"
    return _split(raw)


def canonical_set(items: list[str]) -> set[str]:
    """For the eval: compare branch lists as sets of codes ('ALL', 'ALLIED', or raw text if unknown)."""
    out: set[str] = set()
    for item in items or []:
        parts = _parts(item)
        has_codes = any(classify_item(q)[0] == "branch" for q in parts)
        for part in parts:
            kind, code = classify_item(part)
            if kind == "filler":
                continue
            if kind == "ambiguous" and has_codes and re.search(r"\bbranch", normalize(part)):
                continue  # "Circuit Branches- CS, IT, ECE": the header names the list that follows
            if kind == "all":
                out.add("ALL")
            elif kind == "allied":
                out.add("CS-ALLIED")
            elif kind == "branch":
                out.add(code)
            else:
                out.add(normalize(part))
    return out


def branch_status(allowed: list[str], my_branch: str, allied_includes_me: bool = True,
                  cse_includes_me: bool = False, cs_includes_me: bool = False) -> tuple[str, str]:
    """PASS / FAIL / UNKNOWN for 'is my branch in this list', with a human reason."""
    my_code = code_for(my_branch) or my_branch.upper()
    unknowns: list[str] = []
    recognised: list[str] = []
    for raw in allowed:
        for part in _parts(raw):
            kind, code = classify_item(part)
            if kind == "filler":
                continue
            if kind == "all":
                return "PASS", f"open to all branches (\"{raw}\")"
            m = re.search(r"\bnon ?(.+)$", normalize(part))
            if m:
                excluded = {code_for(q) for q in _split(m.group(1).replace(" ", "/"))} - {None}
                if my_code in excluded or (cs_includes_me and "CS" in excluded) or (cse_includes_me and "CSE" in excluded):
                    return "FAIL", f"\"{raw}\" excludes {my_code} (your profile counts {my_code} as CS)" \
                        if my_code not in excluded else f"\"{raw}\" excludes {my_code}"
                unknowns.append(part)
                continue
            if kind == "allied":
                if allied_includes_me:
                    return "PASS", f"\"{raw}\" counts {my_code} as allied (your profile says so)"
                unknowns.append(raw)
            elif kind == "branch":
                if code == my_code:
                    return "PASS", f"{my_code} is listed"
                if (cs_includes_me and code == "CS") or (cse_includes_me and code == "CSE"):
                    return "PASS", f"\"{part}\" includes {my_code} (your profile says so)"
                recognised.append(code)
            else:
                unknowns.append(part)
    if unknowns:
        return "UNKNOWN", "can't tell if " + my_code + " is included in: " + ", ".join(f'"{u}"' for u in unknowns)
    if recognised:
        return "FAIL", f"{my_code} not in " + ", ".join(dict.fromkeys(recognised))
    return "PASS", "only the degree is named (" + ", ".join(allowed) + "), no branch restriction"
