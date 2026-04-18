"""Tests for :mod:`unclog.scan.mcp_probe`.

Probe behaviour is exercised with real subprocesses — Python one-liners
stand in for MCP servers so we don't pull external binaries into the
test suite. Each subprocess reads JSON-RPC lines from stdin and writes
the expected responses to stdout so the probe sees a realistic
handshake.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from unclog.scan.config import McpServer
from unclog.scan.mcp_probe import (
    ProbeResult,
    _build_env,
    probe_all,
    probe_server,
)


def _fake_server_script(tools_payload: str) -> str:
    """Return a Python one-liner that behaves like a stdio MCP server.

    It reads three JSON-RPC lines (initialize, initialized notification,
    tools/list), then writes two responses — one for initialize, one
    for tools/list. After the responses are flushed it loops on stdin
    so the probe sees the "server is still running" shape of a real
    MCP server.
    """
    return (
        "import sys, json;\n"
        "def _r():\n"
        "    return sys.stdin.readline()\n"
        "init = json.loads(_r())\n"
        "_ = _r()  # initialized notification\n"
        "list_req = json.loads(_r())\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],"
        "'result':{'protocolVersion':'2024-11-05','capabilities':{}}}) + '\\n')\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':list_req['id'],"
        f"'result':{{'tools':{tools_payload}}}}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "# stay alive until killed by probe teardown\n"
        "try:\n"
        "    while True:\n"
        "        line = sys.stdin.readline()\n"
        "        if not line: break\n"
        "except Exception:\n"
        "    pass\n"
    )


def _writable_server(tmp_path: Path, tools_payload: str) -> McpServer:
    script = tmp_path / "server.py"
    script.write_text(_fake_server_script(tools_payload), encoding="utf-8")
    return McpServer(
        name="fake",
        command=sys.executable,
        args=(str(script),),
    )


def test_probe_server_success_counts_tools_schema(tmp_path: Path) -> None:
    server = _writable_server(
        tmp_path,
        '[{"name":"echo","description":"echoes back","inputSchema":{"type":"object"}},'
        '{"name":"ping","description":"health check","inputSchema":{"type":"object"}}]',
    )
    result = probe_server(server, timeout=5.0)
    assert isinstance(result, ProbeResult)
    assert result.ok is True
    assert result.tool_count == 2
    assert result.tools_tokens is not None
    assert result.tools_tokens > 0
    assert result.error is None
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_probe_server_handles_missing_command() -> None:
    server = McpServer(
        name="ghost",
        command="/nonexistent/path/to/absolutely/nothing",
    )
    result = probe_server(server, timeout=2.0)
    assert result.ok is False
    assert result.tool_count is None
    assert result.tools_tokens is None
    assert "command not found" in (result.error or "")


def test_probe_server_handles_missing_command_field() -> None:
    server = McpServer(name="empty", command=None)
    result = probe_server(server, timeout=2.0)
    assert result.ok is False
    assert "no command configured" in (result.error or "")


def test_probe_server_captures_stderr_on_crash(tmp_path: Path) -> None:
    """Server that prints to stderr and exits immediately → ok=False, stderr captured."""
    script = tmp_path / "crash.py"
    script.write_text(
        "import sys; sys.stderr.write('boom: missing config\\n'); sys.exit(1)\n",
        encoding="utf-8",
    )
    server = McpServer(
        name="crashy",
        command=sys.executable,
        args=(str(script),),
    )
    result = probe_server(server, timeout=3.0)
    assert result.ok is False
    assert "boom" in result.stderr_tail


def test_probe_server_timeout_on_unresponsive(tmp_path: Path) -> None:
    """Server that reads stdin forever without replying → timeout, ok=False."""
    script = tmp_path / "silent.py"
    script.write_text(
        "import sys\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if not line: break\n",
        encoding="utf-8",
    )
    server = McpServer(
        name="silent",
        command=sys.executable,
        args=(str(script),),
    )
    result = probe_server(server, timeout=0.5)
    assert result.ok is False
    assert "no tools/list response" in (result.error or "")


def test_probe_server_exits_early_when_response_arrives(tmp_path: Path) -> None:
    """Probe should return well before the 5s timeout on a fast server."""
    server = _writable_server(tmp_path, "[]")
    result = probe_server(server, timeout=5.0)
    assert result.ok is True
    # A healthy local probe should finish in well under 2 seconds.
    assert result.duration_ms is not None
    assert result.duration_ms < 2500


def test_probe_all_runs_serially_and_indexes_by_name(tmp_path: Path) -> None:
    ok = _writable_server(tmp_path, "[]")
    bad = McpServer(name="bad", command="/bin/does_not_exist_please")
    results = probe_all({"ok": ok, "bad": bad}, timeout=3.0)
    assert set(results.keys()) == {"ok", "bad"}
    assert results["ok"].ok is True
    assert results["bad"].ok is False


def test_probe_all_empty_servers() -> None:
    results = probe_all({}, timeout=1.0)
    assert dict(results) == {}


def test_build_env_whitelists_essential_vars(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/tom")
    monkeypatch.setenv("SECRET_API_KEY", "should-not-leak")
    env = _build_env({})
    assert env.get("PATH") == "/usr/bin"
    assert env.get("HOME") == "/home/tom"
    assert "SECRET_API_KEY" not in env


def test_build_env_layers_server_env_over_whitelist(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _build_env({"MCP_TOKEN": "xyz", "PATH": "/custom/bin"})
    # Server-declared env wins — the server may need a narrowed PATH.
    assert env["PATH"] == "/custom/bin"
    assert env["MCP_TOKEN"] == "xyz"


def test_probe_result_to_json_round_trips() -> None:
    r = ProbeResult(name="x", ok=True, tool_count=3, tools_tokens=150, duration_ms=120)
    payload = r.to_json()
    assert payload["name"] == "x"
    assert payload["ok"] is True
    assert payload["tool_count"] == 3
    assert payload["tools_tokens"] == 150


# Sanity: the module must not accidentally inherit the caller's environment.
def test_probe_does_not_leak_parent_env_vars(tmp_path: Path) -> None:
    """Fake server echoes a secret env var back on stderr; probe must not see it."""
    os.environ["UNCLOG_TEST_SECRET"] = "leak-me"
    try:
        script = tmp_path / "env_echo.py"
        script.write_text(
            "import os, sys\n"
            "sys.stderr.write('SECRET=' + os.environ.get('UNCLOG_TEST_SECRET','absent') + '\\n')\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        server = McpServer(
            name="echo",
            command=sys.executable,
            args=(str(script),),
        )
        result = probe_server(server, timeout=2.0)
    finally:
        del os.environ["UNCLOG_TEST_SECRET"]
    assert result.ok is False
    assert "SECRET=absent" in result.stderr_tail
