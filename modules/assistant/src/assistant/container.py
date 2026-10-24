"""
Composition root: the one place that picks real implementations.

Built once per AgentCore Runtime session (a microVM) and reused by the
session's following messages.
"""
from functools import lru_cache

import boto3
import httpx
from langchain_core.language_models import BaseChatModel
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from assistant.definitions import AgentDefinition, load_agents
from assistant.factory import AgentDeps, build_agents
from assistant.llm import chat_model
from assistant.memory import AgentCoreMemory
from assistant.repository import UsageRepository
from assistant.sandbox import AgentCoreSandbox
from assistant.service import UsageService
from assistant.settings import AssistantSettings
from assistant.tools import HttpAppApi, make_tools


@lru_cache
def get_settings() -> AssistantSettings:
    return AssistantSettings()


@lru_cache
def get_usage_service() -> UsageService:
    settings = get_settings()
    table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.assistant_table)
    return UsageService(usage=UsageRepository(table))


@lru_cache
def get_agents() -> dict:
    """Every agent in agents/*.md, by name."""
    settings = get_settings()
    agentcore = boto3.client("bedrock-agentcore", region_name=settings.region)
    usage = get_usage_service()
    tools = make_tools(
        api=HttpAppApi(httpx.Client(timeout=10), settings.api_url),
        usage=usage,
        sandbox=AgentCoreSandbox(agentcore, settings.sandbox_id, settings.sandbox_session_seconds),
    )
    models: dict[str, BaseChatModel] = {}

    def make_model(definition: AgentDefinition) -> BaseChatModel:
        model_id = definition.model or settings.model_id   # `model: inherit` or none: the stack's MODEL_ID
        if model_id not in models:
            models[model_id] = chat_model(model_id, settings)
        return models[model_id]

    return build_agents(load_agents(set(tools)), AgentDeps(
        make_model=make_model,
        checkpointer=AgentCoreMemorySaver(settings.memory_id, region_name=settings.region),
        usage=usage,
        memory=AgentCoreMemory(agentcore, settings.memory_id),
        tools=tools,
    ))

