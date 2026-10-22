import hashlib
from dataclasses import dataclass

from shared.auth import CurrentUser


@dataclass(frozen=True)
class AgentContext:
    """
    Who the agent works for during one message, from the verified login token.
    Middleware and tools read it as runtime.context. It is not part of the
    agent's state: the model never sees it, and checkpoints never store it.
    """
    user: CurrentUser
    authorization: str  # "Bearer <ID token>", passed on as-is when tools call the HTTP API
    session_id: str     # the AgentCore Runtime session: one conversation

    @property
    def actor_id(self) -> str:
        """Whose conversations and memories these are: one user in one workspace."""
        return f"{self.user.tenant_id}:{self.user.user_id}"

    @property
    def conversation_id(self) -> str:
        """
        This user's conversation, whichever agents take part: its sandbox is
        keyed by this. The client picks the session id, so this includes the
        user too: someone who sends another user's session id gets a
        conversation of their own.
        """
        return _hash(f"{self.actor_id}/{self.session_id}")

    def thread_id(self, agent: str) -> str:
        """Where `agent`'s side of this conversation is checkpointed (see factory.thread_config)."""
        return _hash(f"{self.actor_id}/{self.session_id}/{agent}")


def _hash(key: str) -> str:
    # A hash, because AgentCore Memory session ids allow only [a-zA-Z0-9_-], up to 100.
    return hashlib.sha256(key.encode()).hexdigest()
