"""驳回更正双入口协调的回归测试。"""
from __future__ import annotations

import pytest

from app.services.correction_flow import CorrectionFlowService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reload_seed()
    yield
    store.reload_seed()


def test_double_submit_creates_one_receipt_and_keeps_original_rejection():
    svc = CorrectionFlowService()
    ok, _msg, task = svc.claim_original_scope("PB-002", owner="reparse-li")
    assert ok is True
    assert task["重解析编号"] == "RT-002"

    payload = {
        "batch_no": "PB-002",
        "reviewer": "reviewer-wang",
        "original_value": "-50",
        "corrected_value": "50.0",
        "revised_conclusion": "通过",
        "idempotency_key": "fixed-correction-key",
    }
    ok1, msg1, first = svc.submit_correction(payload)
    ok2, msg2, second = svc.submit_correction(payload)

    assert ok1 and ok2
    assert first and second
    assert first["回执编号"] == second["回执编号"]
    assert first["受理状态"] == "转交重解析"
    assert first["重解析编号"] == "RT-002"
    assert "沿用唯一有效回执" in msg2
    assert len([
        r for r in store.rows("correction_receipt")
        if r.get("批次记录编号") == "PB-002"
    ]) == 1

    original = next(r for r in store.rows("sample_review") if r["样本编号"] == "SR-002")
    assert original["status"] == "已驳回"
    assert "25.8%" in original["复核结论"]
    task_row = next(r for r in store.rows("reparse_task") if r["重解析编号"] == "RT-002")
    assert task_row["重解析原因"].startswith("分隔规则DR-003")


def test_verify_and_release_use_one_candidate_and_are_idempotent():
    svc = CorrectionFlowService()
    svc.claim_original_scope("PB-002", owner="reparse-li")
    _ok, _msg, receipt = svc.submit_correction({
        "batch_no": "PB-002",
        "revised_conclusion": "通过",
        "idempotency_key": "release-key",
    })
    receipt_no = receipt["回执编号"]

    ok, msg, verified = svc.verify_receipt({"receipt_no": receipt_no})
    assert ok, msg
    candidate = verified["候选批次编号"]
    assert candidate == "PB-002-V2"
    assert verified["契约编号"]
    assert verified["校验状态"] == "已通过"

    candidate_rows = [
        r for r in store.rows("parse_batch")
        if r.get("更正受理回执") == receipt_no and r.get("正式候选") == "是"
    ]
    assert len(candidate_rows) == 1

    ok, msg, first = svc.claim_deliverable({"receipt_no": receipt_no})
    assert ok, msg
    delivery_no = first["delivery"]["投递记录编号"]
    assert first["adopted_version_count"] == 1
    assert first["adopted_version"] == candidate

    ok, msg, second = svc.claim_deliverable({"receipt_no": receipt_no})
    assert ok
    assert second["delivery"]["投递记录编号"] == delivery_no
    assert "保持不变" in msg


def test_unverified_receipt_cannot_be_delivered():
    svc = CorrectionFlowService()
    _ok, _msg, receipt = svc.submit_correction({
        "batch_no": "PB-002",
        "revised_conclusion": "通过",
        "idempotency_key": "unverified-key",
    })
    # 注意：seed RT-002 是执行中，因此会进入转交重解析；这里不执行 verify。
    ok, msg, result = svc.claim_deliverable({"receipt_no": receipt["回执编号"]})
    assert ok is False
    assert "尚未完成" in msg or "未完成校验" in msg
    assert result is None or isinstance(result, dict)
