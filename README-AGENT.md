# The local Gmail agent

Reads your Gmail on this machine, classifies recruiting mail into stages, and
writes `data/events.json` for the tracker to pick up. Roughly 10 minutes to set
up, once.

```
Gmail --OAuth(readonly)--> agent (scheduled, on your PC) --> data/events.json --> index.html
```

Nothing is published. There is no web endpoint, no shared token, and no third
party in the path — which is the main thing it changes versus the older Apps
Script route in `GMAIL-SETUP.md`. That file still works and still describes the
classifier's phrase lists, which the agent reuses verbatim; the two are
interchangeable and the tracker will talk to either.

---

## 1. Get a Google OAuth client

Google requires the app asking for your mail to be registered, even when it is
your own script reading your own inbox.

1. Go to <https://console.cloud.google.com/> and create a project — any name.
2. **APIs & Services -> Library**, search **Gmail API**, click **Enable**.
3. **APIs & Services -> OAuth consent screen**:
   - User type **External**, then **Create**.
   - App name, your email for both support and developer contact. Save.
   - On **Audience**, under *Test users*, click **Add users** and add your own
     Gmail address. It must be listed there or sign-in will be refused.
   - Leave the app in **Testing**. Publishing it would trigger Google's
     verification review, which you neither need nor want for a personal script.
4. **APIs & Services -> Credentials -> Create credentials -> OAuth client ID**:
   - Application type **Desktop app**.
   - Create, then **Download JSON**.
5. Save that file in this folder as **`credentials.json`**.

> A Testing-mode refresh token expires after 7 days. When a sync starts failing
> with `invalid_grant`, delete `token.json` and run the agent once by hand to
> sign in again. Publishing the app removes the expiry, at the cost of the
> review prompt.

## 2. Install and authorise

```powershell
py -m venv .venv                                    # Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m agent
```

```bash
python3 -m venv .venv                               # macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m agent
```

The first run opens a browser. Choose your account; Google warns the app is
unverified — that is expected, it is your own unpublished client. Click
**Advanced -> Go to (project name)** and allow.

The only scope requested is `gmail.readonly`. It cannot send, delete, archive or
label anything.

Afterwards `token.json` holds the refresh token and the agent runs unattended.

**Keep `credentials.json` and `token.json` on this machine.** Together they read
your mail. If either leaks, revoke access at
<https://myaccount.google.com/permissions> and delete both files.

## 3. See the results

```powershell
.venv\Scripts\python -m agent --serve      # Windows
```

```bash
.venv/bin/python -m agent --serve           # macOS / Linux
```

Syncs, then serves the tracker at <http://127.0.0.1:8732/index.html> and opens
it. Click **Sync Gmail** in the header to pull the latest `events.json`; matches
land in the **Inbox** tab.

The tracker must be served over `http://`, not opened as a `file://` page — a
`file://` page cannot read `data/events.json`. It will tell you so if you try.

## How far back it looks

Two separate windows, and they do different things:

- **`--days` on the agent** decides how much mail is fetched from Gmail.
  Default **120 days**, which covers a UK recruiting season from spring-week
  applications through to autumn outcomes. This is the one that costs API
  calls, and the message cap scales with it, so a wider window genuinely
  scans wider instead of silently stopping at the newest 400 messages.
- **"Show the last ... days" in the tracker** narrows what you look at. It
  filters instantly, needs no sync, and cannot show mail the agent never
  fetched - if you set it wider than the agent's window, the settings panel
  tells you the exact command to widen that.

Mail from a university domain (`.ac.uk`, `.edu`) is held to a higher bar:
high-confidence phrases only, and anything that reads as course admin
(assessments, accommodation, enrolment) is discarded. Without that, widening
the window fills the tracker with applications to your own university.

## What lands where

By default the tracker acts on anything it is confident about instead of
queueing it:

| Mail | What happens |
|---|---|
| High confidence, matches an application | Stage moves forward in the table |
| High confidence, no match | A new application is created |
| Low confidence | Waits in the **Inbox** tab for you to accept or dismiss |

Nothing asks first - delete or edit any row you disagree with. A deleted row
does not come back on the next sync: the mail that created it stays marked as
reviewed.

Applications are seeded from a company's *earliest* confident mail, so
`Applied` is the date you applied rather than whichever status email arrived
last; later mail then advances the stage and appends to the notes.

