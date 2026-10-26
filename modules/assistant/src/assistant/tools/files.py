import json
import posixpath

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from shared.errors import AppError

from assistant.context import AgentContext
from assistant.files import clean_name
from assistant.models import File
from assistant.sandbox import Sandbox, SandboxError
from assistant.service import FileService, UsageService

MAX_WRITTEN_CHARS = 1_000_000   # of a file the model writes out itself (write_file)
SANDBOX_FILES = "files"         # where open_file puts the user's files in the sandbox


def make_file_tools(files: FileService, sandbox: Sandbox, usage: UsageService) -> list[BaseTool]:
    """
    write_file, save_file and open_file: files the agents make for the user
    (kept, with versions, until the user deletes them), and the user's files
    in the sandbox. save_file and open_file need the sandbox (SANDBOX_TOOLS).
    """

    @tool
    def write_file(name: str, content: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Save text you wrote as a file the user can download: a report (.md), data (.csv, .json), a page
        (.html), a picture (.svg). Name it with its extension. Saving the same name again in this conversation
        makes a new version of it. The user sees it in the chat."""
        if len(content) > MAX_WRITTEN_CHARS:
            return f"Error: write at most {MAX_WRITTEN_CHARS:,} characters; for more, build it in the sandbox."
        return _saved(lambda: files.save(runtime.context.user, name=name, data=content.encode(),
                                         content_type=None, thread_id=runtime.context.session_id,
                                         agent=runtime.context.agent))

    @tool
    def save_file(path: str, runtime: ToolRuntime[AgentContext], name: str | None = None) -> str:
        """Save a file from the sandbox (a chart you drew, a spreadsheet you built) as a file the user can
        download. `name`: what the user sees, with its extension (the file's own name if you leave it out).
        Saving the same name again in this conversation makes a new version of it. The user sees it in the
        chat."""
        try:
            data = sandbox.read_file(runtime.context.conversation_id, path)
        except SandboxError as e:
            return f"Error: {e}"
        if data is None:
            return "Error: the sandbox has ended, and its files with it: make the file again."
        return _saved(lambda: files.save(runtime.context.user, name=name or posixpath.basename(path), data=data,
                                         content_type=None, thread_id=runtime.context.session_id,
                                         agent=runtime.context.agent))

    @tool
    def open_file(file_id: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Put one of the user's files in the sandbox, to work on it with code: a file they attached (its id is
        in their message) or one you saved. Returns its path there."""
        user = runtime.context.user
        try:
            file, data = files.read(user, file_id)
            path = posixpath.join(SANDBOX_FILES, clean_name(file.name))
            new = sandbox.write_file(runtime.context.conversation_id, path, data,
                                     on_new_session=lambda: usage.start_sandbox(user))
        except AppError as e:
            return f"Error: {e.message}"
        except SandboxError as e:
            return f"Error: {e}"
        note = " (in a new sandbox: files from before are gone)" if new else ""
        return f"{file.name} is at {path}{note}."

    return [write_file, save_file, open_file]


def _saved(save) -> str:
    """What the model, and the page, read about a file saved: JSON the chat shows as the file (tool-call.tsx)."""
    try:
        file: File = save()
    except AppError as e:
        return f"Error: {e.message}"
    return json.dumps({"file_id": file.file_id, "name": file.name, "content_type": file.content_type,
                       "size": file.size, "version": len(file.versions)})
