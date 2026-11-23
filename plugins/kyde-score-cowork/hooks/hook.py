#!/usr/bin/env python3
"""KYDE Score hook for Claude Code (KYDE-421).

One file, two hook events, told apart by hook_event_name on stdin:

- PostToolUse (default): after a tool call, when the score service says
  watch/deviating or a loop alarm is on record, put one line into the
  agent's context. Warn, don't block.
- PreToolUse: does nothing by default (KYDE_BLOCK=off). Kyde Score warns
  only; pause and block are not part of the service yet (Terms 1.1,
  D-054: waitlist until they open). The code paths stay for internal
  testing: KYDE_BLOCK=ask hands a call on a deviating band or loop alarm
  to the human, KYDE_BLOCK=deny denies every call while the state lasts.
  The watch band never blocks: warn-territory, not stop-territory.

Rules:
- never slow the agent down: at most one API call per THROTTLE_S across
  both events, 3 s network timeout, and any failure exits silently
  (exit 0, no output).
- warn once per new state, repeat after REMIND_S of continued deviation;
  ask once per REMIND_S; deny repeats on every call, because containment
  that lets every second call through is none. State lives in
  ~/.cache/kyde/ per agent name.

Installed by install.sh (score.kyde.com/install.sh); the key and
endpoint come from the same environment the OTel exporter uses — and,
when the environment is bare, from the env block of
~/.claude/settings.json (KYDE-435): Claude Code reads that file for its
own telemetry but does not pass its env on to hook processes, so a
settings-only setup would otherwise export fine while the hook stays
silently dead. CLAUDE_CONFIG_DIR relocates the settings file, as it
does for Claude Code itself.
Which agent: Claude Code does not pass OTEL_SERVICE_NAME to hook
processes either. The hook sends the session_id from its stdin and the
service answers with the agent whose calls carry that session.id
(session_service_name) — the one positive source of who this session
is. A score or contract is only ever attributed to that agent. Until
the session's first call has been attributed the hook stays silent
rather than guess: it never plays another named agent's score (the old
code fell back to OTEL_SERVICE_NAME, then the literal 'claude-code', and
so warned unrelated sessions with a stale 'claude-code' score, KYDE-518).
A score whose last run is older than STALE_SCORE_S is likewise not this
session's behaviour and is not shown. The name-keyed fallback remains
only when there is no session id at all. State lives per session.
Recipient check (PreToolUse, 14.11.2026): the recipient fields of the
call's tool_input (iban, to/cc/bcc/recipient/email/payee/destination,
account*, merchant*, customer, plus the amount as an order of magnitude)
are hashed HERE as SHA256(salt|value) — the collector's convention — and
only the hashes go to POST /score/v0/entities, from a forked child so
the call never waits. The salt is KYDE_SALT (env or settings env), else
a random one kept in ~/.config/kyde/salt; it never leaves the machine,
cleartext never leaves the machine. Kyde Score then warns when a
recipient falls outside the agent's learned contract.
Hold mode (P4, 21.11.2026): on the PAID plan, once a human CONFIRMED the
agent's declared contract, PreToolUse decides locally against it — the
contract arrives as salted hashes, this machine holds the salt, so the
decision needs no network call and no cleartext leaves (D-064). A call
outside the contract is denied with a one-sentence reason and reported
to POST /score/v0/holds (forked, never waits); the human approves by
mail or dashboard, an approved hold admits the exact case until it
expires, approve-and-extend lands in the next contract version. Free
plan and unconfirmed contracts: behavior exactly as before — the
default switch to holding arms only with the confirmed contract
(ADR-003 §5). max_per_run is counted server-side, not here. Failure
rules: fetching the contract fails open (hold mode simply never arms),
but once a call is judged outside the contract it STAYS held even when
the approval lookup fails — holding is the paid guarantee, and the
agent can retry. KYDE_HOLD=off is the kill switch.
Test seams: KYDE_HOOK_SCORES_FILE and KYDE_HOOK_CONTRACT_FILE read
JSON from files, KYDE_HOOK_HOLDS_FILE captures the hold report —
used by the service's test suite.
"""