**Role** is filled in when the mail states one plainly ("for the Software
Engineer Internship (2027 Start) role", "role of Engineering Intern"), and
**Category** is derived from that title - so a software engineering internship
at a trading firm is Engineering, not Quant/Finance. When the mail names no
role, both stay blank rather than being guessed.

Later mail fills gaps: if the first email named no role and a follow-up does,
the row is completed. Values already present are never overwritten, so anything
you type yourself stands.

To review everything by hand instead, untick *Apply high-confidence mail
straight to the table* under the gear icon.

## 4. Run it on a schedule

```powershell
.\schedule.ps1             # every 2 hours, 120-day window
.\schedule.ps1 -Hours 6    # every 6 hours
.\schedule.ps1 -Days 180   # widen the lookback
.\schedule.ps1 -Remove     # unregister
```

The window is written into the task itself, so it does not silently change if
the agent's default moves later. Re-run `schedule.ps1` to change it.

Registers a Windows scheduled task running as you, in your own session, so
`token.json` is readable. No admin rights, no stored password.

```powershell
Start-ScheduledTask -TaskName GmailApplicationAgent   # run now
Get-ScheduledTaskInfo -TaskName GmailApplicationAgent # last result
```

`LastTaskResult` of `0` means success.

---

## Usage

| Command | What it does |
|---|---|
| `python -m agent` | Sync, write `data/events.json` |
| `python -m agent --days 180` | Change the lookback from the default 120. The message cap scales with it. |
| `python -m agent --query label:jobs` | Only scan a label — cuts noise a lot |
| `python -m agent --no-prefilter` | Fetch everything in range and filter locally — slow, rate-limit prone |
| `python -m agent --preview` | Classify and print, leave `events.json` untouched |
| `python -m agent --serve` | Sync, then serve the tracker |

## How it decides

Identical to the Apps Script, because it is a port of the same table. The full
phrase lists and the matching score are documented under *How stage detection
works* in `GMAIL-SETUP.md`. In short:

- A message must contain a recruiting word at all before it is classified.
- Stages are tested in order — **Rejected before Interview**, because rejections
  routinely open with "thank you for interviewing with us".
- A phrase from a `strong` list marks the email **high confidence**; a `weak`
  fallback word marks it **low** and it is never auto-applied.
- The tracker then scores each event against your applications and only ever
  moves an application forward, except a rejection, which can arrive at any
  point and always wins.

It also guesses the **opportunity type** - Internship, Industrial Placement,
Hackathon, Graduate Scheme, Insight/Spring Week - from `KIND_RULES`, and the
tracker prefills it when you create an application from a suggestion. This is a
separate axis from Category: an Optiver internship is Engineering *and* an
Internship. Matching is whole-word, so "intern" cannot fire on "international",
and when nothing in the mail indicates a type it stays blank rather than
guessing.

Two filters run before any of that:

- The Gmail query itself carries the `RELEVANCE` words as an OR-group, so mail
  that could never match is never fetched. On a real inbox this cut 400
  messages to 209. `--no-prefilter` turns it off.
- `NOISE_DOMAINS` drops job boards and newsletters (targetjobs, Forage, FT,
  Bright Network). They discuss roles constantly and trip the weak rules, but
  they never reply to an application. This removed roughly half of all matches.
  Match on the exact sending domain: `hackerrankmail.com` is marketing, while
  `hackerrankforwork.com` carries real assessment invitations.

To tune any of it, edit `agent/rules.py`. There is no redeploy step, and **no
refetch either** — `data/state.json` caches the message text, not the verdict,
so classification is redone from scratch on every run. Edit a phrase, run the
agent, see the difference, with zero API calls.

Delete `data/state.json` only if you want to re-read Gmail itself.

## Files

| Path | |
|---|---|
| `agent/rules.py` | Phrase lists and the classifier. The file you tune. |
| `agent/gmail.py` | OAuth and message fetching. Read-only scope. |
| `agent/sync.py` | Orchestration, caching, writes `events.json`. |
| `credentials.json` | Your OAuth client. **Secret.** You create this. |
| `token.json` | Your refresh token. **Secret.** Generated on first run. |
| `data/events.json` | Classified output. The tracker reads this. |
| `data/state.json` | Per-message cache, so repeat runs are cheap. |

## When something breaks

| Symptom | Cause |
|---|---|
| `Missing credentials.json` | Step 1 not finished, or the file is named differently. |
| `access_denied` at sign-in | Your address is not in *Test users* on the OAuth consent screen. |
| `invalid_grant` after a week | Testing-mode token expiry. Delete `token.json`, run once by hand. |
| `no data/events.json yet` | The agent has not completed a run. Run it by hand and read the output. |
| `Open the tracker with: python -m agent --serve` | You opened `index.html` as a file. Serve it instead. |
| Syncs fine, finds nothing | `--days` too short, or `--query` too narrow. Try `--preview --days 120`. |
| Right email, wrong application | The company name in the tracker does not resemble the sender domain. See the same row in `GMAIL-SETUP.md`. |
| `Quota exceeded` / `rateLimitExceeded` | Normal on a first run. The agent waits and retries, up to 7 times per call; let it finish. Progress is checkpointed every 25 messages, so a rerun resumes. |
| Run stops partway | Rerun it. Nothing already fetched is lost. |
| Scheduled task result `0x1` | Run the same command by hand in this folder to see the real error. |
