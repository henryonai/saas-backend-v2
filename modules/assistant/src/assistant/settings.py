from pydantic import SecretStr

from shared.settings import BaseAppSettings


class AssistantSettings(BaseAppSettings):
    assistant_table: str
    memory_id: str      # AgentCore Memory: conversation checkpoints and long-term memories
    api_url: str        # this stack's HTTP API, which the tools call with the user's token
    # Set explicitly in template.yaml: the AgentCore SDK falls back to us-west-2
    # when the environment doesn't name a region.
    region: str
    sandbox_id: str     # AgentCore Code Interpreter for the bash tool
    # vendor/model = OpenRouter; any other id = Bedrock (see llm.py).
    model_id: str = "deepseek/deepseek-v4.1-flash-1017"
    sandbox_session_seconds: int = 900   # a sandbox, and its files, last this long; then a new one starts
    # Days a conversation's history is kept after its last message: the
    # AgentCore Memory's EventExpiryDuration, so it never outlives what the agent remembers.
    conversation_days: int = 30

    # The OpenRouter API key, needed only for OpenRouter models. In AWS, set
    # OPENROUTER_API_KEY_PARAM to the name of an SSM SecureString parameter
    # (the key itself is never in env vars). Locally, you can set
    # OPENROUTER_API_KEY directly in .env instead.
    openrouter_api_key_param: str | None = None
    openrouter_api_key: SecretStr | None = None  # SecretStr: hidden in logs


class ConversationsApiSettings(BaseAppSettings):
    """The HTTP API's Lambda (handler.py): the users' conversations."""
    assistant_table: str
    purge_queue_url: str   # where deleted conversations go for purge.py
    conversation_days: int = 30   # as the runtime's (AssistantSettings): a message saved here expires with the rest


class PurgeSettings(BaseAppSettings):
    """The purge worker's Lambda (purge.py)."""
    memory_id: str
