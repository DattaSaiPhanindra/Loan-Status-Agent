"""Allowlist enforcement, action risk classification, and PII redaction."""

from __future__ import annotations

import re

from pydantic import BaseModel


# --- Allowlist ---


class PolicyConfig(BaseModel):
    """Configurable policy for what the agent may do."""

    allowed_domains: list[str] = ["127.0.0.1", "localhost"]
    allowed_ports: list[int] = [8080]
    allowed_url_patterns: list[str] = [r"http://127\.0\.0\.1:8080/.*"]
    blocked_url_patterns: list[str] = [r".*/admin/.*"]
    allowed_actions: list[str] = [
        "click",
        "type",
        "select",
        "navigate",
        "wait",
        "scroll",
    ]
    max_actions_per_run: int = 50
    require_confirmation_for: list[str] = ["write", "irreversible"]


DEFAULT_POLICY = PolicyConfig()


def check_url_allowed(
    url: str, policy: PolicyConfig = DEFAULT_POLICY
) -> tuple[bool, str]:
    """Check if a URL is within the allowlist. Returns (allowed, reason)."""
    for pattern in policy.blocked_url_patterns:
        if re.search(pattern, url):
            return False, f"URL matches blocked pattern: {pattern}"

    for pattern in policy.allowed_url_patterns:
        if re.search(pattern, url):
            return True, "URL matches allowed pattern"

    return False, f"URL not in allowlist: {url}"


def check_action_allowed(
    action_type: str,
    risk_level: str = "read",
    policy: PolicyConfig = DEFAULT_POLICY,
) -> tuple[bool, str]:
    """Check if an action type is allowed. Returns (allowed, reason)."""
    if action_type not in policy.allowed_actions:
        return False, f"Action type '{action_type}' not in allowed actions"

    if risk_level in policy.require_confirmation_for:
        return False, f"Action requires confirmation: risk level '{risk_level}'"

    return True, "Action allowed"


# --- Risk Classification ---

ACTION_RISK = {
    "navigate": "safe",
    "wait": "safe",
    "scroll": "safe",
    "click": "read",
    "type": "read",
    "select": "read",
}

WRITE_INDICATORS = [
    "submit",
    "save",
    "create",
    "delete",
    "remove",
    "confirm",
    "approve",
    "deny",
    "reject",
    "send",
    "transfer",
    "close",
    "update",
    "modify",
    "edit",
    "new",
    "add",
]

IRREVERSIBLE_INDICATORS = [
    "delete",
    "remove",
    "close account",
    "deny",
    "reject",
    "transfer",
    "disburse",
    "finalize",
]


def classify_risk(
    action_type: str,
    element_name: str = "",
    element_role: str = "",
    url: str = "",
) -> str:
    """Classify an action's risk level: safe, read, write, or irreversible."""
    base_risk = ACTION_RISK.get(action_type, "read")

    if base_risk == "safe":
        return "safe"

    name_lower = element_name.lower()

    for indicator in IRREVERSIBLE_INDICATORS:
        if indicator in name_lower:
            return "irreversible"

    for indicator in WRITE_INDICATORS:
        if indicator in name_lower:
            return "write"

    return base_risk


# --- PII Redaction ---

PII_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone": r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "account_number": r"\b\d{8,17}\b",
    "dob": r"\b\d{2}/\d{2}/\d{4}\b",
}

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "ssn",
    "social_security",
    "credit_card",
    "cvv",
    "pin",
}


def redact_text(text: str) -> str:
    """Redact PII patterns from text."""
    result = text
    for pii_type, pattern in PII_PATTERNS.items():
        result = re.sub(pattern, f"[REDACTED_{pii_type.upper()}]", result)
    return result


def redact_dict(data: dict, depth: int = 0) -> dict:
    """Recursively redact sensitive values from a dict. Safe for logging."""
    if depth > 10:
        return {"_redacted": "max depth"}
    result = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_KEYS:
            result[key] = "[REDACTED]"
        elif isinstance(value, str):
            result[key] = redact_text(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, depth + 1)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(v, depth + 1)
                if isinstance(v, dict)
                else redact_text(str(v))
                if isinstance(v, str)
                else v
                for v in value
            ]
        else:
            result[key] = value
    return result


def is_sensitive_value(value: str) -> bool:
    """Check if a value contains PII patterns."""
    for pattern in PII_PATTERNS.values():
        if re.search(pattern, value):
            return True
    return False


if __name__ == "__main__":
    ok, reason = check_url_allowed("http://127.0.0.1:8080/search")
    assert ok, reason
    ok, reason = check_url_allowed("http://127.0.0.1:8080/admin/expire-session")
    assert not ok, "Admin should be blocked"
    ok, reason = check_url_allowed("http://evil.com/steal")
    assert not ok, "External should be blocked"

    assert classify_risk("navigate") == "safe"
    assert classify_risk("click", "Search") == "read"
    assert classify_risk("click", "Submit Application") == "write"
    assert classify_risk("click", "Delete Account") == "irreversible"

    assert "[REDACTED_SSN]" in redact_text("SSN is 123-45-6789")
    assert "[REDACTED]" in str(redact_dict({"password": "secret123"}))

    print("Guardrails OK")
