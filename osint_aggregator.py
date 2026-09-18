"""
Telegram OSINT Aggregator Bot
=============================
Monitors source Telegram channels + RSS feeds, filters by keywords,
deduplicates, and forwards matching posts to your own channel.

Sources live in SQLite so the Streamlit dashboard can edit them at runtime
(the bot re-reads the source tables every poll cycle).

Setup:
    1. Copy .env.example to .env and fill in your credentials.
    2. pip install -r requirements.txt
    3. Run the bot:      python osint_aggregator.py
    4. Run the dashboard: streamlit run dashboard.py
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import OpenAI
from telethon import Button, TelegramClient, events
from telethon.errors import FloodWaitError

import config
import db

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("osint")

# ─────────────────────────────────────────────
# KEYWORDS — only posts containing at least one pass the filter.
# Managed at runtime via the dashboard (🔑 Keywords page); defaults
# are seeded into the DB on first run from db.DEFAULT_KEYWORDS.
# ─────────────────────────────────────────────

# Telegram's hard limit per message.
TELEGRAM_MAX_LEN = 4096
# Leave headroom for the "⚡️" / "📡 source · HH:MM UTC" wrapper.
POST_BODY_MAX = TELEGRAM_MAX_LEN - 80

# Minimum posts in a window before the digest LLM call is worth it.
DIGEST_MIN_POSTS = 3

# Marker used inside review-queue messages so the ✅/❌ handler can
# resolve the original post without parsing free-form text.
POST_ID_MARKER = "POST_ID:"

# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def is_relevant(text: str, keywords: list[str] | None = None) -> bool:
    """Return True if `text` contains any keyword (case-insensitive).

    `keywords` is fetched once per poll cycle by the caller and passed in
    so we don't hit the DB for every message. If omitted (e.g. in tests
    or one-off scripts), it's read from the DB on demand.
    """
    if not text:
        return False
    if keywords is None:
        keywords = db.list_keywords(enabled_only=True)
    if not keywords:
        return False
    lower = text.lower()
    return any(kw in lower for kw in keywords)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp() -> str:
    return utc_now().strftime("%H:%M UTC")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_post(text: str, source: str) -> str:
    """Apply consistent channel formatting and respect Telegram's length cap."""
    body = _truncate(text.strip(), POST_BODY_MAX)
    return (
        f"⚡️ {body}\n\n"
        f"📡 {source} · {utc_stamp()}"
    )


def format_digest(text: str, day: str) -> str:
    body = _truncate(text.strip(), POST_BODY_MAX)
    return (
        f"📰 Daily Digest · {day}\n\n"
        f"{body}\n\n"
        f"📡 Synthesised by OSINT Aggregator · {utc_stamp()}"
    )


def extract_post_id(message_text: str) -> str | None:
    if not message_text:
        return None
    m = re.search(rf"{POST_ID_MARKER}([a-f0-9]{{32}})", message_text)
    return m.group(1) if m else None


# ─────────────────────────────────────────────
# POSTING
# ─────────────────────────────────────────────

