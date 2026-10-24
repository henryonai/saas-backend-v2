import hashlib
import re
from dataclasses import dataclass

from shared.auth import CurrentUser

# A conversation's threadId, which is also its AgentCore Runtime session id:
# Runtime wants 33+ characters; AgentCore Memory allows these, up to 100.
SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{32,99}")


def actor_of(user: CurrentUser) -> str:
    """Whose conversations and memories these are: one user in one workspace."""
    return f"{user.tenant_id}:{user.user_id}"


def checkpoint_session(actor_id: str, session_id: str, agent: str) -> str:
    """
    The AgentCore Memory session where `agent`'s side of a conversation is
    checkpointed (factory.thread_config). A hash, because Memory session ids
    allow only [a-zA-Z0-9_-], up to 100; it includes the user, because the
    client picks the session id (someone who sends another user's gets a
    conversation of their own).
    """
    return _hash(f"{actor_id}/{session_id}/{agent}")


@dataclass(frozen=True)
class AgentContext:
    """
    Who the agent works for during one message, from the verified login token.
    Middleware and tools read it as runtime.context. It is not part of the
    agent's state: the model never sees it, and checkpoints never store it.
    """
    user: CurrentUser
    authorization: str  # "Bearer <ID token>", passed on as-is when tools call the HTTP API
    session_id: str     # the AgentCore Runtime session: one conversation (the AG-UI threadId)

    @property
    def actor_id(self) -> str:
        return actor_of(self.user)

    @property
    def conversation_id(self) -> str:
        """This user's conversation, whichever agents take part: its sandbox is keyed by this."""
        return _hash(f"{self.actor_id}/{self.session_id}")

    def thread_id(self, agent: str) -> str:
        """Where `agent`'s side of this conversation is checkpointed (see factory.thread_config)."""
        return checkpoint_session(self.actor_id, self.session_id, agent)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
