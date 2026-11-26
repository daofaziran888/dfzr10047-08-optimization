"""驳回更正协调接口：受理回执、范围领取、复验和发布领取共用同一服务端事实。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from app.services.correction_flow import CorrectionFlowService

router = APIRouter(prefix="/api/correction-flow", tags=["驳回更正协调"])
service = CorrectionFlowService()


@router.get("/board")
def board(
    batch_no: str | None = Query(default=None),
    receipt_no: str | None = Query(default=None),
    task_no: str | None = Query(default=None),
) -> dict[str, Any]:
    return service.board(batch_no=batch_no, receipt_no=receipt_no, task_no=task_no)


@router.get("/receipts/{receipt_no}")
def get_receipt(receipt_no: str) -> dict[str, Any]:
    receipt = service.find_receipt(receipt_no)
    return {"ok": receipt is not None, "receipt": receipt}


@router.post("/claims")
def claim_scope(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, task = service.claim_original_scope(
        str(payload.get("batch_no") or payload.get("批次记录编号") or ""),
        str(payload.get("owner") or payload.get("领取人") or "reparse-owner"),
        str(payload.get("task_no") or payload.get("重解析编号") or "") or None,
    )
    return {"ok": ok, "message": message, "task": task}


@router.post("/corrections")
def submit_correction(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, receipt = service.submit_correction(payload)
    return {"ok": ok, "message": message, "receipt": receipt}


@router.post("/verify")
def verify(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, receipt = service.verify_receipt(payload)
    return {"ok": ok, "message": message, "receipt": receipt}


@router.post("/release-claim")
def release_claim(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, result = service.claim_deliverable(payload)
    response: dict[str, Any] = {"ok": ok, "message": message}
    if isinstance(result, dict) and "receipt" in result:
        response.update(result)
    else:
        response["receipt"] = result
    return response
