# Add Jarvis to nathanielmann.ca/projects

Instructions for an AI agent adding this project to the Projects page of Nathaniel's site.
You're connecting over SSH to the laptop Jarvis runs on (`hyde`). The website's source code
is **not** on this laptop. Use this machine only to read facts about Jarvis, and make the
site change wherever the site's repo lives.

## Rules on this laptop

Jarvis is running live here (systemd user service `jarvis`), so:

- **Read only.** Don't edit, commit or push anything in `~/jarvis` or `~/jarvis-project-mode`.
- **Don't stop or restart** the service, and don't run Jarvis modules: they use the mic and
  speakers, the Discord bot and paid APIs.
- **Don't open secrets:** `.env`, `.google_token.json`, `google_client.json`,
  `~/.git-credentials`, or anything in `data/` (login cookies, notes, email-related state).
- **Good sources:** `README.md`, `git log`, and the code in `jarvis/`.

## Match the existing entries

The page lists five projects. Each entry has this structure, in this order:

1. An image or slideshow (with the "←1 / N→" counter and pause button)
2. A two-digit index and an uppercase category, e.g. `01 / WEB APP`
3. The title, underlined
4. A short subtitle line
5. One summary paragraph, in first person and plain language, about 50 to 100 words
6. Tech tags separated by middots, e.g. `Node.js · Vanilla JavaScript · SVG`
7. Link buttons, where there are any, labelled like `GitHub ↗`

Copy the markup and styling of an existing entry with a GitHub link (Lettuce or Square Game)
rather than writing new styles. Add Jarvis as **06**, at the end, unless the site's source
clearly orders projects another way (for example by date). Projects don't have their own
detail pages, so don't make one.

## The entry

- **Index / category:** `06 / VOICE ASSISTANT`
- **Title:** Jarvis
- **Subtitle:** My own "Hey Jarvis" voice assistant
- **Summary:**

  > I built my own voice assistant that runs on a laptop at home. When I say "Hey Jarvis", it
  > records what I say, transcribes it on the laptop's GPU with Whisper, asks Claude what to do,
  > and answers out loud in a British voice, usually about three seconds after I stop talking.
  > It can check my calendar, assignments on OnQ, and my email, draft replies, add to my to-do
  > list, save notes to Google Drive, and set timers. It also runs as a Discord bot that sends me
  > reminders and a morning briefing. If it rambles, I can say "stop", and "continue" picks it
  > back up.

- **Tags:** `Python · Claude API · Whisper · Piper TTS · Discord.py`
- **Links:** `GitHub ↗` → https://github.com/nmann06/jarvis (the repo is public)

Keep the summary as written unless it breaks the page layout. If it's too long for the card,
cut the last sentence first.

## Images

There are no screenshots yet. Don't make up screenshots or fake UI. In order of preference:

1. Ask Nathaniel for a screenshot of the #jarvis Discord channel (a morning briefing or a
   reminder) and/or a photo of the laptop. Crop out email addresses, people's names and
   Discord IDs.
2. If you need something before then, make a simple diagram of how it works, in the site's
   colours and fonts, as SVG:
   `"Hey Jarvis" (openWakeWord) → speech-to-text (Whisper, on the GPU) → Claude + tools
   (calendar, email, OnQ, Trello, notes) → text-to-speech (Piper) → speaker`
   with Discord as a second way in alongside the microphone.

## Facts you can use

All of these come from the repo:

- **Timeline:** built 2026-10-20 to 2026-10-21, about 40 commits.
- **Hardware:** a laptop with an NVIDIA RTX 3060 Laptop GPU, with a Logitech Brio webcam as
  the microphone.
- **Speech:**
  - Wake word: openWakeWord's `hey_jarvis` model.
  - Speech-to-text: faster-whisper `large-v3-turbo` on the GPU, about 0.15 s per second of audio.
  - Voice: Piper (`en_GB-alan-medium`), about 0.13 s per sentence.
  - End of speech: detected with Silero VAD.
  - "Stop" while it's talking: heard by a small Vosk model, through echo cancellation.
- **Brain:** Claude Haiku 5.5 through the Anthropic API. It streams the reply, so Jarvis starts
  speaking after the first sentence. Typical time from the end of your speech to its voice is
  about 2.7 to 3.3 seconds.
- **Tools:**
  - Apple Calendar (iCloud CalDAV)
  - Gmail (read and draft, never sends)
  - A Google Sheet for job applications
  - Trello to-dos
  - Notes saved as Google Docs
  - OnQ, Queen's course site (assignments, quizzes, grades, announcements, syllabi; read-only)
  - Weather, timers, and laptop status
- **Discord:** the same assistant as a bot. It posts a morning briefing, reminders before
  assignments and events, and alerts for important emails.
- **Privacy design:** audio never leaves the laptop. Only the transcribed text goes to Claude,
  and email text is marked as untrusted so instructions inside an email are ignored.

## Leave out

- Anything still in progress on other branches (for example `project-mode`). Describe only
  what's on `main`.
- Names of people from emails, account emails or usernames, Discord IDs, Nathaniel's address,
  and anything from `data/` or `.env`.
- Costs and API keys.

## When you're done

Check the page at phone width and at desktop width. Make sure the new entry's spacing, numbering
and slideshow controls match the other five, and the GitHub link opens the repo. Then show
Nathaniel the change before you publish it.
