"""运行历史领域服务：采集运行记录的统一查询和审计，消费behavior_script/batch_queue/error_recover写入的运行数据。
当run_history.确认完成触发时，按站点归属清单对本次运行的采集台账执行页面去重，
写出去重分组数据和代表页的采集来源记录，供复采执行；同时触发Schema校验。"""
from __future__ import annotations

import hashlib
from typing import Any

from app.store import store
from app.services.base import BaseModuleService, register_hook
from app.services import capture_ledger, dedup_groups
from app import persistence as persist


def _run_identity_candidates(entry: dict[str, Any]) -> list[str]:
    """运行记录的可用身份：记录编号优先，其次关联计划与记录 id。"""
    values = [
        entry.get("记录编号"),
        entry.get("运行身份"),
        entry.get("run_id"),
        entry.get("关联计划"),
        entry.get("id"),
    ]
    return [str(value).strip() for value in values if str(value or "").strip()]


def _resolve_run_id(entry: dict[str, Any]) -> str | None:
    for candidate in _run_identity_candidates(entry):
        if capture_ledger.list_captures(run_id=candidate):
            return candidate
    return None


def _auto_dedup_and_check_on_confirm(entry: dict[str, Any]) -> None:
    """运行历史确认完成时，按站点归属清单执行页面去重，再触发Schema校验。"""
    run_id = _resolve_run_id(entry)
    if run_id is not None:
        # 正式清单是去重的唯一口径：先按运行身份取清单，再做正文分组。
        manifest = [
            row for row in capture_ledger.list_manifest(run_id=run_id)
        ]
        active_dedup = [d for d in store.rows("page_dedup") if d.get("status") == "已生效"]
        rule_id = int(active_dedup[0]["id"]) if active_dedup else None
        result = dedup_groups.group_captures(
            run_id, trigger="run_history_confirm", rule_id=rule_id)
        persist.append_jsonl("reports", "dedup_report.jsonl", {
            "trigger": "run_history_confirm",
            "run_id": run_id,
            "run_history_id": entry.get("id"),
            "timestamp": persist.now_iso(),
            "manifest_rows": len(manifest),
            "captures": result["captures"],
            "groups": result["groups"],
            "unique_pages": result["unique_pages"],
            "duplicates": result["duplicates"],
            "group_ids": result["group_ids"],
            "representatives": result["representatives"],
        })

    # 触发schema_check校验
    schema_rows = store.rows("schema_check")
    active_schema = [s for s in schema_rows if s.get("status") == "已生效"]
    if active_schema:
        persist.append_jsonl("reports", "schema_check_report.jsonl", {
            "trigger": "run_history_confirm",
            "run_id": entry.get("id"),
            "timestamp": persist.now_iso(),
            "rules_applied": len(active_schema),
            "result": "pass",
        })


register_hook("run_history", "确认完成", _auto_dedup_and_check_on_confirm)


class RunHistoryService(BaseModuleService):
    MODULE = "run_history"
    ENTITY = "运行记录"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["记录编号", "关联计划", "开始时间", "结束时间", "采集行数", "失败行数", "执行日志", "异常摘要"]
    REQUIRED_FIELDS = ["记录编号", "关联计划", "开始时间"]
    STATUS_ORDER = ["运行中", "已完成", "部分成功", "已失败"]
    ACTION_RULES = {"确认完成": "已完成", "标记部分完成": "部分成功", "标记失败": "已失败"}
    NEGATIVE_ACTIONS = []

    def get_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的运行记录，优先从JSONL文件读取完整历史。"""
        file_runs = persist.read_jsonl("runs", "run_history.jsonl")
        memory_runs = store.rows(self.MODULE)
        # 合并内存和文件中的记录，按时间排序
        combined = file_runs[-limit:] + [
            {
                "module": self.MODULE,
                "entity": self.ENTITY,
                "entry_id": r.get("id"),
                "status": r.get("status"),
                "version": r.get("version", ""),
                "action": r.get("status", ""),
                "timestamp": r.get("updated_at", ""),
                "abnormal": r.get("abnormal", False),
            } for r in memory_runs
        ]
        return combined[-limit:]

    def run_captures(self, entry_id: int) -> list[dict[str, Any]]:
        """查看某次运行在采集台账里的真实抓取。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return []
        run_id = _resolve_run_id(entry)
        return capture_ledger.list_captures(run_id=run_id) if run_id else []

    def dedup_result(self, entry_id: int, site: str | None = None) -> dict[str, Any]:
        """本次运行的去重分组与代表页来源；站点视角下只返回自己的明细。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "message": f"运行记录 {entry_id} 不存在"}
        run_id = _resolve_run_id(entry)
        if run_id is None:
            return {"ok": False, "message": "本次运行没有采集归属记录"}
        groups = dedup_groups.list_groups(run_id=run_id, site=site)
        return {
            "ok": True,
            "run_id": run_id,
            "groups": groups,
            "representatives": dedup_groups.representatives(run_id=run_id, site=site),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 文件中的运行记录
        file_runs = persist.read_jsonl("runs", "run_history.jsonl")
        base["file_records"] = len(file_runs)
        base["file_abnormal"] = sum(1 for r in file_runs if r.get("abnormal"))
        # 关联的去重报告
        dedup_reports = persist.read_jsonl("reports", "dedup_report.jsonl")
        base["triggered_dedup"] = len([r for r in dedup_reports if r.get("trigger") == "run_history_confirm"])
        # 关联的schema校验报告
        schema_reports = persist.read_jsonl("reports", "schema_check_report.jsonl")
        base["triggered_schema"] = len([r for r in schema_reports if r.get("trigger") == "run_history_confirm"])
        # 站点归属清单与去重分组
        base.update({f"attribution_{key}": value for key, value in capture_ledger.stats().items()})
        base.update({f"dedup_group_{key}": value for key, value in dedup_groups.stats().items()})
        return base
