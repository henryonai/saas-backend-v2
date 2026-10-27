import base64
import threading
from collections import OrderedDict

from aws_lambda_powertools import Logger
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from shared.auth import CurrentUser
from shared.errors import NotFound

from assistant.context import actor_of
from assistant.files import MAX_IMAGE_BYTES, is_text
from assistant.models import Attachment
from assistant.service import FileService

logger = Logger()

MAX_IMAGES_SENT = 4         # the newest images in what one model call is sent
MAX_TEXT_SENT = 60_000      # characters of text files in what one model call is sent, newest first
TEXT_FILE_CHARS = 20_000    # of one text file
CACHE_BYTES = 64 * 1024 * 1024   # files read for model calls, kept in this microVM (one conversation's)


class AttachmentsMiddleware(AgentMiddleware):
    """
    What the model is sent of the files on user messages, among the messages
    a call is sent (ConversationMiddleware's recent history, before this):
    the newest images, as images, and text files (text, data, code), inline.
    The agent's state has a line per file (agui.user_message), never its
    bytes, so checkpoints stay small. Anything else (a PDF, a spreadsheet),
    and whatever is past these limits, the agent opens in the sandbox
    (open_file). Top-level agents only: a subagent gets its instructions.
    """

    def __init__(self, files: FileService):
        super().__init__()
        self.files = files
        self._cache: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()
        self._cached = 0
        self._lock = threading.Lock()

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        user = request.runtime.context.user
        images, text_left = MAX_IMAGES_SENT, MAX_TEXT_SENT
        messages = list(request.messages)
        for i in range(len(messages) - 1, -1, -1):
            message = messages[i]
            attachments = message.additional_kwargs.get("attachments") if isinstance(message, HumanMessage) else None
            if not attachments:
                continue
            blocks: list[dict] = [{"type": "text", "text": message.text}]
            for attachment in map(Attachment.model_validate, attachments):
                if attachment.kind == "image" and images and attachment.size <= MAX_IMAGE_BYTES:
                    if data := self._read(user, attachment):
                        blocks.append({"type": "image", "base64": base64.b64encode(data).decode(),
                                       "mime_type": attachment.content_type})
                        images -= 1
                elif is_text(attachment.name, attachment.content_type) and text_left > 0 \
                        and (data := self._read(user, attachment)) is not None:
                    text = data.decode("utf-8", errors="replace")
                    shown = text[:min(TEXT_FILE_CHARS, text_left)]
                    cut = f"\n… (the first {len(shown):,} of {len(text):,} characters: open_file for all of it)" \
                        if len(shown) < len(text) else ""
                    blocks.append({"type": "text", "text": f'<file name="{attachment.name}" '
                                                           f'id="{attachment.file_id}">\n{shown}{cut}\n</file>'})
                    text_left -= len(shown)
            messages[i] = HumanMessage(content=blocks, id=message.id)
        return handler(request.override(messages=messages))

    def _read(self, user: CurrentUser, attachment: Attachment) -> bytes | None:
        """
        The file's newest bytes, or None when it is gone (the user deleted
        it): the model has its line only. Each version is read from S3 once.
        """
        try:
            file = self.files.kept(user, attachment.file_id)
            key = (actor_of(user), file.file_id, len(file.versions))
            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
            data = self.files.bytes_of(user, file)
        except NotFound:
            return None
        except Exception:
            logger.warning("Could not read a file for the model; it gets the file's name only",
                           extra={"file_id": attachment.file_id}, exc_info=True)
            return None
        with self._lock:
            self._cache[key] = data
            self._cached += len(data)
            while self._cached > CACHE_BYTES and len(self._cache) > 1:
                _, dropped = self._cache.popitem(last=False)
                self._cached -= len(dropped)
        return data
