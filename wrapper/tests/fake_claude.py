"""A stand-in for `claude -p` in stream-json mode, for testing the window without spending
anything: it answers the control requests Jarvis sends and plays a scripted turn for each
prompt, picked by a keyword in it (hello, think, edit, question, plan, agent, slow).

Every line it receives is appended to $FAKE_CLAUDE_LOG, so tests can check what was sent.
"""

import json
import os
import queue
import sys
import threading
import time
import uuid

LOG = os.environ.get("FAKE_CLAUDE_LOG")
state = {"session": str(uuid.uuid4())}
STARTED = time.monotonic()
lock = threading.Lock()
answers = {}            # request_id -> response, for our can_use_tool requests
answered = threading.Condition()
interrupted = threading.Event()
mode = {"value": "default"}


def emit(obj):
    obj.setdefault("session_id", state["session"])
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def log(line):
    if LOG:
        with open(LOG, "a") as f:
            f.write(line.rstrip("\n") + "\n")


def control_response(request_id, response):
    emit({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                   "response": response}})


def ask(tool_name, tool_input, tool_use_id, suggestions=None, interaction=False):
    request_id = str(uuid.uuid4())
    request = {"subtype": "can_use_tool", "tool_name": tool_name, "display_name": tool_name,
               "input": tool_input, "tool_use_id": tool_use_id,
               "permission_suggestions": suggestions or []}
    if interaction:
        request["requires_user_interaction"] = True
    emit({"type": "control_request", "request_id": request_id, "request": request})
    with answered:
        while request_id not in answers:
            answered.wait(0.1)
        return answers.pop(request_id)


def stream_text(message_id, index, text, delay=0.0):
    emit({"type": "stream_event", "event": {"type": "content_block_start", "index": index,
                                            "content_block": {"type": "text", "text": ""}}})
    for word in text.split(" "):
        if interrupted.is_set():
            return False
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": index,
                                                "delta": {"type": "text_delta", "text": word + " "}}})
        time.sleep(delay)
    emit(assistant(message_id, [{"type": "text", "text": text + " "}]))
    emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": index}})
    return True


def assistant(message_id, content, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": message_id, "model": "claude-haiku-5-5", "role": "assistant",
                        "content": content}}


def message_start(message_id):
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {
        "id": message_id, "model": "claude-haiku-5-5", "usage": {"output_tokens": 1}}}})


def message_stop(tokens=42):
    emit({"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                            "usage": {"output_tokens": tokens}}})
    emit({"type": "stream_event", "event": {"type": "message_stop"}})


def tool_use(message_id, tool_id, name, tool_input, index=0):
    emit({"type": "stream_event", "event": {"type": "content_block_start", "index": index,
                                            "content_block": {"type": "tool_use", "id": tool_id,
                                                              "name": name, "input": {}}}})
    emit(assistant(message_id, [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]))
    emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": index}})


def tool_result(tool_id, content, data=None, is_error=False, parent=None):
    emit({"type": "user", "parent_tool_use_id": parent, "tool_use_result": data,
          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                   "content": content, "is_error": is_error}]}})


