"""
The agents' names, read from agents/*.md without loading the agents: for the
HTTP API's Lambda, which checks the agent a message is for before counting
it, and has none of the agents' dependencies (definitions.py parses the YAML
and checks everything else).
"""
import re
from functools import lru_cache
from pathlib import Path

AGENTS_DIR = Path(__file__).parent / "agents"
MAIN_AGENT = "assistant"   # the one the frontend talks to when it names none

_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)
_NAME = re.compile(r"^name:[ \t]*([\"']?)([a-z][a-z0-9-]{0,63})\1[ \t]*$", re.MULTILINE)


@lru_cache
def agent_names(directory: Path = AGENTS_DIR) -> frozenset[str]:
    """The `name` of every agent in `directory` (recursively)."""
    names = set()
    for path in directory.rglob("*.md"):
        front = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if front and (name := _NAME.search(front.group(1))):
            names.add(name.group(2))
    return frozenset(names)
