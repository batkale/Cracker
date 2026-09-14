"""
The sync itself: search Gmail, classify what comes back, write events.json.

Message bodies are fetched once and the classified result cached in
data/state.json, so a scheduled run that finds nothing new costs one search
call rather than a few hundred fetches.

The output file is byte-compatible with what gmail-sync.gs served, so the
tracker's review queue, matcher and auto-apply all work unchanged.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gmail
from .rules import (academic_admin, ats_tenant, classify, company_from_subject,
                    detect_category, detect_kind, extract_role,
                    gmail_relevance_clause, is_academic, is_ats, is_noise,
                    is_relevant)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EVENTS_FILE = DATA / "events.json"
STATE_FILE = DATA / "state.json"

# Override per-run from the CLI. 120 days covers a UK recruiting season from
# spring-week applications through to autumn outcomes; 45 hid whole companies
# whose only remaining mail was the rejection.
LOOKBACK_DAYS = 120
MAX_MESSAGES = 400
EXTRA_QUERY = ""
SNIPPET_CHARS = 200

# Messages examined per run. None means "scale with the window": a fixed cap
# silently truncates the oldest mail as soon as you widen --days, which looks
# like old applications disappearing.
MESSAGES_PER_DAY = 10
MAX_MESSAGES_CEILING = 3000

# How much message text to keep per message so classification can be redone
# offline. Generous enough for a phrase buried below a long signature, small
# enough that a full cache stays a couple of megabytes.
CACHE_TEXT_CHARS = 6000

# Bump when the cached record shape changes; mismatched caches are discarded
# rather than misread.
CACHE_VERSION = 2


@dataclass
class Event:
    id: str
    date: str
    fromName: str
    fromAddress: str
    domain: str
    isATS: bool
    companyHint: str   # employer label from a per-tenant ATS host, else ""
    kindHint: str      # Internship / Hackathon / ... , or "" if unclear
    roleHint: str      # job title, when the mail states one plainly
    categoryHint: str  # sector, derived from roleHint only
    subject: str
    snippet: str
    stage: str
    confidence: str
    matchedOn: str


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _load_state() -> dict:
    fresh = {"version": CACHE_VERSION, "seen": {}}

    if not STATE_FILE.exists():
        return fresh
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fresh

    if state.get("version") != CACHE_VERSION:
        return fresh  # older layout; refetch once rather than misread it

    state.setdefault("seen", {})
    return state


def _save_state(state: dict) -> None:
    DATA.mkdir(exist_ok=True)
    state["version"] = CACHE_VERSION
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _prune(seen: dict, cutoff: str, live: set[str]) -> dict:
    """
    Drop cached records that have fallen out of the lookback window.

    Skipped messages are stored as None and carry no date, so they cannot be
    aged out by date alone — without `live` they accumulate in state.json for
    the life of the mailbox. Keeping only those still returned by the current
    search bounds the cache to the search window.
    """
    kept = {}
    for mid, rec in seen.items():
        if not rec:
            if mid in live:
                kept[mid] = rec      # still in range; don't pay to refetch it
            continue
        if not rec.get("date") or rec["date"] >= cutoff:
            kept[mid] = rec
    return kept


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #

def _classify_record(mid: str, rec: dict | None) -> Event | None:
    """Turn one cached message into an Event, or None if it isn't one."""
    if not rec:
        return None

    # Job boards advertise openings; they never reply to an application.
    if is_noise(rec["domain"]):
        return None

    hay = f"{rec['subject']} \n {rec['text']}".lower()
    if not is_relevant(hay):
        return None

    verdict = classify(hay)
    if verdict is None:
        return None

    # Your own university talks about assessments, congratulations and
    # unfortunate news constantly. On an academic sender, demand an unambiguous
    # recruiting phrase and reject anything that reads as course admin -
    # otherwise a wider --days manufactures applications to your own uni.
    if is_academic(rec["domain"]):
        if verdict.confidence != "high" or academic_admin(hay):
            return None

    role = extract_role(rec["subject"], rec["text"])

    return Event(
        id=mid,
        date=rec["date"],
        fromName=rec["fromName"],
        fromAddress=rec["fromAddress"],
        domain=rec["domain"],
        isATS=is_ats(rec["domain"]),
        companyHint=(ats_tenant(rec["domain"])
                     or (company_from_subject(rec["subject"])
                         if is_ats(rec["domain"]) else "")),
        kindHint=detect_kind(hay),
        roleHint=role,
        categoryHint=detect_category(role),
        subject=rec["subject"],
        snippet=rec["text"][:SNIPPET_CHARS],
        stage=verdict.stage,
        confidence=verdict.confidence,
        matchedOn=verdict.phrase,
    )


