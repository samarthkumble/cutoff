"""Load placement emails from a folder: .eml files, .mbox archives (Gmail Takeout) or .txt pastes."""
from __future__ import annotations

import email
import hashlib
import mailbox
import re
from email import policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

from .schema import Email


class _HTMLToText(HTMLParser):
    """Good-enough HTML -> text: keeps table cells apart and line breaks where they belong."""

    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
    CELL = {"td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0
        self.links: list[tuple[str, str]] = []  # (link text, href)
        self._href: Optional[str] = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in self.CELL:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((" ".join("".join(self._link_text).split()), self._href))
            self._href = None
        if tag in ("style", "script", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)
            if self._href:
                self._link_text.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip(" |") for ln in raw.splitlines()]
        out, blank = [], 0
        for ln in lines:
            if ln:
                out.append(ln)
                blank = 0
            elif blank == 0:
                out.append("")
                blank = 1
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    p = _HTMLToText()
    p.feed(html)
    return p.text()


APPLY_WORDS = r"apply|register|registration|interest|application form|google form|click here|job profile"
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def find_apply_url(msg: Optional[email.message.EmailMessage], body_text: str) -> Optional[str]:
    """The link a student actually clicks: an <a> whose text says Apply/Register, else a URL on a
    line that talks about applying. Some drives can only be reached through this link."""
    html = msg.get_body(preferencelist=("html",)) if msg is not None else None
    if html is not None:
        p = _HTMLToText()
        p.feed(html.get_content())
        for text, href in p.links:
            if href.startswith("http") and re.search(APPLY_WORDS, text, re.I):
                return href
    lines = body_text.splitlines()
    for i, line in enumerate(lines):
        m = _URL.search(line)
        if not m:
            continue
        context = " ".join(lines[max(0, i - 1): i + 1])
        if re.search(APPLY_WORDS + r"|link|form|jd", context, re.I):
            return m.group(0).rstrip(".,;")
    return None


def _iso_date(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).isoformat()
    except (TypeError, ValueError):
        return None


def _body(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html" or re.search(r"<!doctype html|<html[\s>]", content[:500], re.I):
        return html_to_text(content)
    # Some placement portals send a plain part that is just "view in browser"; prefer HTML then.
    if len(content.strip()) < 80:
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            return html_to_text(html.get_content())
    return content.strip()


# Forwarded-message headers carry long To:/Cc: lists of classmates. They are noise for the
# model (and slow it down), and they are other people's data. Drop them, then redact any
# remaining email addresses and phone numbers before anything is stored or sent to the model.
_FWD_ADDR_BLOCK = re.compile(r"^(To|Cc|Bcc):.*?(?=^\S[^\n]*:\s|^\s*$)", re.I | re.M | re.S)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\d/-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d/-])")


def clean_body(text: str) -> str:
    text = _FWD_ADDR_BLOCK.sub("", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _make_id(subject: str, date: Optional[str], body: str) -> str:
    return hashlib.sha1(f"{subject}|{date}|{body[:500]}".encode()).hexdigest()[:10]


def from_message(msg: email.message.EmailMessage, fallback_id: Optional[str] = None) -> Email:
    subject = str(msg.get("Subject", "")).strip()
    date = _iso_date(msg.get("Date"))
    raw_body = _body(msg)
    body = clean_body(raw_body)
    return Email(
        id=fallback_id or _make_id(subject, date, body),
        subject=subject,
        sender=str(msg.get("From", "")),
        date=date,
        body=body,
        apply_url=find_apply_url(msg, raw_body),
    )


def load_eml(path: Path) -> Email:
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)
    # Short tidy file names (our fixtures) become the id; Gmail's long subject-based names get a hash.
    stem = path.stem if re.fullmatch(r"[\w-]{1,24}", path.stem) else None
    return from_message(msg, fallback_id=stem)


def load_txt(path: Path) -> Email:
    """A pasted email. Optional first lines 'Subject: ...', 'Date: ...' and 'From: ...'."""
    text = path.read_text(encoding="utf-8", errors="replace")
    subject, date, sender, lines = "", None, "", text.splitlines()
    while lines and re.match(r"^(Subject|Date|From):", lines[0], re.I):
        key, _, val = lines.pop(0).partition(":")
        if key.lower() == "subject":
            subject = val.strip()
        elif key.lower() == "date":
            date = _iso_date(val.strip()) or val.strip()
        else:
            sender = val.strip()
    raw = "\n".join(lines).strip()
    return Email(id=path.stem, subject=subject, sender=sender, date=date, body=clean_body(raw),
                 apply_url=find_apply_url(None, raw))


def load_mbox(path: Path) -> Iterable[Email]:
    for msg in mailbox.mbox(str(path), factory=lambda f: email.message_from_binary_file(f, policy=policy.default)):
        yield from_message(msg)


def load_folder(folder: str | Path) -> list[Email]:
    folder = Path(folder)
    emails: list[Email] = []
    for path in sorted(folder.rglob("*")):
        suffix = path.suffix.lower()
        if suffix == ".eml":
            emails.append(load_eml(path))
        elif suffix == ".mbox":
            emails.extend(load_mbox(path))
        elif suffix == ".txt":
            emails.append(load_txt(path))
    return emails
