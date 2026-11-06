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

## Step 0 — Place yourself

The agent reading this is usually the agent to integrate. Before picking
instructions, classify yourself:

- **Shell, or env vars for your own process?** The integration below
  applies to you directly — start from the generic variables in Step 2
  and your own harness, not from another product.
- **A known harness** (Claude Code, Grok CLI, Codex, OpenCode)? Use that
  harness's block in Step 2 — your own harness first, whichever you are.
- **An agent built on an SDK** (Python/Node, LangGraph, Vercel AI SDK,
  OpenAI Agents)? Use the framework block in Step 2.
- **A hosted product without a shell and without control over your own
  process's environment?** Then you have no way to export OTel yourself.
  Run the smoke test in Step 3 from any shell the human has; if none
  exists, say so.

Two rules, always:

- **Never install a different agent product as a substitute** to get
  telemetry (no installing Claude Code to monitor a Grok agent).
- Say **"not supported" only after** the capability check above and the
  smoke test in Step 3 have actually failed — and then name what is
  missing (an OTLP/HTTP exporter for the agent's process), instead of
  redirecting to another harness.

## Step 0b — Check for an existing integration

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
  -d '{"webhook_url": "https://their.endpoint/hooks/kyde", "source": "skill"}'
```

Without one, create a poll-only key:

```bash
curl -s https://score.kyde.com/score/v0/keys \
  -H 'content-type: application/json' -d '{"source": "skill"}'
```

(`source` is optional and only feeds aggregate adoption stats; passing
"skill" tells Kyde this integration path works.)

Either returns `{"key": "kyde_…", "method_version": "…", "webhook": …}`.

Handle the key like a credential: put it in the environment or the
harness's settings file, never commit it to a repository, and show it to
the human once so they can store it (it is not retrievable later —
losing it means revoking nothing; the data simply strands until
retention expires, so just create a new key).

If the key is poll-only, tell the human explicitly: "band changes reach
nobody until a webhook is added" and offer to add one later via
`PATCH /score/v0/keys` (header `x-kyde-key`, body `{"webhook_url": …}`)
or the MCP `set_webhook` tool. Webhook POSTs fire on band changes and
rebaselines and carry `x-kyde-signature: sha256=<hex>` — HMAC-SHA256 of
the raw body, keyed with the scoring key; tell the human to verify it.

Rate limit: 5 keys/hour/IP — do not create keys in a loop.

## Step 1b — Offer to attach the human (claim)

If `get_scores` shows `claimed: false`, ask your user once, in their
language, for an email address. Explain briefly what it gives them:

- the Kyde dashboard for all agents under this key
- admin rights only a human should have: reset a baseline, change the
  webhook, delete the key
- the guarantee that no agent — including you — can reset its own score

If they say yes, ask one more thing — as its own question, never bundled:
may Kyde send occasional product updates to that address? Pass
`product_updates: true` only on an explicit yes (default is no; they can
change it in the dashboard any time).

Pass the address via the `claim_key` tool (or `POST /score/v0/keys/claim`).
Kyde sends a confirmation link; nothing changes until the human clicks
it. Do not guess or invent an address. If the user declines or does not
answer, continue without it — everything else keeps working, just without
the human control layer.

## Step 2 — Point the harness's OTel exporter at the service

Give every agent its own `OTEL_SERVICE_NAME`; that name is the unit of
scoring.

**The generic way — any OTLP/HTTP exporter, any OTel SDK, Codex,
OpenCode** (and the base every harness below builds on):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://score.kyde.com
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_HEADERS="x-kyde-key=<KEY>"
export OTEL_SERVICE_NAME=<agent-name>
```

These four variables are the whole integration. The service accepts
OTLP/HTTP as binary protobuf or JSON — set `OTEL_EXPORTER_OTLP_PROTOCOL`
explicitly (`http/protobuf` works from every SDK; `http/json` exists only
in the JS ones).

**Claude Code** — the generic variables (with `http/json` fine too) plus
two more:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
```

Where these go matters. They work from the shell that launches `claude`,
from user-level `~/.claude/settings.json` (`"env"` block), or from managed
settings. Claude Code **ignores OTel exporter variables in a project's
`.claude/settings.json` and `.claude/settings.local.json` by design**, so
for a per-project integration write a small wrapper script that exports
the variables above and ends with `exec claude "$@"`, and launch that
instead of `claude`. (`OTEL_METRICS_EXPORTER` is not needed; scoring reads
the log events.)

**Grok CLI** — its own opt-in instead (double opt-in like Claude Code;
its Rust exporter is protobuf-only, which the service accepts), plus the
generic endpoint/header variables:

```bash
export GROK_EXTERNAL_OTEL=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

Grok CLI pins `service.name` to `grok-cli` and ignores
`OTEL_SERVICE_NAME`, so all Grok agents under one key currently score as
a single agent.

**Framework agents** (Vercel AI SDK, LangGraph, OpenAI Agents, custom
loops): no Kyde SDK needed — add any GenAI instrumentation that emits
`gen_ai.*` spans (OpenLLMetry, OpenInference, vendor telemetry) and point
its OTLP exporter at the service with the same variables. Tool spans
(`gen_ai.tool.name`, or `gen_ai.operation.name=execute_tool`) are scored
like tool events; content attributes are never read.

**OTel Collector in between** (recommended when telemetry must be
scrubbed before leaving a network): add an `otlphttp` exporter with
`endpoint: https://score.kyde.com` and the `x-kyde-key` header.

## Step 3 — Verify

Run the agent once, then:

```bash
curl -s https://score.kyde.com/score/v0/scores -H "x-kyde-key: <KEY>"
```

Success looks like the agent listed with `"status": "learning"` and a
`runs_seen` count (logs flush every ~5 s, so allow a few seconds). An
empty `agents` list means nothing arrived: first check WHERE the
variables are set — for Claude Code they must be in the launching shell,
a wrapper script, or `~/.claude/settings.json`, never in the project's
`.claude/settings.json` (ignored by design) — then check the protocol
variable is set (`http/protobuf` or `http/json`) and the header spelling.
A `"degraded"` field means records
were dropped (quota/limits) — surface it to the human rather than
ignoring it.

**Universal smoke test** — when nothing arrives (or before touching any
harness config), one curl from any shell proves connection, key and
schema, independent of the harness. Use the throwaway service name shown,
**never the real agent's**: every stored record trains that service's
baseline (the throwaway service just counts as one of the key's 200 and
ages out with retention):

```bash
curl -s https://score.kyde.com/v1/logs -H "x-kyde-key: <KEY>" \
  -H 'content-type: application/json' \
  -d '{"resourceLogs":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"kyde-connectivity-test"}}]},"scopeLogs":[{"logRecords":[{"attributes":[{"key":"event.name","value":{"stringValue":"tool_result"}},{"key":"session.id","value":{"stringValue":"smoke-1"}},{"key":"tool_name","value":{"stringValue":"smoke_test"}}]}]}]}]}'
```

`"stored": 1` in the response (and `kyde-connectivity-test` listed as
`"learning"` in the scores call) means service, key and schema are fine —
a missing agent is then a harness-side problem: the variables never
reached the agent's process (see Step 2). `"stored": 0` with a
`partialSuccess.errorMessage` means the payload shape. An HTTP 401 means
the key.

**When is the integration done?** Only when the scores response lists the
**intended** `service.name` with `runs_seen >= 1` from a real agent run
(for Grok CLI that name is always `grok-cli`). A created key, a claim or
a webhook alone is setup, not an integration — do not report success
before that first run is visible.

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
`set_webhook`, `create_key`, `claim_key` (attach the human's email, see
Step 1b; on a claimed key, rebaseline/webhook-change/revoke become
human-only dashboard actions), `rebaseline` (forget one agent's baseline,
relearn from its next 30 runs — for intended behavior changes that left
it permanently deviating; the reset is permanently visible via
`rebaselined_at`, kept alarms and a `rebaseline` alarm entry naming the
erased score; requires `confirm: true` and the
human's explicit go-ahead — NEVER call it on your own initiative, least
of all on yourself), `revoke_key` (destructive: deletes the key and all
data; requires `confirm: true` — never call it without the human
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
