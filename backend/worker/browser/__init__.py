"""Browser layer: a Playwright session with the typed tool set agents use (see session.py, tools.py)."""

from worker.browser.secrets import SecretVault, TestCredential
from worker.browser.session import BrowserLauncher, BrowserSession
from worker.browser.snapshot import Snapshot, SnapshotElement
from worker.browser.tools import ToolCall, ToolError, ToolResult

__all__ = [
    "BrowserLauncher", "BrowserSession", "SecretVault", "Snapshot", "SnapshotElement",
    "TestCredential", "ToolCall", "ToolError", "ToolResult",
]
