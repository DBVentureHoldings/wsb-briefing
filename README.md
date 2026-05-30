# WSB Morning Briefing

Daily email digest of r/wallstreetbets — top tickers, sentiment, and Claude-summarized themes. Runs weekday mornings via launchd.

## How it works

1. PRAW pulls the highest-scoring posts from the last 24h (with top comments).
2. A regex + universe filter + jargon blacklist extracts ticker mentions and ranks them by mention count and engagement.
3. The corpus goes to Claude Haiku, which returns market mood, 3–5 themes, and a one-line take per top ticker (strict JSON).
4. A markdown report is saved to `reports/`, and an HTML email is sent to everyone in `RECIPIENTS`.

## Setup

```bash
cd "/Users/dbada/Documents/DB Venture Holdings LLC/Claude Code/wsb-briefing"

# 1. Python env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Reddit app
#    Go to https://www.reddit.com/prefs/apps → "create another app"
#    Type: script. Redirect URI: http://localhost. Note client_id and secret.

# 3. Gmail app password
#    https://myaccount.google.com/apppasswords (requires 2FA)

# 4. Anthropic key
#    https://console.anthropic.com/

# 5. Configure
cp .env.example .env
# edit .env and fill in all six values

# 6. Fetch the ticker universe (~8k symbols)
.venv/bin/python update_tickers.py
```

## Verify

```bash
# Dry run — no API calls, no email. Just prints the ticker table.
.venv/bin/python scraper.py --dry-run

# Full run, but skip email (writes report to reports/)
.venv/bin/python scraper.py --no-email

# Full run with email
.venv/bin/python scraper.py
```

If the dry run shows obvious junk like `DD`, `YOLO`, or `CEO` in the top tickers, add the offender to `jargon_blacklist.txt` and re-run.

## Schedule it

```bash
# Copy the plist into LaunchAgents
cp com.dbada.wsb-briefing.plist ~/Library/LaunchAgents/

# Register it (loads + persists across reboots)
launchctl load ~/Library/LaunchAgents/com.dbada.wsb-briefing.plist

# Confirm
launchctl list | grep wsb-briefing

# Force-run once to verify the cron-mode environment works
launchctl start com.dbada.wsb-briefing
tail -50 reports/.cron.log
```

To stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.dbada.wsb-briefing.plist
```

## Notes

- **Timezone**: launchd uses the Mac's local time. The plist fires at **07:00 local, Mon–Fri**. If you set your Mac to ET, that's 7am ET; if you travel, the trigger drifts with the system clock.
- **Cost**: ~$0.01–0.02/day on Claude Haiku. Reddit and Gmail are free.
- **Ticker universe**: rerun `update_tickers.py` weekly (or add it to a separate cron). New IPOs and delistings happen all the time.
- **False positives**: edit `jargon_blacklist.txt` to suppress symbols-that-are-also-acronyms. Cashtags ($TSLA) always bypass the blacklist.
- **`.env` is gitignored** — never commit credentials.
