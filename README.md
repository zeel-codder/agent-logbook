# agent-logbook

A Claude Code plugin that writes down what every agent did in a session: one markdown file for the main agent and one per subagent.

It's a small Python script driven by hooks. It makes no AI calls, uses zero tokens, and needs only the Python standard library.

```
agent-logs/
└── <session_id>/
    ├── index.md     table of every agent (type, start/end, task)
    ├── main.md      the main agent; each "Spawned subagent" line links to its agentN.md
    ├── agent1.md    first subagent to finish
    ├── agent2.md
    └── ...
```

## Why

When a session fans out into several subagents, the main transcript only shows what each one sent back. The logbook gives you each agent's full story in a file you can read, diff, or share:

- **Task**: the prompt it was given
- **Timeline**: what it said, its thinking (when the transcript keeps it), and every tool call: Bash commands, files read, edited and written, searches, subagents spawned
- **Final result**: its last message

## Install

Needs Claude Code and `python3` on your PATH.

**From GitHub (recommended):**

```
/plugin install agent-logbook --marketplace zeel-codder/agent-logbook
```

On Claude Code versions before 2.1.275, add the marketplace first:

```
/plugin marketplace add zeel-codder/agent-logbook
/plugin install agent-logbook@agent-logbook
```

**Try it for one run without installing:**

```bash
git clone https://github.com/zeel-codder/agent-logbook.git
claude --plugin-dir ./agent-logbook
```

**Without the plugin system:** copy `scripts/logbook.py` to `.claude/hooks/logbook.py` in your repo and add this to `.claude/settings.json`:

```json
{
  "hooks": {
    "SubagentStop": [{ "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/logbook.py"], "async": true }] }],
    "Stop":         [{ "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/logbook.py"], "async": true }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/logbook.py"], "timeout": 15 }] }]
  }
}
```

Add `agent-logs/` to your `.gitignore` unless you want the logs committed.

## How it works

| Hook | What it writes |
|---|---|
| `SubagentStop` | `agentN.md` for the subagent that just finished (async, so it doesn't slow the run) |
| `Stop` | refreshes `main.md` after each main-agent turn (async) |
| `SessionEnd` | final pass: rewrites `main.md` and picks up any subagent whose stop hook was missed |

The script reads the session transcript (`transcript_path` from the hook input) and the subagent transcripts stored next to it (`<session>/subagents/agent-*.jsonl`, plus `.meta.json` for the agent type). Agents are numbered in the order they finish. A file lock keeps parallel subagents from taking the same number (on macOS and Linux; Windows runs without the lock).

## Example

A trimmed `main.md`:

```markdown
# Main agent

- **Session:** 558e519f-d6de-48f8-b3e2-906e92d6110d
- **Started:** 2026-10-20T10:41:05.112Z

## Task

Run 'echo hello' in a subagent, then reply done.

## Timeline

1. 🔧 **Spawned subagent** (general-purpose) — Run echo hello → [agent1.md](agent1.md) · 16:11:13

### 💬 16:11:38

Done. The subagent ran `echo hello`, and the output was `hello`.
```

## Options

Set these as environment variables:

| Variable | Default | Effect |
|---|---|---|
| `AGENT_LOGBOOK_DIR` | `<project>/agent-logs` | where session folders go |
| `AGENT_LOGBOOK_RESULTS` | `0` | `1` = also include tool outputs as collapsed `<details>` blocks (files get much bigger) |
| `AGENT_LOGBOOK_MAXLEN` | `4000` | max characters per message block before trimming |

## Rebuild a past session

Transcripts live under `~/.claude/projects/<project-folder>/<session_id>.jsonl`. To write (or rewrite) the logbook for one of them:

```bash
echo '{"hook_event_name":"SessionEnd","session_id":"<id>","transcript_path":"<path to .jsonl>","cwd":"'$PWD'"}' \
  | python3 scripts/logbook.py
```

## Limits

- It records what the agents wrote and did. It doesn't summarize things into "decision: X because Y"; the "why" is whatever the agent said in its messages.
- Thinking only shows up if the transcript stores it in readable form.
- Skill bodies and system reminders injected into the conversation are left out of the timeline.
- If the script fails, your session carries on as normal; errors go to stderr only.

## Project layout

```
.claude-plugin/plugin.json        plugin manifest
.claude-plugin/marketplace.json   lets people install it straight from this repo
hooks/hooks.json                  SubagentStop / Stop / SessionEnd hooks
scripts/logbook.py                the whole thing (standard library only)
```
