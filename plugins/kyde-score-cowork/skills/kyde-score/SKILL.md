---
name: kyde-score
description: Sets up and checks Kyde Score (score.kyde.com) for Claude Cowork tasks, which learns how this agent normally behaves and warns when it deviates. Use when the user says "set up Kyde", "connect Kyde", "is Kyde watching?", "How is my Kyde score?", "what is my agent's score/band", or asks why Kyde shows nothing.
---

# Kyde Score in Cowork

This plugin has a hook that reports every tool call of a Cowork task to
Kyde Score: tool name, success, duration, session and time. Never inputs,
outputs, prompts, file contents or paths. Kyde learns the agent's normal
behavior from about 30 tasks over at least 3 days, then scores each task
0 to 100 (70 to 100 normal, 40 to 69 watch, 0 to 39 deviating). When the
score deviates, the hook puts a one-line warning into your context. On
the Pro plan and above, with a contract your human confirmed, the hook
also HOLDS a call outside that contract before execution and tells you
why; only your human can release it (by mail or dashboard).

## Find the status line first

At session start the hook adds a line to your context that starts with
`Kyde plugin:`. It names `session_id`, `agent`, and either `key from ...`
(active) or `no key` with a `key_file` path. Use those values below. If
there is no such line at all, the plugin's hooks are not running in this
environment: say so plainly (see "What Kyde cannot see") and stop; do not
claim anything is set up.

## Set up (only when the user asks)

1. **Key.** If the status line says `active`, skip to step 4.
   Otherwise ask once whether the user already has a Kyde key (`kyde_...`).
   - Yes: tell them the safest place is the plugin's own setting
     ("Kyde key" in the plugin settings, stored encrypted). If they prefer,
     or the setting is not offered, take the key and continue with step 3.
   - No: create one. Use the MCP tool `create_key` with
     `{"source": "cowork-plugin"}`, or, if the MCP server is not
     available:
     ```bash
     curl -s https://score.kyde.com/score/v0/keys \
       -H 'content-type: application/json' -d '{"source": "cowork-plugin"}'
     ```
     Show the key to the user once and tell them to store it like a
     password; it cannot be retrieved later.
2. **Email (claim). Mandatory question, the user may decline.** Ask once,
   as its own question, for an email address. It gives them the Kyde
   dashboard, the admin rights only a human should have (reset a
   baseline, change alerts, delete the key) and the guarantee that no
   agent can reset its own score. On yes, ask separately whether Kyde may
   send occasional product updates (default no). Then call `claim_key`
   (with `key` and `email`, `product_updates` only on an explicit yes) or
   `POST https://score.kyde.com/score/v0/keys/claim` with header
   `x-kyde-key`. Kyde mails a confirmation link; nothing changes until
   they click it. Never guess an address.
3. **Store the key for the hook** (skip if it is in the plugin setting).
   Write it to the `key_file` from the status line, readable only by the
   owner, and nowhere else: not into chat summaries, not into the user's
   files.
   ```bash
   mkdir -p "$(dirname "<key_file>")" && umask 077 && \
   printf '{"key": "%s"}\n' "<KEY>" > "<key_file>"
   ```
   The hook reads it on the next tool call. If the user wants a different
   agent name than the one in the status line, add `"agent_name": "<name>"`.
4. **Confirm the first run arrived. Do not say "done" before this.**
   Every tool call is reported, so this check itself produces one. Run it,
   wait about 5 seconds, run it again (up to three times):
   ```bash
   curl -s "https://score.kyde.com/score/v0/scores?session_id=<session_id>" \
     -H "x-kyde-key: <KEY>"
   ```
   Setup is finished only when `session_service_name` equals the agent
   name and that agent is listed with `runs_seen` of at least 1. Report
   "runs_seen of runs_needed". If it stays null after three tries, say
   that the key is stored but nothing has arrived, and show the response.
   Never send hand-made records to make this pass: that measures the
   script, not the agent.
