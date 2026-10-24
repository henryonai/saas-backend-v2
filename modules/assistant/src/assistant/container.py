"""
Composition root: the one place that picks real implementations.

Three entry points use it:
  app.py      AgentCore Runtime: the agents, usage, the conversation log.
              Built once per Runtime session (a microVM) and reused by its
              following messages.
  handler.py  Lambda: the HTTP API for the users' conversations.
  purge.py    Lambda: deletes a deleted conversation from AgentCore Memory.

The two Lambdas get the shared layer, not the agents' dependencies (LangChain,
the AgentCore SDK, httpx): only get_agents() imports those, when it runs.
"""
from functools import lru_cache

import boto3

from assistant.memory import AgentCoreMemory
from assistant.repository import ConversationRepository, UsageRepository
from assistant.service import ConversationLog, ConversationService, SqsPurgeQueue, UsageService
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
def get_conversation_log() -> ConversationLog:
    settings = get_settings()
    return ConversationLog(ConversationRepository(_table(settings.assistant_table, settings.region)),
                           days=settings.conversation_days)


@lru_cache
def get_agents() -> dict:
    """Every agent in agents/*.md, by name."""
    import httpx
    from langchain_core.language_models import BaseChatModel
    from langgraph_checkpoint_aws import AgentCoreMemorySaver

    from assistant.definitions import AgentDefinition, load_agents
    from assistant.factory import AgentDeps, build_agents
    from assistant.llm import chat_model
    from assistant.sandbox import AgentCoreSandbox
    from assistant.tools import HttpAppApi, make_tools

    settings = get_settings()
    agentcore = boto3.client("bedrock-agentcore", region_name=settings.region)
    usage = get_usage_service()
    tools = make_tools(
        api=HttpAppApi(httpx.Client(timeout=10), settings.api_url),
        usage=usage,
        sandbox=AgentCoreSandbox(agentcore, settings.sandbox_id, settings.sandbox_session_seconds),
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
        checkpointer=AgentCoreMemorySaver(settings.memory_id, region_name=settings.region),
        usage=usage,
        memory=AgentCoreMemory(agentcore, settings.memory_id),
        tools=tools,
        history=get_conversation_log(),
        title_model=make_model(settings.model_id),
    ))


@lru_cache
def get_conversation_service() -> ConversationService:
    settings = ConversationsApiSettings()
    return ConversationService(ConversationRepository(_table(settings.assistant_table)),
                               purge=SqsPurgeQueue(boto3.client("sqs"), settings.purge_queue_url))


@lru_cache
def get_memory() -> AgentCoreMemory:
    return AgentCoreMemory(boto3.client("bedrock-agentcore"), PurgeSettings().memory_id)
