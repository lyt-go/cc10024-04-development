"""运行历史附加接口：查看一次运行的采集台账、去重分组与代表页来源。

固定路径需先于通用资源路由注册。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services import dedup_groups
from app.services.run_history import RunHistoryService

router = APIRouter(prefix="/api/run_history", tags=["运行历史"])
_service = RunHistoryService()


@router.get("/{entry_id}/captures")
def run_captures(entry_id: int, site: str | None = Query(default=None)) -> dict[str, Any]:
    entry = _service.get_entry(entry_id)
    if entry is None:
        return {"ok": False, "message": f"运行记录 {entry_id} 不存在"}
    from app.services import capture_ledger

    candidates = [
        str(entry.get("记录编号") or ""), str(entry.get("关联计划") or ""), str(entry_id),
    ]
    run_id = next(
        (value for value in candidates if capture_ledger.list_captures(run_id=value)), None)
    rows = capture_ledger.list_captures(run_id=run_id, site=site) if run_id else []
    return {"ok": True, "run_id": run_id, "total": len(rows), "items": rows}


@router.get("/{entry_id}/dedup")
def run_dedup(entry_id: int, site: str | None = Query(default=None)) -> JSONResponse:
    result = _service.dedup_result(entry_id, site=site)
    return JSONResponse(status_code=200 if result.get("ok") else 404, content=result)


@router.get("/{entry_id}/representatives")
def run_representatives(entry_id: int, site: str | None = Query(default=None)) -> dict[str, Any]:
    entry = _service.get_entry(entry_id)
    if entry is None:
        return {"ok": False, "total": 0, "items": [], "message": f"运行记录 {entry_id} 不存在"}
    candidates = [str(entry.get("记录编号") or ""), str(entry.get("关联计划") or ""), str(entry_id)]
    from app.services import capture_ledger

    run_id = next(
        (value for value in candidates if capture_ledger.list_captures(run_id=value)), None)
    rows = dedup_groups.representatives(run_id=run_id, site=site) if run_id else []
    return {"ok": True, "run_id": run_id, "total": len(rows), "items": rows}
