"""
Agents defined in Markdown, in the format of Claude Code's subagents: YAML
frontmatter, then the system prompt. One file per agent in agents/:

    ---
    name: analyst
    description: Analyzes the workspace's orders with code. Use for numbers, trends and exports.
    tools: list_orders, bash          # optional; omitted = every tool (as in Claude Code)
    model: haiku                      # optional; sonnet | opus | haiku | inherit | an OpenRouter or Bedrock model id
    maxTurns: 10                      # optional; model calls per run (default 15)
    memory: user                      # optional; it remembers things about the user across conversations
    subagents: [other-agent]          # optional, ours: agents it may delegate to (the task tool)
    ---
    You are ...

Fields as in Claude Code: name, description, tools, disallowedTools, model,
maxTurns, memory. `memory: user` is Claude Code's user scope, here the
signed-in user's memories (service.MemoryService): the agent is sent them
and gets the tools that save and forget them (MEMORY_TOOLS), which no agent
gets otherwise. `subagents` is this app's: Claude Code lets the main agent
use any subagent; here each agent lists the ones it may call, and a subagent
can't call others (one level, which bounds the cost of a message). Other
Claude Code fields (color, skills, hooks...) are ignored with a warning,
since they mean something this app doesn't do.

Everything is checked when the agents load, at startup: an unknown tool or
subagent fails there, not in the middle of a conversation.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from aws_lambda_powertools import Logger

from assistant.agent_names import AGENTS_DIR, MAIN_AGENT
from assistant.llm import MODEL_ALIASES, is_openrouter_model

logger = Logger()

DEFAULT_MAX_TURNS = 15
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")   # stricter than Claude Code: it appears in tool schemas and URLs
KNOWN_FIELDS = {"name", "description", "tools", "disallowedTools", "model", "maxTurns", "memory", "subagents"}
MEMORY_TOOLS = {"remember", "forget"}   # tools/memory.py: only for agents with `memory: user`


class AgentDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    prompt: str
    tools: tuple[str, ...]          # resolved: every name exists
    model: str | None               # a model id, or None for the default (MODEL_ID)
    max_turns: int
    subagents: tuple[str, ...]
    source: str
    memory: bool = False            # `memory: user`: it is sent the user's memories, and has MEMORY_TOOLS


def load_agents(tool_names: set[str], directory: Path = AGENTS_DIR) -> dict[str, AgentDefinition]:
    """Every agent in `directory` (recursively), by name, checked against the available tools."""
    agents: dict[str, AgentDefinition] = {}
    for path in sorted(directory.rglob("*.md")):
        agent = parse_agent(path.read_text(encoding="utf-8"), str(path.relative_to(directory)), tool_names)
        if agent.name in agents:
            raise AgentDefinitionError(f"{agent.source}: name {agent.name!r} is also used by "
                                       f"{agents[agent.name].source}")
        agents[agent.name] = agent
    _check_subagents(agents)
    if MAIN_AGENT not in agents:
        raise AgentDefinitionError(f"No agent named {MAIN_AGENT!r} in {directory}: the frontend's default agent")
    return agents


def parse_agent(text: str, source: str, tool_names: set[str]) -> AgentDefinition:
    match = re.match(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", text, re.DOTALL)
    if not match:
        raise AgentDefinitionError(f"{source}: start with a YAML frontmatter block between '---' lines")
    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise AgentDefinitionError(f"{source}: the frontmatter is not valid YAML: {e}")
    if not isinstance(front, dict):
        raise AgentDefinitionError(f"{source}: the frontmatter must be key: value pairs")
    prompt = match.group(2).strip()

    name, description = front.get("name"), front.get("description")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise AgentDefinitionError(f"{source}: `name` is required: lowercase letters, digits and '-', up to 64")
    if not isinstance(description, str) or not description.strip():
        raise AgentDefinitionError(f"{source}: `description` is required: it tells other agents when to use this one")
    if not prompt:
        raise AgentDefinitionError(f"{source}: the body (the system prompt) is empty")
    if ignored := sorted(set(front) - KNOWN_FIELDS):
        logger.warning("Ignoring agent fields this app doesn't support", extra={"agent": name, "fields": ignored})

    memory = front.get("memory")
    if memory not in (None, "user"):
        raise AgentDefinitionError(f"{source}: `memory` can only be user (the signed-in user's memories)")
    tools = _names(front.get("tools"), "tools", source)
    if tools is not None and (listed := sorted(tools & MEMORY_TOOLS)):
        raise AgentDefinitionError(f"{source}: {listed} come with `memory: user`; don't list them in `tools`")
    tools = set(tool_names) - MEMORY_TOOLS if tools is None else tools
    tools -= _names(front.get("disallowedTools"), "disallowedTools", source) or set()
    if unknown := sorted(tools - tool_names):
        raise AgentDefinitionError(f"{source}: unknown tools {unknown}; available: {sorted(tool_names)}")
    if memory:
        tools |= MEMORY_TOOLS & tool_names

    max_turns = front.get("maxTurns", DEFAULT_MAX_TURNS)
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 50:
        raise AgentDefinitionError(f"{source}: `maxTurns` must be a whole number from 1 to 50")

    return AgentDefinition(
        name=name, description=" ".join(description.split()), prompt=prompt,
        tools=tuple(sorted(tools)), model=_model(front.get("model"), source), max_turns=max_turns,
        subagents=tuple(sorted(_names(front.get("subagents"), "subagents", source) or ())), source=source,
        memory=memory == "user",
    )


def _names(value, field: str, source: str) -> set[str] | None:
    """A comma-separated string or a YAML list, as Claude Code accepts; None when absent."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise AgentDefinitionError(f"{source}: `{field}` must be a list of names or a comma-separated string")
    return {i.strip() for i in items if i.strip()}


def _model(value, source: str) -> str | None:
    if value in (None, "inherit"):
        return None
    if isinstance(value, str) and value in MODEL_ALIASES:
        return MODEL_ALIASES[value]
    if isinstance(value, str) and (is_openrouter_model(value) or "anthropic." in value):
        return value   # an OpenRouter model, or a Bedrock model or inference profile id
    raise AgentDefinitionError(f"{source}: `model` must be inherit, {', '.join(MODEL_ALIASES)}, "
                               "an OpenRouter model (vendor/model) or a Bedrock model id")


def _check_subagents(agents: dict[str, AgentDefinition]) -> None:
    for agent in agents.values():
        for sub in agent.subagents:
            if sub == agent.name:
                raise AgentDefinitionError(f"{agent.source}: an agent can't list itself in `subagents`")
            if sub not in agents:
                raise AgentDefinitionError(f"{agent.source}: unknown subagent {sub!r}; agents: {sorted(agents)}")
            if agents[sub].subagents:
                raise AgentDefinitionError(f"{agent.source}: {sub!r} has subagents of its own; "
                                           "subagents can't delegate further (one level)")
