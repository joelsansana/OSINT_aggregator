"""
CLI for managing OSINT Aggregator resources.

Two modes:

  1. One-shot (local). Run a single command and exit. No authentication —
     the CLI runs against the same SQLite DB the bot and dashboard use.

       python cli.py keyword add breaking
       python cli.py telegram list
       python cli.py rss add https://example.com/feed.xml
       python cli.py status

  2. Serve. Start a line-based TCP REPL on CLI_HOST:CLI_PORT. The first
     line from a client must be the CLI_TOKEN from .env; subsequent lines
     are commands. Designed for `nc`, scripting, or SSH-tunneled remote
     access.

       python cli.py serve
       nc 127.0.0.1 8765
       > secret-token-from-env
       OK
       > keyword add breaking
       OK
       > keyword list
       OK
       breaking

     Output protocol: every command returns one or more lines. The first
     line is `OK` (success) or `ERR: <message>` (failure). List commands
     emit one item per subsequent line. Responses are terminated by an
     empty line so a client can frame them reliably.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import secrets
import sys
from dataclasses import dataclass

import config
import db

log = logging.getLogger("cli")

TELEGRAM_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

MAX_LINE_LEN = 4096  # mirrors Telegram's per-message cap; prevents buffer abuse.

# ── command result ───────────────────────────────────────────────────

@dataclass
class CommandResult:
    ok: bool
    lines: list[str]

    @classmethod
    def ok_msg(cls, *lines: str) -> "CommandResult":
        return cls(ok=True, lines=list(lines))

    @classmethod
    def err(cls, msg: str) -> "CommandResult":
        return cls(ok=False, lines=[msg])


def _wire(result: CommandResult) -> bytes:
    """Format a result for the wire / one-shot stdout.

    OK / ERR: <msg>\n<data line>\n<data line>\n\n
    The trailing blank line terminates the response.
    """
    status = "OK" if result.ok else f"ERR: {result.lines[0]}"
    body = result.lines[1:] if not result.ok else result.lines
    out = status + "".join(f"\n{line}" for line in body) + "\n\n"
    return out.encode("utf-8")


# ── command parsing + dispatch ──────────────────────────────────────

def execute(line: str) -> CommandResult:
    """Parse and execute a single command line. Pure function over `db`."""
    if not line or not line.strip():
        return CommandResult.err("empty command")
    parts = line.strip().split(maxsplit=1)
    verb = parts[0].lower()
    arg_str = parts[1] if len(parts) > 1 else ""
    rest = arg_str.split() if arg_str else []

    if verb in ("help", "?"):
        return CommandResult.ok_msg(
            "Commands:",
            "  help",
            "  status",
            "  keyword {add|remove|enable|disable|list} [value]",
            "  telegram {add|remove|enable|disable|list} [value]",
            "  rss {add|remove|enable|disable|list} [value]",
            "  quit",
        )

    if verb == "quit" or verb == "exit":
        return CommandResult.ok_msg("bye")

    if verb == "status":
        db.init_db()
        with db.connect() as conn:
            kw_total = conn.execute("SELECT COUNT(*) AS n FROM keywords").fetchone()["n"]
            kw_on = conn.execute(
                "SELECT COUNT(*) AS n FROM keywords WHERE enabled = 1"
            ).fetchone()["n"]
            tg_total = conn.execute(
                "SELECT COUNT(*) AS n FROM telegram_sources"
            ).fetchone()["n"]
            tg_on = conn.execute(
                "SELECT COUNT(*) AS n FROM telegram_sources WHERE enabled = 1"
            ).fetchone()["n"]
            rss_total = conn.execute("SELECT COUNT(*) AS n FROM rss_feeds").fetchone()["n"]
            rss_on = conn.execute(
                "SELECT COUNT(*) AS n FROM rss_feeds WHERE enabled = 1"
            ).fetchone()["n"]
            posts = conn.execute("SELECT COUNT(*) AS n FROM posted_log").fetchone()["n"]
        return CommandResult.ok_msg(
            f"posts_logged: {posts}",
            f"keywords: {kw_on} enabled ({kw_total} total)",
            f"telegram_sources: {tg_on} enabled ({tg_total} total)",
            f"rss_feeds: {rss_on} enabled ({rss_total} total)",
            f"db: {config.DB_PATH}",
        )

    resource_map = {
        "keyword": _kw,
        "keywords": _kw,
        "telegram": _tg,
        "tg": _tg,
        "rss": _rss,
        "feed": _rss,
        "feeds": _rss,
    }
    handler = resource_map.get(verb)
    if handler is None:
        return CommandResult.err(
            f"unknown resource {verb!r}. Try: help"
        )

    if not rest:
        return CommandResult.err(
            f"{verb} requires a subcommand. Try: {verb} list"
        )
    sub = rest[0].lower()
    value = " ".join(rest[1:]).strip() if len(rest) > 1 else ""
    return handler(sub, value)


def _kw(sub: str, value: str) -> CommandResult:
    if sub == "list":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT keyword, enabled FROM keywords ORDER BY added_at"
            ).fetchall()
        lines = [r["keyword"] if r["enabled"] else f"-{r['keyword']}" for r in rows]
        return CommandResult.ok_msg(*lines)
    if not value:
        return CommandResult.err(f"keyword {sub} requires a value")
    if sub == "add":
        candidate = value.strip().lower()
        if len(candidate) > 50:
            return CommandResult.err("keyword must be <= 50 chars")
        if not db.add_keyword(candidate):
            return CommandResult.err(f"keyword '{candidate}' already exists")
        return CommandResult.ok_msg(f"added '{candidate}'")
    if sub == "remove" or sub == "delete":
        db.delete_keyword(value)
        return CommandResult.ok_msg(f"removed '{value.strip().lower()}'")
    if sub == "enable":
        db.set_keyword_enabled(value, True)
        return CommandResult.ok_msg(f"enabled '{value.strip().lower()}'")
    if sub == "disable":
        db.set_keyword_enabled(value, False)
        return CommandResult.ok_msg(f"disabled '{value.strip().lower()}'")
    return CommandResult.err(f"unknown keyword subcommand '{sub}'")


def _tg(sub: str, value: str) -> CommandResult:
    if sub == "list":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT channel, enabled FROM telegram_sources ORDER BY added_at"
            ).fetchall()
        lines = [
            f"@{r['channel']}" if r["enabled"] else f"-@{r['channel']}"
            for r in rows
        ]
        return CommandResult.ok_msg(*lines)
    if not value:
        return CommandResult.err(f"telegram {sub} requires a channel username")
    candidate = value.strip().lstrip("@").lower()
    if sub == "add":
        if not TELEGRAM_CHANNEL_RE.match(candidate):
            return CommandResult.err(
                "invalid channel name (4–32 chars, letters/digits/_, must start with a letter)"
            )
        if not db.add_telegram_source(candidate):
            return CommandResult.err(f"channel '{candidate}' already exists")
        return CommandResult.ok_msg(f"added @{candidate}")
    if sub == "remove" or sub == "delete":
        db.delete_telegram_source(candidate)
        return CommandResult.ok_msg(f"removed @{candidate}")
    if sub == "enable":
        db.set_telegram_source_enabled(candidate, True)
        return CommandResult.ok_msg(f"enabled @{candidate}")
    if sub == "disable":
        db.set_telegram_source_enabled(candidate, False)
        return CommandResult.ok_msg(f"disabled @{candidate}")
    return CommandResult.err(f"unknown telegram subcommand '{sub}'")


def _rss(sub: str, value: str) -> CommandResult:
    if sub == "list":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT url, enabled FROM rss_feeds ORDER BY added_at"
            ).fetchall()
        lines = [r["url"] if r["enabled"] else f"-{r['url']}" for r in rows]
        return CommandResult.ok_msg(*lines)
    if not value:
        return CommandResult.err(f"rss {sub} requires a URL")
    url = value.strip()
    if sub == "add":
        if not url.startswith(("http://", "https://")):
            return CommandResult.err("URL must start with http:// or https://")
        if not db.add_rss_feed(url):
            return CommandResult.err(f"feed '{url}' already exists")
        return CommandResult.ok_msg(f"added {url}")
    if sub == "remove" or sub == "delete":
        db.delete_rss_feed(url)
        return CommandResult.ok_msg(f"removed {url}")
    if sub == "enable":
        db.set_rss_feed_enabled(url, True)
        return CommandResult.ok_msg(f"enabled {url}")
    if sub == "disable":
        db.set_rss_feed_enabled(url, False)
        return CommandResult.ok_msg(f"disabled {url}")
    return CommandResult.err(f"unknown rss subcommand '{sub}'")


# ── one-shot CLI ─────────────────────────────────────────────────────

def run_oneshot(argv: list[str]) -> int:
    """Run a single command and print its result. Returns process exit code."""
    db.init_db()
    line = " ".join(argv).strip()
    if not line or line in ("-h", "--help"):
        print(
            "Usage: python cli.py <resource> <action> [value]\n"
            "       python cli.py serve [--host HOST] [--port PORT]\n"
            "       python cli.py status | help\n"
            "\n"
            "Resources: keyword, telegram, rss\n"
            "Actions:   add, remove, enable, disable, list"
        )
        return 0 if line in ("-h", "--help") else 1
    result = execute(line)
    sys.stdout.buffer.write(_wire(result))
    return 0 if result.ok else 1


# ── TCP server ───────────────────────────────────────────────────────

class _ProtocolError(Exception):
    pass


async def _read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readuntil(b"\n")
    if len(line) > MAX_LINE_LEN:
        raise _ProtocolError("line too long")
    return line.rstrip(b"\r\n").decode("utf-8", errors="replace")


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_token: str,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        auth_line = await asyncio.wait_for(_read_line(reader), timeout=10.0)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, _ProtocolError) as e:
        log.info("auth timeout/bad input from %s: %s", peer, e)
        writer.close()
        return
    if not secrets.compare_digest(auth_line, expected_token):
        log.warning("auth failure from %s", peer)
        writer.close()
        return
    log.info("client connected: %s", peer)
    try:
        while True:
            try:
                line = await _read_line(reader)
            except asyncio.IncompleteReadError:
                break
            except _ProtocolError:
                writer.write(_wire(CommandResult.err("line too long")))
                await writer.drain()
                continue
            if not line:
                continue
            if line.strip().lower() in ("quit", "exit"):
                writer.write(_wire(CommandResult.ok_msg("bye")))
                await writer.drain()
                break
            result = execute(line)
            writer.write(_wire(result))
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        log.info("client disconnected: %s", peer)
        writer.close()


async def serve(host: str, port: int, token: str) -> None:
    if not token:
        raise RuntimeError(
            "CLI_TOKEN is not set in .env — refusing to start an unauthenticated server."
        )
    db.init_db()
    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, token),
        host=host,
        port=port,
    )
    log.info("CLI server listening on %s:%d", host, port)
    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()


# ── entry point ──────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="Manage OSINT Aggregator resources from the CLI.",
    )
    p.add_argument(
        "--bind",
        help="Host:port for serve mode (overrides CLI_HOST/CLI_PORT).",
    )
    sub = p.add_subparsers(dest="mode")

    p_serve = sub.add_parser("serve", help="Run the CLI TCP server.")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)

    sub.add_parser("status", help="Print a quick status summary.")
    sub.add_parser("help", help="Print available commands.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = _build_parser()
    # Allow the legacy zero-arg form (`python cli.py keyword add foo`).
    args_rest = argv if argv is not None else sys.argv[1:]
    if args_rest and args_rest[0] in ("serve", "status", "help", "-h", "--help"):
        args = parser.parse_args(args_rest)
    else:
        return run_oneshot(args_rest)

    if args.mode == "status":
        db.init_db()
        result = execute("status")
        sys.stdout.buffer.write(_wire(result))
        return 0 if result.ok else 1
    if args.mode == "help":
        result = execute("help")
        sys.stdout.buffer.write(_wire(result))
        return 0

    if args.mode == "serve":
        cfg = config.cli()
        host = args.host or cfg.host
        port = args.port or cfg.port
        if args.bind:
            h, p = args.bind.rsplit(":", 1)
            host, port = h, int(p)
        try:
            asyncio.run(serve(host, port, cfg.token))
        except KeyboardInterrupt:
            log.info("shutting down")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())