import calendar
import glob
import hashlib
import json
import math
import re
import secrets
import os
import sys
import time
import urllib.parse
import urllib.request

THROTTLE_S = 30.0   # at most one scores call per agent per this window
REMIND_S = 600.0    # repeat an unchanged warning / ask after this long
KEEP_S = 7 * 86400  # per-session state files older than this are removed
STALE_SCORE_S = 86400  # a score whose last run is older than this is not
                       # about this session's behaviour (KYDE-518)


def _settings_env():
    """The env block of the user's Claude Code settings file."""
    base = (os.environ.get("CLAUDE_CONFIG_DIR")
            or os.path.expanduser("~/.claude"))
    try:
        with open(os.path.join(base, "settings.json")) as fh:
            env = json.load(fh).get("env") or {}
        return {k: str(v) for k, v in env.items()}
    except Exception:
        return {}


def _from_headers(headers):
    for part in (headers or "").split(","):
        part = part.strip()
        if part.startswith("x-kyde-key="):
            return part[len("x-kyde-key="):]
    return None


def _key():
    key = (_from_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"))
           or os.environ.get("KYDE_KEY"))
    if not key:
        env = _settings_env()
        key = (_from_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS"))
               or env.get("KYDE_KEY"))
    return key or None


def _endpoint():
    return (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            or _settings_env().get("OTEL_EXPORTER_OTLP_ENDPOINT")
            or "https://score.kyde.com")


def _fallback_name():
    return (os.environ.get("OTEL_SERVICE_NAME")
            or _settings_env().get("OTEL_SERVICE_NAME")
            or "claude-code")


def _safe(session_id):
    """A session id fit for a file name and a query string."""
    return "".join(c for c in str(session_id or "")
                   if c.isalnum() or c in "-_")[:80]


def _scores(endpoint, key, session_id=""):
    path = os.environ.get("KYDE_HOOK_SCORES_FILE")
    if path:
        with open(path) as fh:
            return json.load(fh)
    url = endpoint.rstrip("/") + "/score/v0/scores"
    if session_id:
        url += "?" + urllib.parse.urlencode({"session_id": session_id})
    req = urllib.request.Request(url, headers={"x-kyde-key": key})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.load(resp)


def pick_agent(scores, name):
    if not name:
        return None
    for a in scores.get("agents", []):
        if a.get("service_name") == name:
            return a
    return None


def _stale(agent, now):
    """True when the agent's last run is older than STALE_SCORE_S. Such a
    score describes some earlier day, not this session, so it must not be
    played as 'pause and check' (KYDE-518). Unknown recency is not stale."""
    ts = agent.get("last_seen_at") if agent else None
    if not ts:
        return False
    try:
        t = calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return False
    return (now - t) > STALE_SCORE_S


def snapshot(agent):
    """The few fields both events decide on, cache-friendly."""
    if agent is None:
        return {"band": None, "score": None, "alarms_total": 0,
                "loop_calls": 0}
    loops = [int(a.get("duplicate_calls") or 0)
             for a in agent.get("alarms") or []
             if a.get("channel") == "loop"]
    return {"band": agent.get("band"), "score": agent.get("score"),
            "alarms_total": agent.get("alarms_total", 0),
            "loop_calls": max(loops, default=0)}


def message(snap):
    """The context line for the agent, or None while everything is normal."""
    parts = []
    if snap.get("band") in ("watch", "deviating"):
        parts.append(f"score {snap.get('score')} ({snap['band']}) — your "
                     "behavior deviates from this agent's baseline")
    if snap.get("loop_calls"):
        parts.append("a loop alarm is on record "
                     f"({snap['loop_calls']}x the same call in a row)")
    if not parts:
        return None
    return ("KYDE Score: " + "; ".join(parts) + ". Pause and check with "
            "your human before continuing. Details: "
            "https://score.kyde.com/dashboard")


