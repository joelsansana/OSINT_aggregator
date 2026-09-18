# OSINT Aggregator

Monitors source Telegram channels and RSS feeds, filters by keyword, deduplicates, and forwards matching posts to your own Telegram channel. Comes with a Streamlit dashboard for live editing of sources and viewing the digest.

## 📣 Follow the output channel

See what this bot actually publishes in real time:

**👉 [t.me/osint_ai_aggregator](https://t.me/osint_ai_aggregator)**

## What it does

1. Polls a list of Telegram channels and RSS feeds on a schedule.
2. Filters posts against a keyword list.
3. Deduplicates via MD5 of the post body.
4. Sends matching posts to your output channel — either directly or via a review-queue channel where an admin approves with ✅ or discards with ❌.
5. Logs every post (sent, queued, discarded, or digest) to a local SQLite DB.
6. Generates a daily digest at 09:00 UTC by asking an LLM to summarise the last 24h of posts.

The bot re-reads its source lists every poll cycle, so changes made in the dashboard apply within ~60 seconds without a restart.

## Architecture

```
┌──────────────┐      poll      ┌────────────────┐
│  Telegram    │ ─────────────▶ │                │
│  channels    │                │                │
└──────────────┘                │                │      ┌──────────────┐
                                │   osint_       │ ───▶ │  Output /    │
┌──────────────┐      poll      │   aggregator   │      │  review      │
│  RSS feeds   │ ─────────────▶ │   .py          │      │  channel     │
└──────────────┘                │                │      └──────────────┘
                                │   (scheduler + │
┌──────────────┐      writes    │    digest job) │
│  Dashboard   │ ─────────────▶ │                │
│  dashboard.py│                └───────┬────────┘
└──────────────┘                        │
        ▲                               │ reads/writes
        │ edits                         ▼
        │                       ┌────────────────┐
        └────────────────────── │ aggregator.db  │
                                │ (SQLite, WAL)  │
                                └────────────────┘
```

The bot and the dashboard share the same SQLite file. The bot writes `posted_log`, `digest_jobs`, and `seen`; the dashboard reads them and writes `telegram_sources` / `rss_feeds`.

## Quick start

```bash
# 1. Get Telegram API credentials at https://my.telegram.org
# 2. Copy the env template and fill it in
cp .env.example .env
$EDITOR .env

# 3. Install deps
pip install -r requirements.txt

# 4. Run the bot (terminal 1)
python osint_aggregator.py

# 5. Run the dashboard (terminal 2)
streamlit run dashboard.py
```

On first run the bot seeds four default Telegram sources and four default RSS feeds into the DB. The dashboard picks them up immediately.

## Configuration

All config is loaded from `.env` (see `.env.example` for the full list).

| Var | Purpose | Default |
|---|---|---|
| `API_ID`, `API_HASH` | Telegram credentials | required |
| `OUTPUT_CHANNEL` | Where posts are sent | required |
| `MANUAL_REVIEW` | If `true`, posts go to `REVIEW_CHANNEL` first | `true` |
| `REVIEW_CHANNEL` | Admin channel for approve/discard | required if `MANUAL_REVIEW=true` |
| `SESSION_NAME` | Telethon session filename | `aggregator` |
| `TELEGRAM_POLL_INTERVAL` | Seconds between source polls | `60` |
| `RSS_POLL_INTERVAL` | Seconds between RSS polls | `300` |
| `MESSAGES_PER_CHANNEL` | Recent messages fetched per channel per poll | `5` |
| `LLM_PROVIDER` | LLM preset: `openai`, `minimax`, or `glm` | `openai` |
| `LLM_API_KEY` | API key for the chosen provider | required for digests |
| `LLM_MODEL` | Model name passed to the provider | provider default |
| `LLM_BASE_URL` | Override the provider's API endpoint | provider default |
| `OPENAI_API_KEY` | Legacy alias for `LLM_API_KEY` | — |
| `OPENAI_MODEL` | Legacy alias for `LLM_MODEL` | — |
| `DIGEST_HOUR_UTC`, `DIGEST_MINUTE_UTC` | When the daily digest fires | `9`, `0` |
| `DIGEST_INTERVAL_HOURS` | If >0, run digest every N hours instead of daily; window becomes N hours too | `0` (daily) |
| `DIGEST_ONLY` | If `true`, skip per-post Telegram sends; posts are buffered into the DB and surfaced only via the digest | `false` |
| `DB_PATH` | Override the SQLite location | `<project>/aggregator.db` |

### Keyword filter

Keywords live in `osint_aggregator.py` at the top of the file:

```python
KEYWORDS = [
    "breaking", "strike", "attack", ...
]
```

A post passes the filter if its lowercased text contains any keyword. Editing keywords requires a bot restart (this is the one piece of config not in the dashboard).

## Dashboard

Launch with `streamlit run dashboard.py` and open the URL it prints.

### 📥 Review queue
- Live list of posts the bot is holding for human review (`status IN ('review','digest')`).
- Each row has **✅ Approve** and **❌ Discard** buttons. Approvals move the post to `approved`; the bot's `process_review_actions` job picks it up within ~30 seconds and forwards it to the output channel.
- In Telegram, the same choice is available as inline buttons under each queued message — tap to approve/discard, the bot forwards inline.

### 📰 Digest
- Filter `posted_log` by date range, source, status (sent / review / discarded / digest / buffered).
- Export the current view to CSV.
- Click **Generate digest now** to enqueue an ad-hoc digest over a custom window. The bot picks it up within ~60 seconds.
- See the status of recent digest jobs.

### 📡 Telegram sources
- List channels with on/off toggles.
- Add a channel by username (no `@`). Format is validated against `[A-Za-z][A-Za-z0-9_]{3,31}`.
- Delete to stop monitoring.

### 🗞 RSS feeds
- List feeds with on/off toggles.
- Add a feed by URL. The **Preview** button parses it and shows the title + last 3 entries.
- Delete to stop polling.

### 📊 Stats
- Posts per day (line chart, configurable window).
- Posts per source (bar chart).

## Daily digest

At `DIGEST_HOUR_UTC`:`DIGEST_MINUTE_UTC` every day the bot:

1. Pulls `posted_log` rows from the last N hours where `status IN ('sent','review','buffered')`.
2. If fewer than 3 posts are found, skips silently (cheap).
3. Otherwise asks OpenAI to summarise them, with a system prompt that asks for grouped, factual, under-700-word output.
4. Sends the summary through the standard `send_post` flow — so if `MANUAL_REVIEW=true`, it lands in the review queue first and is logged with `status='digest'` once approved.
5. Updates the corresponding `digest_jobs` row to `done`.

The dashboard can also enqueue an out-of-band digest by clicking **Generate digest now** on the Digest page. The bot polls `digest_jobs` for pending entries every 60 seconds.

Cost is roughly $0.01–0.05 per digest with `gpt-4o-mini` at typical post volumes.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

80 tests, ~3s. Coverage focuses on the highest-leverage surfaces:

- `tests/test_db.py` — schema, WAL mode, seed behaviour, source/feed CRUD, `is_new` dedup, `posted_log` filters, `posts_for_window` (incl. `buffered` status), `digest_jobs` lifecycle, review-queue helpers (`list_pending_review`, `mark_review_action`), cross-connection visibility.
- `tests/test_bot_helpers.py` — `make_id`, `is_relevant`, `format_post` / `format_digest` (including truncation), `extract_post_id`.
- `tests/test_config.py` — env parsing, defaults, `require_secrets` happy / error paths, LLM provider selection (`openai` / `minimax` / `glm`), legacy `OPENAI_*` fallback behaviour.

Streamlit pages and the Telegram network code aren't covered (would need `streamlit.testing` and Telethon mocks).

## File layout

```
OSINT_aggregator/
├── osint_aggregator.py    # bot entry point
├── dashboard.py           # Streamlit entry point
├── db.py                  # SQLite schema + helpers (shared)
├── config.py              # .env loader + typed config dataclasses
├── requirements.txt       # runtime deps
├── requirements-dev.txt   # adds pytest
├── .env.example
├── .gitignore
├── tests/
│   ├── conftest.py
│   ├── test_db.py
│   ├── test_bot_helpers.py
│   └── test_config.py
└── aggregator.db          # created on first run (gitignored)
```

## Caveats

- **Dashboard has no auth.** Run it on localhost or behind a reverse proxy. Don't expose it publicly.
- **Single-instance only.** A `digest_jobs` row stuck in `running` after a bot crash will block new digests until manually cleared. For multi-instance deployment, add a startup sweep that marks stale `running` jobs as failed.
- **Source validation is regex-only.** Telegram source names are checked against a simple format pattern; the bot logs an error at poll time if a channel is unreachable. If you want live `get_entity` validation in the dashboard, it would need to share the Telethon session with the bot.
- **Keywords are not in the dashboard.** Editing `KEYWORDS` in `osint_aggregator.py` requires a bot restart. This was deliberately scoped out.
- **`posted_log` grows unbounded.** Add a retention job (e.g. `DELETE FROM posted_log WHERE sent_at < datetime('now', '-90 days')`) if disk space matters.
- **No content beyond text.** Telethon can pull media; the bot only inspects `message.text`. Posts that are purely images or videos are silently skipped.

## License

Personal project; no license declared. Use at your own discretion.
