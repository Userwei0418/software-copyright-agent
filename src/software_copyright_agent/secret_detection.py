import re
from dataclasses import dataclass
from typing import Iterable, List, Pattern, Tuple


@dataclass(frozen=True)
class SecretFinding:
    relative_path: str
    line_number: int
    rule_id: str


SECRET_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
            r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
        ),
    ),
)


def detect_secrets(relative_path: str, text: str) -> Iterable[SecretFinding]:
    findings: List[SecretFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_id, pattern in SECRET_RULES:
            if pattern.search(line):
                findings.append(
                    SecretFinding(
                        relative_path=relative_path,
                        line_number=line_number,
                        rule_id=rule_id,
                    )
                )
    return findings
