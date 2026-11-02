# kyde-score agent skill

A skill that teaches an AI agent (Claude Code or any harness that reads
[agent skills](https://code.claude.com/docs/en/skills)) to integrate itself
with [kyde score](https://score.kyde.com): hosted behavioral drift scoring
for AI agents. The agent creates a scoring key, points its harness's
OpenTelemetry exporter at the service, verifies ingest, and can add the MCP
server for in-session drift checks. Free tier, no signup form, metadata only.

## Install as a Claude Code plugin

```bash
claude plugin marketplace add kydehq/kyde-score-skill
claude plugin install kyde-score@kyde-score-skill
```

## Or install the skill file directly

```bash
# Project-scoped
mkdir -p .claude/skills/kyde-score
curl -fsSL https://score.kyde.com/skill.md -o .claude/skills/kyde-score/SKILL.md

# Or user-wide
mkdir -p ~/.claude/skills/kyde-score
curl -fsSL https://score.kyde.com/skill.md -o ~/.claude/skills/kyde-score/SKILL.md
```

Then ask the agent to "set up drift detection for this agent" (or anything in
that spirit) and it knows the whole flow.

## What kyde score is

Point an agent's OTLP/HTTP JSON telemetry at `https://score.kyde.com`; the
service learns a per-agent baseline from the first 30 runs, then scores every
later run 0–100 with deterministic detectors (CUSUM + windowed Jensen–Shannon
divergence — no LLM in the loop) and fires an HMAC-signed webhook when a score
changes band. Bands: 70–100 normal, 40–69 watch, 0–39 deviating.

- Machine-readable reference: https://score.kyde.com/llms.txt
- MCP server (streamable HTTP): `https://score.kyde.com/mcp`
- OpenAPI schema: https://score.kyde.com/openapi.json
- Open-source detectors: https://github.com/kydehq/behavioral-drift-detection
- The company: https://kyde.com

## Canonical source

The skill is published at **https://score.kyde.com/skill.md**; this repository
mirrors it so it is installable as a plugin and findable on GitHub. If the two
ever disagree, the URL wins.
