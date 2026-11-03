"""Project mode tools: hand a longer job to Claude Code on the owner's Claude plan (see
jarvis.project)."""

from datetime import datetime

from jarvis import notify, project
from jarvis.tools import tool
from jarvis.tools.clock import TZ


@tool("Start a project: hand a longer job (schoolwork such as a prelab, lab report or "
      "assignment, research on people or companies, a long discussion, anything needing many "
      "steps or web searches) to Claude Code on the owner's Claude plan, which works on it for "
      "minutes and can read OnQ and save files to OneDrive. When the owner says \"new project\" "
      "without saying what it is, ask what they're working on. Then make a OneDrive folder for "
      "it inside the matching course or subject folder (find that folder with onedrive_list or "
      "onedrive_search; skip this if OneDrive isn't set up), say what you're making while you "
      "make it, then say \"Done. How shall I start?\" or similar; their answer is the task. Its "
      "spoken updates come as it works and its full write-up goes to a Discord thread. While "
      "it's open, questions and instructions about its work go to it (project_message) and "
      "anything else you answer yourself, until they say \"end project\". Tell them that in a "
      "few words. It can also start later (\"start a project at 3am: do my prelab\"): give "
      "start_at, still making the folder now. A delayed project works on its own in Discord "
      "only, with nothing said aloud, and you'll hear how it went to tell the owner later; "
      "confirm when it will start (\"I'll start the prelab project at three o'clock.\").",
      name={"type": "string", "description": "A short name for the project, two to four words, "
            "e.g. '324 lab 4'"},
      task={"type": "string", "description": "The job, in full: the owner's request with any "
            "details from this conversation Claude Code will need (course, lab number, due date)"},
      folder={"type": "string", "description": "The project's OneDrive folder if you made one, "
              "e.g. 'School/ELEC 324/Lab 4', else empty"},
      start_at={"type": "string", "description": "When to start it, in local time "
                "'YYYY-MM-DD HH:MM' (a time already past today means tomorrow), or empty to "
                "start now"})
def start_project(name, task, folder, start_at):
    if start_at.strip():
        now = datetime.now(TZ)
        at = project.start_time(start_at, now)
        project.schedule(name, task, folder, at)
        return (f"The {name} project will start at {project.written(at, now)}. It will work "
                "in Discord only: nothing said aloud, its write-up in a Discord thread.")
    p = project.start(name, task, voice=notify.channel.get() == "voice", folder=folder)
    return f"Started the {p.name} project."


@tool("Pass the owner's words to a project's Claude Code: questions about the work in it (their "
      "homework, lab, code or files for it) or instructions for it. Claude Code reads their "
      "files (OneDrive, OnQ), works it out and answers aloud itself, so just say in a few words "
      "that you've passed it on. Don't use it for anything not about the project's work.",
      message={"type": "string", "description": "What the owner said, in their words (fix "
               "obvious mishearings)"},
      name={"type": "string", "description": "Which project, or empty for the one that's open"})
def project_message(message, name):
    p = (project.find(name) if name else None) or project.voice_project() or project.latest()
    if p is None:
        return "No project is open. Start one with start_project, or name a saved one."
    if notify.channel.get() == "voice":
        project.open_for_voice(p)
    if p.send(message):
        return (f"Passed to the {p.name} project. It's still finishing the last thing, so it "
                "will get to this right after.")
    return f"Passed to the {p.name} project; it will answer aloud."


@tool("Go back to a saved project and, optionally, send it a message.",
      name={"type": "string", "description": "The project's name, or part of it"},
      message={"type": "string", "description": "What to tell it, or empty to just reopen it"})
def resume_project(name, message):
    p = project.find(name)
    if p is None:
        saved = ", ".join(project.all_names()) or "none"
        return f"No project called {name}. Saved projects: {saved}."
    if notify.channel.get() == "voice":
        project.open_for_voice(p)
    if message:
        p.send(message)
    return f"Reopened the {p.name} project." + ("" if message else " It's waiting for a message.")


@tool("List the owner's saved projects, most recent first, and delayed ones waiting to start.")
def list_projects():
    now = datetime.now(TZ)
    waiting = [{"name": s["name"], "starts": project.written(s["at"], now), "task": s["task"]}
               for s in project.scheduled()]
    saved = project.all_names()
    if not saved and not waiting:
        return "No projects yet."
    return {"saved": saved, "waiting_to_start": waiting}


@tool("Cancel a delayed project that hasn't started yet (one set with start_project's "
      "start_at). Its OneDrive folder stays.",
      name={"type": "string", "description": "The project's name, or part of it; empty if "
            "there's only one waiting"})
def cancel_delayed_project(name):
    gone = project.cancel(name)
    if not gone:
        waiting = project.scheduled()
        if not waiting:
            return "No delayed projects are waiting to start."
        now = datetime.now(TZ)
        return ("None matched. Waiting to start: " + "; ".join(
            f"{s['name']} at {project.written(s['at'], now)}" for s in waiting) + ".")
    now = datetime.now(TZ)
    return "Cancelled: " + "; ".join(
        f"the {s['name']} project, which was to start at {project.written(s['at'], now)}"
        for s in gone) + "."