5. **Describe the agent.** Suggest a display name and one plain sentence
   of purpose, so the dashboard shows more than the ID: MCP tool
   `describe_agent`, or `PUT https://score.kyde.com/score/v0/agents/<agent
   name>/profile` with header `x-kyde-key` and body
   `{"display_name": "...", "purpose": "..."}`. Describe it honestly: the
   dashboard shows next to it what Kyde measured from the tools. The user
   can change both; the agent name itself never changes.
6. **Tell the user, in your own words:**
   - Kyde now sees the tool calls of Cowork tasks under the agent name.
     Scores start after about 30 tasks over a few days; until then the
     dashboard shows "learning".
   - What it cannot see (below), in one or two sentences.
   - Measuring is free; holding a call before execution is the Pro
     plan and above and needs a contract the human confirmed. Never
     confirm or release anything yourself: https://kyde.com/pricing/
   - If the key is in the key file, not the plugin setting: a new Cowork
     task may start on a fresh machine. If the next task's status line
     says `no key`, move the key into the plugin setting.

## "How is my Kyde score?"

Call the MCP tool `get_scores` (pass `key` as an argument if the MCP
server has no key header), or the scores call from step 4 without
`session_id`. For the agent from the status line, report status
(learning or scored), `score` and `band`, `runs_seen` of `runs_needed`,
and any alarms in plain words. Link https://score.kyde.com/dashboard.

If the response has `feedback` entries and the user is present, ask once
per alarm, in one sentence, whether the alarm was right, with its
`answer_url`. Never suggest the answer, never open or submit the link
yourself.

Never call `rebaseline` or `revoke_key` on your own initiative. Both need
the user's explicit request and `confirm: true`; a reset is permanently
visible. When the user asks for a reset, record why: pass `kind`
(role_change, model_change, prompt_change, tool_change, other) and a
one-sentence `reason` — both land in the alarm, the timeline and the
webhook event. A deviating agent's `get_scores` entry carries
`proposed_action` with the exact call.

When the user says a change is coming (new model, new prompt, new role),
offer `announce_change` BEFORE it ships: the expected drift is then held
in the announcement instead of alarming, rule violations still alarm,
and `get_scores` `notices` shows the `rebaseline {announcement_id}` call
that accepts the new baseline. It mutes deliveries, so it also needs the
user's explicit go-ahead.

While an agent is still learning with no real alarm, `notices` carries a
`sample_alarm` item (`sample: true`) showing what an alarm will read
like. Never relay the sample as a real alarm — real ones carry an
`alarm_id`.

On the free plan (every key starts free; `get_scores` carries `plan`)
only one alert mail goes out per month. An `alert_held` item in
`notices` means an alert is waiting in the weekly review mail — tell
the user, since this API saw the alarm immediately and their mailbox
did not. Upgrading is the user's decision, never yours.

Before a model change, check `GET /score/v0/drift?model=<target>` (no
key needed): fleet aggregates of how agents behaved after switching,
never a single customer, never model quality. Tell the user the
numbers.

A behavior report (the share page of the last 30 days) is created and
shared only by the user from the dashboard; you may suggest it, never
create or send one.

A declared contract (propose_contract) is confirmed only by the user,
never by you. If a call of yours is denied with "KYDE held this call",
it was outside the confirmed contract: tell the user a hold awaits
their choice (approve once, approve and extend, deny, by mail or
dashboard) and retry only after approval. A `contract_proposed` notice
means your proposal still awaits them.

## What Kyde cannot see

Say this honestly whenever it matters:

- Normal chat in Claude Desktop or claude.ai, where Claude answers
  without running a task or tools: no hooks run there, Kyde sees nothing.
- Tools inside a task are seen; what happens inside a single tool (for
  example the pages a web search reads) is not.
- Claude Code (terminal, IDE, Code tab) is not covered by this plugin;
  it has its own one-line setup: `curl -fsSL https://score.kyde.com/install.sh | sh`.
