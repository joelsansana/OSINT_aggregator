"""Tests for cli.py — command parsing, wire format, and the TCP server."""

from __future__ import annotations

import asyncio
import socket
import threading

import pytest

import cli
import config
import db


# ── execute() ────────────────────────────────────────────────────────

def test_help_returns_ok(fresh_db):
    _db_path, _db = fresh_db
    result = cli.execute("help")
    assert result.ok is True
    assert "Commands:" in result.lines


def test_empty_command_is_err(fresh_db):
    _db_path, _db = fresh_db
    assert cli.execute("").ok is False
    assert cli.execute("   ").ok is False


def test_unknown_verb_is_err(fresh_db):
    _db_path, _db = fresh_db
    result = cli.execute("nope")
    assert result.ok is False
    assert "unknown" in result.lines[0]


# ── keyword commands ─────────────────────────────────────────────────

def test_keyword_add_and_list(fresh_db):
    _db_path, _db = fresh_db
    assert cli.execute("keyword add evacuation").ok is True
    assert cli.execute("keyword add EVACUATION2").ok is True  # lowercased
    result = cli.execute("keyword list")
    assert result.ok is True
    assert "evacuation" in result.lines
    assert "evacuation2" in result.lines


def test_keyword_add_rejects_duplicate(fresh_db):
    _db_path, _db = fresh_db
    cli.execute("keyword add evacuation")
    result = cli.execute("keyword add evacuation")
    assert result.ok is False


def test_keyword_add_rejects_empty(fresh_db):
    _db_path, _db = fresh_db
    result = cli.execute("keyword add")
    assert result.ok is False
    assert "requires a value" in result.lines[0]


def test_keyword_add_rejects_overlong(fresh_db):
    _db_path, _db = fresh_db
    result = cli.execute("keyword add " + ("x" * 60))
    assert result.ok is False
    assert "50 chars" in result.lines[0]


def test_keyword_disable_and_enable(fresh_db):
    _db_path, _db = fresh_db
    cli.execute("keyword add evacuation")
    cli.execute("keyword disable evacuation")
    lines = cli.execute("keyword list").lines
    assert "-evacuation" in lines  # disabled prefix
    assert "evacuation" not in lines
    cli.execute("keyword enable evacuation")
    lines = cli.execute("keyword list").lines
    assert "evacuation" in lines
    assert "-evacuation" not in lines


def test_keyword_remove(fresh_db):
    _db_path, _db = fresh_db
    cli.execute("keyword add evacuation")
    cli.execute("keyword remove evacuation")
    assert "evacuation" not in cli.execute("keyword list").lines


# ── telegram commands ────────────────────────────────────────────────

def test_telegram_add_validates_name(fresh_db):
    _db_path, _db = fresh_db
    assert cli.execute("telegram add newchan").ok is True
    bad = cli.execute("telegram add @bad-name!")
    assert bad.ok is False
    assert "invalid" in bad.lines[0]


def test_telegram_list_prefixes_at(fresh_db):
    _db_path, _db = fresh_db
    cli.execute("telegram add newchan")
    result = cli.execute("telegram list")
    assert result.ok is True
    assert "@newchan" in result.lines


def test_telegram_alias_works(fresh_db):
    _db_path, _db = fresh_db
    assert cli.execute("tg add freshchan").ok is True
    cli.execute("tg disable freshchan")
    lines = cli.execute("tg list").lines
    assert "-@freshchan" in lines  # disabled prefix
    assert "@freshchan" not in lines


# ── rss commands ─────────────────────────────────────────────────────

def test_rss_add_requires_http_scheme(fresh_db):
    _db_path, _db = fresh_db
    bad = cli.execute("rss add ftp://example.com/feed")
    assert bad.ok is False
    assert "http" in bad.lines[0]


def test_rss_add_and_remove(fresh_db):
    _db_path, _db = fresh_db
    assert cli.execute("rss add https://example.com/feed.xml").ok is True
    cli.execute("rss remove https://example.com/feed.xml")
    result = cli.execute("rss list")
    assert "https://example.com/feed.xml" not in result.lines


# ── status ───────────────────────────────────────────────────────────

def test_status_includes_counts(fresh_db):
    _db_path, _db = fresh_db
    cli.execute("keyword add customkw")
    result = cli.execute("status")
    assert result.ok is True
    joined = "\n".join(result.lines)
    assert "posts_logged:" in joined
    assert "keywords:" in joined
    assert "customkw" not in joined  # counts only, not the values


# ── wire format ──────────────────────────────────────────────────────

def test_wire_format_success_with_data():
    result = cli.CommandResult.ok_msg("a", "b", "c")
    out = cli._wire(result).decode()
    assert out == "OK\na\nb\nc\n\n"


def test_wire_format_success_no_data():
    out = cli._wire(cli.CommandResult.ok_msg()).decode()
    assert out == "OK\n\n"


def test_wire_format_error():
    out = cli._wire(cli.CommandResult.err("nope")).decode()
    assert out == "ERR: nope\n\n"


# ── TCP server ───────────────────────────────────────────────────────

def _free_port() -> int:
    """Ask the kernel for an unused TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_in_thread(host: str, port: int, token: str) -> threading.Thread:
    """Start the asyncio server in a background thread. Returns the thread."""
    import time

    started = threading.Event()
    error_box: list[BaseException] = []

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def go() -> None:
            try:
                server = await asyncio.start_server(
                    lambda r, w: cli._handle_client(r, w, token),
                    host=host,
                    port=port,
                )
            except OSError as e:
                error_box.append(e)
                started.set()
                return
            started.set()
            try:
                async with server:
                    await server.serve_forever()
            except asyncio.CancelledError:
                pass
            finally:
                server.close()
                await server.wait_closed()

        try:
            loop.run_until_complete(go())
        except Exception as e:  # pragma: no cover
            error_box.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    # Wait for the server to bind.
    if not started.wait(timeout=2.0):
        raise RuntimeError("server failed to start")
    if error_box:
        raise RuntimeError(f"server start error: {error_box[0]}")
    # Brief settle in case the OS still hasn't propagated the listener.
    for _ in range(20):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return t
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("server not accepting connections")


def _read_response(sock: socket.socket) -> str:
    """Read one wire response (terminated by a blank line) from `sock`."""
    buf = b""
    while b"\n\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.decode()


def test_server_rejects_bad_token(fresh_db):
    _db_path, _db = fresh_db
    port = _free_port()
    token = "secret-123"
    _run_server_in_thread("127.0.0.1", port, token)
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.sendall(b"wrong-token\n")
        # Server should close immediately.
        data = s.recv(1024)
    assert data == b""


def test_server_round_trip(fresh_db):
    _db_path, _db = fresh_db
    port = _free_port()
    token = "secret-abc"
    _run_server_in_thread("127.0.0.1", port, token)
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.settimeout(2.0)
        # The server does not respond to the auth line — it just unlocks
        # for subsequent commands. Send a command directly.
        s.sendall((token + "\n").encode())
        s.sendall(b"help\n")
        resp = _read_response(s)
        assert resp.startswith("OK\n")
        assert "Commands:" in resp

        s.sendall(b"keyword add newkey\n")
        resp = _read_response(s)
        assert "added 'newkey'" in resp

        s.sendall(b"keyword list\n")
        resp = _read_response(s)
        assert "newkey" in resp

        s.sendall(b"keyword add\n")
        resp = _read_response(s)
        assert resp.startswith("ERR:")

        s.sendall(b"quit\n")
        bye = _read_response(s)
        assert "bye" in bye