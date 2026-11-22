"""页面快照领域服务：页面DOM快照的采集与存储，消费login_mgr凭证和canvas_config配置。

快照不再只留页面地址和摘要：``capture_snapshot`` 会把入口地址、落地地址、运行身份
一并写进快照文件，并登记到站点归属清单，让运行确认后的页面去重能按归属交付并回查。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.store import store
from app.services.base import BaseModuleService, register_hook
from app.services import capture_ledger
from app import persistence as persist


class PageSnapshotService(BaseModuleService):
    MODULE = "page_snapshot"
    ENTITY = "页面快照"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["快照编号", "采集来源", "页面版本", "DOM摘要", "快照时间", "快照类型", "数据量", "回收时间"]
    REQUIRED_FIELDS = ["快照编号", "采集来源", "页面版本"]
    STATUS_ORDER = ["草稿", "已存档", "已清洗", "已回收"]
    ACTION_RULES = {"标记存档": "已存档", "确认清洗": "已清洗", "启动回收": "已回收"}
    NEGATIVE_ACTIONS = []

    def capture_snapshot(
        self,
        page_url: str,
        dom_hash: str,
        raw_dom: str,
        *,
        entry_url: str | None = None,
        run_id: str | None = None,
        entry_site: str | None = None,
        landing_site: str | None = None,
        site: str | None = None,
        role: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """采集页面快照并存入snapshots/目录，同时登记站点归属。"""
        summary_text = summary or raw_dom[:280]
        dom_hash = dom_hash or hashlib.md5(raw_dom.encode("utf-8")).hexdigest()[:16]
        snapshot = {
            "page_url": page_url,
            "landing_url": page_url,
            "entry_url": entry_url or page_url,
            "run_id": run_id,
            "entry_site": entry_site,
            "landing_site": landing_site,
            "site": site,
            "role": role,
            "dom_hash": dom_hash,
            "raw_dom_length": len(raw_dom),
            "summary": summary_text,
            "timestamp": persist.now_iso(),
            "dom_preview": raw_dom[:500],
        }
        attribution = None
        if run_id:
            attribution = capture_ledger.ingest_capture({
                "run_id": run_id,
                "entry_url": entry_url or page_url,
                "landing_url": page_url,
                "entry_site": entry_site,
                "landing_site": landing_site,
                "attribution_site": site,
                "role": role,
                "summary": summary_text,
                "content_hash": dom_hash,
                "captured_at": snapshot["timestamp"],
            })
            if attribution.get("capture"):
                snapshot["attribution_id"] = attribution["capture"].get("attribution_id")
                snapshot["primary_site"] = attribution["capture"].get("primary_site")
            if attribution.get("duplicate"):
                snapshot["duplicate"] = True
                snapshot["processing_record"] = attribution.get("processing_record")
        persist.write_json("snapshots", f"page_{dom_hash}.json", snapshot)
        return snapshot

    def snapshot_attribution(self, dom_hash: str) -> dict[str, Any] | None:
        """按快照回查站点归属清单。"""
        snapshot = persist.read_json("snapshots", f"page_{dom_hash}.json")
        if not snapshot:
            return None
        attribution_id = snapshot.get("attribution_id")
        if attribution_id:
            return capture_ledger.get_capture(attribution_id)
        return None

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        snap_files = persist.list_files("snapshots", ".json")
        base["snapshot_files"] = len([f for f in snap_files if f.startswith("page_")])
        base["snapshot_attributions"] = capture_ledger.stats()["captures"]
        # 关联的登录凭证
        credentials = store.rows("login_mgr")
        snaps = store.rows(self.MODULE)
        sites = {r.get("采集来源") for r in snaps}
        base["sites_with_credentials"] = sum(1 for c in credentials if c.get("目标站点") in sites)
        return base
