"""Test-site credentials without leaking them to the LLM.

Agents only ever see placeholders like {{cred:Test shopper:password}}. The browser session swaps in the real value
right before typing, and any text going to the LLM (snapshots, step messages) is passed through `redact()`.
"""

import re
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\{\{cred:([^:{}]+):(username|password)\}\}")


@dataclass(frozen=True)
class TestCredential:
    __test__ = False  # not a pytest test class

    label: str
    username: str
    password: str


class SecretVault:
    def __init__(self, credentials: list[TestCredential] | None = None):
        self.credentials = credentials or []
        self._values: dict[str, str] = {}
        for cred in self.credentials:
            label = cred.label.replace(":", " ").replace("{", "").replace("}", "").strip()
            self._values[f"{{{{cred:{label}:username}}}}"] = cred.username
            self._values[f"{{{{cred:{label}:password}}}}"] = cred.password

    @property
    def placeholders(self) -> list[str]:
        return list(self._values)

    def fill(self, text: str) -> str:
        """Replace placeholders with real values. Unknown placeholders raise KeyError."""

        def swap(match: re.Match[str]) -> str:
            if match.group(0) not in self._values:
                raise KeyError(f"Unknown credential placeholder {match.group(0)}")
            return self._values[match.group(0)]

        return PLACEHOLDER.sub(swap, text)

    def redact(self, text: str) -> str:
        """Replace any real credential value in `text` with its placeholder (longest values first)."""
        for placeholder, value in sorted(self._values.items(), key=lambda kv: -len(kv[1])):
            if len(value) >= 3:
                text = text.replace(value, placeholder)
        return text
