"""Claude client: Jarvis persona, short conversation memory, and the tool-use loop.

Run: python -m jarvis.llm "What's the weather like?"  to ask one question from the terminal.
"""

import logging
import sys
from collections import deque

import anthropic

from jarvis import config, tools

log = logging.getLogger(__name__)

SYSTEM = """You are Jarvis, a voice assistant running on a laptop in your owner's home in \
Kingston, Ontario. Your owner is an electrical engineering student.

Everything you write is converted to speech, so:
- Reply in one or two short spoken sentences unless asked for more.
- Plain words only: no markdown, lists, emoji, URLs or symbols. Write numbers and units the way \
you would say them, e.g. "nine degrees" not "9°C", "half past seven" not "19:30".

Personality: calm, capable, dry British wit. Address the owner as "sir" now and then, not in \
every reply.

Use your tools for the time, date, weather, timers, notes and this computer's status rather than \
guessing. If something can't be done with your tools, say so briefly. Your earlier replies in \
this conversation already used tools where needed, so trust them. The transcript comes from \
speech recognition and may contain misheard words; go with the most likely meaning. If no tool \
can do what was asked, say so instead of guessing. Do not include internal or system XML tags in \
your response."""

MAX_TOOL_ROUNDS = 5  # guard against a tool loop that never ends

IGNORE = "IGNORE"
FOLLOW_UP_NOTE = f"""This was picked up in the few seconds after your last reply, without the wake \
word, so it may not be meant for you: the owner could be talking to someone else in the room. \
If it is clearly a follow-up to you or a request you can help with, answer it normally. If it \
sounds like conversation with someone else, reply with exactly {IGNORE} and nothing else."""


def _thinking():
    # Thinking makes replies noticeably slower; with it off, the prompt line about tools keeps
    # Claude from writing a tool call out as text instead of making it.
    return {} if config.CLAUDE_THINKING else {"thinking": {"type": "disabled"}}


class Brain:
    def __init__(self):
        self._client = anthropic.Anthropic()
        # Finished turns as plain (question, reply) text. Old thinking and tool blocks aren't
        # replayed, so trimming the oldest turn never invalidates anything still sent.
        self._history = deque(maxlen=config.HISTORY_TURNS)

    def ask(self, text, follow_up=False):
        """Send the owner's words to Claude, running any tools it calls. Returns the reply.

        follow_up means it was heard without the wake word; Claude may then decide it wasn't
        meant for Jarvis, and ask returns None.
        """
        messages = []
        for question, reply in self._history:
            messages += [{"role": "user", "content": question},
                         {"role": "assistant", "content": reply}]
        messages.append({"role": "user", "content": text})
        if follow_up:
            # A system message rather than text in the user turn, so it can't read as the owner's words.
            messages.append({"role": "system", "content": FOLLOW_UP_NOTE})

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.messages.create(
                model=config.CLAUDE_MODEL, max_tokens=config.MAX_TOKENS, system=SYSTEM,
                tools=tools.SCHEMAS, messages=messages,
                output_config={"effort": config.CLAUDE_EFFORT}, **_thinking())
            u = response.usage
            log.info("claude %s: %d in, %d out, stop=%s", config.CLAUDE_MODEL,
                     u.input_tokens, u.output_tokens, response.stop_reason)

            if response.stop_reason == "refusal":
                reply = "I'm afraid I can't help with that one, sir."
                break
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                reply = " ".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    log.warning("reply cut off at max_tokens=%d", config.MAX_TOKENS)
                break

            # Within a turn, send back the whole response (thinking blocks included) unchanged.
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for call in calls:
                result, is_error = tools.run(call.name, call.input)
                log.info("tool %s(%s) -> %s", call.name, call.input, result[:200])
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": result, "is_error": is_error})
            messages.append({"role": "user", "content": results})  # all results in one message
        else:
            reply = "Sorry sir, I got tangled up there."

        if follow_up and reply.strip().rstrip(".").upper() == IGNORE:
            return None  # not kept in history either
        reply = reply or "Done."
        self._history.append((text, reply))
        return reply


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    brain = Brain()
    for question in sys.argv[1:] or ["What time is it?"]:
        print(f"> {question}\n{brain.ask(question)}")
