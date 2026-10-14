"""HTTP API. The contract is contracts/openapi.yaml; this module grows to match it."""

from fastapi import FastAPI

app = FastAPI(title="agent-wallet", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
