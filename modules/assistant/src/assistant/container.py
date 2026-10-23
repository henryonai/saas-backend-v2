"""
Composition root: the one place that picks real implementations.

Built once per AgentCore Runtime session (a microVM) and reused by the
session's following messages.
"""
from collections.abc import Callable
from functools import lru_cache

import boto3
import httpx
from aws_lambda_powertools.utilities import parameters
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from assistant.definitions import AgentDefinition, is_openrouter_model, load_agents
from assistant.factory import AgentDeps, build_agents
from assistant.memory import AgentCoreMemory
from assistant.repository import UsageRepository
from assistant.sandbox import AgentCoreSandbox
from assistant.service import UsageService
from assistant.settings import AssistantSettings
from assistant.tools import HttpAppApi, make_tools

OPENROUTER_URL = "https://openrouter.ai/api/v1"   # OpenAI-compatible
MAX_OUTPUT_TOKENS = 4096


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


def chat_model(model_id: str, settings: AssistantSettings) -> BaseChatModel:
    """An OpenRouter model (vendor/model) or a Bedrock one (any other id)."""
    if not is_openrouter_model(model_id):
        return ChatBedrockConverse(model=model_id, region_name=settings.region, max_tokens=MAX_OUTPUT_TOKENS)
    return ChatOpenAI(
        model=model_id,
        base_url=OPENROUTER_URL,
        api_key=openrouter_api_key(settings),
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=60,
        use_responses_api=False,   # Chat Completions, which OpenRouter supports for every model
        # Token usage on the last chunk: UsageMiddleware counts it against the
        # plan. ChatOpenAI only asks for it by itself on OpenAI's own URL.
        stream_usage=True,
        # Workspace data goes only to providers that don't collect it (store it or train on it).
        extra_body={"provider": {"data_collection": "deny"}},
    )


def openrouter_api_key(settings: AssistantSettings) -> Callable[[], str]:
    """The key, read when a model call needs it: from .env locally, else SSM (decrypted, cached 5 minutes)."""
    if settings.openrouter_api_key:
        key = settings.openrouter_api_key.get_secret_value()
        return lambda: key
    if not settings.openrouter_api_key_param:
        raise RuntimeError("Set OPENROUTER_API_KEY_PARAM (AWS) or OPENROUTER_API_KEY (local) to use OpenRouter models")
    ssm = parameters.SSMProvider(boto3_client=boto3.client("ssm", region_name=settings.region))
    return lambda: ssm.get(settings.openrouter_api_key_param, decrypt=True, max_age=300)
