"""
Builds each module's HTTP app the same way, so module handlers stay tiny:

    app = create_app(router, title="Orders API")
    handler = create_handler(app)

CORS is configured on the HTTP API in template.yaml (API Gateway ignores CORS
headers returned by functions). The OpenAPI document is generated offline by
scripts/export-openapi.py instead of being served, since every route needs a
login token.
"""
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.depends import DependencyResolutionError
from aws_lambda_powertools.logging import correlation_paths

from shared.errors import AppError

logger = Logger()


def create_app(*routers, title: str) -> APIGatewayHttpResolver:
    app = APIGatewayHttpResolver(enable_validation=True)
    for router in routers:
        app.include_router(router)
    app.configure_openapi(title=title)

    @app.exception_handler(AppError)
    def handle_app_error(error: AppError) -> Response:
        return Response(
            status_code=error.status_code,
            content_type=content_types.APPLICATION_JSON,
            body={"message": error.message},
        )

    # Errors raised inside a Depends() function (e.g. get_current_user) arrive wrapped.
    @app.exception_handler(DependencyResolutionError)
    def handle_dependency_error(error: DependencyResolutionError) -> Response:
        if isinstance(error.__cause__, AppError):
            return handle_app_error(error.__cause__)
        raise error

    return app


def create_handler(app: APIGatewayHttpResolver):
    """The Lambda entry point: adds tenant/user to every log line, then routes."""

    @logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
    def handler(event: dict, context) -> dict:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        logger.append_keys(tenant_id=claims.get("tenant_id"), user_id=claims.get("sub"))
        return app.resolve(event, context)

    return handler
