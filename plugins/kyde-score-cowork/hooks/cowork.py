#!/usr/bin/env python3
"""Kyde Score hook for Claude Cowork (plugin kyde-score).

Cowork tasks run in Anthropic's cloud (Linux) and export no telemetry a
user can point at Kyde, so this hook reports the calls itself — the way
Claude Code's own OTel exporter does — and then runs the unchanged
Claude Code warn-hook (hook.py, shipped next to this file) so the agent
gets the same one-line warning when its score deviates.

One file, every event, told apart by hook_event_name on stdin:

- SessionStart: flush leftovers, tell Claude (context) and the human
  (status line) whether Kyde is watching, under which agent name, and
  where a key would go. The skill reads that line.
- UserPromptSubmit: a new task id (prompt.id) — Kyde counts one run per
  user prompt, like Claude Code (KYDE-418).
- PreToolUse: remember when the call started (for duration_ms).
- PostToolUse / PostToolUseFailure: one tool_result record, buffered;
  then hook.py's warning, if any.
- Stop / SessionEnd: flush the buffer.

What leaves the machine: tool name, success, duration, session id, task
id, sequence number, time. Never inputs, outputs, prompts or paths.

Key, never in the plugin: the plugin's sensitive user setting
(CLAUDE_PLUGIN_OPTION_KYDE_KEY), else KYDE_KEY / the OTel header in the
environment, else the key file the kyde-score skill writes into the
plugin's data directory. No key: the hook stays silent apart from the
SessionStart status line.

Rules (as hook.py): never break or slow the agent — 3 s network
timeout, at most one ingest request per FLUSH_S (the first record of a
session goes out at once, so setup can confirm the first run), any
failure exits 0. Records that could not be sent stay buffered and go
with the next flush (capped at MAX_BUFFER).

Test seams: KYDE_COWORK_SEND_FILE appends each payload to a file
instead of the network; KYDE_HOOK_SCORES_FILE (hook.py) reads scores
from a file.
"""

import contextlib
import glob
import io
import json
import os
import platform
import sys
import time
import uuid
import urllib.request

FLUSH_S = 5.0
MAX_BATCH = 20
MAX_BUFFER = 500
KEEP_S = 7 * 86400
DEFAULT_AGENT = "cowork"
KEY_FILE = "kyde.json"

HERE = os.path.dirname(os.path.abspath(__file__))


def _option(name):
    for var in (f"CLAUDE_PLUGIN_OPTION_{name.upper()}",
                f"CLAUDE_PLUGIN_OPTION_{name}"):
        val = (os.environ.get(var) or "").strip()
        if val and not val.startswith("${"):
            return val
    return None


def data_dir():
    return (os.environ.get("CLAUDE_PLUGIN_DATA")
            or os.path.join(os.environ.get("XDG_CONFIG_HOME")
                            or os.path.expanduser("~/.config"), "kyde"))


def key_file():
    return os.path.join(data_dir(), KEY_FILE)


def _saved():
    try:
        with open(key_file()) as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _from_headers(headers):
    for part in (headers or "").split(","):
        part = part.strip()
        if part.startswith("x-kyde-key="):
            return part[len("x-kyde-key="):]
    return None


def resolve_key():
    """(key, where it came from) — never the key itself in any output."""
    if _option("kyde_key"):
        return _option("kyde_key"), "plugin setting"
    env = (_from_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"))
           or (os.environ.get("KYDE_KEY") or "").strip())
    if env:
        return env, "environment"
    saved = str(_saved().get("key") or "").strip()
    if saved:
        return saved, "key file"
    return None, None


def agent_name():
    name = (_option("agent_name") or os.environ.get("KYDE_AGENT_NAME")
            or str(_saved().get("agent_name") or "") or DEFAULT_AGENT)
    name = "".join(c for c in name.strip() if c.isalnum() or c in "-_.")
    return name[:80] or DEFAULT_AGENT


def endpoint():
    return (os.environ.get("KYDE_ENDPOINT") or "https://score.kyde.com").rstrip("/")


def _safe(session_id):
    return "".join(c for c in str(session_id or "")
                   if c.isalnum() or c in "-_")[:80]


def _state_path(sid):
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME")
                         or os.path.expanduser("~/.cache"), "kyde")
    os.makedirs(cache, exist_ok=True)
    return os.path.join(cache, f"cowork-{sid or 'nosession'}.json")


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _prune(now):
    cache = os.path.dirname(_state_path(""))
    for path in glob.glob(os.path.join(cache, "cowork-*.json")):
        try:
            if now - os.path.getmtime(path) > KEEP_S:
                os.remove(path)
        except OSError:
            pass


def _attr(k, v):
    return {"key": k, "value": {"stringValue": str(v)}}


def failed(event, data):
    if event == "PostToolUseFailure":
        return True
    resp = data.get("tool_response")
    return isinstance(resp, dict) and bool(resp.get("is_error") or resp.get("error"))


