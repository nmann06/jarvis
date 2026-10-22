"""Project mode tools: hand a longer job to Claude Code on the owner's Claude plan (see
jarvis.project)."""

from jarvis import notify, project
from jarvis.tools import tool


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
      "few words.",
      name={"type": "string", "description": "A short name for the project, two to four words, "
            "e.g. '324 lab 4'"},
      task={"type": "string", "description": "The job, in full: the owner's request with any "
            "details from this conversation Claude Code will need (course, lab number, due date)"},
      folder={"type": "string", "description": "The project's OneDrive folder if you made one, "
              "e.g. 'School/ELEC 324/Lab 4', else empty"})
def start_project(name, task, folder):
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


@tool("List the owner's saved projects, most recent first.")
def list_projects():
    return project.all_names() or "No projects yet."
