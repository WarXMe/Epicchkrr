"""
parser.py — ULP / combo file parser
"""
import re
from config import EPIC_DOMAINS


def parse_ulp(text: str, epic_only: bool = True) -> list[tuple[str, str]]:
    """
    Parse ULP or plain combo lines from raw text.

    Supported formats:
      https://epicgames.com:email@x.com:pass   ← scheme stripped before split
      epicgames.com:email@x.com:pass
      email@x.com:pass                         (plain combo)

    Args:
        text:       raw file content
        epic_only:  if True, only return lines whose URL contains an Epic domain

    Returns:
        list of (email, password) tuples, deduped
    """
    results = []
    seen    = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # ── fix: strip http(s):// BEFORE splitting on ":"  ──────────────────
        # Without this, "https://epicgames.com:email:pass" splits as:
        #   ["https", "//epicgames.com", "email:pass"]  ← wrong
        # After strip: "epicgames.com:email:pass" → splits correctly
        line = re.sub(r"^https?://", "", line)

        parts = line.split(":", 2)

        if len(parts) == 3:
            # URL:LOGIN:PASS
            url, login, password = parts
            if epic_only:
                if not any(d in url.lower() for d in EPIC_DOMAINS):
                    continue
            email = login.strip()

        elif len(parts) == 2:
            # LOGIN:PASS — no domain info
            if epic_only:
                continue
            email, password = parts[0].strip(), parts[1].strip()

        else:
            continue

        password = password.strip()
        if not email or not password or "@" not in email:
            continue

        key = f"{email}:{password}"
        if key in seen:
            continue
        seen.add(key)
        results.append((email, password))

    return results