def make_record(state, data, event, now):
    state["seq"] = int(state.get("seq", 0)) + 1
    attrs = [
        _attr("event.name", "tool_result"),
        _attr("event.timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))),
        _attr("event.sequence", state["seq"]),
        _attr("session.id", data.get("session_id") or "unknown"),
        _attr("tool_name", data.get("tool_name") or "unknown"),
        _attr("success", "false" if failed(event, data) else "true"),
    ]
    if state.get("prompt_id"):
        attrs.append(_attr("prompt.id", state["prompt_id"]))
    tuid = str(data.get("tool_use_id") or "")
    if tuid:
        attrs.append(_attr("tool_use_id", tuid))
        start = (state.get("starts") or {}).pop(tuid, None)
        if start is not None:
            attrs.append(_attr("duration_ms", max(0, int((now - start) * 1000))))
    return {"attributes": attrs}


def payload(name, records):
    return {"resourceLogs": [{
        "resource": {"attributes": [_attr("service.name", name)]},
        "scopeLogs": [{"logRecords": records}]}]}


def send(key, body):
    """POST one OTLP/JSON batch; True when the service took it."""
    path = os.environ.get("KYDE_COWORK_SEND_FILE")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps({"key": key, "body": body}) + "\n")
        return True
    req = urllib.request.Request(
        endpoint() + "/v1/logs", json.dumps(body).encode(),
        {"content-type": "application/json", "x-kyde-key": key})
    with urllib.request.urlopen(req, timeout=3) as resp:
        resp.read()
    return True


def flush(state, key, name, now, force=False):
    buf = state.get("buffer") or []
    if not buf:
        return
    if not force and len(buf) < MAX_BATCH and now - state.get("flushed", 0) < FLUSH_S:
        return
    state["flushed"] = now
    try:
        while buf:
            send(key, payload(name, buf[:MAX_BATCH]))
            buf = buf[MAX_BATCH:]
            state["sent"] = int(state.get("sent", 0)) + 1
    except Exception:
        pass  # keep what is left for the next flush
    state["buffer"] = buf[-MAX_BUFFER:]


def warn(key, name, event, sid):
    """Run the Claude Code warn-hook (hook.py, unchanged) for this agent."""
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"x-kyde-key={key}"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint()
    os.environ["OTEL_SERVICE_NAME"] = name
    sys.path.insert(0, HERE)
    import hook  # noqa: E402  (shipped next to this file)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            hook.main(event, sid)
        except Exception:
            pass
    text = out.getvalue().strip()
    return json.loads(text) if text else None


def session_status(key, where, name, sid):
    env = "cloud" if not os.path.exists("/Applications") else "local"
    if key:
        human = (f"Kyde: watching this task as agent '{name}' "
                 f"(tool names and timing only, never content).")
        ctx = (f"Kyde plugin: active. session_id={sid}, agent={name}, "
               f"key from {where}, env={env}/{platform.system()}. "
               "Tool calls in this task are reported to Kyde Score as "
               "metadata only. Normal chat without tool calls is not seen.")
    else:
        human = "Kyde: not watching yet, no key. Ask Claude to 'set up Kyde'."
        ctx = (f"Kyde plugin: installed but no key, nothing is reported. "
               f"session_id={sid}, agent={name}, env={env}/{platform.system()}, "
               f"key_file={key_file()}. Only if the user asks to set up Kyde, "
               "follow the kyde-score skill.")
    return {"systemMessage": human,
            "hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": ctx}}


def main(data):
    event = data.get("hook_event_name") or "PostToolUse"
    sid = _safe(data.get("session_id"))
    key, where = resolve_key()
    name = agent_name()
    now = time.time()
    path = _state_path(sid)
    state = _load(path)
    if not state:
        _prune(now)
    out = None

    if event == "SessionStart":
        out = session_status(key, where, name, sid)
        if key:
            flush(state, key, name, now, force=True)
    elif not key:
        return None
    elif event == "UserPromptSubmit":
        state["prompt_id"] = uuid.uuid4().hex
    elif event == "PreToolUse":
        tuid = str(data.get("tool_use_id") or "")
        if tuid:
            starts = state.setdefault("starts", {})
            starts[tuid] = now
            if len(starts) > 200:  # calls that never finished
                for old in sorted(starts, key=starts.get)[:-200]:
                    starts.pop(old)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        state.setdefault("buffer", []).append(make_record(state, data, event, now))
        flush(state, key, name, now)
        out = warn(key, name, "PostToolUse", sid)
        if out and "hookSpecificOutput" in out:
            out["hookSpecificOutput"]["hookEventName"] = event
    elif event in ("Stop", "SessionEnd"):
        flush(state, key, name, now, force=True)

    _save(path, state)
    return out


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    try:
        result = main(data if isinstance(data, dict) else {})
        if result:
            print(json.dumps(result))
    except Exception:
        pass  # a broken hook must never break the agent
