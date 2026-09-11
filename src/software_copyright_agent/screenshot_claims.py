"""Separate observation reminders from unresolved claims in screenshot evidence.

This is a deterministic guard on the model's own uncertainty declarations, not
a replacement for comparing its description with the image during review.
"""

from __future__ import annotations

import re


def unresolved_screenshot_claims(interpretation: dict | None) -> list[str]:
    value = interpretation or {}
    claims = [str(item).strip() for item in value.get("unresolved_claims", [])
              if str(item).strip()]
    # Older interpretation revisions had only warnings. Keep harmless reminders
    # (for example "未观察到失败提示") usable without silently approving guesses.
    for warning in value.get("warnings", []):
        text = str(warning).strip()
        candidate = re.sub(r"不(?:作|做|进行)(?:任何)?(?:推断|推测)|不推断", "", text)
        if re.search(r"推断|推测|猜测|假设|待核实|待确认|无法确认|未经验证|未验证|不确定|"
                     r"\b(?:infer(?:red|ence)?|guess(?:ed)?|assum(?:e|ed|ption)|"
                     r"unverified|uncertain)\b", candidate, re.I):
            claims.append(text)
    return list(dict.fromkeys(claims))


def screenshot_analysis_reminders(interpretation: dict | None) -> list[str]:
    value = interpretation or {}
    unresolved = set(unresolved_screenshot_claims(value))
    return [str(item).strip() for item in value.get("warnings", [])
            if str(item).strip() and str(item).strip() not in unresolved]
