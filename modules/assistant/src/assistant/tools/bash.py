from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from shared.errors import PlanLimitReached

from assistant.context import AgentContext
from assistant.sandbox import CommandResult, Sandbox
from assistant.service import UsageService

MAX_OUTPUT_CHARS = 10_000  # per stream (stdout, stderr) sent back to the model: it pays for every token


def make_bash_tool(sandbox: Sandbox, usage: UsageService) -> BaseTool:
    @tool
    def bash(command: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Run a shell command in this conversation's sandbox: a Linux machine with bash, Python and
        Node.js. Each call is a new shell: files persist between calls, `cd` and variables may not, so
        chain steps (cd work && python run.py). It has no access to the app or its data, and usually
        no internet: to work on workspace data, fetch it with the other tools and write it to a file."""
        user = runtime.context.user
        try:
            # Keyed by conversation, not agent: a subagent works in the same sandbox as the agent that called it.
            result = sandbox.run(runtime.context.conversation_id, command,
                                 on_new_session=lambda: usage.start_sandbox(user))
        except PlanLimitReached as e:
            return f"Error: {e.message}"
        return describe(result)

    return bash


def describe(result: CommandResult) -> str:
    """The command's outcome, as the model reads it."""
    lines = []
    if result.restarted:
        lines.append("Note: the previous sandbox had ended, so this ran in a new, empty one.")
    lines.append(f"exit code: {result.exit_code if result.exit_code is not None else 'unknown'}")
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        if text:
            lines.append(f"{name}:\n{_cut(text)}")
    return "\n".join(lines)


def _cut(text: str) -> str:
    """Long output keeps its start and its end (where errors usually are)."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters cut ...]\n{text[-half:]}"