async def send_post(
    client: TelegramClient,
    text: str,
    source: str,
    *,
    kind: str = "post",
    digest_of: str | None = None,
) -> str | None:
    """
    Format + send (or queue for review). Returns the post_id of the
    message that was queued/sent, or None if the post was dropped.

    `kind` is 'post' for normal items and 'digest' for daily summaries;
    it changes the format prefix and the posted_log.status default.
    """
    if kind == "digest":
        day = digest_of or utc_now().date().isoformat()
        formatted = format_digest(text, day)
        status = "digest"
        effective_digest_of = day
    else:
        formatted = format_post(text, source)
        status = "review" if config.telegram().manual_review else "sent"
        effective_digest_of = None

    post_id = make_id(formatted)
    digest_only = config.telegram().digest_only

    # In digest-only mode, individual posts are buffered for the digest
    # but never sent to Telegram. The digest itself still goes through
    # the normal flow below so the user keeps getting the summary.
    skip_telegram_send = digest_only and kind != "digest"

    if skip_telegram_send:
        status = "buffered"
        log.info(
            "Digest-only: buffered post without sending | source=%s",
            source,
        )
    else:
        try:
            if config.telegram().manual_review:
                review_text = (
                    f"📥 REVIEW QUEUE\n"
                    f"{POST_ID_MARKER}{post_id}\n"
                    f"Source: {source}\n"
                    f"──────────────\n"
                    f"{formatted}\n\n"
                    f"Tap ✅ to approve or ❌ to discard "
                    f"(reply ✅/❌ still works)."
                )
                buttons = [
                    [
                        Button.inline("✅ Approve", data=f"approve:{post_id}".encode()),
                        Button.inline("❌ Discard", data=f"discard:{post_id}".encode()),
                    ],
                ]
                await client.send_message(
                    config.telegram().review_channel,
                    review_text,
                    buttons=buttons,
                )
                log.info("Queued for review | source=%s | kind=%s", source, kind)
            else:
                await client.send_message(config.telegram().output_channel, formatted)
                log.info("Posted | source=%s | kind=%s | %s...", source, kind, text[:60])
        except FloodWaitError as e:
            log.warning("Flood wait: sleeping %ss", e.seconds)
            await asyncio.sleep(e.seconds)
            return None
        except Exception:
            log.exception("send_post failed | source=%s | kind=%s", source, kind)
            return None

    try:
        db.log_post(
            post_id=post_id,
            source=source,
            text=formatted,
            status=status,
            digest_of=effective_digest_of,
        )
    except Exception:
        log.exception("posted_log write failed (post_id=%s)", post_id)

    return post_id


# ─────────────────────────────────────────────
# REVIEW-QUEUE HANDLER
# ─────────────────────────────────────────────

def register_review_handler(client: TelegramClient) -> None:
    """
    Watch the review channel for admin replies.
    ✅ in a reply → forward the original post to the output channel.
    ❌ in a reply → discard.

    Originals are looked up by POST_ID marker (no fragile string parsing
    of the formatted body).
    """
    out_channel = config.telegram().output_channel

    # Inline-button handler (callback data: "approve:<post_id>" /
    # "discard:<post_id>"). Posts are forwarded inline so the user sees
    # the result in the output channel immediately on tap.
    @client.on(events.CallbackQuery(data=re.compile(rb"^(approve|discard):([a-f0-9]{32})$")))
    async def handle_review_button(event):
        action, post_id = event.data.decode().split(":", 1)
        if action == "approve":
            try:
                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT text FROM posted_log "
                        "WHERE post_id = ? AND status IN ('review', 'digest') "
                        "ORDER BY id DESC LIMIT 1",
                        (post_id,),
                    ).fetchone()
                if not row:
                    await event.answer("Already actioned.", alert=True)
                    return
                await client.send_message(out_channel, row["text"])
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE posted_log SET status = 'sent' "
                        "WHERE post_id = ? AND status IN ('review', 'digest')",
                        (post_id,),
                    )
                await event.answer("✅ Posted")
                log.info("Review approved (button) | post_id=%s", post_id)
            except Exception:
                log.exception("Button approve failed | post_id=%s", post_id)
                await event.answer("⚠️ Failed", alert=True)
        else:
            try:
                with db.connect() as conn:
                    cur = conn.execute(
                        "UPDATE posted_log SET status = 'discarded' "
                        "WHERE post_id = ? AND status IN ('review', 'digest')",
                        (post_id,),
                    )
                if cur.rowcount == 0:
                    await event.answer("Already actioned.", alert=True)
                    return
                await event.answer("🗑 Discarded")
                log.info("Review discarded (button) | post_id=%s", post_id)
            except Exception:
                log.exception("Button discard failed | post_id=%s", post_id)
                await event.answer("⚠️ Failed", alert=True)

    @client.on(events.NewMessage(chats=config.telegram().review_channel))
    async def handle_review(event):
        if not event.is_reply:
            return
        original = await event.get_reply_message()
        if not original or not original.text:
            return

        post_id = extract_post_id(original.text)
        if not post_id:
            await event.reply("⚠️ Could not find POST_ID on the original message.")
            return

        decision = event.text.strip()
        if "✅" in decision:
            try:
                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT text, source FROM posted_log "
                        "WHERE post_id = ? AND status = 'review' "
                        "ORDER BY id DESC LIMIT 1",
                        (post_id,),
                    ).fetchone()
                if not row:
                    await event.reply("⚠️ No matching review record in log.")
                    return
                await client.send_message(out_channel, row["text"])
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE posted_log SET status = 'sent' "
                        "WHERE post_id = ? AND status = 'review'",
                        (post_id,),
                    )
                await event.reply("✅ Posted.")
                log.info("Review approved | post_id=%s", post_id)
            except Exception:
                log.exception("Approval flow failed | post_id=%s", post_id)
                await event.reply("⚠️ Failed to post — see bot logs.")

        elif "❌" in decision:
            try:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE posted_log SET status = 'discarded' "
                        "WHERE post_id = ? AND status = 'review'",
                        (post_id,),
                    )
                await event.reply("🗑 Discarded.")
                log.info("Review discarded | post_id=%s", post_id)
            except Exception:
                log.exception("Discard flow failed | post_id=%s", post_id)
                await event.reply("⚠️ Failed to record discard.")