def result(text="", is_error=False, subtype="success"):
    emit({"type": "result", "subtype": subtype, "is_error": is_error, "result": text,
          "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0.0})


def turn(prompt):
    interrupted.clear()
    emit({"type": "system", "subtype": "init", "session_id": state["session"], "model": "claude-haiku-5-5",
          "permissionMode": mode["value"], "tools": ["Bash", "Edit", "Read"], "mcp_servers": []})
    emit({"type": "user", "isReplay": True, "parent_tool_use_id": None,
          "message": {"role": "user", "content": prompt}})
    emit({"type": "system", "subtype": "status", "status": "requesting"})
    mid = "msg_" + uuid.uuid4().hex[:8]
    message_start(mid)
    p = prompt.lower().rsplit("\n", 1)[-1]  # the words typed, after any ! output sent along

    if p.startswith("/model"):
        emit({"type": "assistant", "parent_tool_use_id": None, "local_command_source": "x",
              "message": {"id": mid, "model": "<synthetic>", "role": "assistant",
                          "content": [{"type": "text", "text": "Set model to `Sonnet 5.5` for this session only"}]}})
        result()
        return
    if p.startswith("/clear"):
        old, state["session"] = state["session"], str(uuid.uuid4())
        emit({"type": "conversation_reset", "new_conversation_id": state["session"], "session_id": old,
              "trigger": "clear"})
        result()
        return
    if p.startswith("/"):
        emit({"type": "assistant", "parent_tool_use_id": None,
              "message": {"id": mid, "model": "<synthetic>", "role": "assistant",
                          "content": [{"type": "text", "text": f"ran {prompt}"}]}})
        result()
        return

    if "think" in p:
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                "content_block": {"type": "thinking", "thinking": ""}}})
        for part in ("Seventeen times ", "twenty-three is 391."):
            emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                    "delta": {"type": "thinking_delta", "thinking": part}}})
        emit(assistant(mid, [{"type": "thinking", "thinking": "Seventeen times twenty-three is 391."}]))
        emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})
        stream_text(mid, 1, "It is **391**, sir.")
    elif "edit" in p:
        path = os.path.join(os.getcwd(), "notes.txt")
        tool_input = {"file_path": path, "old_string": "alpha", "new_string": "beta"}
        tool_use(mid, "toolu_edit", "Edit", tool_input)
        decision = ask("Edit", tool_input, "toolu_edit",
                       [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}])
        if decision.get("behavior") == "allow":
            for update in decision.get("updatedPermissions") or []:
                if update.get("type") == "setMode":
                    mode["value"] = update["mode"]
                    emit({"type": "system", "subtype": "status", "status": None, "permissionMode": mode["value"]})
            tool_result("toolu_edit", "The file has been updated.", {
                "filePath": path, "oldString": "alpha", "newString": "beta",
                "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1,
                                     "lines": ["-alpha", "+beta"]}]})
            message_stop()
            mid = "msg_" + uuid.uuid4().hex[:8]
            message_start(mid)
            stream_text(mid, 0, "Changed alpha to beta.")
        else:
            tool_result("toolu_edit", decision.get("message", "rejected"), "Error: rejected", is_error=True)
            if decision.get("interrupt"):
                result("", subtype="success")
                return
    elif "question" in p:
        tool_input = {"questions": [{"question": "Tea or coffee?", "header": "Drink", "multiSelect": False,
                                     "options": [{"label": "Tea", "description": "Earl Grey"},
                                                 {"label": "Coffee", "description": "Black"}]}]}
        tool_use(mid, "toolu_q", "AskUserQuestion", tool_input)
        decision = ask("AskUserQuestion", tool_input, "toolu_q", interaction=True)
        picked = (decision.get("updatedInput") or {}).get("answers", {})
        tool_result("toolu_q", f"answers: {picked}", {"questions": tool_input["questions"], "answers": picked})
        stream_text(mid, 1, f"You chose {picked.get('Tea or coffee?', 'nothing')}.")
    elif "plan" in p:
        tool_input = {"plan": "# Plan\n\n1. Add snooze\n2. Test it"}
        tool_use(mid, "toolu_plan", "ExitPlanMode", tool_input)
        decision = ask("ExitPlanMode", tool_input, "toolu_plan", interaction=True)
        for update in decision.get("updatedPermissions") or []:
            if update.get("type") == "setMode":
                mode["value"] = update["mode"]
                emit({"type": "system", "subtype": "status", "status": None, "permissionMode": mode["value"]})
        if decision.get("behavior") == "allow":
            tool_result("toolu_plan", "User has approved your plan.", {"plan": tool_input["plan"]})
            stream_text(mid, 1, "Starting on the plan.")
        else:
            tool_result("toolu_plan", decision.get("message", ""), None, is_error=True)
    elif "agent" in p:
        tool_use(mid, "toolu_agent", "Agent", {"description": "List files", "subagent_type": "Explore",
                                               "prompt": "list them"})
        emit({"type": "system", "subtype": "task_started", "tool_use_id": "toolu_agent",
              "description": "List files", "task_type": "local_agent"})
        emit(assistant("msg_sub", [{"type": "tool_use", "id": "toolu_sub1", "name": "Bash",
                                    "input": {"command": "ls"}}], parent="toolu_agent"))
        tool_result("toolu_sub1", "a.txt\nb.txt", {"stdout": "a.txt\nb.txt", "stderr": ""}, parent="toolu_agent")
        tool_result("toolu_agent", [{"type": "text", "text": "Two files."}],
                    {"status": "completed", "totalToolUseCount": 1, "totalTokens": 1500,
                     "totalDurationMs": 2100, "content": [{"type": "text", "text": "Two files."}]})
        stream_text(mid, 1, "There are two files.")
    elif "bash" in p:
        tool_input = {"command": "ls -la", "description": "List files"}
        tool_use(mid, "toolu_bash", "Bash", tool_input)
        decision = ask("Bash", tool_input, "toolu_bash", [
            {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls:*"}],
             "behavior": "allow", "destination": "localSettings"}])
        out = "\n".join(f"file{i}.txt" for i in range(12))
        tool_result("toolu_bash", out, {"stdout": out, "stderr": ""})
        stream_text(mid, 1, "Twelve files.")
    elif "slow" in p:
        if not stream_text(mid, 0, " ".join(["word"] * 60), delay=0.03):
            result("", subtype="error_during_execution", is_error=True)
            return
    elif "tasks" in p:
        tool_use(mid, "toolu_tc", "TaskCreate", {"subject": "Write the tests", "activeForm": "Writing the tests"})
        tool_result("toolu_tc", "Task #1 created", {"task": {"id": "1", "subject": "Write the tests"}})
        tool_use(mid, "toolu_tu", "TaskUpdate", {"taskId": "1", "status": "in_progress"}, index=1)
        tool_result("toolu_tu", "Updated task #1 status", {"success": True})
        stream_text(mid, 2, "On it.")
    elif "start background" in p:
        emit({"type": "system", "subtype": "task_started", "task_id": "b1", "is_backgrounded": True,
              "description": "npm run dev", "task_type": "local_bash"})
        stream_text(mid, 0, "Started the dev server.")
    elif "stop background" in p:
        emit({"type": "system", "subtype": "task_notification", "task_id": "b1", "status": "completed"})
        stream_text(mid, 0, "Stopped.")
    elif "table" in p:
        stream_text(mid, 0, "Here you are, sir:\n\n| Category | Tokens | Share |\n|---|---|---|\n"
                    "| System prompt | 3.3k | 0.3% |\n| Skills | 5.3k | 0.5% |\n| Messages | 6.2k | 0.6% |\n\n"
                    "## Next steps\n\n1. Read `timers.py`\n2. Add a **snooze**\n\n```python\ndef snooze(minutes):\n    return minutes * 60\n```")
    else:
        stream_text(mid, 0, "Hello, sir. How may I help?")
    message_stop()
    if not interrupted.is_set():
        result("ok")


