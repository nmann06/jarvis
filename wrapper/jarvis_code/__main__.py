"""Open the Jarvis window in this folder.

    jarvis                    a new conversation
    jarvis -c                 continue the last conversation here
    jarvis -r [id]            resume one (without an id, pick from a list)
    jarvis --model sonnet     start on another model (also --effort, --mode)
    jarvis -p "fix the tests" start with a prompt
"""

import argparse
import os
import sys

MODES = ["default", "acceptEdits", "plan", "auto", "bypassPermissions"]


def parse(argv):
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis: Claude Code, in a window of its own.")
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true",
                        help="continue the most recent conversation in this folder")
    parser.add_argument("-r", "--resume", nargs="?", const="", default=None, metavar="ID",
                        help="resume a conversation (pick one if no id is given)")
    parser.add_argument("-p", "--prompt", help="send this as the first message")
    parser.add_argument("--model", help="model to start with (opus, sonnet, haiku, fable, or a full name)")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "auto"])
    parser.add_argument("--mode", "--permission-mode", dest="mode", choices=MODES,
                        help="permission mode to start in (shift+tab switches later)")
    parser.add_argument("--plain", action="store_true", help="answer as plain Claude Code, without the Jarvis persona")
    parser.add_argument("--thinking", choices=["summarized", "omitted"],
                        default=os.environ.get("JARVIS_THINKING", "summarized"),
                        help="show summaries of Claude's thinking (default), or not")
    parser.add_argument("--log", metavar="FILE", help="write everything said with Claude Code to FILE")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    from .app import JarvisApp, Options  # after parsing, so --help is instant

    options = Options(cwd=os.getcwd(), model=args.model, effort=args.effort,
                      permission_mode=args.mode, resume=args.resume, continue_last=args.continue_last,
                      prompt=args.prompt, persona=not args.plain, thinking_display=args.thinking,
                      log_path=args.log)
    tty = sys.stdout.isatty()
    if tty:
        sys.stdout.write("\033[22;0t\033]0;Jarvis\007")  # keep the old title, show Jarvis
        sys.stdout.flush()
    try:
        session = JarvisApp(options).run()
    finally:
        if tty:
            sys.stdout.write("\033[23;0t")
            sys.stdout.flush()
    if session and tty:
        print(f"\033[2mResume this conversation with: jarvis -r {session}\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
