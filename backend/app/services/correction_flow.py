"""驳回更正受理与双入口放行协调服务，产出更正受理回执数据集。

这个服务只负责一件事：当抽样复核已经驳回后，复核员提交的修订样本不能和
重解析接手人各自把同一个旧批次判成可发。所有受理、转交、复验版本和发布领取
都以同一份更正受理回执为准，并在进程内加锁保证连续提交不会产生两份结论。
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.output_contract import OutputContractService
from app.services.reparse_task import ReparseTaskService
from app.services.result_deliver import ResultDeliverService
from app.store import store


class CorrectionFlowService(BaseModuleService):
    MODULE = "correction_receipt"
    ENTITY = "更正受理回执"
    OPERATION_TERM = "受理"
    LIST_FIELDS = [
        "回执编号", "批次记录编号", "关联投递批次", "原样本编号", "更正样本编号",
        "修订人", "修订后结论", "受理状态", "放行方向", "重解析编号",
        "候选批次编号", "契约编号", "投递记录编号", "确认状态",
    ]
    REQUIRED_FIELDS = ["批次记录编号", "修订后结论"]
    STATUS_ORDER = ["待受理", "已受理", "转交重解析", "复验通过", "已发布"]
    ACTION_RULES = {
        "继续放行核验": "复验通过",
        "继续核验": "复验通过",
        "领取可交付批次": "已发布",
    }

    STATE_ACCEPTED = "已受理"
    STATE_REPARSE = "转交重解析"
    STATE_VERIFIED = "复验通过"
    STATE_RELEASED = "已发布"
    VERIFIED_STATES = {STATE_VERIFIED, STATE_RELEASED}

    # 所有实例共享同一把进程锁；通用动作入口和专用 API 不会绕过彼此的幂等判断。
    _global_lock = threading.RLock()

    def __init__(self) -> None:
        self._lock = self._global_lock

    # ------------------------------------------------------------------
    # 基础查询与快照
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _rows(self) -> list[dict[str, Any]]:
        return store.rows(self.MODULE)

    @staticmethod
    def _split_refs(value: Any) -> set[str]:
        return {part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip()}

    def resolve_batch(self, identifier: Any) -> dict[str, Any] | None:
        """支持批次记录编号 PB-*、投递批次 FI-*、行 id 和纯数字编号。"""
        text = str(identifier or "").strip()
        if not text:
            return None
        for row in store.rows("parse_batch"):
            if str(row.get("批次记录编号")) == text or str(row.get("关联投递批次")) == text:
                return row
            if text.isdigit() and int(row.get("id", -1)) == int(text):
                return row
        if text.isdigit():
            return store.find("parse_batch", int(text))
        return None

    def find_rejection(self, batch: dict[str, Any],
                       sample_no: str | None = None) -> dict[str, Any] | None:
        pb = str(batch.get("批次记录编号") or "")
        intake = str(batch.get("关联投递批次") or "")
        refs = {pb, intake}
        candidates = [
            row for row in store.rows("sample_review")
            if str(row.get("关联批次") or "") in refs and row.get("status") == "已驳回"
        ]
        if sample_no:
            selected = next(
                (row for row in candidates if str(row.get("样本编号")) == sample_no), None)
            if selected is not None:
                return selected
        if not candidates:
            return None
        return max(candidates, key=lambda r: int(r.get("id", 0)))

    def find_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        return next(
            (r for r in self._rows() if str(r.get("回执编号")) == str(receipt_no)), None)

    def _find_task_by_no(self, task_no: Any) -> dict[str, Any] | None:
        text = str(task_no or "").strip()
        if not text:
            return None
        for row in store.rows("reparse_task"):
            if str(row.get("重解析编号")) == text:
                return row
        if text.isdigit():
            return store.find("reparse_task", int(text))
        return None

    def _task_matches_batch(self, task: dict[str, Any], pb: str, intake: str) -> bool:
        refs = {str(task.get("关联批次") or ""), str(task.get("范围批次") or "")}
        refs.update(self._split_refs(task.get("受影响批次")))
        refs.discard("")
        return pb in refs or intake in refs

    def find_claimed_task(self, batch: dict[str, Any],
                          rejection: dict[str, Any] | None = None,
                          task_no: str | None = None,
                          explicit_only: bool = False) -> dict[str, Any] | None:
        """找出真正接手该驳回范围的任务，而不是只按批次名猜一个入口。"""
        pb = str(batch.get("批次记录编号") or "")
        intake = str(batch.get("关联投递批次") or "")
        sample_no = str((rejection or {}).get("样本编号") or "")

        explicit = self._find_task_by_no(task_no)
        if explicit is not None:
            return explicit

        matching = [
            t for t in store.rows("reparse_task")
            if self._task_matches_batch(t, pb, intake)
        ]
        if sample_no:
            scoped = [t for t in matching if str(t.get("原驳回样本") or "") == sample_no]
            if scoped:
                return max(scoped, key=lambda t: int(t.get("id", 0)))

        claimed = [t for t in matching if str(t.get("领取人") or t.get("接手人") or "").strip()]
        if claimed:
            return max(claimed, key=lambda t: int(t.get("id", 0)))
        if explicit_only:
            return None

        active = [t for t in matching if t.get("status") == "执行中"]
        if active:
            return max(active, key=lambda t: int(t.get("id", 0)))
        return None

    def _append_event(self, receipt: dict[str, Any], state: str, note: str) -> None:
        events = receipt.setdefault("流转记录", [])
        if isinstance(events, list):
            events.append({"状态": state, "时间": self._now(), "说明": note})

    def _new_receipt(self) -> tuple[int, str]:
        next_id = max((int(r.get("id", 0)) for r in self._rows()), default=0) + 1
        return next_id, f"CR-{next_id:04d}"

    def board(self, batch_no: str | None = None, receipt_no: str | None = None,
              task_no: str | None = None) -> dict[str, Any]:
        """给解析批次、重解析单和发布入口提供同一份服务端事实。"""
        rejected_batches: list[dict[str, Any]] = []
        for batch in store.rows("parse_batch"):
            rejection = self.find_rejection(batch)
            if rejection is None:
                continue
            item = dict(batch)
            item["原驳回样本"] = rejection.get("样本编号")
            item["原驳回结论"] = rejection.get("复核结论")
            item["原复核人"] = rejection.get("复核人标识")
            item["原样本值"] = rejection.get("原样本值")
            item["原驳回来源依据"] = rejection.get("来源依据")
            task = self.find_claimed_task(batch, rejection, explicit_only=True)
            if task is not None:
                item["接手任务"] = task.get("重解析编号")
                item["接手人"] = task.get("领取人") or task.get("接手人") or ""
            rejected_batches.append(item)

        receipts = [dict(r) for r in self._rows()]
        if batch_no:
            batch = self.resolve_batch(batch_no)
            if batch is not None:
                pb = str(batch.get("批次记录编号") or "")
                receipts = [
                    r for r in receipts
                    if r.get("批次记录编号") == pb or r.get("关联投递批次") == batch.get("关联投递批次")
                ]
        if receipt_no:
            receipts = [r for r in receipts if r.get("回执编号") == receipt_no]
        if task_no:
            task = self._find_task_by_no(task_no)
            no = str((task or {}).get("重解析编号") or task_no)
            receipts = [r for r in receipts if r.get("重解析编号") == no]

        tasks: list[dict[str, Any]] = []
        for task in store.rows("reparse_task"):
            related_receipts = [
                r.get("回执编号") for r in self._rows()
                if r.get("重解析编号") == task.get("重解析编号")
            ]
            if related_receipts or task.get("原驳回样本") or task.get("领取人"):
                item = dict(task)
                item["关联更正回执列表"] = related_receipts
                tasks.append(item)

        deliverables = [
            dict(r) for r in self._rows()
            if r.get("受理完成") is True and r.get("受理状态") == self.STATE_VERIFIED
        ]
        released = [
            dict(r) for r in self._rows()
            if r.get("受理状态") == self.STATE_RELEASED
        ]
        return {
            "ok": True,
            "rejected_batches": rejected_batches,
            "receipts": receipts,
            "tasks": tasks,
            "deliverables": deliverables,
            "released": released,
        }

    # ------------------------------------------------------------------
    # 领取原驳回范围
    # ------------------------------------------------------------------
    def claim_original_scope(self, batch_no: str, owner: str = "reparse-owner",
                             task_no: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            batch = self.resolve_batch(batch_no)
            if batch is None:
                return False, f"批次 {batch_no} 不存在，不能领取原驳回范围", None
            rejection = self.find_rejection(batch)
            if rejection is None:
                return False, f"批次 {batch.get('批次记录编号')} 没有可接手的已驳回样本", None

            task = self._find_task_by_no(task_no) if task_no else self.find_claimed_task(batch, rejection)
            svc = ReparseTaskService()

            if task is None:
                pb = str(batch.get("批次记录编号") or batch_no)
                intake = str(batch.get("关联投递批次") or "")
                new_no = f"RT-SCOPE-{str(rejection.get('样本编号') or pb).replace('/', '-')}"
                ok, msg, created = svc.plan_position_range(
                    new_no,
                    f"接手{rejection.get('样本编号')}的原驳回范围并按更正结论复验",
                    ReparseTaskService.KEEP_OLD,
                    [intake or pb],
                    {intake or pb: str(batch.get("完成时间") or self._now())},
                )
                if not ok or created is None:
                    return False, msg, None
                task = self._find_task_by_no(new_no) or created

            assert task is not None
            if task.get("status") == "已完成":
                return False, f"重解析单 {task.get('重解析编号')} 已完成，不能重复领取", dict(task)
            if task.get("status") == "被中止":
                return False, f"重解析单 {task.get('重解析编号')} 已被中止，不能接手", dict(task)

            task_no_value = str(task.get("重解析编号"))
            if task.get("status") == "待执行":
                ok, msg = svc.start_task(task_no_value)
                if not ok:
                    return False, msg, dict(task)

            task["领取人"] = owner or "reparse-owner"
            task["接手人"] = task["领取人"]
            task["领取时间"] = self._now()
            task["责任范围"] = "原驳回范围"
            task["范围批次"] = batch.get("批次记录编号")
            task["原驳回样本"] = rejection.get("样本编号")
            task["原驳回凭据"] = (
                f"{rejection.get('样本编号')}：{rejection.get('复核结论')}；"
                f"复核人 {rejection.get('复核人标识')}"
            )
            task["关联投递批次范围"] = batch.get("关联投递批次")
            task["pending"] = True
            batch["接手任务"] = task_no_value
            batch["接手人"] = task["领取人"]
            return True, f"已由 {task['领取人']} 领取 {task_no_value} 的原驳回范围", dict(task)

    # ------------------------------------------------------------------
    # 提交更正：幂等受理并决定继续放行还是转交真实重解析任务
    # ------------------------------------------------------------------
    def _fingerprint(self, data: dict[str, Any]) -> str:
        keys = [
            "batch_no", "sample_no", "reviewer", "corrected_sample_no",
            "original_value", "corrected_value", "revised_conclusion",
            "sample_values", "source_basis", "remark",
        ]
        compact: dict[str, Any] = {}
        for key in keys:
            value = data.get(key)
            if value is not None:
                compact[key] = value
        raw = json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def submit_correction(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        data = dict(payload or {})
        nested = data.get("values")
        if isinstance(nested, dict):
            for key, value in nested.items():
                data.setdefault(key, value)

        batch_no = str(data.get("batch_no") or data.get("批次记录编号") or "").strip()
        with self._lock:
            batch = self.resolve_batch(batch_no)
            if batch is None:
                return False, f"批次 {batch_no} 不存在，更正未受理", None
            sample_no = data.get("sample_no") or data.get("原样本编号")
            rejection = self.find_rejection(batch, str(sample_no) if sample_no else None)
            if rejection is None:
                return False, f"批次 {batch.get('批次记录编号')} 找不到原驳回凭据，不能提交更正", None

            fingerprint = self._fingerprint(data)
            idem_key = str(
                data.get("idempotency_key")
                or data.get("submit_token")
                or f"AUTO-{fingerprint}"
            )

            existing = next((r for r in self._rows() if str(r.get("幂等键")) == idem_key), None)
            if existing is None and str(data.get("idempotency_key") or "") == "":
                existing = next((r for r in self._rows() if str(r.get("提交指纹")) == fingerprint), None)
            if existing is not None:
                # 重复请求只回读原回执，不修改任何已确认字段，也不新增样本或结论。
                replay = dict(existing)
                replay["idempotent_replay"] = True
                return True, (
                    f"相同更正动作已受理，沿用唯一有效回执 {existing.get('回执编号')}，"
                    "不产生第二份结论"
                ), replay

            receipt_id, receipt_no = self._new_receipt()
            pb = str(batch.get("批次记录编号") or "")
            intake = str(batch.get("关联投递批次") or "")
            reviewer = str(data.get("reviewer") or data.get("修订人") or "reviewer-correction")
            conclusion = str(
                data.get("revised_conclusion")
                or data.get("修订后结论")
                or data.get("修订结论")
                or "通过"
            ).strip()
            original_value = str(data.get("original_value") or data.get("样本原值") or "")
            corrected_value = str(data.get("corrected_value") or data.get("更正后值") or data.get("修订值") or "")
            sample_values = data.get("sample_values") or data.get("修订样本") or data.get("修订样本值") or {}
            if not isinstance(sample_values, (dict, list)):
                sample_values = {"修订样本值": str(sample_values)}
            source_basis = str(data.get("source_basis") or data.get("来源依据") or rejection.get("复核结论") or "")

            task = self.find_claimed_task(
                batch, rejection, str(data.get("task_no") or data.get("重解析编号") or ""))
            task_no_value = str(task.get("重解析编号")) if task is not None else ""

            corrected_sample_no = str(
                data.get("corrected_sample_no")
                or data.get("更正样本编号")
                or f"SR-CORR-{receipt_id:04d}"
            )
            revised_sample = {
                "id": max((int(r.get("id", 0)) for r in store.rows("sample_review")), default=0) + 1,
                "status": "复核中",
                "pending": True,
                "abnormal": conclusion == "驳回",
                "样本编号": corrected_sample_no,
                "关联批次": pb,
                "抽样比例": str(data.get("ratio") or rejection.get("抽样比例") or "0.10"),
                "抽样行数": str(data.get("sample_rows") or data.get("抽样行数") or "1"),
                "字段一致数": str(data.get("consistent_count") or data.get("字段一致数") or ("1" if conclusion == "通过" else "0")),
                "取值偏差数": str(data.get("deviation_count") or data.get("取值偏差数") or ("0" if conclusion == "通过" else "1")),
                "复核结论": f"更正申报:{conclusion}；{corrected_value or source_basis}",
                "复核人标识": reviewer,
                "更正自": rejection.get("样本编号"),
                "更正受理回执": receipt_no,
                "原值保留": original_value or str(rejection.get("复核结论") or ""),
                "更正后值": corrected_value,
                "来源依据": source_basis,
                "样本值": sample_values,
            }
            store.rows("sample_review").append(revised_sample)

            direction = "转交重解析" if task is not None else ("继续放行" if conclusion == "通过" else "维持驳回")
            state = self.STATE_REPARSE if task is not None else (self.STATE_ACCEPTED if conclusion == "通过" else "已驳回")
            receipt: dict[str, Any] = {
                "id": receipt_id,
                "status": state,
                "pending": state not in self.VERIFIED_STATES,
                "abnormal": direction == "维持驳回",
                "回执编号": receipt_no,
                "批次记录编号": pb,
                "关联投递批次": intake,
                "原样本编号": rejection.get("样本编号"),
                "原驳回结论": rejection.get("复核结论"),
                "原复核人": rejection.get("复核人标识"),
                "原驳回快照": dict(rejection),
                "原样本值": original_value or rejection.get("复核结论"),
                "更正样本编号": corrected_sample_no,
                "修订人": reviewer,
                "修订后结论": conclusion,
                "样本原值": original_value,
                "更正后值": corrected_value,
                "修订样本值": sample_values,
                "来源依据": source_basis,
                "放行方向": direction,
                "受理状态": state,
                "受理完成": False,
                "已确认回执": True,
                "确认状态": "受理已确认，结论唯一",
                "目标回执编号": receipt_no,
                "幂等键": idem_key,
                "提交指纹": fingerprint,
                "重复提交次数": 1,
                "重解析编号": task_no_value,
                "接手人": (task or {}).get("领取人") or (task or {}).get("接手人") or "",
                "责任范围": (task or {}).get("责任范围") or ("原驳回范围" if task else ""),
                "候选批次编号": "",
                "契约编号": "",
                "投递记录编号": "",
                "流转记录": [],
                "创建时间": self._now(),
            }
            self._append_event(receipt, "原驳回", f"{rejection.get('样本编号')} 保留原样本值和来源依据")
            self._append_event(receipt, state, f"更正已受理，放行方向：{direction}")
            self._rows().append(receipt)

            batch["更正受理回执"] = receipt_no
            batch["放行冲突状态"] = state
            if task is not None:
                task["关联更正回执"] = receipt_no
                task["更正复核结论"] = f"{conclusion}：{corrected_value or source_basis}"
                task.setdefault(
                    "原驳回凭据",
                    f"{rejection.get('样本编号')}：{rejection.get('复核结论')}",
                )
                task["读取更正结论时间"] = self._now()
                message = (
                    f"更正回执 {receipt_no} 已受理；服务端核实到 {task_no_value} "
                    "已接手原驳回范围，不能从解析批次入口单独放行，已转交重解析复验"
                )
            elif conclusion == "通过":
                message = f"更正回执 {receipt_no} 已受理；未发现已接手的重解析任务，可继续完成同一版本校验后放行"
            else:
                message = f"更正回执 {receipt_no} 已受理；修订结论仍为驳回，不能放行"
            return True, message, dict(receipt)

    # ------------------------------------------------------------------
    # 复验并生成唯一正式候选版本
    # ------------------------------------------------------------------
    def _build_candidate_version(self, receipt: dict[str, Any]) -> tuple[bool, str]:
        pb = str(receipt.get("批次记录编号") or "")
        intake = str(receipt.get("关联投递批次编号") or receipt.get("关联投递批次") or "")
        original = self.resolve_batch(pb) or {}
        existing_versions = [
            b for b in store.rows("parse_batch")
            if str(b.get("源批次记录编号") or "") == pb and b.get("正式候选") == "是"
        ]
        version = f"V{len(existing_versions) + 2}"
        candidate_pb = f"{pb}-{version}"
        candidate_intake = f"{intake}-{version}" if intake else candidate_pb

        existing = next(
            (b for b in store.rows("parse_batch") if b.get("批次记录编号") == candidate_pb), None)
        if existing is not None:
            receipt["候选批次编号"] = candidate_pb
            return True, candidate_pb

        total = self._int(original, "总行数") or 1
        success = self._int(original, "成功行数") or total
        if success <= 0:
            success = total
        now = self._now()

        # 候选使用独立版本标识，避免旧驳回样本、旧隔离行或旧指纹把新版本再次判成旧产物。
        intake_rows = store.rows("file_intake")
        if not any(r.get("批次编号") == candidate_intake for r in intake_rows):
            source_intake = next((r for r in intake_rows if r.get("批次编号") == intake), {})
            intake_rows.append({
                "id": max((int(r.get("id", 0)) for r in intake_rows), default=0) + 1,
                "status": "已入库", "pending": False, "abnormal": False,
                "批次编号": candidate_intake,
                "来源渠道": source_intake.get("来源渠道", "correction-flow"),
                "原始文件名": source_intake.get("原始文件名", f"{pb}-corrected"),
                "字节大小": source_intake.get("字节大小", "0"),
                "投递时间": now,
                "文件后缀": source_intake.get("文件后缀", "csv"),
                "声明格式": source_intake.get("声明格式", "csv"),
                "接收节点": source_intake.get("接收节点", "correction-flow"),
                "正式候选": "是",
                "源投递批次": intake,
                "更正受理回执": receipt.get("回执编号"),
            })

        batch_rows = store.rows("parse_batch")
        candidate_batch = {
            "id": max((int(r.get("id", 0)) for r in batch_rows), default=0) + 1,
            "status": "已闭合", "pending": False, "abnormal": False,
            "批次记录编号": candidate_pb,
            "关联投递批次": candidate_intake,
            "解析器标识": original.get("解析器标识", "correction-reparse-parser"),
            "总行数": str(total),
            "成功行数": str(success),
            "隔离行数": "0",
            "批次状态": "已闭合",
            "完成时间": now,
            "版本号": version,
            "正式候选": "是",
            "源批次记录编号": pb,
            "源投递批次": intake,
            "更正受理回执": receipt.get("回执编号"),
            "校验唯一标记": f"{receipt.get('回执编号')}:{version}",
        }
        batch_rows.append(candidate_batch)

        fp_rows = store.rows("dup_fingerprint")
        fp_no = f"DF-CORR-{int(receipt.get('id', 0)):04d}"
        if not any(r.get("指纹编号") == fp_no for r in fp_rows):
            fp_rows.append({
                "id": max((int(r.get("id", 0)) for r in fp_rows), default=0) + 1,
                "status": "判定唯一", "pending": False, "abnormal": False,
                "指纹编号": fp_no,
                "关联批次": candidate_intake,
                "内容指纹": f"sha256:corrected-{receipt.get('id')}",
                "结构指纹": f"md5:candidate-{receipt.get('id')}",
                "命中重复批次": "",
                "重复判定": "更正后正式候选",
                "去重处置": "放行",
                "判定时间": now,
            })

        source_contract = next(
            (c for c in store.rows("output_contract") if c.get("关联批次") == pb), None)
        contract_no = f"OC-{candidate_pb}"
        columns = OutputContractService._split((source_contract or {}).get("输出列集合"))
        if not columns:
            columns = ["rec_id", "rec_timestamp", "data_value", "payload_bytes"]
        min_rows = max(1, self._int(source_contract or {}, "行数下限"))
        actual_rows = max(success, min_rows)

        contract_rows = store.rows("output_contract")
        if not any(c.get("契约编号") == contract_no for c in contract_rows):
            contract_rows.append({
                "id": max((int(r.get("id", 0)) for r in contract_rows), default=0) + 1,
                "status": "待校验", "pending": True, "abnormal": False,
                "契约编号": contract_no,
                "关联批次": candidate_pb,
                "输出列集合": ",".join(columns),
                "列顺序约定": (source_contract or {}).get("列顺序约定") or ",".join(columns),
                "必填列": (source_contract or {}).get("必填列") or columns[0],
                "行数下限": str(min_rows),
                "契约校验结论": "",
                "校验时间": "",
                "更正受理回执": receipt.get("回执编号"),
                "批次版本": version,
            })
        passed, failures, contract = OutputContractService().validate_batch(
            contract_no, columns, actual_rows, now)
        if not passed or contract is None:
            return False, f"候选版本 {candidate_pb} 契约校验未完成：{';'.join(failures)}"

        pass_sample_no = f"SR-PASS-{int(receipt.get('id', 0)):04d}"
        sample_rows = store.rows("sample_review")
        if not any(s.get("样本编号") == pass_sample_no for s in sample_rows):
            sample_rows.append({
                "id": max((int(r.get("id", 0)) for r in sample_rows), default=0) + 1,
                "status": "已通过", "pending": False, "abnormal": False,
                "样本编号": pass_sample_no,
                "关联批次": candidate_pb,
                "抽样比例": receipt.get("抽样比例", "0.10"),
                "抽样行数": receipt.get("抽样行数", "1"),
                "字段一致数": receipt.get("字段一致数", "1"),
                "取值偏差数": "0",
                "复核结论": f"更正复验通过：{receipt.get('修订后结论')}",
                "复核人标识": receipt.get("修订人"),
                "更正自": receipt.get("原样本编号"),
                "更正受理回执": receipt.get("回执编号"),
                "原值快照": receipt.get("原驳回快照"),
                "来源依据": receipt.get("来源依据"),
                "正式候选": "是",
            })

        receipt.update({
            "批次版本": version,
            "候选批次编号": candidate_pb,
            "候选投递批次": candidate_intake,
            "契约编号": contract_no,
            "校验状态": contract.get("status"),
            "校验完成时间": contract.get("校验时间"),
            "有效结论编号": pass_sample_no,
            "完成校验版本": candidate_pb,
        })
        return True, candidate_pb

    @staticmethod
    def _int(row: dict[str, Any], field: str) -> int:
        text = str(row.get(field) or "").strip()
        return int(text) if text.isdigit() else 0

    def verify_receipt(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        data = dict(payload or {})
        receipt_no = str(data.get("receipt_no") or data.get("回执编号") or "").strip()
        task_no = str(data.get("task_no") or data.get("重解析编号") or "").strip()
        with self._lock:
            receipt = self.find_receipt(receipt_no)
            if receipt is None and task_no:
                receipt = next(
                    (r for r in self._rows() if r.get("重解析编号") == task_no), None)
            if receipt is None:
                return False, f"找不到回执 {receipt_no or task_no}，不能继续核验", None
            receipt_no = str(receipt.get("回执编号"))

            if receipt.get("受理状态") in self.VERIFIED_STATES and receipt.get("候选批次编号"):
                return True, (
                    f"回执 {receipt_no} 已完成校验，沿用唯一版本 "
                    f"{receipt.get('候选批次编号')}，不重复生成修复产物"
                ), dict(receipt)

            if receipt.get("修订后结论") != "通过":
                return False, f"回执 {receipt_no} 的修订结论不是通过，不能放行", dict(receipt)

            linked_task_no = str(receipt.get("重解析编号") or task_no or "")
            if linked_task_no:
                task = self._find_task_by_no(linked_task_no)
                if task is None:
                    return False, f"回执关联的真实接手任务 {linked_task_no} 不存在", dict(receipt)
                if task_no and str(task.get("重解析编号")) != task_no:
                    return False, (
                        f"入口任务 {task_no} 与回执唯一关联任务 {linked_task_no} 不一致，"
                        "不能代其他接手任务核验"
                    ), dict(receipt)
                if task.get("status") == "被中止":
                    return False, f"重解析单 {linked_task_no} 已被中止，不能继续核验", dict(receipt)
                if not (task.get("领取人") or task.get("接手人")) and task.get("status") == "待执行":
                    return False, f"重解析单 {linked_task_no} 尚未被真实接手，不能继续核验", dict(receipt)

                svc = ReparseTaskService()
                if task.get("status") != "已完成":
                    count = max(1, self._int(task, "受影响投递数"))
                    ok, msg, _updated = svc.complete_task(
                        linked_task_no, count,
                        f"读取更正复核结论 {receipt.get('更正样本编号')} 与原驳回凭据 "
                        f"{receipt.get('原样本编号')}，完成关联批次范围重解析"
                    )
                    if not ok and "已完成" not in msg:
                        return False, msg, dict(receipt)
                # 补充真实处理范围，但绝不覆盖“重解析原因”这一原始出处。
                processed = self._split_refs(task.get("受影响批次"))
                processed.update(self._split_refs(task.get("关联投递批次范围")))
                processed.add(str(receipt.get("关联投递批次") or ""))
                processed.discard("")
                task["已处理批次范围"] = ",".join(sorted(processed))
                task["更正复核结论已读取"] = receipt.get("更正样本编号")
                task["原驳回凭据编号"] = receipt.get("原样本编号")
                task["处理回执"] = receipt_no
                self._append_event(receipt, "重解析完成", f"真实任务 {linked_task_no} 已处理范围 {sorted(processed)}")

            ok, version_or_msg = self._build_candidate_version(receipt)
            if not ok:
                receipt["受理状态"] = "校验未完成"
                return False, version_or_msg, dict(receipt)

            receipt["受理状态"] = self.STATE_VERIFIED
            receipt["status"] = self.STATE_VERIFIED
            receipt["pending"] = False
            receipt["abnormal"] = False
            receipt["受理完成"] = True
            receipt["可交付"] = True
            receipt["确认状态"] = "目标回执已确认，等待发布领取"
            self._append_event(receipt, self.STATE_VERIFIED, f"唯一完成校验版本：{version_or_msg}")
            return True, (
                f"回执 {receipt_no} 复验通过；正式候选 {version_or_msg} 已完成契约校验，"
                "可由发布入口领取"
            ), dict(receipt)

    # ------------------------------------------------------------------
    # 发布入口领取并实际投递唯一候选
    # ------------------------------------------------------------------
    def claim_deliverable(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        data = dict(payload or {})
        receipt_no = str(data.get("receipt_no") or data.get("回执编号") or "").strip()
        batch_no = str(data.get("batch_no") or data.get("批次记录编号") or "").strip()
        with self._lock:
            receipt = self.find_receipt(receipt_no) if receipt_no else None
            if receipt is None and batch_no:
                batch = self.resolve_batch(batch_no)
                if batch is not None:
                    candidates = [
                        r for r in self._rows()
                        if r.get("批次记录编号") == batch.get("批次记录编号")
                        and r.get("受理状态") in self.VERIFIED_STATES
                    ]
                    if len(candidates) > 1:
                        return False, "该批次存在多个已受理回执，必须指定唯一回执后再发布", None
                    receipt = candidates[0] if candidates else None
            if receipt is None:
                deliverables = [r for r in self._rows() if r.get("受理状态") in self.VERIFIED_STATES]
                if len(deliverables) == 1:
                    receipt = deliverables[0]
                elif not deliverables:
                    return False, "当前没有完成复验和契约校验的可交付批次，未受理完的修复产物不得送达", None
                else:
                    return False, "存在多个可交付回执，请指定要领取的唯一回执", None

            receipt_no = str(receipt.get("回执编号"))
            existing_delivery_no = str(receipt.get("投递记录编号") or "")
            if receipt.get("受理状态") == self.STATE_RELEASED and existing_delivery_no:
                delivery = ResultDeliverService()._find(existing_delivery_no)
                return True, (
                    f"回执 {receipt_no} 已由发布入口领取，目标回执 {existing_delivery_no} 保持不变"
                ), {"receipt": dict(receipt), "delivery": dict(delivery) if delivery else None,
                    "adopted_version": receipt.get("候选批次编号")}

            if receipt.get("受理状态") != self.STATE_VERIFIED or not receipt.get("受理完成"):
                return False, f"回执 {receipt_no} 尚未完成受理和校验，修复产物不得送达", dict(receipt)
            contract_no = str(receipt.get("契约编号") or "")
            contract = next((c for c in store.rows("output_contract") if c.get("契约编号") == contract_no), None)
            if contract is None or contract.get("status") != "已通过":
                return False, f"回执 {receipt_no} 未绑定同一个已通过校验的契约版本，不得送达", dict(receipt)
            candidate_no = str(receipt.get("候选批次编号") or "")
            candidate = next((b for b in store.rows("parse_batch") if b.get("批次记录编号") == candidate_no), None)
            if candidate is None or candidate.get("批次状态") != "已闭合":
                return False, f"候选版本 {candidate_no} 未闭合或不存在，不得送达", dict(receipt)

            version_count = sum(
                1 for b in store.rows("parse_batch")
                if b.get("更正受理回执") == receipt_no and b.get("正式候选") == "是"
            )
            if version_count != 1:
                return False, f"回执 {receipt_no} 对应 {version_count} 个候选版本，必须只采用一个完成校验的版本", dict(receipt)

            delivery_no = f"RD-{receipt_no}"
            target = str(data.get("target_location") or data.get("目标位置") or (
                f"/data/cooked/corrected/{receipt.get('批次记录编号')}/{candidate_no}/"
            ))
            row_count = self._int(contract, "实际行数") or self._int(candidate, "成功行数") or 1
            deliver_svc = ResultDeliverService()
            delivery = deliver_svc._find(delivery_no)
            if delivery is None:
                ok, msg, delivery = deliver_svc.create_deliver_record(
                    delivery_no, contract_no, target, row_count,
                    str(data.get("output_format") or data.get("投递格式") or "parquet"))
                if not ok or delivery is None:
                    return False, msg, dict(receipt)
            ok, msg = deliver_svc.confirm_delivered(delivery_no, self._now())
            if not ok and "已确认送达" not in msg:
                return False, msg, dict(receipt)
            delivery = deliver_svc._find(delivery_no)

            owner = str(data.get("owner") or data.get("领取人") or "release-duty")
            receipt.update({
                "受理状态": self.STATE_RELEASED,
                "status": self.STATE_RELEASED,
                "pending": False,
                "abnormal": False,
                "投递记录编号": delivery_no,
                "发布领取人": owner,
                "发布时间": self._now(),
                "目标位置": target,
                "adopted_version": candidate_no,
                "采用版本数": 1,
            })
            self._append_event(receipt, self.STATE_RELEASED, f"发布入口 {owner} 已领取并送达 {delivery_no}")
            return True, (
                f"可交付批次已领取：唯一采用 {candidate_no}，投递回执 {delivery_no} 已确认"
            ), {"receipt": dict(receipt), "delivery": dict(delivery) if delivery else None,
                "adopted_version": candidate_no, "adopted_version_count": 1}
