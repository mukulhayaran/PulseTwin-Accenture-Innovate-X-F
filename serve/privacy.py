"""Privacy predicates shared by deterministic runtime and GenAI verification."""

from __future__ import annotations

import re

# An operator id is never legal in output or user-entered operational notes. The only
# legal operator token is an opaque group bucket (CLAUDE.md section 3).
_OPERATOR_PATTERNS = (
    re.compile(r"operator[_\s-]*(?:id|#)?[_\s-]*\d+", re.I),
    re.compile(r"\bop[-_]\d{2,}\b", re.I),
    re.compile(r"operator[_\s-]*id", re.I),
)
_LEGAL_OPERATOR = re.compile(r"^operator_group_[A-Z0-9]+$")


def contains_operator_identity(text: str) -> bool:
    """The project's single operator-identity detector."""
    if _LEGAL_OPERATOR.match(text.strip()):
        return False
    return any(pattern.search(text) for pattern in _OPERATOR_PATTERNS)
