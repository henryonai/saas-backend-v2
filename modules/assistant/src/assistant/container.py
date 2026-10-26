"""
Composition root: the one place that picks real implementations.

Three entry points use it:
  app.py      AgentCore Runtime: the agents, usage, the conversation log, titles.
              Built once per Runtime session (a microVM) and reused by its
              following messages.
  handler.py  Lambda: the HTTP API for the users' conversations, which also
              takes their messages (and counts them against the plan).
  purge.py    Lambda: deletes what is left of a deleted or expired
              conversation, or of a removed member's: the agents'
              checkpoints, and the messages.

The two Lambdas get the shared layer, not the agents' dependencies (LangChain,
the AgentCore SDK, httpx): only get_agents() and get_titles() import those,
when they run.
"""
from functools import lru_cache

import boto3

from assistant.checkpoints import CheckpointPurge
from assistant.repository import ConversationRepository, MemoryRepository, UsageRepository
from assistant.service import ConversationLog, ConversationService, MemoryService, SqsPurgeQueue, UsageService
from assistant.settings import AssistantSettings, ConversationsApiSettings, PurgeSettings


@lru_cache
def get_settings() -> AssistantSettings:
    return AssistantSettings()


def _table(name: str, region: str | None = None):
    return boto3.resource("dynamodb", region_name=region).Table(name)


@lru_cache
def get_usage_service() -> UsageService:
    settings = get_settings()
    return UsageService(usage=UsageRepository(_table(settings.assistant_table, settings.region)))


@lru_cache
def get_memories() -> MemoryService:
    """What the assistant remembers about each user (the agents', in the runtime)."""
    settings = get_settings()
    return MemoryService(MemoryRepository(_table(settings.assistant_table, settings.region)))


@lru_cache
def get_conversation_log() -> ConversationLog:
    settings = get_settings()
    return ConversationLog(ConversationRepository(_table(settings.assistant_table, settings.region)),
                           days=settings.conversation_days)


@lru_cache
def get_agents() -> dict:
    """Every agent in agents/*.md, by name."""
    import httpx
    from langchain_core.language_models import BaseChatModel
    from langgraph_checkpoint_aws import DynamoDBSaver

    from assistant.definitions import AgentDefinition, load_agents
    from assistant.factory import AgentDeps, build_agents
    from assistant.llm import chat_model
    from assistant.sandbox import AgentCoreSandbox
    from assistant.tools import HttpAppApi, make_tools

    settings = get_settings()
    agentcore = boto3.client("bedrock-agentcore", region_name=settings.region)
    usage = get_usage_service()
    memories = get_memories()
    tools = make_tools(
        api=HttpAppApi(httpx.Client(timeout=10), settings.api_url),
        usage=usage,
        sandbox=AgentCoreSandbox(agentcore, settings.sandbox_id, settings.sandbox_session_seconds),
        memories=memories,
    )
    models: dict[str, BaseChatModel] = {}

    def make_model(model_id: str) -> BaseChatModel:
        if model_id not in models:
            models[model_id] = chat_model(model_id, settings)
        return models[model_id]

    def model_for(definition: AgentDefinition) -> BaseChatModel:
        return make_model(definition.model or settings.model_id)   # `model: inherit` or none: the stack's MODEL_ID

    return build_agents(load_agents(set(tools)), AgentDeps(
        make_model=model_for,
        # Each checkpoint holds the agent's whole state, so the newest one is
        # all a conversation needs, and older ones can expire. When the newest
        # has too, the agent starts again from the history (app.restored).
        checkpointer=DynamoDBSaver(
            table_name=settings.checkpoint_table,
            region_name=settings.region,
            ttl_seconds=settings.checkpoint_days * 24 * 60 * 60,
            enable_checkpoint_compression=True,
            s3_offload_config={"bucket_name": settings.checkpoint_bucket},   # over 350 KB
        ),
        usage=usage,
        tools=tools,
        memories=memories,
    ))


@lru_cache
def get_titles():
    """Names new conversations with the stack's default model (titles.py)."""
    from assistant.llm import chat_model
    from assistant.titles import Titles

    settings = get_settings()
    return Titles(chat_model(settings.model_id, settings), get_conversation_log(), get_usage_service())


@lru_cache
def get_conversation_service() -> ConversationService:
    settings = ConversationsApiSettings()
    table = _table(settings.assistant_table)
    return ConversationService(ConversationRepository(table),
                               purge=SqsPurgeQueue(boto3.client("sqs"), settings.purge_queue_url),
                               usage=UsageService(usage=UsageRepository(table)), days=settings.conversation_days)


@lru_cache
def get_memory_service() -> MemoryService:
    """For the HTTP API: the user sees, edits and deletes what the assistant remembers."""
    return MemoryService(MemoryRepository(_table(ConversationsApiSettings().assistant_table)))


@lru_cache
def get_checkpoint_purge() -> CheckpointPurge:
    settings = PurgeSettings()
    return CheckpointPurge(_table(settings.checkpoint_table), boto3.client("s3"), settings.checkpoint_bucket)


@lru_cache
def get_purged_conversations() -> ConversationRepository:
    """For the purge worker: what is left of the conversations that expired, and a removed member's."""
    return ConversationRepository(_table(PurgeSettings().assistant_table))


@lru_cache
def get_purged_memories() -> MemoryRepository:
    """For the purge worker: a removed member's memories."""
    return MemoryRepository(_table(PurgeSettings().assistant_table))
