# Kyde Score in Claude Desktop / Cowork — Anleitung

Für Gregor und Christoph (oder jeden mit Claude Pro/Max). Kein Download,
kein ZIP: der Marketplace wird einmal hinzugefügt, dann installiert man
das Plugin und bekommt Updates automatisch.

## Installieren (ca. 2 Minuten)

1. In Claude Desktop: **Customize → Plugins → Add → Add marketplace**.
2. Marketplace eintragen: `kydehq/kyde-score-skill` (oder die URL
   `https://github.com/kydehq/kyde-score-skill`).
3. Aus dem Marketplace **kyde-score-cowork** installieren (nicht
   `kyde-score` — das ist die Claude-Code-Variante ohne Hooks).
4. Beim Plugin **Sync automatically** / Auto-Update einschalten, damit
   neue Versionen von selbst ankommen.

In der Claude-Code-CLI geht es in einem Schritt (ab Version 2.1.275):

    /plugin install kyde-score-cowork --marketplace kydehq/kyde-score-skill

## Einrichten (einmal)

1. Einen Kyde-Key besorgen: entweder im Dashboard auf
   https://score.kyde.com/dashboard, oder Claude in einer Cowork-Aufgabe
   bitten: **"set up Kyde"** — der mitgelieferte Skill führt durch das
   Anlegen und Claimen.
2. Den Key in die **Plugin-Einstellung "Kyde key"** eintragen (dort ist
   er verschlüsselt). Wichtig: in die Einstellung, nicht in eine Datei —
   Cowork-Aufgaben laufen jedes Mal auf einer frischen Cloud-Maschine.
3. **Agent name** in der Plugin-Einstellung setzen (Default `cowork`).
   Pro Person einen eigenen Namen, z. B. `gregor-cowork`, damit die
   Scores nicht zusammenlaufen.
4. Eine kleine Cowork-Aufgabe mit ein paar Werkzeug-Aufrufen laufen
   lassen, dann im Dashboard prüfen, dass unter dem Agentennamen Aktivität
   erscheint. Scores starten nach etwa 30 Aufgaben über einige Tage;
   bis dahin steht dort "learning".

## Was es tut, was nicht

- **Misst** jeden Werkzeug-Aufruf (Name, Erfolg, Dauer, Session, Zeit),
  **nie Inhalte** — keine Prompts, keine Dateien, keine Ausgaben.
- **Warnt** den Agenten mit einer Zeile, wenn sein Verhalten vom
  gelernten Normal abweicht.
- **Hält** einen Aufruf vor der Ausführung an, wenn er außerhalb des
  vom Menschen bestätigten Vertrags liegt — aber nur ab **Pro-Plan** und
  mit einem **bestätigten Vertrag**, und nur ein Mensch gibt wieder frei
  (per Mail oder Dashboard). Der MCP-Server ist remote
  (`https://score.kyde.com/mcp`), lädt also auch in Cowork.
- **Sieht nicht**: normalen Chat ohne Aufgabe.

## Ehrliche Hinweise

- **Messen ist erprobt. Das Halten in Cowork hat bisher niemand live
  geprüft** — der Hook ist identisch mit dem von score.kyde.com und ein
  Test hält beide gleich, aber in der Cowork-Oberfläche ist es noch nicht
  gegen echte Aufrufe getestet. Für den ersten Eindruck also das Messen
  zeigen, das Halten als "sollte greifen" benennen.
- Halten braucht Pro + bestätigten Vertrag + einen Menschen, der
  freigibt — kein Ein-Klick-Ding.

## Zwei Plugins im selben Marketplace

- **kyde-score** — Claude Code / jede OpenTelemetry-Umgebung. Skill-basiert,
  keine Hooks.
- **kyde-score-cowork** — Claude Desktop / Cowork. Mit Hooks (Messen +
  Halten), dem MCP-Server und dem Setup-Skill. **Das hier nehmen.**

## Für uns (Release, nicht für Jörg)

Der kanonische Hook lebt im score-Repo (`deploy/landing/hook.py`, ein Test
hält die Cowork-Kopie gleich). Dieses Verzeichnis ist ein veröffentlichter
Schnappschuss: bei jedem Release den aktuellen Build aus score hierher
spiegeln und `version` in `plugins/kyde-score-cowork/.claude-plugin/plugin.json`
hochzählen (sonst bekommt niemand die neue Fassung). Stand dieser Fassung:
0.2.0, enthält die Fixes KYDE-518 (Agenten-Attribution) und KYDE-519
(eigenständiger Org-Rahmen).
