"""Checks the real wiring: every setting comes from the environment, and every agent in agents/*.md builds."""
import boto3
import pytest
from langchain_aws import ChatBedrockConverse
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from assistant import container

SETTINGS = {"ASSISTANT_TABLE": "assistant-dev", "MEMORY_ID": "saas_dev_assistant-abcdefghij",
            "API_URL": "https://api.example.com", "REGION": "ap-southeast-1",
            "SANDBOX_ID": "saas_dev_sandbox-abcdefghij", "OPENROUTER_API_KEY_PARAM": "/saas/test/openrouter-api-key"}


@pytest.fixture(autouse=True)
def fresh_container():
    for cached in (container.get_settings, container.get_usage_service, container.get_agents):
        cached.cache_clear()
    yield
    for cached in (container.get_settings, container.get_usage_service, container.get_agents):
        cached.cache_clear()


def test_agents_build_from_settings(monkeypatch):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    agents = container.get_agents()   # no AWS call until a message arrives
    assert set(agents) == {"assistant", "analyst"}
    assert set(agents["assistant"].get_graph().nodes) >= {"model", "tools"}
    assert container.get_settings().model_id == "deepseek/deepseek-v4.1-flash-1017"


def test_a_missing_setting_fails_clearly(monkeypatch):
    for name, value in SETTINGS.items():
        if name != "MEMORY_ID":
            monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match="memory_id"):
        container.get_agents()


def test_openrouter_models_read_the_key_from_ssm_when_called(monkeypatch):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    model = container.chat_model("anthropic/claude-haiku-5.5", container.get_settings())   # no SSM call yet
    boto3.client("ssm").put_parameter(Name="/saas/test/openrouter-api-key", Value="sk-or-test", Type="SecureString")

    assert isinstance(model, ChatOpenAI) and model.model_name == "anthropic/claude-haiku-5.5"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.openai_api_key() == "sk-or-test"
    assert model.stream_usage                     # else no token usage while streaming, and no allowance
    assert model.extra_body == {"provider": {"data_collection": "deny"}}


def test_a_bedrock_model_id_still_uses_bedrock(monkeypatch):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    model = container.chat_model("global.anthropic.claude-sonnet-5-5", container.get_settings())
    assert isinstance(model, ChatBedrockConverse)


def test_openrouter_without_a_key_fails_at_startup(monkeypatch):
    for name, value in SETTINGS.items():
        if name != "OPENROUTER_API_KEY_PARAM":
            monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        container.get_agents()