def dangerous(snap):
    """Deviating band or a live loop alarm — the block-worthy states."""
    return snap.get("band") == "deviating" or bool(snap.get("loop_calls"))


# key in tool_input (lowercased, non-letters dropped) -> entity class
_RECIPIENT = {"iban": "iban", "to": "recipient", "cc": "recipient",
              "bcc": "recipient", "recipient": "recipient",
              "recipients": "recipient", "email": "recipient",
              "payee": "recipient", "destination": "recipient",
              "customer": "customer", "customerid": "customer",
              "participants": "recipient", "attendees": "recipient",
              "invitees": "recipient"}
_PREFIX = (("account", "account"), ("merchant", "merchant"),
           ("iban", "iban"), ("recipient", "recipient"))
_AMOUNT = ("amount", "amountcents")
# "to"/"cc"/"bcc" also mean date ranges and diffs: only an address counts
# so do invitation lists and "*_email" fields (user_email, contact_email)
_ADDRESS_ONLY = ("to", "cc", "bcc", "participants", "attendees", "invitees")
# who a search filters by is no recipient
_NOT_SENT_TO = ("from", "sender", "replyto", "author")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+|\+\d[\d \-]{6,}")


def _class_of(key):
    k = re.sub(r"[^a-z]", "", str(key).lower())
    if k in _RECIPIENT:
        return _RECIPIENT[k]
    if k in _AMOUNT:
        return "amount"
    for p, c in _PREFIX:
        if k.startswith(p):
            return c
    if k.endswith("email") and not k.startswith(_NOT_SENT_TO):
        return "recipient"
    return None


def _leaves(value):
    if isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield value


def recipients(tool_input, depth=0):
    """{class: [cleartext]} from a tool_input — stays on this machine."""
    out = {}
    if depth > 6 or not isinstance(tool_input, dict):
        return out
    for key, value in tool_input.items():
        if isinstance(value, dict):
            for c, vs in recipients(value, depth + 1).items():
                out.setdefault(c, []).extend(vs)
            continue
        cls = _class_of(key)
        plain = re.sub(r"[^a-z]", "", str(key).lower())
        if cls is None:
            if isinstance(value, list):
                for v in value:
                    for c, vs in recipients(v, depth + 1).items():
                        out.setdefault(c, []).extend(vs)
            continue
        for item in (value if isinstance(value, list) else []):
            if isinstance(item, dict):  # "to": [{"email": ...}]
                for c, vs in recipients(item, depth + 1).items():
                    out.setdefault(c, []).extend(vs)
        for v in _leaves(value):
            if cls == "amount":
                try:
                    n = abs(float(v))
                except (TypeError, ValueError):
                    continue
                v = "0" if n < 1 else f"1e{int(math.log10(n))}"
            else:
                v = str(v).strip()
                if ((plain in _ADDRESS_ONLY or plain.endswith("email"))
                        and not _ADDRESS.fullmatch(v)):
                    continue
                if "@" in v:
                    v = v.lower()
                if not v:
                    continue
            out.setdefault(cls, []).append(str(v))
    return out


