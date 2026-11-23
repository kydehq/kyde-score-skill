# Kyde Score plugin for Claude Cowork

Reports each tool call of a Cowork task (tool name, success, duration,
session, time; never content) to Kyde Score, runs the same warn-hook as
the Claude Code integration, and adds the `kyde-score` skill and the
Kyde MCP server ("How is my Kyde score?").

Install (Claude Code 2.1.275 or later):

    /plugin install kyde-score-cowork --marketplace kydehq/kyde-score-skill

In Claude Desktop / Cowork: Customize > Plugins > Add > Add marketplace,
enter `kydehq/kyde-score-skill`, then install kyde-score-cowork.

- Key: plugin setting "Kyde key" (sensitive, stored encrypted), else
  `KYDE_KEY` in the environment, else the key file the skill writes into
  the plugin data directory. Never in the plugin files.
- Agent name: plugin setting "Agent name", default `cowork`. Cowork
  tasks run on a fresh cloud machine, so the name never includes a host.
- Not covered: normal chat without tasks; Claude Code (use install.sh).

Build: `scripts/build_cowork_plugin.sh` writes `dist/kyde-score-cowork.zip`.
`hooks/hook.py` is a copy of `deploy/landing/hook.py`; a test keeps them equal.
