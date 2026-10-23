"""Files to offer after an @: the project's files (git's view of them when it's a repo),
matched the forgiving way Claude Code matches them."""

import asyncio
import os

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".cache", "dist", "build", ".idea", ".tox"}
MAX_FILES = 20000


class FileIndex:
    def __init__(self, cwd):
        self.cwd = cwd
        self.paths = []
        self.loaded = False

    async def load(self):
        paths = await self._git_files()
        if not paths:  # not a repo, or a folder git ignores
            paths = await asyncio.to_thread(self._walk)
        dirs = set()
        for path in paths:
            parent = os.path.dirname(path)
            while parent and parent not in dirs:
                dirs.add(parent)
                parent = os.path.dirname(parent)
        self.paths = sorted(paths) + sorted(d + "/" for d in dirs)
        self.loaded = True

    async def _git_files(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files", "--cached", "--others", "--exclude-standard",
                cwd=self.cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except (OSError, asyncio.TimeoutError):
            return None
        if proc.returncode != 0:
            return None
        return [p for p in out.decode(errors="replace").splitlines() if p][:MAX_FILES]

    def _walk(self):
        paths = []
        for root, dirs, files in os.walk(self.cwd):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                paths.append(os.path.relpath(os.path.join(root, name), self.cwd))
                if len(paths) >= MAX_FILES:
                    return paths
        return paths

    def search(self, query, limit=10):
        """Best matches for what follows the @: name starts, then anywhere, then in order."""
        if not query:
            top = [p for p in self.paths if "/" not in p.rstrip("/")]
            return top[:limit]
        q = query.lower()
        scored = []
        for path in self.paths:
            lower = path.lower()
            name = os.path.basename(lower.rstrip("/"))
            if name.startswith(q):
                score = 0
            elif lower.startswith(q):
                score = 1
            elif q in name:
                score = 2
            elif q in lower:
                score = 3
            elif _in_order(q, lower):
                score = 4
            else:
                continue
            scored.append((score, len(path), path))
        scored.sort()
        return [path for _, _, path in scored[:limit]]


def _in_order(needle, haystack):
    position = 0
    for char in needle:
        position = haystack.find(char, position)
        if position < 0:
            return False
        position += 1
    return True
