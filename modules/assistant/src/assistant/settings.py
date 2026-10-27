from pydantic import SecretStr

from shared.settings import BaseAppSettings


class AssistantSettings(BaseAppSettings):
    assistant_table: str
    checkpoint_table: str    # the agents' checkpoints of each conversation (DynamoDBSaver)
    checkpoint_bucket: str   # where a checkpoint over 350 KB goes
    files_bucket: str        # the users' files: attached, and made by agents (files.py)
    api_url: str       # this stack's HTTP API, which the tools call with the user's token
    # Set explicitly in template.yaml: the AgentCore SDK falls back to us-west-2
    # when the environment doesn't name a region.
    region: str
    sandbox_id: str     # AgentCore Code Interpreter for the bash tool
    # vendor/model = OpenRouter; any other id = Bedrock (see llm.py).
    model_id: str = "deepseek/deepseek-v4.1-flash-1017"
    sandbox_session_seconds: int = 900   # a sandbox, and its files, last this long; then a new one starts
    # Days a conversation is kept after its last message; 0 = until its user
    # deletes it or leaves the workspace (AssistantConversationDays).
    conversation_days: int = 0
    # Days each of the agents' checkpoints is kept after it was written: the
    # newest has the agent's whole state. An agent whose checkpoints expired
    # while the conversation is kept starts again from the history (app.restored).
    checkpoint_days: int = 30

    # The OpenRouter API key, needed only for OpenRouter models. In AWS, set
    # OPENROUTER_API_KEY_PARAM to the name of an SSM SecureString parameter
    # (the key itself is never in env vars). Locally, you can set
    # OPENROUTER_API_KEY directly in .env instead.
    openrouter_api_key_param: str | None = None
    openrouter_api_key: SecretStr | None = None  # SecretStr: hidden in logs


class ConversationsApiSettings(BaseAppSettings):
    """The HTTP API's Lambda (handler.py): the users' conversations, memories and files."""
    assistant_table: str
    files_bucket: str      # uploads and downloads are presigned for it
    purge_queue_url: str   # where deleted conversations go for purge.py
    region: str            # set in template.yaml, as the runtime's: S3 links are signed for the bucket's region
    conversation_days: int = 0   # as the runtime's (AssistantSettings): sending a message moves the expiry on


class PurgeSettings(BaseAppSettings):
    """The purge worker's Lambda (purge.py)."""
    assistant_table: str     # the messages of an expired conversation, or of a removed member's
    checkpoint_table: str
    checkpoint_bucket: str
    files_bucket: str        # a removed member's files
    region: str              # set in template.yaml, as the runtime's
    exports_bucket: str | None = None   # where it writes the assistant's part of a user's export
