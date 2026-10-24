"""Gemma (via Ollama, on your own machine) reads one placement email and fills the form.

Flow per email:
  1. Ask Gemma for every field + the exact sentence it came from (JSON schema enforced by Ollama).
  2. Ground every field against the email (grounding.py).
  3. If anything fails, retry ONCE, naming each rejected value and why.
  4. Whatever still fails stays UNKNOWN. Nothing unverified reaches the rules.

Responses are cached by (model, prompt version, email), so re-running the eval is free and
the same email always gives the same answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .grounding import evidence_in_email, ground, mentions_topic
from .schema import FIELDS, OLLAMA_SCHEMA, Email, Extraction, Field

PROMPT_VERSION = "v3"
DEFAULT_MODEL = os.environ.get("CUTOFF_MODEL", "gemma4:e4b")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not OLLAMA_URL.startswith("http"):
    OLLAMA_URL = "http://" + OLLAMA_URL
MAX_EMAIL_CHARS = 12000

SYSTEM_PROMPT = """You read campus placement emails sent to engineering students in India and fill in a form.
You copy facts from the email. You never guess, infer or fill gaps.

For every field return {"value": ..., "evidence": ...}.
- evidence is the shortest sentence, line or table row from the email that states the value,
  copied word for word. No paraphrasing, no "...", no added words.
- If the email does not state the field, return {"value": null, "evidence": null}.
- Lines like "[Attachment: name.pdf]" are file names, not content. Never take a value from a file name.
- If the email offers several roles and a criterion is written for only one of them, leave that
  criterion null: it does not apply to the whole email.

Fields:
- roles_with_different_criteria: true only if the email offers two or more roles AND some
  criterion (batch, branch, CGPA, percentage, backlogs) is written for only some of those roles.
  false if there is one role, or every criterion applies to every role.
- is_placement_drive: true if the email announces a job, internship or hiring drive that students
  can apply or register for (including registration reminders), or tells students about a company's
  upcoming campus drive even if registration opens later. false for anything else:
  results, shortlists, dress codes, schedules of talks with nothing to register for, newsletters,
  webinars, hackathons and competitions.
- company: the hiring company's name, as written.
- role: the job or internship title, as written. In portal notices like
  "X's Job Profile - Software Developer in Tier 1 category", the role is "Software Developer";
  "Tier 1" is a category, never a role.
- min_cgpa: the minimum CGPA / CGPI / CPI / GPA on a 10-point scale, as a number.
  A percentage is NOT a CGPA: "60% throughout" goes in min_percentage, never in min_cgpa.
- min_percentage: the minimum percentage of marks (10th, 12th, diploma or degree), as a number.
  Use as evidence the line that also says which marks it applies to, if there is one.
- branches: every eligible branch or stream exactly as written, one per item, including
  phrases like "and related fields" or "other IT-related branches" as their own item,
  e.g. ["CSE", "ISE", "CS & allied branches"]. If a group name is followed by its list, like
  "Circuit Branches- CS, IT, ECE", return the listed branches. Return ["All branches"] ONLY if the
  email itself says all branches are eligible. If the email says nothing about branches, or says
  they are in the JD or an attachment, return null.
- batch_years: the graduation / passing-out years that are eligible, e.g. "2027 batch",
  "graduating in 2027", "Year of Passing: 2027" -> [2027]. An internship start date
  ("January 2027 start") is not a batch, and neither is a year in a file name.
- max_active_backlogs: the maximum number of active/standing backlogs allowed.
  "No active backlogs" means 0.
