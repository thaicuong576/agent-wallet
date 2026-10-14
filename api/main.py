"""HTTP API entrypoint matching contracts/openapi.yaml."""

from api.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
