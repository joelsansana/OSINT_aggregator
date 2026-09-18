"""
Streamlit dashboard for the OSINT Aggregator.

Run with:
    streamlit run dashboard.py

Pages:
- Digest: filter the posted_log, render formatted posts, export CSV, kick
  off an ad-hoc digest job.
- Telegram Sources: list / toggle / add / delete channels that the bot
  monitors at runtime.
- RSS Feeds: list / toggle / add / delete RSS feeds.
- Stats: per-source counts and a daily-post chart.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import config
import db

TELEGRAM_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

st.set_page_config(
    page_title="OSINT Aggregator",
    page_icon="📡",
    layout="wide",
)


def _ensure_db() -> None:
    db.init_db()


_ensure_db()


def page_review_queue() -> None:
    st.header("📥 Review queue")
    st.caption(
        "Posts waiting for human review. Approvals are forwarded to the "
        "output channel automatically within ~30 seconds by the bot."
    )

    rows = db.list_pending_review()
    if not rows:
        st.success("Review queue is empty. ✅")
        return

    st.metric("Pending", len(rows))

    for row in rows:
        with st.container(border=True):
            cols = st.columns([3, 1, 1])
            with cols[0]:
                is_digest = bool(row["digest_of"])
                icon = "📰" if is_digest else "📡"
                kind_label = "Daily Digest" if is_digest else row["source"]
                ts = row["sent_at"][:16].replace("T", " ")
                st.markdown(f"{icon} **{kind_label}** · _{ts} UTC_")
                preview = row["text"]
                if len(preview) > 400:
                    preview = preview[:400].rstrip() + "…"
                st.text(preview)
            with cols[1]:
                if st.button(
                    "✅ Approve",
                    key=f"approve_{row['post_id']}",
                    width="stretch",
                ):
                    db.mark_review_action(row["post_id"], "approve")
                    st.toast("Approved — forwarding shortly", icon="✅")
            with cols[2]:
                if st.button(
                    "❌ Discard",
                    key=f"discard_{row['post_id']}",
                    width="stretch",
                ):
                    db.mark_review_action(row["post_id"], "discard")
                    st.toast("Discarded", icon="🗑")


def page_digest() -> None:
    st.header("📰 Digest")
    st.caption(
        "All posts the bot has handled — sent, queued for review, discarded, or generated as a digest."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        since_date = st.date_input(
            "From",
            value=(datetime.now(timezone.utc).date() - timedelta(days=7)),
            max_value=datetime.now(timezone.utc).date(),
        )
    with col2:
        until_date = st.date_input(
            "To",
            value=datetime.now(timezone.utc).date(),
            max_value=datetime.now(timezone.utc).date(),
        )
    with col3:
        source_filter = st.text_input("Source contains…", value="")
    with col4:
        status_options = ["sent", "review", "discarded", "digest", "buffered"]
        status = st.selectbox("Status", ["(all)"] + status_options)

    since_iso = f"{since_date.isoformat()}T00:00:00+00:00"
    until_iso = f"{(until_date + timedelta(days=1)).isoformat()}T00:00:00+00:00"

    rows = db.list_posts(
        since=since_iso,
        until=until_iso,
        source=source_filter.strip() or None,
        status=None if status == "(all)" else status,
        limit=1000,
    )

    st.divider()
    sub1, sub2, sub3 = st.columns([1, 1, 2])
    sub1.metric("Posts in range", len(rows))
    sub2.metric("Digests in range", sum(1 for r in rows if r["status"] == "digest"))
    with sub3:
        st.write("")
        if rows:
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(["sent_at", "status", "source", "text", "digest_of"])
            for r in rows:
                writer.writerow(
                    [r["sent_at"], r["status"], r["source"], r["text"], r["digest_of"]]
                )
            st.download_button(
                "⬇️ Export CSV",
                data=csv_buf.getvalue(),
                file_name=f"osint_digest_{since_date}_{until_date}.csv",
                mime="text/csv",
                width="stretch",
            )

    st.divider()
    st.subheader("Generate a digest now")
    gen_col1, gen_col2 = st.columns([1, 3])
    with gen_col1:
        hours = st.number_input("Window (hours)", min_value=1, max_value=168, value=24)
    with gen_col2:
        st.write("")
        st.write("")
        if st.button("🚀 Generate digest", type="primary"):
            if not config.llm().api_key:
                st.error(
                    f"API key for LLM provider {config.llm().provider!r} "
                    "is not set in .env — digests require it."
                )
            else:
                end = datetime.now(timezone.utc)
                start = end - timedelta(hours=int(hours))
                job_id = db.enqueue_digest_job(start.isoformat(), end.isoformat())
                st.success(
                    f"Job #{job_id} queued ({start:%Y-%m-%d %H:%M UTC} → "
                    f"{end:%Y-%m-%d %H:%M UTC}). "
                    "The bot will pick it up within ~60 seconds."
                )

    st.divider()
    st.subheader("Recent digest jobs")
    jobs = db.list_digest_jobs(limit=10)
    if not jobs:
        st.info("No digest jobs yet.")
    else:
        job_df = pd.DataFrame([dict(j) for j in jobs])[
            ["id", "requested_at", "status", "window_start", "window_end", "error"]
        ]
        st.dataframe(job_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Posts")
    if not rows:
        st.info("No posts match the current filters.")
        return
    for r in rows:
        badge = {
            "sent": "🟢 sent",
            "review": "🟡 review",
            "discarded": "🔴 discarded",
            "digest": "📰 digest",
            "buffered": "📦 buffered",
        }.get(r["status"], r["status"])
        with st.container(border=True):
            meta = f"**{badge}** · `{r['source']}` · {r['sent_at']}"
            if r["digest_of"]:
                meta += f" · digest of {r['digest_of']}"
            st.markdown(meta)
            st.text(r["text"])


def page_telegram_sources() -> None:
    st.header("📡 Telegram sources")
    st.caption("Channels the bot polls. Changes apply on the next poll (~60s).")

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT channel, added_at, enabled FROM telegram_sources ORDER BY added_at"
        ).fetchall()

    for r in rows:
        cols = st.columns([3, 2, 1, 1])
        cols[0].code(f"@{r['channel']}")
        cols[1].caption(f"added {r['added_at']}")
        new_enabled = cols[2].toggle(
            "on",
            value=bool(r["enabled"]),
            key=f"tg_on_{r['channel']}",
            label_visibility="collapsed",
        )
        if new_enabled != bool(r["enabled"]):
            db.set_telegram_source_enabled(r["channel"], new_enabled)
            st.rerun()
        if cols[3].button("🗑", key=f"tg_del_{r['channel']}"):
            db.delete_telegram_source(r["channel"])
            st.rerun()

    st.divider()
    st.subheader("Add a Telegram source")
    with st.form("add_tg", clear_on_submit=True):
        ch = st.text_input("Channel username (without @)", placeholder="osintdefender")
        submitted = st.form_submit_button("➕ Add")
    if submitted:
        candidate = ch.strip().lstrip("@")
        if not TELEGRAM_CHANNEL_RE.match(candidate):
            st.error(
                "Invalid channel name. Use 4–32 chars, letters/digits/underscore, must start with a letter."
            )
        elif db.add_telegram_source(candidate):
            st.success(f"Added @{candidate}.")
        else:
            st.warning(f"@{candidate} already exists.")


def page_rss_feeds() -> None:
    st.header("🗞 RSS feeds")
    st.caption(
        "RSS feeds the bot polls every 5 minutes. Changes apply on the next poll."
    )

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT url, added_at, enabled FROM rss_feeds ORDER BY added_at"
        ).fetchall()

    for r in rows:
        cols = st.columns([5, 2, 1, 1])
        cols[0].code(r["url"])
        cols[1].caption(f"added {r['added_at']}")
        new_enabled = cols[2].toggle(
            "on",
            value=bool(r["enabled"]),
            key=f"rss_on_{r['url']}",
            label_visibility="collapsed",
        )
        if new_enabled != bool(r["enabled"]):
            db.set_rss_feed_enabled(r["url"], new_enabled)
            st.rerun()
        if cols[3].button("🗑", key=f"rss_del_{r['url']}"):
            db.delete_rss_feed(r["url"])
            st.rerun()

    st.divider()
    st.subheader("Add an RSS feed")
    with st.form("add_rss", clear_on_submit=True):
        url = st.text_input("Feed URL", placeholder="https://example.com/feed.xml")
        preview = st.form_submit_button("🔍 Preview")
        submitted = st.form_submit_button("➕ Add")
    if preview:
        if not url.strip():
            st.error("Enter a URL.")
        else:
            import feedparser

            try:
                feed = feedparser.parse(url.strip())
            except Exception as e:  # noqa: BLE001
                st.error(f"Parse error: {e}")
                feed = None
            if feed is not None:
                title = feed.feed.get("title", "(no title)")
                st.write(f"**{title}**")
                for entry in feed.entries[:3]:
                    st.markdown(
                        f"- [{entry.get('title', '(no title)')}]({entry.get('link', '#')})"
                    )
    if submitted:
        if not url.strip().startswith(("http://", "https://")):
            st.error("URL must start with http:// or https://")
        elif db.add_rss_feed(url.strip()):
            st.success("Feed added.")
        else:
            st.warning("That feed is already in the list.")


def page_stats() -> None:
    st.header("📊 Stats")

    days = st.slider("Window (days)", min_value=1, max_value=90, value=30)
    daily = db.posts_per_day(days=days)
    daily_df = pd.DataFrame([dict(r) for r in daily])
    if not daily_df.empty:
        daily_df["day"] = pd.to_datetime(daily_df["day"])
        st.subheader("Posts per day")
        st.line_chart(daily_df, x="day", y="n")

    per_source = db.posts_per_source()
    if per_source:
        st.subheader("Posts per source")
        src_df = pd.DataFrame([dict(r) for r in per_source])
        st.bar_chart(src_df, x="source", y="n")
    else:
        st.info("No posts logged yet.")


def page_keywords() -> None:
    st.header("🔑 Keywords")
    st.caption(
        "Posts must contain at least one enabled keyword (case-insensitive "
        "substring match). Changes apply on the next poll (~60s)."
    )

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT keyword, added_at, enabled FROM keywords ORDER BY added_at"
        ).fetchall()

    if not rows:
        st.info("No keywords yet — add one below.")

    for r in rows:
        cols = st.columns([3, 2, 1, 1])
        cols[0].code(r["keyword"])
        cols[1].caption(f"added {r['added_at']}")
        new_enabled = cols[2].toggle(
            "on",
            value=bool(r["enabled"]),
            key=f"kw_on_{r['keyword']}",
            label_visibility="collapsed",
        )
        if new_enabled != bool(r["enabled"]):
            db.set_keyword_enabled(r["keyword"], new_enabled)
            st.rerun()
        if cols[3].button("🗑", key=f"kw_del_{r['keyword']}"):
            db.delete_keyword(r["keyword"])
            st.rerun()

    st.divider()
    st.subheader("Add a keyword")
    with st.form("add_kw", clear_on_submit=True):
        kw = st.text_input("Keyword or phrase", placeholder="breaking")
        submitted = st.form_submit_button("➕ Add")
    if submitted:
        candidate = kw.strip().lower()
        if not candidate:
            st.error("Enter a keyword.")
        elif len(candidate) > 50:
            st.error("Keyword must be 50 characters or fewer.")
        elif db.add_keyword(candidate):
            st.success(f"Added '{candidate}'.")
        else:
            st.warning(f"'{candidate}' already exists.")


PAGES = {
    "📥 Review queue": page_review_queue,
    "📰 Digest": page_digest,
    "📡 Telegram sources": page_telegram_sources,
    "🗞 RSS feeds": page_rss_feeds,
    "🔑 Keywords": page_keywords,
    "📊 Stats": page_stats,
}


def main() -> None:
    st.sidebar.title("OSINT Aggregator")
    st.sidebar.caption(f"DB: `{config.DB_PATH}`")
    choice = st.sidebar.radio("Navigate", list(PAGES.keys()))
    PAGES[choice]()


if __name__ == "__main__":
    main()