- deadline: the last date and time for students to register or apply, as YYYY-MM-DDTHH:MM
  (24-hour, IST). Use the email's date to work out the year if the email leaves it out.
  If no time is given, use 23:59."""

RETRY_PROMPT = """Some of your values are not supported by the email:
{problems}
Read the email again. For each field listed above, either copy the exact sentence from the email
that states it and give the value that sentence states, or set both value and evidence to null.
Keep every other field exactly as before."""


class OllamaError(RuntimeError):
    pass


def _cache_path(cache_dir: Path, model: str, email: Email) -> Path:
    key = hashlib.sha256(f"{model}|{PROMPT_VERSION}|{email.date}|{email.full_text}".encode()).hexdigest()[:24]
    return cache_dir / f"{key}.json"


def _user_message(email: Email) -> str:
    body = email.body[:MAX_EMAIL_CHARS]
    return f"Email date: {email.date or 'unknown'}\nSubject: {email.subject}\n\n{body}"


def _chat(messages: list[dict], model: str, think: Optional[bool]) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "format": OLLAMA_SCHEMA,
        "stream": False,
        "keep_alive": "15m",
        "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
    }
    if think is not None:
        payload["think"] = think
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
    except requests.ConnectionError as e:
        raise OllamaError(f"Can't reach Ollama at {OLLAMA_URL}. Start it with `ollama serve`.") from e
    if r.status_code == 404:
        raise OllamaError(f"Model {model} isn't pulled. Run `ollama pull {model}`.")
    if r.status_code == 400 and "think" in payload and "think" in r.text.lower():
        # Older Ollama or a model without a thinking switch: ask again without it.
        return _chat(messages, model, None)
    if r.status_code >= 400:
        raise OllamaError(f"Ollama error {r.status_code}: {r.text[:300]}")
    return r.json()


def _coerce(name: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        if name in ("min_cgpa", "min_percentage"):
            return float(value)
        if name == "max_active_backlogs":
            return int(value)
        if name == "batch_years":
            return [int(v) for v in (value if isinstance(value, list) else [value])]
        if name == "branches":
            items = value if isinstance(value, list) else [value]
            return [str(v).strip() for v in items if str(v).strip()]
        if name in ("company", "role", "deadline"):
            return str(value).strip() or None
    except (TypeError, ValueError):
        return value  # grounding will reject it with a reason
    return value


def _to_extraction(email: Email, data: dict, model: str) -> Extraction:
    x = Extraction(email_id=email.id, extractor=f"gemma:{model}:{PROMPT_VERSION}")
    x.is_placement_drive = bool(data.get("is_placement_drive", True))
    x.roles_differ = bool(data.get("roles_with_different_criteria", False))
    for name in FIELDS:
        raw = data.get(name) or {}
        if not isinstance(raw, dict):
            raw = {"value": raw, "evidence": None}
        x.fields[name] = Field(value=_coerce(name, raw.get("value")), evidence=raw.get("evidence"))
    return x


def _problems(x: Extraction) -> str:
    lines = []
    for name, f in x.fields.items():
        if f.unknown:
            lines.append(f"- {name}: you said {json.dumps(f.value)} with evidence {json.dumps(f.evidence)}, but {f.reason}.")
    return "\n".join(lines)


def extract(email: Email, model: str = DEFAULT_MODEL, cache_dir: str | Path = ".cache/gemma",
            think: Optional[bool] = False, use_cache: bool = True) -> Extraction:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cpath = _cache_path(cache_dir, model, email)

    if use_cache and cpath.exists():
        cached = json.loads(cpath.read_text(encoding="utf-8"))
        responses, timings = cached["responses"], cached["timings"]
    else:
        responses, timings = [], []

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user_message(email)}]

    def call(i: int) -> dict:
        if i < len(responses):
            return json.loads(responses[i])
        t0 = time.perf_counter()
        resp = _chat(messages, model, think)
        content = resp.get("message", {}).get("content", "{}")
        responses.append(content)
        timings.append({
            "wall_ms": round((time.perf_counter() - t0) * 1000),
            "prompt_tokens": resp.get("prompt_eval_count"),
            "output_tokens": resp.get("eval_count"),
        })
        return json.loads(content)

    # Attempt 1
    first = _to_extraction(email, call(0), model)
    rejected_first = ground(first, email)
    first_reasons = {n: f"{first.fields[n].value!r}: {first.fields[n].reason}" for n in rejected_first}
    final, rejected_final = first, rejected_first

    # Attempt 2: name every rejected value and why.
    if rejected_first:
        messages += [{"role": "assistant", "content": responses[0]},
                     {"role": "user", "content": RETRY_PROMPT.format(problems=_problems(first))}]
        second = _to_extraction(email, call(1), model)
        ground(second, email)
        # Only the rejected fields are taken from the retry; the rest stay as first answered.
        for name in rejected_first:
            new, old = second.fields[name], first.fields[name]
            if new.value is None and evidence_in_email(old.evidence, email.full_text) \
                    and mentions_topic(name, old.evidence):
                # The email does talk about this (the first quote was real) but we still can't
                # read a value we trust. That is UNKNOWN, not "no restriction".
                old.reason = f"{old.reason}; retry gave up"
                continue
            first.fields[name] = new
        # A fix often moves a value, not just removes it: 60% leaves min_cgpa and lands in
        # min_percentage. Take newly stated, verified fields from the retry too.
        for name, new in second.fields.items():
            old = first.fields[name]
            if name not in rejected_first and old.value is None and new.stated:
                first.fields[name] = new
        final = first
        rejected_final = [n for n, f in final.fields.items() if f.unknown]

    cpath.write_text(json.dumps({"model": model, "prompt_version": PROMPT_VERSION,
                                 "email_id": email.id, "responses": responses, "timings": timings}, indent=1), encoding="utf-8")
    final.meta = {
        "attempts": 2 if rejected_first else 1,
        "rejected_first": rejected_first,
        "rejected_reasons": first_reasons,
        "rejected_final": rejected_final,
        "wall_ms": sum(t["wall_ms"] for t in timings[: (2 if rejected_first else 1)]),
        "output_tokens": sum((t.get("output_tokens") or 0) for t in timings[: (2 if rejected_first else 1)]),
    }
    return final


def doctor(model: str = DEFAULT_MODEL) -> str:
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
    except requests.RequestException:
        return f"✗ Ollama not reachable at {OLLAMA_URL}. Install from ollama.com, then run `ollama serve`."
    names = [m.get("name", "") for m in tags.get("models", [])]
    if not any(n == model or n.startswith(model + ":") or n.split(":")[0] == model for n in names):
        return f"✓ Ollama is running, ✗ {model} not pulled. Run: ollama pull {model}\n  (pulled: {', '.join(names) or 'none'})"
    return f"✓ Ollama is running and {model} is ready."
