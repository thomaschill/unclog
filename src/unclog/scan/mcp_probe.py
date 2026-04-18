"""Opt-in live probing of MCP servers via stdio JSON-RPC.

The default unclog scan never spawns MCP servers — spec §5.4 used to
forbid it outright. ``--probe-mcps`` opts into a minimal, safety-first
probe that upgrades ``dead_mcp`` from "declared but not observed" to
"declared, started, here's the actual tools schema size (or the stderr
that explains why it failed to start)".

Safety posture:

- **Minimal env whitelist.** MCP servers are third-party binaries the
  user has configured but unclog has no relationship with. We pass only
  ``PATH``, ``HOME``, ``TMPDIR``, ``USERPROFILE``, ``SystemRoot`` from
  our environment, plus the server's own ``env`` from ``.claude.json``.
  This avoids leaking API keys from unrelated tools into servers that
  happen to ``os.environ.copy()``.

- **No ``shell=True``.** Command + args are passed as a list; no shell
  expansion. Malformed config surfaces as "failed to start" rather than
  accidental execution.

- **Serial, not parallel.** Users can have many MCP servers; parallel
  probing would fork-bomb a laptop. 5s per server, run one at a time,
  for the typical handful of servers — bounded and predictable.

- **5-second total timeout per server.** Covers spawn, initialize,
  tools/list, and teardown. Timed-out probes are killed and reported
  as failures, they don't hang the scan.

- **Stderr truncated to 500 chars.** Long traceback spam (some servers
  print megabytes on misconfig) must not drown the report.

Token counting reuses :class:`TiktokenCounter` so probe-attributed
rows use the same measurement as session-attributed ones — the two
sources are directly comparable.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from unclog.scan.config import McpServer
from unclog.scan.tokens import TiktokenCounter

# Environment variables that must survive the spawn for most servers to
# function. Everything else is stripped. Windows-specific names are
# included as a forward-looking concession even though v0.1 is
# mac/Linux-only (see ship plan).
_ENV_WHITELIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "USERPROFILE",
        "SystemRoot",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
    }
)

# Hard cap on captured stderr so a misbehaving server can't OOM the
# report. 500 chars is enough to see a Python traceback's tail or a
# node.js "Cannot find module" line.
_STDERR_TAIL_CHARS = 500

# Default per-server probe timeout. 5s covers spawn, initialize handshake,
# tools/list round-trip, and clean teardown on a busy laptop.
DEFAULT_PROBE_TIMEOUT_SEC = 5.0

# Protocol version declared in the initialize request. Must match one
# of the versions Claude Code itself sends; 2024-11-05 is the last
# widely-deployed release and matches what most third-party servers
# were built against.
_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC request id used for ``tools/list``. We read responses until
# we see one with this id and a ``result`` field, then exit the loop.
_TOOLS_LIST_REQUEST_ID = 2


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of probing a single MCP server.

    ``ok=True`` means initialize + tools/list both succeeded and we
    have a measured token count for the tools schema. ``ok=False``
    covers every failure mode: server missing on PATH, crashed on
    startup, rejected the handshake, or blew the timeout. The caller
    uses ``error`` / ``stderr_tail`` for diagnostics.
    """

    name: str
    ok: bool
    tool_count: int | None = None
    tools_tokens: int | None = None
    error: str | None = None
    stderr_tail: str = ""
    duration_ms: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "tool_count": self.tool_count,
            "tools_tokens": self.tools_tokens,
            "error": self.error,
            "stderr_tail": self.stderr_tail,
            "duration_ms": self.duration_ms,
        }


def _build_env(server_env: Mapping[str, str]) -> dict[str, str]:
    """Compose the minimal env for the subprocess.

    Starts with the whitelist drawn from ``os.environ``, then layers
    the server's declared ``env`` on top. Server-declared values win —
    servers sometimes need a narrowed ``PATH`` or a custom ``HOME``.
    """
    env: dict[str, str] = {
        key: os.environ[key] for key in _ENV_WHITELIST if key in os.environ
    }
    env.update(server_env)
    return env


def _rpc_line(req_id: int | None, method: str, params: dict[str, Any]) -> bytes:
    """Serialise one JSON-RPC frame as a newline-terminated line.

    ``req_id=None`` produces a notification (no ``id`` field) per the
    JSON-RPC 2.0 spec. stdio-transport MCP servers read one JSON
    object per line on stdin.
    """
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if req_id is not None:
        msg["id"] = req_id
    return (json.dumps(msg) + "\n").encode("utf-8")


def _drain_thread(stream: Any, out_q: queue.Queue[bytes | None]) -> None:
    """Thread target: push every line from ``stream`` into ``out_q``.

    Puts ``None`` when the stream closes so the main thread can detect
    EOF without blocking on a further read.
    """
    try:
        for line in iter(stream.readline, b""):
            out_q.put(line)
    finally:
        out_q.put(None)


