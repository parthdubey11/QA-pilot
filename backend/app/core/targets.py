"""Which sites the server-side browser may visit (SSRF guard).

The worker's browser runs inside our network, so a project URL (or a redirect / script on the target site) must not
reach QA Pilot's own services (database, API) or cloud metadata endpoints. Localhost and private LAN addresses stay
allowed on purpose: student teams test apps running on their own machine. Only literal IPs and configured host names
are checked (no DNS resolution), which covers the realistic cases for a self-hosted dev tool.
"""

import ipaddress
from urllib.parse import urlsplit

from app.core.config import get_settings


def blocked_hosts() -> set[str]:
    return {h.strip().lower() for h in get_settings().blocked_target_hosts.split(",") if h.strip()}


def blocked_reason(url: str) -> str | None:
    """Why the browser may not open `url`, or None if it may."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "invalid URL"
    if not host:
        return None  # data:, about:blank, … carry no host
    if host in blocked_hosts():
        return f"{host} is one of QA Pilot's own services"
    allowed = {h.strip().lower() for h in get_settings().allowed_target_hosts.split(",") if h.strip()}
    if allowed and not any(host == a or host.endswith("." + a) for a in allowed):
        return f"this QA Pilot server only tests {', '.join(sorted(allowed))}"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return f"{host} is a link-local, reserved or metadata address"
    return None



def browser_args() -> list[str]:
    """Chromium flags that make blocked hosts unresolvable inside the browser's own network stack. Request
    interception alone isn't enough: Playwright doesn't intercept the target of a redirect."""
    rules = [f"MAP {host} ~NOTFOUND" for host in sorted(blocked_hosts())]
    rules += ["MAP 169.254.* ~NOTFOUND", "MAP [fe80::*] ~NOTFOUND"]  # link-local, incl. cloud metadata 169.254.169.254
    return ["--host-resolver-rules=" + ", ".join(rules)]
