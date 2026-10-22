"""
AgentCore Runtime starts this file (EntryPoint in template.yaml); the code is
in the assistant package. scripts/package-assistant.py puts it at the root of
the zip.
"""
from assistant.app import app
from assistant.container import get_agents

if __name__ == "__main__":
    get_agents()  # load agents/*.md and build them before serving: a bad file or setting fails here, clearly
    app.run()
