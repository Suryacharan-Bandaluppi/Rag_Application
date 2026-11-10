"""
Week 9 — minimal MCP client for the stdio transport (standard library only).

Speaks newline-delimited JSON-RPC 2.0 to a server subprocess:
    initialize -> notifications/initialized -> tools/list | resources/* | tools/call

If `wire` is a list, every raw line sent or received is appended to it
verbatim, so the exact exchange can be captured for inspection.

No model is called here: this is plumbing between the host and a server.
"""

import json
import os
import queue
import subprocess
import threading
import time

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO      = {"name": "week9-claims-agent", "version": "1.0.0"}


class MCPError(Exception):
    """A JSON-RPC error response from the server."""

    def __init__(self, code, message):
        super().__init__(f"MCP error {code}: {message}")
        self.code, self.message = code, message


class StdioMCPClient:
    def __init__(self, name, command, args=(), env=None, cwd=None,
                 stderr_path=None, wire=None):
        self.name        = name
        self.command     = [command, *args]
        self.env         = env or {}
        self.cwd         = cwd
        self.stderr_path = stderr_path
        self.wire        = wire
        self.proc        = None
        self.init_result = None
        self._next_id    = 0
        self._lines      = queue.Queue()
        self._stderr     = None

    # ── process lifecycle ─────────────────────────────────────────────────────

    def start(self):
        env = dict(os.environ)
        env.update(self.env)
        if self.stderr_path:
            self._stderr = open(self.stderr_path, "ab")
        self.proc = subprocess.Popen(
            self.command, cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr or subprocess.DEVNULL,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        return self

    def _read_stdout(self):
        for raw in self.proc.stdout:
            self._lines.put(raw.decode("utf-8").rstrip("\n"))
        self._lines.put(None)                       # EOF sentinel

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        if self._stderr:
            self._stderr.close()

    # ── JSON-RPC ──────────────────────────────────────────────────────────────

    def _log(self, direction, line):
        if self.wire is not None:
            self.wire.append({"server": self.name, "direction": direction,
                              "t": round(time.time(), 3), "raw": line})

    def _write(self, msg: dict):
        line = json.dumps(msg)
        self._log("client->server", line)
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def request(self, method, params=None, timeout=30.0):
        self._next_id += 1
        mid = self._next_id
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"{self.name}: no response to {method} in {timeout}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise ConnectionError(f"{self.name}: server exited during {method}")
            if not line.strip():
                continue
            self._log("server->client", line)
            reply = json.loads(line)
            if "method" in reply:                    # server-initiated message
                if "id" in reply:                    # a request: we support only ping
                    if reply["method"] == "ping":
                        self._write({"jsonrpc": "2.0", "id": reply["id"], "result": {}})
                    else:
                        self._write({"jsonrpc": "2.0", "id": reply["id"], "error": {
                            "code": -32601, "message": f"Method not found: {reply['method']}"}})
                continue                             # notifications are ignored
            if reply.get("id") != mid:
                continue
            if "error" in reply:
                raise MCPError(reply["error"].get("code"), reply["error"].get("message"))
            return reply.get("result", {})

    # ── MCP methods ───────────────────────────────────────────────────────────

    def initialize(self):
        self.init_result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities":    {},
            "clientInfo":      CLIENT_INFO,
        })
        self.notify("notifications/initialized")
        return self.init_result

    def _paged(self, method, key):
        items, cursor = [], None
        while True:
            res = self.request(method, {"cursor": cursor} if cursor else None)
            items.extend(res.get(key, []))
            cursor = res.get("nextCursor")
            if not cursor:
                return items

    def list_tools(self):
        return self._paged("tools/list", "tools")

    def list_resources(self):
        return self._paged("resources/list", "resources")

    def read_resource(self, uri):
        return self.request("resources/read", {"uri": uri})

    def call_tool(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})