# ─────────────────────────────────────────────
# TELEGRAM SOURCE POLLING
# ─────────────────────────────────────────────

async def poll_telegram_sources(client: TelegramClient) -> None:
    cfg = config.telegram()
    channels = db.list_telegram_sources(enabled_only=True)
    keywords = db.list_keywords(enabled_only=True)
    if not channels:
        log.info("No enabled Telegram sources; skipping poll.")
        return
    log.info("Polling %d Telegram source(s)...", len(channels))
    for channel_name in channels:
        try:
            entity = await client.get_entity(channel_name)
        except Exception as e:
            log.error("Could not resolve @%s: %s", channel_name, e)
            continue
        try:
            async for message in client.iter_messages(
                entity, limit=cfg.messages_per_channel
            ):
                if not message.text:
                    continue
                post_id = make_id(message.text)
                source = f"@{channel_name}"
                if not db.is_new(post_id, source):
                    continue
                if not is_relevant(message.text, keywords):
                    continue
                await send_post(client, message.text, source)
                await asyncio.sleep(1)
        except Exception as e:
            log.error("Error polling @%s: %s", channel_name, e)


# ─────────────────────────────────────────────
# RSS FEED POLLING
# ─────────────────────────────────────────────

async def poll_rss_feeds(client: TelegramClient) -> None:
    feeds = db.list_rss_feeds(enabled_only=True)
    keywords = db.list_keywords(enabled_only=True)
    if not feeds:
        log.info("No enabled RSS feeds; skipping poll.")
        return
    log.info("Polling %d RSS feed(s)...", len(feeds))
    for feed_url in feeds:
        try:
            feed = await asyncio.to_thread(feedparser.parse, feed_url)
            source_name = feed.feed.get("title", feed_url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                full_text = f"{title}\n{summary}"
                post_id = make_id(full_text)
                if not db.is_new(post_id, source_name):
                    continue
                if not is_relevant(full_text, keywords):
                    continue
                body = (
                    f"{title}\n\n"
                    f"{summary[:300]}{'...' if len(summary) > 300 else ''}\n\n"
                    f"🔗 {link}"
                )
                await send_post(client, body, source_name)
                await asyncio.sleep(1)
        except Exception as e:
            log.error("Error polling RSS %s: %s", feed_url, e)


# ─────────────────────────────────────────────
# DIGEST GENERATION (LLM)
# ─────────────────────────────────────────────

DIGEST_SYSTEM_PROMPT = (
    "You are an OSINT editor writing a daily briefing. Given a list of "
    "raw posts collected over the last 24 hours, produce a concise daily "
    "digest of the most important developments. Group related items under "
    "short headings. Be factual, neutral, and avoid speculation. Keep the "
    "total length under 700 words. Do not invent facts that are not in the "
    "supplied posts; if a post is unclear, omit it."
)


def _build_llm_client() -> OpenAI:
    cfg = config.llm()
    if not cfg.api_key:
        raise RuntimeError(
            f"API key for LLM provider {cfg.provider!r} not set in .env"
        )
    return OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)


