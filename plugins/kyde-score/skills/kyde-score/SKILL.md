---
name: kyde-score
description: Integrate an AI agent or agent harness with kyde score (score.kyde.com), the hosted behavioral drift scoring service — create a scoring key, point the harness's OpenTelemetry exporter at the service, verify ingest, and optionally add the MCP server for in-session score checks. Use when asked to "monitor this agent", "set up drift detection / behavioral scoring", "integrate kyde score", or to check an agent's drift score.
---

# Integrate kyde score

kyde score learns each agent's behavioral baseline from its OpenTelemetry
and scores every later run 0–100 against it with deterministic detectors
(no LLM). 70–100 normal, 40–69 watch, 0–39 deviating. A band change can
fire an HMAC-signed webhook. Agents are identified by OTel `service.name`;
one key covers all agents under it. Canonical machine-readable reference:
https://score.kyde.com/llms.txt

## Step 0 — Check for an existing integration

Look before creating anything: an `OTEL_EXPORTER_OTLP_ENDPOINT` containing
`score.kyde.com`, an `x-kyde-key` in the harness config/env, or a
`kyde-score` MCP server entry means a key already exists. Reuse it; one
key per team/host is the model, not one per agent.

## Step 1 — Create a key

Ask the human whether they have an https endpoint for alerts (Slack
webhook bridge, PagerDuty, their own app). With one:

```bash
curl -s https://score.kyde.com/score/v0/keys \
  -H 'content-type: application/json' \
  -d '{"webhook_url": "https://their.endpoint/hooks/kyde"}'
```

Without one, create a poll-only key (no body):

```bash
curl -s -X POST https://score.kyde.com/score/v0/keys
```

Either returns `{"key": "kyde_…", "method_version": "…", "webhook": …}`.

Handle the key like a credential: put it in the environment or the
harness's settings file, never commit it to a repository, and show it to
the human once so they can store it (it is not retrievable later —
losing it means revoking nothing; the data simply strands until
retention expires, so just create a new key).

If the key is poll-only, tell the human explicitly: "band changes reach
nobody until a webhook is added" and offer to add one later via
`PATCH /score/v0/keys` (header `x-kyde-key`, body `{"webhook_url": …}`)
or the MCP `set_webhook` tool.

Rate limit: 5 keys/hour/IP — do not create keys in a loop.

## Step 2 — Point the harness's OTel exporter at the service

Give every agent its own `OTEL_SERVICE_NAME`; that name is the unit of
scoring.

**Claude Code** (settings env or the shell that launches it):

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=https://score.kyde.com
export OTEL_EXPORTER_OTLP_HEADERS="x-kyde-key=<KEY>"
export OTEL_SERVICE_NAME=<agent-name>
```

**Any OTel SDK / other harnesses** (Codex, OpenCode, frameworks): the last
four variables above are the whole integration. The service accepts
OTLP/HTTP **JSON** (`OTEL_EXPORTER_OTLP_PROTOCOL=http/json` — protobuf is
not accepted yet, so this variable is not optional).

**OTel Collector in between** (recommended when telemetry must be
scrubbed before leaving a network): add an `otlphttp` exporter with
`endpoint: https://score.kyde.com` and the `x-kyde-key` header.

## Step 3 — Verify

Run the agent once, then:

```bash
curl -s https://score.kyde.com/score/v0/scores -H "x-kyde-key: <KEY>"
```

Success looks like the agent listed with `"status": "learning"` and a
`runs_seen` count. An empty `agents` list means nothing arrived: check
the protocol is `http/json` and the header spelling. A `"degraded"`
field means records were dropped (quota/limits) — surface it to the
human rather than ignoring it.

Scoring starts automatically once 30 runs are seen (`"status": "scored"`,
with `score` and `band`). There is nothing to configure for that; just
tell the human scores appear after ~30 runs.

## Step 4 (optional) — Add the MCP server for in-session checks

```bash
claude mcp add --transport http kyde-score https://score.kyde.com/mcp \
  --header "x-kyde-key: <KEY>"
```

(Any MCP client: `{"type": "http", "url": "https://score.kyde.com/mcp",
"headers": {"x-kyde-key": "<KEY>"}}`.)

Tools: `get_scores` (check drift, including your own, mid-session),
`set_webhook`, `create_key`, `revoke_key` (destructive: deletes the key
and all data; requires `confirm: true` — never call it without the human
asking).

## Facts worth relaying to the human

- Free tier limits: 100k records/day and 60 ingest req/min per key,
  200 services per key, 90-day retention.
- Privacy: attributes pass a deny-by-default allowlist before storage;
  prompts and payload bodies are never stored.
- Leaving: `DELETE /score/v0/keys` (header `x-kyde-key`) removes the key
  and every record under it in one request.
- Scores carry `method_version`, so they are comparable over time and
  reproducible against the open-source detectors
  (github.com/kydehq/behavioral-drift-detection).
