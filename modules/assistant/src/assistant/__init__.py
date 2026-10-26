"""
Assistant module: an AI agent (LangChain create_agent) hosted on Amazon
Bedrock AgentCore Runtime instead of Lambda.

Owns: the assistant table (monthly usage per workspace, each user's
conversations and their history) and the checkpoint table and bucket (each
agent's state in each conversation, DynamoDBSaver).
Serves: the chat on AgentCore Runtime (AG-UI, app.py), and the users'
conversations on the HTTP API (/assistant/conversations, handler.py), with a
worker that deletes a deleted conversation's checkpoints (purge.py).
Publishes: nothing.
Reads the caller from the login token, which AgentCore Runtime verifies
before our code runs. Talks to other modules only through their public HTTP
API, with the caller's own token, so the agent can never do more than the
user could.
"""