def _salt():
    salt = os.environ.get("KYDE_SALT") or _settings_env().get("KYDE_SALT")
    if salt:
        return salt
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME")
                        or os.path.expanduser("~/.config"), "kyde", "salt")
    try:
        with open(path) as fh:
            salt = fh.read().strip()
    except OSError:
        salt = ""
    if not salt:
        salt = secrets.token_hex(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(salt)
    return salt


def entity_hashes(tool_input, salt):
    """{class: [sha256 hex]} — what leaves the machine."""
    return {c: sorted({hashlib.sha256(f"{salt}|{v}".encode()).hexdigest()
                       for v in vs})[:10]
            for c, vs in recipients(tool_input).items() if vs}


def raw_amounts(tool_input, depth=0):
    """Raw numeric amounts from a tool_input, in currency units
    (amountcents / 100). Only the local amount_max check reads these;
    what leaves the machine stays the salted magnitude hash (D-064)."""
    out = []
    if depth > 6 or not isinstance(tool_input, dict):
        return out
    for key, value in tool_input.items():
        if isinstance(value, dict):
            out.extend(raw_amounts(value, depth + 1))
            continue
        k = re.sub(r"[^a-z]", "", str(key).lower())
        if k not in _AMOUNT:
            if isinstance(value, list):
                for v in value:
                    out.extend(raw_amounts(v, depth + 1))
            continue
        for v in _leaves(value):
            try:
                n = abs(float(v))
            except (TypeError, ValueError):
                continue
            out.append(n / 100 if k == "amountcents" else n)
    return out


def send_entities(endpoint, key, session_id, tool_name, tool_input):
    """Hash and send in a forked child: the call never waits for it."""
    if not (session_id and tool_name and isinstance(tool_input, dict)):
        return
    ents = entity_hashes(tool_input, _salt())
    if not ents:
        return
    body = json.dumps({"session_id": session_id, "tool_name": tool_name,
                       "entities": ents}).encode()
    out = os.environ.get("KYDE_HOOK_ENTITIES_FILE")  # test seam
    if out:
        with open(out, "w") as fh:
            fh.write(body.decode())
        return
    if hasattr(os, "fork") and os.fork() != 0:
        return  # parent: back to the agent
    try:
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/score/v0/entities", data=body,
            headers={"x-kyde-key": key, "content-type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass
    finally:
        if hasattr(os, "fork"):
            os._exit(0)


# --- hold mode (P4): the local check against the confirmed contract ---------

def doc_tools(effective):
    """The plaintext tool names the hook can check, or None when the
    document lists no checkable tools (hashed names are the gateway's
    job, it holds a different salt convention)."""
    tools = effective.get("tools") or []
    if not tools:
        return None
    names = {str(t.get("name")) for t in tools
             if not str(t.get("name", "")).startswith("h:")}
    return names or None


def doc_allowed(effective):
    """{class: set of bare 64-hex hashes} from the contract document."""
    return {g["class"]: {str(a)[2:] for a in g.get("allowed", [])}
            for g in effective.get("goals") or []}


def hold_decision(effective, tool_name, entities, amounts=()):
    """None = inside the contract, else the one-sentence reason. Mirrors
    the service's contract_doc.check_record; the service's test suite
    pins the two against the shared vectors. The limits.amount_max check
    runs only here: the server sees amounts as salted magnitude hashes
    (D-064) and cannot compare them to a limit."""
    tools = doc_tools(effective)
    if tools is not None and tool_name not in tools:
        return f"{tool_name} is not in the confirmed contract"
    allowed = doc_allowed(effective)
    if allowed:
        for cls, hashes in (entities or {}).items():
            if set(hashes) - allowed.get(cls, set()):
                return (f"a {cls.replace('_', ' ')} outside the "
                        "confirmed contract")
    am = (effective.get("limits") or {}).get("amount_max")
    if am is not None and amounts:
        try:
            cap = float(am["value"]) * (
                1 + float(am.get("leeway_pct") or 0) / 100)
        except (KeyError, TypeError, ValueError):
            cap = None
        # the raw amount stays local: the reason names only the limit,
        # which the confirmed contract on the server carries anyway
        if cap is not None and any(a > cap for a in amounts):
            cur = str(am.get("currency") or "").strip()
            return ("an amount over the confirmed limit "
                    + f"{am['value']}" + (f" {cur}" if cur else ""))
    return None


def _fetch_contract(endpoint, key, name):
    path = os.environ.get("KYDE_HOOK_CONTRACT_FILE")
    if path:
        with open(path) as fh:
            return json.load(fh)
    url = (endpoint.rstrip("/") + "/score/v0/contracts?"
           + urllib.parse.urlencode({"service_name": name}))
    req = urllib.request.Request(url, headers={"x-kyde-key": key})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.load(resp)


def _approved(endpoint, key, name, tool_name, entities):
    """An earlier hold of this exact case the human already approved."""
    path = os.environ.get("KYDE_HOOK_HOLDS_READ_FILE")  # test seam
    if path:
        with open(path) as fh:
            holds = json.load(fh).get("holds") or []
    else:
        url = (endpoint.rstrip("/") + "/score/v0/holds?"
               + urllib.parse.urlencode({"service_name": name, "limit": 50}))
        req = urllib.request.Request(url, headers={"x-kyde-key": key})
        with urllib.request.urlopen(req, timeout=3) as resp:
            holds = json.load(resp).get("holds") or []
    want = {c: sorted(v) for c, v in (entities or {}).items()}
    for h in holds:
        if (h.get("status") in ("approved", "approved_extended")
                and h.get("tool_name") == tool_name
                and {c: sorted(v) for c, v in
                     (h.get("entities") or {}).items()} == want):
            return True
    return False


def _report_hold(endpoint, key, name, session_id, tool_name, entities,
                 reason):
    """Tell the service (it mails the human) from a forked child."""
    body = json.dumps({"service_name": name, "session_id": session_id,
                       "tool_name": tool_name, "entities": entities,
                       "reason": reason}).encode()
    out = os.environ.get("KYDE_HOOK_HOLDS_FILE")  # test seam
    if out:
        with open(out, "w") as fh:
            fh.write(body.decode())
        return
    if hasattr(os, "fork") and os.fork() != 0:
        return
    try:
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/score/v0/holds", data=body,
            headers={"x-kyde-key": key, "content-type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass
    finally:
        if hasattr(os, "fork"):
            os._exit(0)


def maybe_hold(endpoint, key, state, session_id, tool_name, tool_input):
    """The PreToolUse hold path. Returns the deny reason, or None."""
    if (os.environ.get("KYDE_HOLD") or "on").strip().lower() in (
            "off", "0", "no"):
        return None
    doc = state.get("contract")
    if not doc or not tool_name:
        return None
    ents = (entity_hashes(tool_input, _salt())
            if isinstance(tool_input, dict) else {})
    amounts = (raw_amounts(tool_input)
               if isinstance(tool_input, dict) else [])
    reason = hold_decision(doc, tool_name, ents, amounts)
    if reason is None:
        return None
    name = state.get("agent") or _fallback_name()
    try:
        approved = _approved(endpoint, key, name, tool_name, ents)
    except Exception:
        # unknown is not approved: the call stays held (fail closed on
        # the paid path — holding is the guarantee, retrying is cheap)
        approved = False
    if approved:
        return None
    try:
        _report_hold(endpoint, key, name, session_id, tool_name, ents,
                     reason)
    except Exception:
        pass
    return ("KYDE held this call before execution: " + reason + ". Your "
            "human decides by mail or dashboard (approve once, approve "
            "and extend the contract, or deny); retry after approval.")


def _prune(cache, now):
    for path in glob.glob(os.path.join(cache, "hook-s-*.json")):
        try:
            if now - os.path.getmtime(path) > KEEP_S:
                os.remove(path)
        except OSError:
            pass


def main(event_name, session_id="", tool_name="", tool_input=None):
    endpoint = _endpoint()
    key = _key()
    if not key:
        return
    if event_name == "PreToolUse":
        try:
            send_entities(endpoint, key, session_id, tool_name, tool_input)
        except Exception:
            pass
    sid = _safe(session_id)
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME")
                         or os.path.expanduser("~/.cache"), "kyde")
    os.makedirs(cache, exist_ok=True)
    state_file = os.path.join(
        cache, f"hook-s-{sid}.json" if sid else f"hook-{_fallback_name()}.json")
    state = {}
    try:
        with open(state_file) as fh:
            state = json.load(fh)
    except Exception:
        if sid:
            _prune(cache, time.time())  # a new session: tidy old ones
    now = time.time()
    changed = False
    if now - state.get("checked", 0) >= THROTTLE_S:
        state["checked"] = now
        scores = _scores(endpoint, key, sid)
        if scores.get("session_service_name"):
            # the service attributed this session's calls to an agent:
            # the one positive source of "who is this session".
            state["agent"] = scores["session_service_name"]
            state["agent_confirmed"] = True
        # Attribute only to THIS session's agent. With a session id the
        # name must come from the service (session_service_name) or a
        # confirmation already cached for this same session. The guessed
        # fallback name (process env, settings, or the literal
        # "claude-code") must never pull another agent's score or
        # contract in: that played a stale 'claude-code' deviating score
        # at unrelated sessions (KYDE-518). Without a session id there is
        # no per-session attribution, so the name-keyed fallback stays.
        if sid:
            name = state.get("agent") if state.get("agent_confirmed") else None
        else:
            name = state.get("agent") or _fallback_name()
        agent = pick_agent(scores, name)
        # Staleness suppresses the band/score message only: an old score
        # is not this session's behaviour. A human-confirmed contract
        # does not go stale, so the hold path below keeps the full agent.
        score_agent = None if _stale(agent, now) else agent
        seen = snapshot(score_agent)
        changed = any(seen[k] != state.get(k)
                      for k in ("band", "alarms_total"))
        state.update(seen)
        # Hold mode (P4): cache the document the hold path checks against
        # while the plan is paid; anything else drops it and PreToolUse
        # stays hands-off. With a confirmed agent contract that document
        # is its frame-capped effective form. Without one, the
        # organization frame still holds on its own (KYDE-519): "no agent
        # sends money" bites before an agent has proposed anything.
        # D-071: holding ships from the Pro plan up ("paid" covers an
        # older score deployment that predates the ladder).
        tri = (agent or {}).get("contract") or {}
        agent_hash = (tri.get("hash")
                      if tri.get("status") == "confirmed" else None)
        frame_hash = (scores.get("frame") or {}).get("hash")
        paid = scores.get("plan") in ("pro", "team", "control", "paid")
        # a known agent is required so a hold is reported and approved
        # under the right name (KYDE-518); without one, stay hands-off.
        if paid and name and (agent_hash or frame_hash):
            doc_key = [agent_hash, frame_hash]
            if doc_key != state.get("doc_key"):
                try:
                    resp = _fetch_contract(endpoint, key, name)
                    cur = resp.get("current") or {}
                    eff = cur.get("effective") or cur.get("document")
                    if agent_hash and eff:
                        state["contract"] = eff
                    else:
                        # standalone frame: the org boundary alone
                        state["contract"] = (
                            (resp.get("frame") or {}).get("document"))
                    state["doc_key"] = doc_key
                except Exception:
                    pass
        else:
            state.pop("contract", None)
            state.pop("doc_key", None)
    snap = {k: state.get(k)
            for k in ("band", "score", "alarms_total", "loop_calls")}
    msg = message(snap)
    if event_name == "PreToolUse":
        held = None
        try:
            held = maybe_hold(endpoint, key, state, sid, tool_name,
                              tool_input)
        except Exception:
            held = None
        if held:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": held}}))
            with open(state_file, "w") as fh:
                json.dump(state, fh)
            return
        mode = (os.environ.get("KYDE_BLOCK") or "off").strip().lower()
        if mode not in ("off", "0", "no") and msg and dangerous(snap):
            if mode == "deny" or now - state.get("asked", 0) >= REMIND_S:
                state["asked"] = now
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": ("deny" if mode == "deny"
                                           else "ask"),
                    "permissionDecisionReason": msg}}))
    elif msg and (changed or now - state.get("warned", 0) >= REMIND_S):
        state["warned"] = now
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg}}))
    with open(state_file, "w") as fh:
        json.dump(state, fh)


if __name__ == "__main__":
    event, session, tool, tool_input = "PostToolUse", "", "", None
    try:
        data = json.load(sys.stdin)
        event = data.get("hook_event_name") or event
        session = data.get("session_id") or ""
        tool = data.get("tool_name") or ""
        tool_input = data.get("tool_input")
    except Exception:
        pass
    try:
        main(event, session, tool, tool_input)
    except Exception:
        pass  # a broken hook must never break the agent
