"""Phase 1 check: send one prompt to Claude and print the reply and token usage.

Usage: python -m tests.claude_hello
"""

import time

import anthropic

from jarvis import config


def main():
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY, which config loaded from .env
    start = time.perf_counter()
    reply = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=300,
                                   messages=[{"role": "user", "content": "Say hello in five words"}])
    elapsed = time.perf_counter() - start
    print(f"> {reply.content[0].text}")
    print(f"{config.CLAUDE_MODEL}: {elapsed:.2f}s, "
          f"{reply.usage.input_tokens} tokens in, {reply.usage.output_tokens} out")


if __name__ == "__main__":
    main()
