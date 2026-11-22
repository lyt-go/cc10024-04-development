"""站点归属清单领域服务：把跳转采集的入口、落地地址和运行身份落成可查询的归属清单。

列表与明细都走通用资源路由 ``/api/site_attribution``；采集登记、重送、入口回查、
去重分组与复采来源通过模块内的动作和附加接口完成。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services import capture_ledger

MODULE = "site_attribution"


class SiteAttributionService(BaseModuleService):
    MODULE = MODULE
    ENTITY = "站点归属"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "归属编号", "采集归属", "主站点", "入口站点", "入口地址", "落地站点", "落地地址",
        "运行身份", "采集角色", "页面摘要", "抓取次数", "处理次数",
    ]
    REQUIRED_FIELDS = ["运行身份", "入口地址", "落地地址", "页面摘要"]
    STATUS_ORDER = ["已归属", "已分组", "已归档"]
    ACTION_RULES = {"标记归档": "已归档"}
    NEGATIVE_ACTIONS = []

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """正式站点归属清单；``采集归属`` 过滤主站点，``入口站点`` 供按入口核对。"""
        filters = filters or {}
        rows = capture_ledger.list_manifest(
            run_id=filters.get("运行身份") or None,
            site=filters.get("采集归属") or filters.get("归属站点") or None,
            entry_site=filters.get("入口站点") or None,
            landing_site=filters.get("落地站点") or None,
            role=filters.get("采集角色") or None,
        )
        if keyword:
            needle = keyword.casefold()
            rows = [
                row for row in rows
                if any(needle in str(row.get(field, "")).casefold() for field in ("归属编号", "入口地址", "落地地址"))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return capture_ledger.get_capture(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        result = capture_ledger.ingest_capture(values)
        if not result.get("ok"):
            return None, list(result.get("missing") or ["归属信息"])
        return result.get("entry"), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{MODULE}可执行范围"
        entry = capture_ledger.mark_status(entry_id, target)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        return entry, f"{self.ENTITY}已{action}"

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        stats = capture_ledger.stats()
        rows = capture_ledger.list_manifest()
        base["count"] = stats["captures"]
        base.update(stats)
        base["by_status"] = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return base


SERVICE = SiteAttributionService()
