from aws_lambda_powertools import Logger
from langchain.agents.middleware import ToolCallRequest

logger = Logger()


def tool_failed(error: Exception, request: ToolCallRequest) -> str:
    """For ToolErrorMiddleware: the model gets a plain result; the details stay in the logs."""
    logger.warning("Tool failed", extra={"tool": request.tool_call["name"]}, exc_info=error)
    return "This tool failed. Tell the user it didn't work this time and to try again later."
