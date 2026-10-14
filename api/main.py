"""HTTP API entrypoint matching contracts/openapi.yaml."""

import pathlib

from fastapi.staticfiles import StaticFiles

from api.app import create_app

app = create_app()

UI_DIR = pathlib.Path(__file__).resolve().parent.parent / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

__all__ = ["app", "create_app"]
