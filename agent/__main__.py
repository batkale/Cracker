"""
CLI entrypoint.

    python -m agent                    sync using defaults
    python -m agent --days 90          widen the lookback
    python -m agent --query label:jobs restrict to a label
    python -m agent --preview          classify and print, write nothing
    python -m agent --serve            sync, then serve the tracker on :8732
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import socketserver
import sys
import webbrowser
from pathlib import Path

from . import sync

ROOT = Path(__file__).resolve().parent.parent
PORT = 8732


def port_in_use(port: int) -> bool:
    """
    True if something already answers on the port.

    Bind failure is not a usable signal here. Windows lets a bind to
    127.0.0.1 succeed while another process holds 0.0.0.0 on the same port --
    and `python -m http.server` sets SO_REUSEADDR, which explicitly permits
    exactly that. Requests would then split between the two servers. Asking
    for a connection is the only check that actually detects it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def make_console_safe() -> None:
    """
    Stop an emoji in a subject line from killing the run.

    Email subjects are arbitrary Unicode, and a Windows console is usually a
    legacy codepage (cp1254 here) that cannot represent most of it. Without
    this, printing the results throws UnicodeEncodeError *after* every API
    call has already been paid for. Substituting the odd character is a far
    better outcome than losing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass  # already detached or redirected; nothing to do


def main(argv: list[str] | None = None) -> int:
    make_console_safe()

    p = argparse.ArgumentParser(
        prog="agent",
        description="Read Gmail, classify recruiting mail, feed the tracker.",
    )
    p.add_argument("--days", type=int, default=sync.LOOKBACK_DAYS,
                   help=f"how far back to scan (default {sync.LOOKBACK_DAYS})")
    p.add_argument("--max", type=int, default=None, dest="max_messages",
                   help="cap on messages examined (default: scales with --days, "
                        f"min {sync.MAX_MESSAGES})")
    p.add_argument("--query", default=sync.EXTRA_QUERY,
                   help='extra Gmail search terms, e.g. "label:jobs"')
    p.add_argument("--no-prefilter", action="store_false", dest="prefilter",
                   help="fetch every message in range and filter locally; "
                        "thorough, far slower, and prone to rate limits")
    p.add_argument("--preview", action="store_true",
                   help="print what was found without writing events.json")
    p.add_argument("--serve", action="store_true",
                   help=f"after syncing, serve the tracker on localhost:{PORT}")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)

    if args.preview:
        # Preview still uses the cache, but restores events.json afterwards so a
        # dry run can never disturb what the tracker is reading.
        before = sync.EVENTS_FILE.read_bytes() if sync.EVENTS_FILE.exists() else None
        events = sync.run(args.days, args.max_messages, args.query,
                          verbose=not args.quiet, prefilter=args.prefilter)
        if before is None:
            sync.EVENTS_FILE.unlink(missing_ok=True)
        else:
            sync.EVENTS_FILE.write_bytes(before)

        print()
        for e in events[:40]:
            print(f"{e['date']}  {e['stage']:<18} ({e['confidence']:<4}) "
                  f"{(e['fromName'] or e['domain'])[:28]:<28} - {e['subject'][:60]}")
        if len(events) > 40:
            print(f"... and {len(events) - 40} more")
        return 0

    sync.run(args.days, args.max_messages, args.query,
             verbose=not args.quiet, prefilter=args.prefilter)

    if args.serve:
        url = f"http://127.0.0.1:{PORT}/index.html"

        # Almost always this is a tracker left running from earlier, and the
        # sync above has already refreshed the file it reads. Point at it
        # rather than starting a second server that would fight it for
        # requests.
        if port_in_use(PORT):
            print(f"\nPort {PORT} is already serving - reusing it.")
            print(f"Tracker : {url}")
            webbrowser.open(url)
            return 0

        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(ROOT))
        with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
            print(f"\nTracker : {url}   (ctrl-c to stop)")
            webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nstopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
