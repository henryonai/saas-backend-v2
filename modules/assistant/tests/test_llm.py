"""The model id picks the provider: OpenRouter (vendor/model) or Bedrock."""
import boto3
import pytest
from langchain_aws import ChatBedrockConverse
from langchain_openai import ChatOpenAI

from assistant.llm import chat_model, is_openrouter_model
from assistant.settings import AssistantSettings

REQUIRED = {"assistant_table": "assistant-dev", "memory_id": "saas_dev_assistant-abcdefghij",
            "api_url": "https://api.example.com", "region": "ap-southeast-1",
            "sandbox_id": "saas_dev_sandbox-abcdefghij"}


def settings(**values) -> AssistantSettings:
    return AssistantSettings(_env_file=None, **REQUIRED, **values)


@pytest.mark.parametrize("model_id, openrouter", [
    ("anthropic/claude-sonnet-5.5", True), ("deepseek/deepseek-v4.1-flash-1017", True), ("openai/gpt-5.2", True),
    ("global.anthropic.claude-sonnet-5-5", False), ("anthropic.claude-haiku-5-5", False),
])
def test_the_model_id_picks_the_provider(model_id, openrouter):
    assert is_openrouter_model(model_id) is openrouter


def test_openrouter_models_read_the_key_from_ssm_when_called():
    model = chat_model("anthropic/claude-haiku-5.5", settings(openrouter_api_key_param="/saas/test/openrouter-api-key"))
    boto3.client("ssm").put_parameter(Name="/saas/test/openrouter-api-key", Value="sk-or-test", Type="SecureString")

    assert isinstance(model, ChatOpenAI) and model.model_name == "anthropic/claude-haiku-5.5"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.openai_api_key() == "sk-or-test"   # read now, not when the model was built
    assert model.stream_usage                       # else no token usage while streaming, and no allowance
    assert model.extra_body == {"provider": {"data_collection": "deny"}}


def test_locally_the_key_can_come_from_the_environment():
    model = chat_model("anthropic/claude-haiku-5.5", settings(openrouter_api_key="sk-or-local"))
    assert model.openai_api_key() == "sk-or-local"


def test_an_openrouter_model_without_a_key_fails_clearly():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        chat_model("anthropic/claude-haiku-5.5", settings())


def test_a_bedrock_model_id_uses_bedrock():
    assert isinstance(chat_model("global.anthropic.claude-sonnet-5-5", settings()), ChatBedrockConverse)
