#!/usr/bin/env python3
"""
agent-logbook: write every agent's actions in a Claude Code session to markdown.

No AI calls. Hooks pipe their JSON input to this script on stdin; the script
reads the session transcripts (.jsonl) and writes:

    <output_root>/<session_id>/
        index.md      table of every agent in the session
        main.md       the main (orchestrating) agent
        agent1.md     first subagent to finish
        agent2.md     ...

Settings (environment variables, all optional):
    AGENT_LOGBOOK_DIR       output root (default: <project>/agent-logs)
    AGENT_LOGBOOK_RESULTS   "1" to include tool results (default: off)
    AGENT_LOGBOOK_MAXLEN    max characters per text/result block (default: 4000)
"""
import glob
import json
import os
import sys
from datetime import datetime

try:
    import fcntl
except ImportError:  # Windows: no flock, run without the lock
    fcntl = None

INCLUDE_RESULTS = os.environ.get("AGENT_LOGBOOK_RESULTS", "0") == "1"
try:
    MAXLEN = int(os.environ.get("AGENT_LOGBOOK_MAXLEN", "4000"))
except ValueError:
    MAXLEN = 4000


# ---------------------------------------------------------------- helpers

def clip(text, n=MAXLEN):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= n else text[:n] + f"\n… [{len(text) - n} more chars]"


def read_jsonl(path):
    entries = []
    if not path or not os.path.isfile(path):
        return entries
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def blocks_of(entry):
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def is_injected(entry, text):
    """Skill bodies, system reminders and other text the user didn't type."""
    return entry.get("isMeta") or (text.startswith("<") and "reminder" in text[:40])


def first_user_prompt(entries):
    for e in entries:
        if e.get("type") != "user" or e.get("isMeta"):
            continue
        for b in blocks_of(e):
            if b.get("type") == "text" and b.get("text", "").strip():
                return b["text"].strip()
    return ""


def last_assistant_text(entries):
    """Fallback final result when the hook didn't pass last_assistant_message."""
    for e in reversed(entries):
        if e.get("type") != "assistant":
            continue
        texts = [b.get("text", "").strip() for b in blocks_of(e) if b.get("type") == "text"]
        if any(texts):
            return "\n\n".join(t for t in texts if t)
    return ""


