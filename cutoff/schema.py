"""Data shapes shared by the extractors, the grounding check, the rules and the eval."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Every field the extractor fills. Order matters: it is the order the eval table prints.
FIELDS = [
    "company",
    "role",
    "min_cgpa",
    "min_percentage",
    "branches",
    "batch_years",
    "max_active_backlogs",
    "deadline",
]


@dataclass
class Field:
    """One extracted value plus the exact sentence it came from.

    status:
      "ok"        value is grounded in the email (or the email doesn't state it: value None)
      "rejected"  the extractor gave a value we could not verify against the email
    """

    value: Any = None
    evidence: Optional[str] = None
    status: str = "ok"
    reason: Optional[str] = None  # why grounding rejected it

    @property
    def stated(self) -> bool:
        return self.status == "ok" and self.value not in (None, [], "")

    @property
    def unknown(self) -> bool:
        return self.status == "rejected"


@dataclass
class Extraction:
    email_id: str
    is_placement_drive: bool = True
    roles_differ: bool = False  # several roles, and some criterion applies to only some of them
    fields: dict[str, Field] = field(default_factory=lambda: {k: Field() for k in FIELDS})
    extractor: str = ""
    meta: dict[str, Any] = field(default_factory=dict)  # attempts, latency_ms, rejected...

    def get(self, name: str) -> Field:
        return self.fields[name]

    def to_dict(self) -> dict:
        return {
            "email_id": self.email_id,
            "is_placement_drive": self.is_placement_drive,
            "roles_differ": self.roles_differ,
            "extractor": self.extractor,
            "fields": {
                k: {"value": f.value, "evidence": f.evidence, "status": f.status, "reason": f.reason}
                for k, f in self.fields.items()
            },
            "meta": self.meta,
        }


@dataclass
class Email:
    id: str
    subject: str
    sender: str
    date: Optional[str]  # ISO 8601, used to resolve "25th Oct" into a year
    body: str  # plain text (HTML already converted)
    apply_url: Optional[str] = None  # the email's own Apply/Register link; some drives are only reachable through it

    @property
    def full_text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}"


# JSON schema handed to Ollama's `format` parameter so Gemma can only emit this shape.
def _field_schema(value_schema: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"anyOf": [value_schema, {"type": "null"}]},
            "evidence": {"type": ["string", "null"]},
        },
        "required": ["value", "evidence"],
    }


OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {
        "is_placement_drive": {"type": "boolean"},
        "roles_with_different_criteria": {"type": "boolean"},
        "company": _field_schema({"type": "string"}),
        "role": _field_schema({"type": "string"}),
        "min_cgpa": _field_schema({"type": "number"}),
        "min_percentage": _field_schema({"type": "number"}),
        "branches": _field_schema({"type": "array", "items": {"type": "string"}}),
        "batch_years": _field_schema({"type": "array", "items": {"type": "integer"}}),
        "max_active_backlogs": _field_schema({"type": "integer"}),
        "deadline": _field_schema({"type": "string"}),
    },
    "required": ["is_placement_drive", "roles_with_different_criteria"] + FIELDS,
}
