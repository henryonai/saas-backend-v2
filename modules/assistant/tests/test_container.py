"""Checks the real wiring: every setting comes from the environment, and every agent in agents/*.md builds."""
import pytest
from pydantic import ValidationError

from assistant import container

SETTINGS = {"ASSISTANT_TABLE": "assistant-dev", "CHECKPOINT_TABLE": "checkpoints-dev",
            "CHECKPOINT_BUCKET": "checkpoints-dev", "FILES_BUCKET": "files-dev", "API_URL": "https://api.example.com",
            "REGION": "ap-southeast-1",
            "SANDBOX_ID": "saas_dev_sandbox-abcdefghij",
            "OPENROUTER_API_KEY_PARAM": "/saas/test/openrouter-api-key"}


@pytest.fixture(autouse=True)
def fresh_container():
    cached = (container.get_settings, container.get_usage_service, container.get_agents, container.get_memories,
              container.get_file_service)
    for function in cached:
        function.cache_clear()
    yield
    for function in cached:
        function.cache_clear()


def test_agents_build_from_settings(monkeypatch):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    agents = container.get_agents()   # no AWS call until a message arrives
    assert set(agents) == {"assistant", "analyst"}
    assert set(agents["assistant"].get_graph().nodes) >= {"model", "tools"}
    assert container.get_settings().model_id == "deepseek/deepseek-v4.1-flash-1017"


def test_a_missing_setting_fails_clearly(monkeypatch):
    for name, value in SETTINGS.items():
        if name != "CHECKPOINT_TABLE":
            monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match="checkpoint_table"):
        container.get_agents()



def test_openrouter_without_a_key_fails_at_startup(monkeypatch):
    for name, value in SETTINGS.items():
        if name != "OPENROUTER_API_KEY_PARAM":
            monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        container.get_agents()


@pytest.mark.parametrize(("settings", "factory"), [
    ({"ASSISTANT_TABLE": "assistant-dev", "FILES_BUCKET": "files-dev",
      "PURGE_QUEUE_URL": "https://sqs.example.com/purge"}, container.get_api_file_service),
    ({"ASSISTANT_TABLE": "assistant-dev", "CHECKPOINT_TABLE": "checkpoints-dev",
      "CHECKPOINT_BUCKET": "checkpoints-dev", "FILES_BUCKET": "files-dev"}, container.get_purged_files),
])
def test_the_api_and_the_purge_need_their_region(monkeypatch, settings, factory):
    """Not the SDK's guess: a link signed for another region than the bucket's fails in the browser."""
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("REGION", raising=False)
    factory.cache_clear()
    with pytest.raises(ValidationError, match="region"):
        factory()
    monkeypatch.setenv("REGION", "ap-southeast-1")
    factory.cache_clear()
    store = factory().store
    assert store.bucket == "files-dev" and store.client.meta.region_name == "ap-southeast-1"
    factory.cache_clear()
