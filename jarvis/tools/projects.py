"""Project mode tools: hand a longer job to Claude Code (see jarvis.project)."""

from jarvis import notify, project
from jarvis.tools import tool


@tool("Start a project: hand a longer job, like research on people or companies, a long "
      "discussion, or anything needing many web searches, to Claude Code, which works on it for "
      "minutes. Use only when the owner asks for a project. Its updates are spoken as it works "
      "and its full write-up goes to a Discord thread; by voice, what the owner says next goes "
      "to the project until they say \"end project\". Tell them that briefly.",
      name={"type": "string", "description": "A short name for the project, two to four words"},
      task={"type": "string",
            "description": "The job, in full: the owner's request with any details from this "
                           "conversation Claude Code will need"})
def start_project(name, task):
    p = project.start(name, task, voice=notify.channel.get() == "voice")
    return f"Started the {p.name} project."


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