def _truncate_stderr(raw: bytes | None) -> str:
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if len(text) <= _STDERR_TAIL_CHARS:
        return text
    return "..." + text[-(_STDERR_TAIL_CHARS - 3) :]


def _serialise_tools(tools: list[dict[str, Any]]) -> str:
    """Produce the canonical token-counting text for a tools list.

    Mirrors what Claude Code actually injects: the full tool schema
    array as JSON. ``separators=(",",":")`` matches the dense form
    typically seen in session JSONLs, so probe-measured counts are
    directly comparable to session-measured ones.
    """
    return json.dumps(tools, separators=(",", ":"))


def probe_server(
    server: McpServer,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SEC,
    counter: TiktokenCounter | None = None,
) -> ProbeResult:
    """Spawn a single MCP server, exchange JSON-RPC, return a :class:`ProbeResult`.

    Never raises — every failure mode collapses into ``ok=False`` with
    a descriptive ``error`` field. Callers can enumerate results and
    decide how to surface them without defensive try/except walls.
    """
    if not server.command:
        return ProbeResult(
            name=server.name,
            ok=False,
            error="no command configured",
            duration_ms=0,
        )

    argv = [server.command, *server.args]
    env = _build_env(server.env)

    started = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            shell=False,
            bufsize=0,
        )
    except FileNotFoundError:
        return ProbeResult(
            name=server.name,
            ok=False,
            error=f"command not found: {server.command}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except PermissionError:
        return ProbeResult(
            name=server.name,
            ok=False,
            error=f"permission denied executing: {server.command}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return ProbeResult(
            name=server.name,
            ok=False,
            error=f"failed to spawn: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # Background thread so we don't block on readline while the
    # server is still warming up; we can bail the moment we see our
    # tools/list response.
    stdout_q: queue.Queue[bytes | None] = queue.Queue()
    reader = threading.Thread(
        target=_drain_thread,
        args=(proc.stdout, stdout_q),
        daemon=True,
    )
    reader.start()

    try:
        assert proc.stdin is not None
        # initialize request, then the required initialized notification,
        # then tools/list. Some servers won't answer tools/list until
        # they've seen the notification.
        proc.stdin.write(
            _rpc_line(
                1,
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "unclog", "version": "0.1"},
                },
            )
        )
        proc.stdin.write(_rpc_line(None, "notifications/initialized", {}))
        proc.stdin.write(_rpc_line(_TOOLS_LIST_REQUEST_ID, "tools/list", {}))
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        # Server died before we finished writing — fall through to the
        # drain-and-report block below with stdin closed.
        error_from_stdin: str | None = f"server closed stdin: {exc}"
    else:
        error_from_stdin = None

    tools_resp: dict[str, Any] | None = None
    deadline = started + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = stdout_q.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            continue
        if line is None:
            break
        try:
            parsed = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get("id") == _TOOLS_LIST_REQUEST_ID
            and "result" in parsed
        ):
            tools_resp = parsed
            break

    # Tear the server down. Always kill — servers are long-lived, there
    # is no graceful shutdown in the MCP stdio protocol.
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        proc.kill()
    try:
        stderr_bytes = proc.stderr.read() if proc.stderr is not None else b""
    except OSError:
        stderr_bytes = b""
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass

    duration_ms = int((time.monotonic() - started) * 1000)
    stderr_tail = _truncate_stderr(stderr_bytes)

    if tools_resp is None:
        err = error_from_stdin or "no tools/list response before timeout"
        return ProbeResult(
            name=server.name,
            ok=False,
            error=err,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
        )

    result_obj = tools_resp.get("result")
    if not isinstance(result_obj, dict):
        return ProbeResult(
            name=server.name,
            ok=False,
            error="tools/list response missing 'result' object",
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
        )
    tools_list_raw = result_obj.get("tools", [])
    tools_list: list[dict[str, Any]] = (
        [t for t in tools_list_raw if isinstance(t, dict)]
        if isinstance(tools_list_raw, list)
        else []
    )

    token_counter = counter if counter is not None else TiktokenCounter()
    tokens = token_counter.count(_serialise_tools(tools_list))

    return ProbeResult(
        name=server.name,
        ok=True,
        tool_count=len(tools_list),
        tools_tokens=tokens,
        stderr_tail=stderr_tail,
        duration_ms=duration_ms,
    )


def probe_all(
    servers: Mapping[str, McpServer],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SEC,
) -> Mapping[str, ProbeResult]:
    """Probe every server in ``servers`` serially and return name -> result.

    Never raises — every server yields exactly one :class:`ProbeResult`
    so the caller can always enumerate ``servers`` and find a match.
    """
    counter = TiktokenCounter()
    results: dict[str, ProbeResult] = {}
    for name in sorted(servers):
        results[name] = probe_server(servers[name], timeout=timeout, counter=counter)
    return MappingProxyType(results)