CONFIG_KEYS = {"autoCompact": "autoCompactEnabled", "verbose": "verbose", "theme": "theme",
               "permissionMode": "permissions.defaultMode", "language": "language", "model": "model"}
CONFIG_USAGE = """Usage: /config key=value [key=value ...]
  autoCompact=true|false
  verbose=true|false
  permissionMode=default|plan|acceptEdits|auto|dontAsk
  theme=auto|dark|light|light-daltonized|dark-daltonized|light-ansi|dark-ansi
  language=<value>
  model=default|sonnet|opus|haiku"""


def read_settings():
    try:
        with open(os.environ["FAKE_SETTINGS"]) as f:
            return json.load(f)
    except (KeyError, OSError, ValueError):
        return {}


def print_mode(prompt):
    """`claude -p "/config ..." --no-session-persistence`: the window's side commands."""
    if prompt.strip() == "/config":
        print(CONFIG_USAGE)
        return
    if prompt.startswith("/config "):
        settings = read_settings()
        for pair in prompt.split()[1:]:
            key, value = pair.split("=", 1)
            path = CONFIG_KEYS.get(key, key).split(".")
            target = settings
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = {"true": True, "false": False}.get(value, value)
            print(f"Set {key} to {value}")
        with open(os.environ["FAKE_SETTINGS"], "w") as f:
            json.dump(settings, f)
        return
    if "<message>" in prompt:  # the window asking for a conversation's title
        print("Notes About Alpha")
        return
    print(f"ran {prompt}")


MCP = [
    {"name": "claude.ai Jarvis", "status": "connected", "scope": "claudeai", "source": "claudeai",
     "serverInfo": {"name": "jarvis", "version": "1.0"},
     "config": {"type": "claudeai-proxy", "url": "https://hyde.example.ts.net/mcp/SECRET-TOKEN"},
     "tools": [{"name": "get_weather", "annotations": {"readOnly": True}}, {"name": "add_note", "annotations": {}}]},
    {"name": "claude.ai Gmail", "status": "needs-auth", "scope": "claudeai", "source": "claudeai", "config": {}},
]

USAGE = {
    "session": {"total_cost_usd": 0.12, "total_api_duration_ms": 4000, "total_duration_ms": 9000},
    "subscription_type": "pro", "rate_limits_available": True,
    "rate_limits": {
        "limits": [{"kind": "session", "percent": 62, "resets_at": "2026-10-23T20:10:00+00:00"},
                   {"kind": "weekly_all", "percent": 23, "resets_at": "2026-10-29T07:00:00+00:00"}],
        "seven_day_breakdown": {"rows": [{"display_name": "Claude Code", "percent": 98},
                                         {"display_name": "Chats", "percent": 2}]},
        "spend": {"enabled": False}},
    "behaviors": {
        "day": {"request_count": 750, "session_count": 17,
                "recommendations": [{"pct": 60, "title": "Start fresh when you switch tasks", "detail": "run /clear"},
                                    {"pct": 28, "title": "Run /plugin-authoring on a cheaper model"}],
                "fix": {"keys": ["skill"], "messages": ["Make the plugin-authoring skill cheaper."]},
                "skills": [{"name": "plugin-authoring", "pct": 28}],
                "mcp_servers": [{"name": "claude.ai Jarvis", "pct": 1}]},
        "week": {"request_count": 2007, "session_count": 51,
                 "recommendations": [{"pct": 68, "title": "Start fresh when you switch tasks"}],
                 "fix": {}, "skills": [{"name": "claude-api", "pct": 23}]}},
}


