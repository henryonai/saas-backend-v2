"""
Long-term memory in AgentCore Memory, behind a small interface so tests can
use a fake.

We save each exchange (the user's message and the final answer) as a
conversational event. In the background, the memory strategies in
template.yaml extract facts and preferences from those events and store them
under /actors/<actor_id>/... namespaces, where recall() searches them.

The actor is "<tenant_id>:<user_id>", built only from the login token, so
one user's memories are never searched for another user or workspace.

Memory is best effort: if AgentCore Memory fails, the agent answers without
it rather than failing the user's message.
"""
from datetime import UTC, datetime
from typing import Protocol

from aws_lambda_powertools import Logger
from botocore.exceptions import BotoCoreError, ClientError

logger = Logger()

RECALL_LIMIT = 5


def actor_namespace(actor_id: str) -> str:
    """Parent of the strategies' namespaces in template.yaml. The trailing slash
    stops "t1:u1" from matching "t1:u10"."""
    return f"/actors/{actor_id}/"


class LongTermMemory(Protocol):
    def recall(self, actor_id: str, query: str) -> list[str]:
        """What we learned about this actor that is relevant to `query`, most relevant first."""
        ...

    def remember(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        """Saves one exchange, for AgentCore to learn from."""
        ...


class AgentCoreMemory:
    def __init__(self, client, memory_id: str):
        self.client = client  # boto3 "bedrock-agentcore" (data plane)
        self.memory_id = memory_id

    def recall(self, actor_id: str, query: str) -> list[str]:
        try:
            response = self.client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespacePath=actor_namespace(actor_id),  # facts and preferences in one search
                searchCriteria={"searchQuery": query, "topK": RECALL_LIMIT},
                maxResults=RECALL_LIMIT,
            )
        except (BotoCoreError, ClientError):
            logger.warning("Could not recall memories; answering without them", exc_info=True)
            return []
        return [text for record in response.get("memoryRecordSummaries", [])
                if (text := record.get("content", {}).get("text"))]

    def remember(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        try:
            self.client.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(UTC),
                payload=[
                    {"conversational": {"role": "USER", "content": {"text": user_text}}},
                    {"conversational": {"role": "ASSISTANT", "content": {"text": assistant_text}}},
                ],
            )
        except (BotoCoreError, ClientError):
            logger.warning("Could not save the exchange to memory", exc_info=True)