def _summarise_sync(posts: list, day: str) -> str:
    cfg = config.llm()
    client = _build_llm_client()
    bullet_lines = []
    for p in posts:
        snippet = (p["text"] or "").strip()
        if len(snippet) > 500:
            snippet = snippet[:500].rstrip() + "…"
        bullet_lines.append(f"- [{p['source']}] {snippet}")
    user_msg = (
        f"Date: {day}\n\n"
        f"Posts collected (most recent first is NOT guaranteed; treat as a set):\n"
        + "\n".join(bullet_lines)
    )
    response = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


async def generate_digest(client: TelegramClient, *, day: str | None = None) -> bool:
    """
    Build a digest of recent posted_log entries and send it through the
    standard send_post flow (so MANUAL_REVIEW is respected).

    The window is DIGEST_INTERVAL_HOURS when set, otherwise 24h.
    Returns True if a digest was produced, False if skipped.
    """
    digest_cfg = config.digest()
    window_hours = digest_cfg.interval_hours if digest_cfg.interval_hours > 0 else 24
    end = utc_now()
    start = end - timedelta(hours=window_hours)
    target_day = day or end.date().isoformat()
    posts = db.posts_for_window(start.isoformat(), end.isoformat())

    if len(posts) < DIGEST_MIN_POSTS:
        log.info("Digest skipped: only %d posts in window (min=%d)",
                 len(posts), DIGEST_MIN_POSTS)
        return False

    log.info("Generating digest for %s from %d posts...", target_day, len(posts))
    try:
        summary = await asyncio.to_thread(_summarise_sync, posts, target_day)
    except Exception:
        log.exception("LLM digest call failed")
        return False
    if not summary.strip():
        log.warning("LLM returned empty digest")
        return False

    await send_post(
        client,
        summary,
        source="Daily Digest",
        kind="digest",
        digest_of=target_day,
    )
    return True


# ─────────────────────────────────────────────
# PENDING DIGEST JOBS (dashboard trigger)
# ─────────────────────────────────────────────

