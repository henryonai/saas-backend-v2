"""
The models agents call. The model id picks the provider: vendor/model ids
(anthropic/claude-sonnet-5.5) go to OpenRouter, any other id to Bedrock (a
model or inference profile id, such as global.anthropic.claude-sonnet-5-5).

definitions.py checks each agent's `model:` against these names;
container.py builds the models.
"""
import re
from collections.abc import Callable

import boto3
from aws_lambda_powertools.utilities import parameters
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from assistant.settings import AssistantSettings

MODEL_ALIASES = {          # what an agent's `model:` may name; Claude through OpenRouter
    "sonnet": "anthropic/claude-sonnet-5.5",
    "opus": "anthropic/claude-opus-5.5",
    "haiku": "anthropic/claude-haiku-5.5",
}
OPENROUTER_MODEL = re.compile(r"[a-z0-9-]+/[\w.:-]+")   # vendor/model
OPENROUTER_URL = "https://openrouter.ai/api/v1"          # OpenAI-compatible
MAX_OUTPUT_TOKENS = 4096


def is_openrouter_model(model_id: str) -> bool:
    return bool(OPENROUTER_MODEL.fullmatch(model_id))


def chat_model(model_id: str, settings: AssistantSettings) -> BaseChatModel:
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
