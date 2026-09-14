# Cracker

A job application tracker that reads your Gmail and keeps itself up to date.

Built for the UK student recruiting season — internships, industrial placements,
spring weeks and hackathons — where you apply to dozens of places over months and
lose track of which ones are still alive.

```
Gmail --OAuth(read-only)--> local agent --> data/events.json --> tracker (browser)
        token on your machine    scheduled,
                                 keyword rules
```

Nothing is published and nothing leaves your machine. The agent runs locally with
a read-only Gmail scope; the tracker is a single HTML file.

## What it does

- **Reads recruiting mail** and classifies each message into a stage — Applied,
  OA/Assessment, Interview, Offer, Rejected/Withdrawn.
- **Files it against the right application**, matching on sender domain, ATS
  tenant, company name in the subject, and the role title.
- **Acts on what it is sure about.** High-confidence mail creates or advances
  applications directly; anything uncertain waits in an inbox for one click.
- **Extracts the role and category** when the mail states them, so one company
  with three open roles becomes three applications rather than one.
- **Flags what needs you** — overdue deadlines, deadlines within five days, and
  applications that have gone quiet for three weeks.

## Getting started

1. **Set up the agent** — Google OAuth client, install, sign in once:
   see [README-AGENT.md](README-AGENT.md).
2. **Run it:**

   ```bash
   python -m agent --serve
   ```

   Syncs your mail, then serves the tracker at <http://127.0.0.1:8732>.

3. **Keep it fresh** (Windows):

   ```powershell
   .\schedule.ps1
   ```

## Importing what you already have

The tracker reads `.xlsx` and `.csv` straight from the Import button, matching
your own column headings ("Company Name", "Position", "Status", "Date Applied")
and status vocabulary ("Interviewing", "To Apply", "Ghosted"). It shows you what
it understood before writing anything.

## Layout

| Path | |
|---|---|
| `index.html` | The tracker: table, board, dashboard, review inbox. No build step. |
| `agent/rules.py` | Phrase lists and classifiers. The file worth tuning. |
| `agent/gmail.py` | OAuth and message fetching, read-only scope, with backoff. |
| `agent/sync.py` | Orchestration and the message cache. |
| `schedule.ps1` | Registers the agent as a Windows scheduled task. |
| `gmail-sync.gs` | Older Apps Script route, kept as an alternative. |

## Privacy

`credentials.json`, `token.json` and `data/` are gitignored and must stay that
way — the first two together can read your mail, and `data/` holds cached message
text. Applications themselves live in your browser's local storage.

The Gmail scope requested is `gmail.readonly`. The agent cannot send, delete,
archive or label anything.
