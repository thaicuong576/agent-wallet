"""Health and evaluation report routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.problems import make_problem_response
from api.schemas import ROOT_DIR, get_validator

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/eval/report")
def get_eval_report(request: Request) -> JSONResponse:
    report = getattr(request.app.state, "eval_report", None)
    if report is None:
        report_path = ROOT_DIR / "reports" / "eval_report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                report = None

    if report is None:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Eval Report Not Found",
            detail="No eval report has been generated yet.",
        )

    get_validator("eval-report").validate(report)
    return JSONResponse(status_code=200, content=report)