def work(prompts):
    while (prompt := prompts.get()) is not None:
        turn(prompt)


def main():
    log(json.dumps({"argv": sys.argv[1:]}))
    if len(sys.argv) > 2 and sys.argv[1] == "-p" and not sys.argv[2].startswith("--"):
        print_mode(sys.argv[2])
        return
    prompts = queue.Queue()
    worker = threading.Thread(target=work, args=(prompts,), daemon=True)
    worker.start()
    for line in sys.stdin:
        log(line)
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind = msg.get("type")
        if kind == "control_request":
            request = msg.get("request") or {}
            sub = request.get("subtype")
            rid = msg.get("request_id")
            if sub == "initialize":
                control_response(rid, {
                    "commands": [{"name": "compact", "description": "Free up context", "argumentHint": ""},
                                 {"name": "model", "description": "Set the AI model", "argumentHint": "<model>"},
                                 {"name": "clear", "description": "Start a new session", "argumentHint": ""},
                                 {"name": "context", "description": "Show current context usage", "argumentHint": ""}],
                    "models": [{"value": "default", "resolvedModel": "claude-haiku-5-5", "displayName": "Default"},
                               {"value": "haiku", "resolvedModel": "claude-haiku-5-5", "displayName": "Haiku 5.5",
                                "description": "Fastest", "supportedEffortLevels": ["low", "medium", "high"]},
                               {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet 5.5",
                                "description": "Efficient"}],
                    "account": {"email": "test@example.com", "subscriptionType": "Claude Pro"},
                    "claude_code_version": "9.9.9", "current_permission_mode": mode["value"]})
            elif sub == "get_settings":
                hooks = {}
                if os.environ.get("FAKE_HOOK_OUT"):
                    hooks = {"Notification": [{"matcher": "permission_prompt|elicitation_dialog", "hooks": [
                        {"type": "command", "command": f"cat > {os.environ['FAKE_HOOK_OUT']}", "timeout": 5}]}]}
                control_response(rid, {"applied": {"model": "claude-haiku-5-5", "effort": "high"},
                                       "effective": {"hooks": hooks, **read_settings()}})
            elif sub == "get_status":
                control_response(rid, {"sections": [
                    {"title": "Session", "rows": [{"label": "Version", "value": "9.9.9"},
                                                  {"label": "Session ID", "value": state["session"]},
                                                  {"label": "Email", "value": "test@example.com"}]},
                    {"title": "Environment", "rows": [{"label": "Model", "value": "claude-haiku-5-5"},
                                                      {"label": "Setting sources", "value": "User settings"}]}]})
            elif sub == "get_usage":
                # The real one also names each window; the 5-hour one resets in two hours here.
                five_hour = {"utilization": 62, "resets_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 7200))}
                control_response(rid, {**USAGE, "rate_limits": {**USAGE["rate_limits"], "five_hour": five_hour}})
            elif sub == "get_session_cost":
                control_response(rid, {"text": "Total cost:            $0.1200\nTotal duration (API):  4s"})
            elif sub in ("mcp_reconnect", "mcp_toggle", "mcp_clear_auth"):
                for server in MCP:
                    if server["name"] == request.get("serverName") and sub == "mcp_toggle":
                        server["status"] = "connected" if request.get("enabled") else "disabled"
                control_response(rid, {})
            elif sub == "get_context_usage":
                control_response(rid, {"percentage": 3, "totalTokens": 3000, "maxTokens": 100000})
            elif sub == "set_permission_mode":
                mode["value"] = request.get("mode")
                control_response(rid, {"mode": mode["value"]})
            elif sub == "interrupt":
                interrupted.set()
                control_response(rid, {"still_queued": []})
            elif sub == "mcp_status":
                late = float(os.environ.get("FAKE_MCP_LATE", "0"))  # seconds until they connect
                connected = time.monotonic() - STARTED >= late
                control_response(rid, {"mcpServers": MCP if connected else []})
            else:
                control_response(rid, {})
        elif kind == "control_response":
            response = msg.get("response") or {}
            with answered:
                answers[response.get("request_id")] = response.get("response") or {}
                answered.notify_all()
        elif kind == "user":
            content = msg["message"]["content"]
            text = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content if b.get("type") == "text")
            prompts.put(text)
    prompts.put(None)
    worker.join(timeout=5)


if __name__ == "__main__":
    main()
