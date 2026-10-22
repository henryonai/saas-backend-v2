"""Agents defined in Markdown (Claude Code's subagent format): parsing, defaults and the checks at startup."""
import pytest

from assistant.definitions import (
    DEFAULT_MAX_TURNS,
    MODEL_ALIASES,
    AgentDefinitionError,
    load_agents,
    parse_agent,
)

TOOLS = {"list_orders", "get_order", "assistant_usage", "bash"}


def agent_md(front: str, body: str = "You are a test agent.") -> str:
    return f"---\n{front.strip()}\n---\n{body}\n"


def write(directory, name: str, text: str):
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def test_the_shipped_agents_load():
    agents = load_agents(TOOLS)
    assert set(agents) == {"assistant", "analyst"}
    assert agents["assistant"].subagents == ("analyst",)
    assert agents["analyst"].tools == ("bash", "get_order", "list_orders") and agents["analyst"].max_turns == 12


def test_a_full_definition():
    agent = parse_agent(agent_md("""
name: reporter
description: >
  Writes weekly reports.
  Use for summaries.
tools: list_orders, bash
model: haiku
maxTurns: 5
color: purple            # a Claude Code field this app ignores
"""), "reporter.md", TOOLS)
    assert agent.name == "reporter" and agent.description == "Writes weekly reports. Use for summaries."
    assert agent.tools == ("bash", "list_orders") and agent.model == MODEL_ALIASES["haiku"]
    assert agent.max_turns == 5 and agent.prompt == "You are a test agent." and agent.subagents == ()


def test_defaults_follow_claude_code():
    agent = parse_agent(agent_md("name: helper\ndescription: Helps."), "helper.md", TOOLS)
    assert agent.tools == tuple(sorted(TOOLS))           # no `tools`: every tool
    assert agent.model is None                            # the stack's MODEL_ID
    assert agent.max_turns == DEFAULT_MAX_TURNS


def test_tools_as_a_list_and_disallowed_tools():
    agent = parse_agent(agent_md("name: safe\ndescription: No shell.\ntools: [list_orders, bash]\n"
                                 "disallowedTools: bash"), "safe.md", TOOLS)
    assert agent.tools == ("list_orders",)


def test_a_full_bedrock_model_id_is_accepted():
    agent = parse_agent(agent_md("name: big\ndescription: Big.\nmodel: global.anthropic.claude-opus-5-5"),
                        "big.md", TOOLS)
    assert agent.model == "global.anthropic.claude-opus-5-5"


@pytest.mark.parametrize("text, message", [
    ("You are an agent without frontmatter.", "frontmatter"),
    (agent_md("description: No name."), "`name` is required"),
    (agent_md("name: Bad Name\ndescription: Spaces."), "`name` is required"),
    (agent_md("name: quiet"), "`description` is required"),
    (agent_md("name: empty\ndescription: Empty.", body=""), "body"),
    (agent_md("name: typo\ndescription: Typo.\ntools: list_order"), "unknown tools ['list_order']"),
    (agent_md("name: gpt\ndescription: Other model.\nmodel: gpt-5"), "`model`"),
    (agent_md("name: busy\ndescription: Busy.\nmaxTurns: 500"), "`maxTurns`"),
    (agent_md("name: broken\ndescription: [unclosed"), "not valid YAML"),
])
def test_mistakes_fail_at_startup_with_the_file_name(text, message):
    with pytest.raises(AgentDefinitionError, match=f"x.md.*{message}".replace("[", r"\[").replace("]", r"\]")):
        parse_agent(text, "x.md", TOOLS)


def test_subagent_rules(tmp_path):
    write(tmp_path, "assistant", agent_md("name: assistant\ndescription: Main.\nsubagents: [helper]"))
    write(tmp_path, "helper", agent_md("name: helper\ndescription: Helps."))
    assert load_agents(TOOLS, tmp_path)["assistant"].subagents == ("helper",)

    write(tmp_path, "assistant", agent_md("name: assistant\ndescription: Main.\nsubagents: [nobody]"))
    with pytest.raises(AgentDefinitionError, match="unknown subagent 'nobody'"):
        load_agents(TOOLS, tmp_path)

    write(tmp_path, "assistant", agent_md("name: assistant\ndescription: Main.\nsubagents: [assistant]"))
    with pytest.raises(AgentDefinitionError, match="itself"):
        load_agents(TOOLS, tmp_path)

    # One level only: a subagent can't delegate further.
    write(tmp_path, "assistant", agent_md("name: assistant\ndescription: Main.\nsubagents: [helper]"))
    write(tmp_path, "helper", agent_md("name: helper\ndescription: Helps.\nsubagents: [deep]"))
    write(tmp_path, "deep", agent_md("name: deep\ndescription: Deeper."))
    with pytest.raises(AgentDefinitionError, match="one level"):
        load_agents(TOOLS, tmp_path)


def test_names_are_unique_and_the_main_agent_exists(tmp_path):
    write(tmp_path, "a", agent_md("name: assistant\ndescription: One."))
    write(tmp_path, "b", agent_md("name: assistant\ndescription: Two."))
    with pytest.raises(AgentDefinitionError, match="also used by"):
        load_agents(TOOLS, tmp_path)

    (tmp_path / "b.md").unlink()
    (tmp_path / "a.md").write_text(agent_md("name: other\ndescription: Not the main one."))
    with pytest.raises(AgentDefinitionError, match="No agent named 'assistant'"):
        load_agents(TOOLS, tmp_path)


def test_files_in_subfolders_are_found(tmp_path):
    write(tmp_path, "assistant", agent_md("name: assistant\ndescription: Main."))
    (tmp_path / "team").mkdir()
    write(tmp_path / "team", "helper", agent_md("name: helper\ndescription: Helps."))
    assert set(load_agents(TOOLS, tmp_path)) == {"assistant", "helper"}
