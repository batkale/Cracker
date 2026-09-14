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

## Install

Windows, macOS or Linux. About fifteen minutes, most of it on Google's side.

### 1. Get the code

Needs [Python 3.11+](https://www.python.org/downloads/) and Git.

```bash
git clone https://github.com/batkale/Cracker.git
cd Cracker
```

**Windows**

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 2. Make your own Google OAuth client

Full steps in [README-AGENT.md](README-AGENT.md#1-get-a-google-oauth-client) —
create a Cloud project, enable the Gmail API, add your own address as a test
user, download the Desktop-app JSON as `credentials.json`.

**Everyone needs their own.** The app stays in Google's *Testing* mode, which
only admits accounts explicitly listed on the project, so you cannot hand a
working setup to someone else. The ten minutes buys you a client nobody else
depends on.

### 3. Sign in and run

```powershell
.venv\Scripts\python -m agent --serve      # Windows
```

```bash
.venv/bin/python -m agent --serve           # macOS / Linux
```

The first run opens a browser once. Google warns the app is unverified — that is
expected, it is your own unpublished client. After that the tracker opens at
<http://127.0.0.1:8732>.

### 4. Keep it running

**Windows** — registers a scheduled task every two hours:

```powershell
.\schedule.ps1
```

**macOS / Linux** — `crontab -e`, then add (with your own path):

```cron
0 */2 * * * cd ~/Cracker && .venv/bin/python -m agent --quiet
```

Nothing is shared between installs. Each person's mail, token and applications
stay on their own machine.

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