def ts(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return value


def tool_line(name, inp):
    """One readable line for a tool call."""
    inp = inp or {}
    if name == "Bash":
        return f"**Bash** — {inp.get('description', '')}\n```bash\n{clip(inp.get('command', ''), 1500)}\n```"
    if name in ("Edit", "MultiEdit"):
        return f"**{name}** `{inp.get('file_path', '')}`"
    if name == "Write":
        return f"**Write** `{inp.get('file_path', '')}` ({len(inp.get('content', ''))} chars)"
    if name == "Read":
        return f"**Read** `{inp.get('file_path', '')}`"
    if name in ("Grep", "Glob"):
        return f"**{name}** `{inp.get('pattern', '')}` in `{inp.get('path', '.')}`"
    if name in ("Agent", "Task"):
        return (f"**Spawned subagent** ({inp.get('subagent_type', 'general-purpose')}) — "
                f"{inp.get('description', '')}")
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate"):
        return f"**{name}** {clip(json.dumps(inp, ensure_ascii=False), 300)}"
    return f"**{name}** `{clip(json.dumps(inp, ensure_ascii=False), 300)}`"


def result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return clip(c or "", 1500)


def read_meta(transcript):
    """agent-<id>.meta.json sits next to a subagent transcript (agent type, description)."""
    if not transcript:
        return {}
    try:
        with open(os.path.splitext(transcript)[0] + ".meta.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------- rendering

def render(title, meta, entries, final_message=None, spawn_links=None):
    spawn_links = spawn_links or {}
    out = [f"# {title}", ""]
    for k, v in meta.items():
        if v:
            out.append(f"- **{k}:** {v}")
    out.append("")

    task = first_user_prompt(entries)
    if task:
        out += ["## Task", "", clip(task), ""]

    out += ["## Timeline", ""]
    step = 0
    seen_task = False
    for e in entries:
        kind = e.get("type")
        if kind not in ("user", "assistant"):
            continue
        when = ts(e.get("timestamp"))
        for b in blocks_of(e):
            bt = b.get("type")
            if kind == "user" and bt == "text":
                text = b.get("text", "").strip()
                if not text or is_injected(e, text):
                    continue
                if not seen_task and text == task:
                    seen_task = True
                    continue
                out += [f"### 👤 User · {when}", "", clip(text), ""]
            elif kind == "user" and bt == "tool_result" and INCLUDE_RESULTS:
                err = " ❌" if b.get("is_error") else ""
                out += [f"<details><summary>result{err}</summary>", "", "```",
                        result_text(b), "```", "</details>", ""]
            elif kind == "assistant" and bt == "thinking" and b.get("thinking"):
                out += [f"> 🧠 *thinking · {when}*", ">",
                        *["> " + l for l in clip(b["thinking"]).splitlines()], ""]
            elif kind == "assistant" and bt == "text" and b.get("text", "").strip():
                out += [f"### 💬 {when}", "", clip(b["text"].strip()), ""]
            elif kind == "assistant" and bt == "tool_use":
                step += 1
                line = tool_line(b.get("name", "?"), b.get("input"))
                link = spawn_links.get(b.get("id"))
                if link:
                    line += f" → [{link}]({link})"
                head, _, rest = line.partition("\n")
                out += [f"{step}. 🔧 {head} · {when}"] + ([rest] if rest else []) + [""]

    if final_message:
        out += ["## Final result", "", clip(final_message), ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- session state

class Session:
    def __init__(self, hook):
        self.hook = hook
        self.sid = hook.get("session_id", "unknown-session")
        self.transcript = hook.get("transcript_path", "")
        project = os.environ.get("CLAUDE_PROJECT_DIR") or hook.get("cwd") or os.getcwd()
        root = os.environ.get("AGENT_LOGBOOK_DIR") or os.path.join(project, "agent-logs")
        self.dir = os.path.join(root, self.sid)
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, ".index.json")

    # -- locking so parallel subagents don't grab the same agentN number
    def __enter__(self):
        self._lock = open(os.path.join(self.dir, ".lock"), "w")
        if fcntl:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            with open(self.index_path, encoding="utf-8") as f:
                self.index = json.load(f)
        except (OSError, json.JSONDecodeError):
            self.index = {"agents": {}}
        return self

    def __exit__(self, *exc):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, indent=2)
        if fcntl:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
        self._lock.close()

    # -- where subagent transcripts live
    def subagent_files(self):
        if not self.transcript:
            return []
        base = os.path.splitext(self.transcript)[0]  # .../<session_id>
        found = glob.glob(os.path.join(base, "subagents", "*.jsonl"))
        found += glob.glob(os.path.join(os.path.dirname(self.transcript), self.sid, "subagents", "*.jsonl"))
        return sorted(set(found), key=os.path.getmtime)

    def find_subagent_transcript(self, agent_id):
        p = self.hook.get("agent_transcript_path")
        if p and os.path.isfile(p):
            return p
        for f in self.subagent_files():
            if agent_id and agent_id in os.path.basename(f):
                return f
        return None

    def sidechain_entries(self, agent_id):
        """Older Claude Code versions keep subagent turns in the main transcript."""
        return [e for e in read_jsonl(self.transcript)
                if e.get("isSidechain") and e.get("agentId") == agent_id]

    def agent_number(self, agent_id):
        agents = self.index["agents"]
        if agent_id not in agents:
            agents[agent_id] = {"n": len(agents) + 1}
        return agents[agent_id]["n"]

    # -- writers
    def write_agent(self, agent_id, agent_type=None, final=None, path=None):
        path = path or self.find_subagent_transcript(agent_id)
        entries = read_jsonl(path) if path else self.sidechain_entries(agent_id)
        if not entries and not final:
            return
        n = self.agent_number(agent_id)
        rec = self.index["agents"][agent_id]
        meta = read_meta(path)
        rec["type"] = agent_type or meta.get("agentType") or rec.get("type") or next(
            (e.get("agentType") for e in entries if e.get("agentType")), None) or "subagent"
        rec["file"] = f"agent{n}.md"
        rec["prompt"] = first_user_prompt(entries)[:500]
        stamps = [e.get("timestamp") for e in entries if e.get("timestamp")]
        rec["started"], rec["ended"] = (stamps[0], stamps[-1]) if stamps else ("", "")
        if final:
            rec["final"] = final
        md = render(
            f"Agent {n} — {rec['type']}",
            {"Session": self.sid, "Agent ID": agent_id, "Type": rec["type"],
             "Started": rec["started"], "Ended": rec["ended"],
             "Tool calls": sum(1 for e in entries for b in blocks_of(e) if b.get("type") == "tool_use")},
            entries, final_message=rec.get("final") or last_assistant_text(entries),
        )
        with open(os.path.join(self.dir, rec["file"]), "w", encoding="utf-8") as f:
            f.write(md)

    def sweep_subagents(self):
        """Catch any subagent whose SubagentStop we missed (crash, workflow runner, etc.)."""
        for f in self.subagent_files():
            name = os.path.splitext(os.path.basename(f))[0]
            agent_id = name[len("agent-"):] if name.startswith("agent-") else name
            if agent_id not in self.index["agents"] or "file" not in self.index["agents"][agent_id]:
                self.write_agent(agent_id, path=f)

    def write_main(self, final=None):
        if final:
            self.index["main_final"] = final
        entries = [e for e in read_jsonl(self.transcript) if not e.get("isSidechain")]
        if not entries:
            return
        final = self.index.get("main_final") or last_assistant_text(entries)
        # link each "Spawned subagent" line to its agentN.md by matching the prompt
        by_prompt = {v.get("prompt", "")[:200]: v["file"]
                     for v in self.index["agents"].values() if v.get("file") and v.get("prompt")}
        links = {}
        for e in entries:
            for b in blocks_of(e):
                if b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                    p = ((b.get("input") or {}).get("prompt") or "").strip()[:200]
                    if p in by_prompt:
                        links[b.get("id")] = by_prompt[p]
        stamps = [e.get("timestamp") for e in entries if e.get("timestamp")]
        md = render(
            "Main agent",
            {"Session": self.sid, "Project": self.hook.get("cwd", ""),
             "Started": stamps[0] if stamps else "", "Ended": stamps[-1] if stamps else ""},
            entries, final_message=final, spawn_links=links,
        )
        with open(os.path.join(self.dir, "main.md"), "w", encoding="utf-8") as f:
            f.write(md)

    def write_index(self):
        rows = sorted(self.index["agents"].items(), key=lambda kv: kv[1]["n"])
        out = [f"# Session {self.sid}", "", "- [main.md](main.md) — main agent", "",
               "| # | File | Type | Started | Ended | Task |", "|---|---|---|---|---|---|"]
        for aid, r in rows:
            if not r.get("file"):
                continue
            task = (r.get("prompt") or "").splitlines()[0][:80] if r.get("prompt") else ""
            out.append(f"| {r['n']} | [{r['file']}]({r['file']}) | {r.get('type','')} | "
                       f"{ts(r.get('started'))} | {ts(r.get('ended'))} | {task.replace('|', '/')} |")
        with open(os.path.join(self.dir, "index.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------- entry point

def main():
    try:
        hook = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    event = hook.get("hook_event_name", "")
    with Session(hook) as s:
        if event == "SubagentStop":
            s.write_agent(hook.get("agent_id") or "unknown",
                          hook.get("agent_type"), hook.get("last_assistant_message"))
        elif event == "Stop":
            s.write_main(hook.get("last_assistant_message"))
        else:  # SessionEnd or manual run: final full pass
            s.sweep_subagents()
            s.write_main()
        s.write_index()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the user's session over logging
        print(f"agent-logbook: {exc}", file=sys.stderr)
    sys.exit(0)
