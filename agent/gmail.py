"""
Gmail access. Read-only by construction: the only scope requested is
gmail.readonly, which cannot send, delete, archive or label anything.

Auth is the OAuth desktop flow. The first run opens a browser once; after that
token.json holds a refresh token and the agent runs unattended.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import html as html_module
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = ROOT / "credentials.json"
TOKEN_FILE = ROOT / "token.json"


# Statuses worth waiting out. 403 is ambiguous - it covers both "slow down"
# and "you may never do this" - so it is only retried when the reason says
# rate limit.
_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
_RETRY_REASONS = {
    "rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
    "backendError", "internalError",
}
_MAX_ATTEMPTS = 7


@dataclass
class Message:
    id: str
    thread_id: str
    date: str          # YYYY-MM-DD
    from_name: str
    from_address: str
    domain: str
    subject: str
    body: str


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

def _retry_reason(err: HttpError) -> str:
    """The machine-readable reason, or "" if the body isn't the usual shape."""
    try:
        body = json.loads(err.content.decode("utf-8", errors="replace"))
        return body["error"]["errors"][0].get("reason", "")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ""


def execute(request, *, attempts: int = _MAX_ATTEMPTS, quiet: bool = False):
    """
    Run an API request, backing off when Gmail says we are going too fast.

    Gmail's per-user ceiling is a moving average, so a burst can trip it even
    when the total is modest. Waiting and retrying is the sanctioned response;
    the alternative is losing a whole run to one 403.
    """
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as err:
            status = getattr(err.resp, "status", None)
            last = attempt == attempts - 1

            if status not in _RETRY_STATUSES or last:
                raise
            if status == 403 and _retry_reason(err) not in _RETRY_REASONS:
                raise  # a real permission problem, not congestion

            delay = min(64.0, 2.0 ** attempt) + random.uniform(0, 1)
            if not quiet:
                print(f"  rate limited - waiting {delay:.0f}s "
                      f"(attempt {attempt + 2}/{attempts})", file=sys.stderr)
            time.sleep(delay)

    raise AssertionError("unreachable: loop either returns or raises")


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def get_service(credentials_file: Path = CREDENTIALS_FILE,
                token_file: Path = TOKEN_FILE):
    """Return an authorised Gmail API client, refreshing or prompting as needed."""
    creds: Credentials | None = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise SystemExit(
                    f"Missing {credentials_file.name}.\n"
                    "Create an OAuth client (Desktop app) in Google Cloud Console, "
                    "download the JSON, and save it there. See README-AGENT.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_file), SCOPES
            )
            # port=0 lets the OS pick a free loopback port for the redirect.
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def my_address(service) -> str:
    return (service.users().getProfile(userId="me").execute()
            .get("emailAddress", "").lower())


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def search_ids(service, query: str, max_results: int) -> list[str]:
    """Page through a Gmail search, newest first, up to max_results ids."""
    ids: list[str] = []
    page_token = None

    while len(ids) < max_results:
        resp = execute(service.users().messages()
                       .list(userId="me", q=query, pageToken=page_token,
                             maxResults=min(500, max_results - len(ids))))
        before = len(ids)
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")

        if not page_token:
            break
        if len(ids) == before:
            # Gmail can hand back an empty page that still carries a token
            # when its filters strip a whole batch. Following that forever
            # would hang the run, so stop once a page adds nothing.
            break

    return ids[:max_results]


def fetch(service, message_id: str) -> Message:
    raw = execute(service.users().messages()
                  .get(userId="me", id=message_id, format="full"))

    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"]
               for h in payload.get("headers", [])}

    sender = headers.get("from", "")
    address = _extract_address(sender)

    return Message(
        id=message_id,
        thread_id=raw.get("threadId", ""),
        date=_header_date(headers.get("date", ""), raw.get("internalDate")),
        from_name=_extract_name(sender),
        from_address=address,
        domain=address.split("@")[-1] if "@" in address else "",
        subject=headers.get("subject", ""),
        body=_plain_body(payload),
    )


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #

def _decode(data: str) -> str:
    # Gmail uses base64url; padding is stripped and must be restored.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _plain_body(payload: dict) -> str:
    """
    Depth-first walk preferring text/plain. Falls back to stripping tags from
    text/html, which is what most ATS mail actually ships.
    """
    plain: list[str] = []
    markup: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_decode(data))
            elif mime == "text/html":
                markup.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)

    if plain:
        return "\n".join(plain)
    if markup:
        return strip_html("\n".join(markup))
    return ""


def strip_html(markup: str) -> str:
    """
    Reduce HTML mail to something worth matching phrases against.

    Order matters. Marketing mail hides <style> inside MSO conditional
    comments, so comments go first or the CSS survives and lands in the
    snippet - which is exactly what a stray rule then matches on.
    """
    text = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    text = re.sub(r"<head\b.*?</head\s*>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text,
                  flags=re.S | re.I)
    # An unclosed <style> would otherwise dump a stylesheet into the body.
    text = re.sub(r"<(script|style)\b[^>]*>.*", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_module.unescape(text)


def _extract_address(sender: str) -> str:
    return email.utils.parseaddr(sender)[1].strip().lower()


def _extract_name(sender: str) -> str:
    return email.utils.parseaddr(sender)[0].strip()


def _header_date(header: str, internal_ms: str | None) -> str:
    """Prefer the Date: header; fall back to Gmail's own receipt timestamp."""
    if header:
        try:
            return email.utils.parsedate_to_datetime(header).date().isoformat()
        except (TypeError, ValueError):
            pass
    if internal_ms:
        return (dt.datetime.fromtimestamp(int(internal_ms) / 1000)
                .date().isoformat())
    return ""


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
