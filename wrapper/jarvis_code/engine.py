"""Claude Code, running headless behind the Jarvis window.

`claude -p` with stream-json both ways: user messages go in on stdin, and everything Claude
Code does comes back on stdout as JSON lines. Each side can also send the other control
requests: Jarvis asks to interrupt, switch permission mode or read settings; Claude Code asks
whether it may use a tool (`can_use_tool`), which the window answers with a dialog.
"""

import asyncio
import collections
import json
import os
import shlex
import shutil
import uuid

# Set by the Claude Code session a shell may be running inside. Inherited, they make the
# engine think it is a child of that session (and CLAUDE_EFFORT overrides the user's effort).
SESSION_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
    "CLAUDE_EFFORT", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "AI_AGENT",
)

LINE_LIMIT = 256 * 1024 * 1024  # one JSON line can carry a whole file


class EngineError(Exception):
    pass


def claude_path():
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def claude_command(command=None):
    """The program to run as Claude Code: a test double, $JARVIS_CLAUDE, or `claude`."""
    if command:
        return list(command)
    override = os.environ.get("JARVIS_CLAUDE")  # another Claude Code build, or a test double
    return shlex.split(override) if override else [claude_path()]


def clean_environment():
    """This process's environment without a surrounding Claude Code session's variables."""
    return {k: v for k, v in os.environ.items() if k not in SESSION_VARS}


class Engine:
    """One `claude -p` process: start it, talk to it, stop it.

    on_message(msg) is awaited for every line Claude Code sends that isn't an answer to one
    of our control requests, in order, ending with {"type": "_exit"} when the process goes.
    on_permission(request_id, request) is called (not awaited) for each can_use_tool request;
    answer it with respond().
    """

    def __init__(self, cwd, on_message, on_permission, *, model=None, effort=None,
                 permission_mode=None, resume=None, continue_last=False, fork=False,
                 append_system_prompt=None, thinking_display="summarized", add_dirs=(),
                 command=None, log_path=None):
        self.cwd = cwd
        self.on_message = on_message
        self.on_permission = on_permission
        self.model, self.effort, self.permission_mode = model, effort, permission_mode
        self.resume, self.continue_last, self.fork = resume, continue_last, fork
        self.append_system_prompt = append_system_prompt
        self.thinking_display = thinking_display
        self.add_dirs = list(add_dirs)
        self.command = command  # replaces `claude` (tests run a fake engine)
        self.log = open(log_path, "a", buffering=1) if log_path else None
        self.proc = None
        self.info = {}
        self.stderr = collections.deque(maxlen=60)
        self.returncode = None
        self._pending = {}
        self._write_lock = asyncio.Lock()
        self._tasks = []

    def argv(self):
        argv = claude_command(self.command)
        argv += ["-p", "--input-format", "stream-json", "--output-format", "stream-json",
                 "--verbose", "--include-partial-messages", "--replay-user-messages",
                 "--permission-prompt-tool", "stdio"]
        if self.thinking_display:
            argv += ["--thinking-display", self.thinking_display]
        if self.append_system_prompt:
            argv += ["--append-system-prompt", self.append_system_prompt]
        for flag, value in (("--model", self.model), ("--effort", self.effort),
                            ("--permission-mode", self.permission_mode),
                            ("--resume", self.resume)):
            if value:
                argv += [flag, value]
        if self.continue_last and not self.resume:
            argv.append("--continue")
        if self.fork and (self.resume or self.continue_last):
            argv.append("--fork-session")
        for path in self.add_dirs:
            argv += ["--add-dir", path]
        return argv

    def environment(self):
        env = clean_environment()
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
        return env

    async def start(self):
        """Start Claude Code and complete the handshake; returns what it reported about itself."""
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv(), cwd=self.cwd, env=self.environment(), limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._read_stderr())]
        self.info = await self.request("initialize", timeout=60)
        return self.info

    @property
    def running(self):
        return self.proc is not None and self.returncode is None

    async def send(self, content):
        """A user message: a string, or a list of content blocks."""
        await self._write({"type": "user", "message": {"role": "user", "content": content},
                           "parent_tool_use_id": None, "session_id": ""})

    async def request(self, subtype, timeout=30, **fields):
        """A control request; returns Claude Code's response, or raises EngineError."""
        if not self.running:
            raise EngineError("Claude Code isn't running")
        request_id = f"jarvis-{uuid.uuid4().hex[:12]}"
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda f: f.cancelled() or f.exception())  # never "unretrieved"
        self._pending[request_id] = future
        try:
            await self._write({"type": "control_request", "request_id": request_id,
                               "request": {"subtype": subtype, **fields}})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise EngineError(f"Claude Code didn't answer {subtype}") from None
        finally:
            self._pending.pop(request_id, None)

    async def respond(self, request_id, response):
        """Answer one of Claude Code's control requests (a permission decision)."""
        await self._write({"type": "control_response", "response": {
            "subtype": "success", "request_id": request_id, "response": response}})

    async def interrupt(self):
        return await self.request("interrupt", timeout=10)

    async def stop(self):
        if self.proc is None:
            return
        if self.returncode is None:
            try:
                self.proc.stdin.close()
            except (OSError, RuntimeError):
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 3)
                except asyncio.TimeoutError:
                    self.proc.kill()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.log:
            self.log.close()
            self.log = None

    async def _write(self, obj):
        line = json.dumps(obj) + "\n"
        self._trace(">", line)
        async with self._write_lock:
            if not self.running:
                raise EngineError("Claude Code isn't running")
            try:
                self.proc.stdin.write(line.encode())
                await self.proc.stdin.drain()
            except (ConnectionError, OSError) as e:
                raise EngineError(f"Claude Code stopped listening ({e})") from None

    def _trace(self, direction, line):
        if self.log:
            self.log.write(f"{direction} {line if line.endswith(chr(10)) else line + chr(10)}")

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                self._trace("<", line.decode(errors="replace"))
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                kind = msg.get("type")
                if kind == "control_response":
                    self._resolve(msg.get("response") or {})
                elif kind == "control_request":
                    await self._handle_request(msg)
                else:
                    await self.on_message(msg)
        except (ValueError, OSError) as e:  # a line past LINE_LIMIT, or the pipe broke
            self.stderr.append(f"Jarvis stopped reading Claude Code: {e}")
            self.proc.kill()
        # stdout has closed, so the process is on its way out (cancellation skips all this)
        self.returncode = await self.proc.wait()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(EngineError("Claude Code stopped"))
        await asyncio.sleep(0.05)  # let stderr's last lines in
        await self.on_message({"type": "_exit", "code": self.returncode,
                               "stderr": "\n".join(self.stderr)})

    def _resolve(self, response):
        future = self._pending.get(response.get("request_id"))
        if future is None or future.done():
            return
        if response.get("subtype") == "error":
            future.set_exception(EngineError(response.get("error") or "control request failed"))
        else:
            future.set_result(response.get("response") or {})

    async def _handle_request(self, msg):
        request = msg.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            self.on_permission(msg["request_id"], request)
            return
        # Hook callbacks and SDK MCP servers belong to SDK hosts; Jarvis registers neither.
        await self._write({"type": "control_response", "response": {
            "subtype": "error", "request_id": msg.get("request_id"),
            "error": f"Jarvis doesn't handle {request.get('subtype')}"}})

    async def _read_stderr(self):
        while line := await self.proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            self._trace("!", text)
            if text:
                self.stderr.append(text)