async def process_pending_digest_jobs(client: TelegramClient) -> None:
    """Pick up digest_jobs rows inserted by the dashboard and run them."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, window_start, window_end FROM digest_jobs "
            "WHERE status = 'pending' ORDER BY requested_at LIMIT 1"
        ).fetchall()
        if not rows:
            return
        job = rows[0]
        conn.execute(
            "UPDATE digest_jobs SET status = 'running', started_at = ? "
            "WHERE id = ?",
            (utc_now().isoformat(), job["id"]),
        )

    posts = db.posts_for_window(job["window_start"], job["window_end"])
    target_day = (job["window_end"][:10]) if job["window_end"] else utc_now().date().isoformat()

    if len(posts) < DIGEST_MIN_POSTS:
        with db.connect() as conn:
            conn.execute(
                "UPDATE digest_jobs SET status = 'done', finished_at = ?, "
                "error = ? WHERE id = ?",
                (utc_now().isoformat(),
                 f"skipped: only {len(posts)} posts in window", job["id"]),
            )
        return

    try:
        summary = await asyncio.to_thread(_summarise_sync, posts, target_day)
    except Exception as e:
        with db.connect() as conn:
            conn.execute(
                "UPDATE digest_jobs SET status = 'failed', finished_at = ?, "
                "error = ? WHERE id = ?",
                (utc_now().isoformat(), str(e), job["id"]),
            )
        log.exception("Pending digest job failed")
        return

    if not summary.strip():
        with db.connect() as conn:
            conn.execute(
                "UPDATE digest_jobs SET status = 'failed', finished_at = ?, "
                "error = ? WHERE id = ?",
                (utc_now().isoformat(), "LLM returned empty summary", job["id"]),
            )
        return

    post_id = await send_post(
        client, summary, source="Daily Digest",
        kind="digest", digest_of=target_day,
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE digest_jobs SET status = 'done', finished_at = ?, "
            "digest_post_id = ? WHERE id = ?",
            (utc_now().isoformat(), post_id, job["id"]),
        )


# ─────────────────────────────────────────────
# REVIEW-ACTION FORWARDING (polling for dashboard approvals)
# ─────────────────────────────────────────────

async def process_review_actions(client: TelegramClient) -> None:
    """
    Forward posts the dashboard marked 'approved' to the OUTPUT_CHANNEL,
    then mark them 'sent'. Idempotent: the 'sending' claim state means
    only one poll cycle processes a given post.
    """
    cfg = config.telegram()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT post_id, text FROM posted_log "
            "WHERE status = 'approved' ORDER BY sent_at LIMIT 20"
        ).fetchall()
    for row in rows:
        post_id = row["post_id"]
        with db.connect() as conn:
            cur = conn.execute(
                "UPDATE posted_log SET status = 'sending' "
                "WHERE post_id = ? AND status = 'approved'",
                (post_id,),
            )
            if cur.rowcount == 0:
                continue
        try:
            await client.send_message(cfg.output_channel, row["text"])
            with db.connect() as conn:
                conn.execute(
                    "UPDATE posted_log SET status = 'sent' WHERE post_id = ?",
                    (post_id,),
                )
            log.info("Dashboard-approved post forwarded | post_id=%s", post_id)
        except Exception:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE posted_log SET status = 'approved' WHERE post_id = ?",
                    (post_id,),
                )
            log.exception("Forward failed | post_id=%s", post_id)


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

async def daily_digest_job(client: TelegramClient) -> None:
    await generate_digest(client)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def main():
    config.require_secrets()
    db.init_db()

    log.info("Connecting to Telegram...")
    client = TelegramClient(
        config.telegram().session_name,
        config.telegram().api_id,
        config.telegram().api_hash,
    )
    await client.start()
    me = await client.get_me()
    log.info("Connected as %s (%s)", me.username or me.first_name, me.id)

    if config.telegram().manual_review:
        register_review_handler(client)
        log.info("Review queue active → %s", config.telegram().review_channel)

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        poll_telegram_sources,
        "interval",
        seconds=config.telegram().poll_interval,
        args=[client],
        next_run_time=utc_now(),
    )
    scheduler.add_job(
        poll_rss_feeds,
        "interval",
        seconds=config.rss().poll_interval,
        args=[client],
        next_run_time=utc_now(),
    )
    if config.telegram().manual_review:
        scheduler.add_job(
            process_review_actions,
            "interval",
            seconds=30,
            args=[client],
            next_run_time=utc_now(),
        )
    scheduler.add_job(
        process_pending_digest_jobs,
        "interval",
        seconds=60,
        args=[client],
        next_run_time=utc_now() + timedelta(minutes=1),
    )
    if config.digest().interval_hours > 0:
        scheduler.add_job(
            daily_digest_job,
            "interval",
            hours=config.digest().interval_hours,
            args=[client],
            next_run_time=utc_now(),
        )
    else:
        scheduler.add_job(
            daily_digest_job,
            "cron",
            hour=config.digest().hour_utc,
            minute=config.digest().minute_utc,
            args=[client],
        )

    scheduler.start()
    log.info(
        "Scheduler running | TG=%ds | RSS=%ds | digest=%s",
        config.telegram().poll_interval,
        config.rss().poll_interval,
        (
            f"every {config.digest().interval_hours}h"
            if config.digest().interval_hours > 0
            else f"{config.digest().hour_utc:02d}:{config.digest().minute_utc:02d} UTC"
        ),
    )

    try:
        await client.run_until_disconnected()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
