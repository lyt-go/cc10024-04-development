"""站点归属清单接口：采集登记/重送、入口回查、正式归属清单、去重分组与复采来源。

这些固定路径必须注册在通用资源路由 ``/api/{module}`` 之前，否则会被
``/{module}/{entry_id}`` 抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from app.services import capture_ledger, dedup_groups

router = APIRouter(prefix="/api/site_attribution", tags=["站点归属清单"])


def _ingest_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """同时支持单条、{captures: [...]} 以及 capture 别名。"""
    for key in ("captures", "capture", "items", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return [payload]


@router.post("/captures")
def ingest_captures(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """登记一次或多次跳转/直接采集；重复送同一次抓取只追加处理记录。"""
    items = _ingest_payload(payload)
    results = [capture_ledger.ingest_capture(item) for item in items]
    ok = all(result.get("ok") for result in results)
    counted = sum(1 for result in results if result.get("counted"))
    return {
        "ok": ok,
        "counted": counted,
        "duplicates": sum(1 for result in results if result.get("duplicate")),
        "results": results,
        "captures": [result.get("capture") for result in results if result.get("capture")],
    }


@router.get("/captures")
def list_captures(
    run_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
    entry_site: str | None = Query(default=None),
    landing_site: str | None = Query(default=None),
    content_site: str | None = Query(default=None),
    role: str | None = Query(default=None),
) -> dict[str, Any]:
    """采集台账：每次真实抓取一行，重送不会增加行。"""
    rows = capture_ledger.list_captures(
        run_id=run_id, site=site, entry_site=entry_site, landing_site=landing_site,
        content_site=content_site, role=role,
    )
    return {"ok": True, "total": len(rows), "items": rows}


@router.get("/manifest")
def list_manifest(
    run_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
    source_site: str | None = Query(default=None),
    entry_site: str | None = Query(default=None),
    landing_site: str | None = Query(default=None),
    content_site: str | None = Query(default=None),
    primary_site: str | None = Query(default=None),
    role: str | None = Query(default=None),
) -> dict[str, Any]:
    """正式站点归属清单：一次抓取一行，只记一个主站点。"""
    rows = capture_ledger.list_manifest(
        run_id=run_id, site=site, source_site=source_site, entry_site=entry_site,
        landing_site=landing_site, content_site=content_site,
        primary_site=primary_site, role=role,
    )
    return {"ok": True, "total": len(rows), "items": rows}


@router.get("/lookup")
def lookup_entry(
    attribution_id: str | None = Query(default=None),
    entry_url: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
) -> JSONResponse:
    """入口回查：带站点标识时做归属隔离，只返回该站点有采集归属的明细。"""
    result = capture_ledger.lookup_entry(
        attribution_id=attribution_id, entry_url=entry_url, run_id=run_id, site=site,
    )
    status = 200 if result.get("ok") or result.get("visible") else 404
    return JSONResponse(status_code=status, content=result)


@router.get("/lookup/{attribution_id}")
def lookup_entry_detail(
    attribution_id: str,
    site: str | None = Query(default=None),
) -> JSONResponse:
    result = capture_ledger.lookup_entry(attribution_id=attribution_id, site=site)
    status = 200 if result.get("ok") or result.get("visible") else 404
    return JSONResponse(status_code=status, content=result)


@router.get("/groups")
def list_groups(
    run_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """去重分组数据；带站点时只返回该站点有采集归属的组内成员。"""
    rows = dedup_groups.list_groups(run_id=run_id, site=site, group_id=group_id)
    return {"ok": True, "total": len(rows), "items": rows}


@router.get("/groups/{group_id}")
def get_group(group_id: str, site: str | None = Query(default=None)) -> JSONResponse:
    group = dedup_groups.get_group(group_id, site=site)
    if group is None:
        return JSONResponse(status_code=404, content={
            "ok": False, "group_id": group_id,
            "message": f"去重分组 {group_id} 不存在或站点无采集归属",
        })
    return JSONResponse(status_code=200, content={"ok": True, "group": group})


@router.get("/groups/{group_id}/recrawl")
def recrawl(group_id: str, site: str | None = Query(default=None)) -> JSONResponse:
    """复采执行：返回可复采的内容来源记录，可追到入口、落地地址与原摘要。"""
    result = dedup_groups.recrawl_target(group_id, site=site)
    return JSONResponse(status_code=200 if result.get("ok") else 404, content=result)


@router.get("/representatives")
def representatives(
    run_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
) -> dict[str, Any]:
    """代表页的采集来源记录清单。"""
    rows = dedup_groups.representatives(run_id=run_id, site=site)
    return {"ok": True, "total": len(rows), "items": rows}


@router.post("/dedup")
def run_dedup(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """按运行身份对采集台账执行正文去重，写出去重分组与代表页来源记录。"""
    run_id = str(payload.get("run_id") or payload.get("运行身份") or "").strip()
    if not run_id:
        return {"ok": False, "message": "缺少运行身份"}
    rule_id = payload.get("rule_id")
    result = dedup_groups.group_captures(
        run_id,
        trigger=str(payload.get("trigger") or "manual"),
        rule_id=int(rule_id) if rule_id not in (None, "") else None,
    )
    return {"ok": True, **result}


@router.get("/stats")
def stats() -> dict[str, Any]:
    return {
        "ok": True,
        "ledger": capture_ledger.stats(),
        "groups": dedup_groups.stats(),
    }


@router.get("/state")
def state() -> dict[str, Any]:
    """一次性返回台账、正式清单、分组和复采来源，便于核对。"""
    return {
        "ok": True,
        "captures": capture_ledger.list_captures(),
        "manifest": capture_ledger.list_manifest(),
        "groups": dedup_groups.list_groups(),
        "representatives": dedup_groups.representatives(),
    }
