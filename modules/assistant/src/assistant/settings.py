from shared.settings import BaseAppSettings


class AssistantSettings(BaseAppSettings):
    assistant_table: str
    memory_id: str      # AgentCore Memory: conversation checkpoints and long-term memories
    api_url: str        # this stack's HTTP API, which the tools call with the user's token
    # Set explicitly in template.yaml: the AgentCore SDK falls back to us-west-2
    # when the environment doesn't name a region.
    region: str
    sandbox_id: str     # AgentCore Code Interpreter for the bash tool
    model_id: str = "global.anthropic.claude-sonnet-5-5"
    sandbox_session_seconds: int = 900   # a sandbox, and its files, last this long; then a new one starts