def build_query(days: int, extra: str = EXTRA_QUERY,
                prefilter: bool = True) -> str:
    parts = [f"newer_than:{days}d", "-in:chats", "-in:drafts",
             "-category:promotions", "-category:social"]
    if prefilter:
        parts.append(gmail_relevance_clause())
    if extra:
        parts.append(extra)
    return " ".join(parts)


def auto_max(days: int) -> int:
    """A cap that grows with the window, so widening --days actually widens."""
    return min(MAX_MESSAGES_CEILING, max(MAX_MESSAGES, days * MESSAGES_PER_DAY))


def run(days: int = LOOKBACK_DAYS,
        max_messages: int | None = None,
        extra_query: str = EXTRA_QUERY,
        verbose: bool = True,
        prefilter: bool = True) -> list[Event]:

    if max_messages is None:
        max_messages = auto_max(days)

    service = gmail.get_service()
    me = gmail.my_address(service)

    query = build_query(days, extra_query, prefilter)
    if verbose:
        print(f"Mailbox : {me}")
        print(f"Query   : {query}")

    ids = gmail.search_ids(service, query, max_messages)
    if verbose:
        print(f"Matched : {len(ids)} messages")
    if len(ids) >= max_messages:
        # Gmail returns newest first, so the overflow is the *oldest* mail --
        # silently dropping it would look like old applications vanishing.
        print(f"WARNING : hit the {max_messages}-message cap; mail older than "
              f"the newest {max_messages} was not examined. Raise --max or "
              f"narrow with --query.", file=sys.stderr)

    state = _load_state()
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    seen: dict = _prune(state["seen"], cutoff, set(ids))

    todo = [mid for mid in ids if mid not in seen]
    if verbose and todo:
        print(f"Fetching: {len(todo)} new ({len(ids) - len(todo)} cached)")

    fetched = 0
    try:
        for mid in todo:
            msg = gmail.fetch(service, mid)
            fetched += 1

            # Checkpoint periodically. Gmail can still refuse mid-run, and
            # without this every fetch already paid for is thrown away.
            if fetched % 25 == 0:
                state["seen"] = seen
                _save_state(state)
                if verbose:
                    print(f"          {fetched}/{len(todo)}...")

            # Outbound mail is never a status update, and nothing about the
            # rules could change that - so this one is safe to decide now.
            if msg.from_address == me:
                seen[mid] = None
                continue

            seen[mid] = {
                "date": msg.date,
                "fromName": msg.from_name,
                "fromAddress": msg.from_address,
                "domain": msg.domain,
                "subject": msg.subject,
                "text": gmail.squash(msg.body)[:CACHE_TEXT_CHARS],
            }
    except KeyboardInterrupt:
        print(f"\nInterrupted after {fetched} fetches - progress kept, "
              f"rerun to continue.")
        state["seen"] = seen
        _save_state(state)
        raise
    except Exception:
        # Keep what we paid for, then let the caller see the real error.
        state["seen"] = seen
        _save_state(state)
        raise

    state["seen"] = seen
    state["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    state["mailbox"] = me
    _save_state(state)

    # Classification runs over the cache on every run, never at fetch time.
    # That way editing RULES or NOISE_DOMAINS takes effect on the next run
    # with zero API calls - the alternative caches verdicts, and tuning a
    # phrase then appears to do nothing until the cache ages out.
    # Only ids in the current search window are considered, so mail ageing
    # past the lookback drops out on its own.
    events = [asdict(e) for e in
              (_classify_record(mid, seen.get(mid)) for mid in ids)
              if e is not None]
    events.sort(key=lambda e: e["date"], reverse=True)

    DATA.mkdir(exist_ok=True)
    EVENTS_FILE.write_text(json.dumps({
        "ok": True,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        # The tracker shows this so a lookback slider set wider than the agent
        # ever scanned can say so, instead of just returning nothing.
        "lookbackDays": days,
        "truncated": len(ids) >= max_messages,
        "events": events,
    }, indent=2), encoding="utf-8")

    if verbose:
        print(f"Fetched : {fetched} new, {len(ids) - fetched} cached")
        print(f"Events  : {len(events)} -> {EVENTS_FILE.relative_to(ROOT)}")

    return events
