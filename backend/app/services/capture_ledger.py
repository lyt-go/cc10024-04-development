"""跳转采集台账与站点归属清单。

页面快照原本只记录页面地址和摘要，无法回答「这次抓取是从哪个入口进来的、落在哪个
站点、属于哪一次运行」。本模块把每一次真实抓取（入口地址 + 落地地址 + 运行身份）
持久化下来，并给出正式的站点归属清单：

* 一次抓取只记一个主站点：主站点取采集归属站点（跳转采集为入口站点，直接采集则入口
  与落地同一站点）。入口站点、落地站点分别保留，采集团队按入口核对覆盖，处理正文的
  团队通过落地地址复采，两种口径不再争抢同一条抓取的主归属。
* 同一次抓取被重送时按 ``(运行身份, 入口地址, 落地地址)`` 去重，只追加处理记录，
  不会多算一次抓取。
* 归属清单、采集台账和重送处理记录都落盘到 ``data/attributions/``，重启后可回查。

站点视角：正式清单按 ``site``（主站点/采集归属）检索；采集团队按入口核对时用
``entry_site``，正文团队按落地站点检索用 ``landing_site`` / ``content_site``。
查看单条明细时必须传入站点，只有该站点确实持有采集归属（入口站点或显式归属站点）
时才可见，避免共享正文时互相看到对方明细。
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from typing import Any, Iterable
from urllib.parse import urlparse

from app import persistence as persist

_SUB = "attributions"
_CAPTURES_FILE = "captures.json"
_MANIFEST_FILE = "manifest.jsonl"
_PROCESSING_FILE = "processing.jsonl"

_LOCK = threading.RLock()
_captures: dict[str, dict[str, Any]] = {}
_next_id = 1
_loaded_mtime: float | None = None


# ---------------------------------------------------------------------------
# 入参归一化：同时接受中文字段和常见英文别名，方便接口与脚本调用。
# ---------------------------------------------------------------------------

ALIASES: dict[str, tuple[str, ...]] = {
    "run_id": ("run_id", "run_identity", "运行身份", "运行ID", "运行编号", "运行标识"),
    "entry_site": ("entry_site", "入口站点", "入口站", "入口方", "采集入口站点"),
    "entry_url": ("entry_url", "入口地址", "入口URL", "入口页地址", "采集入口", "entry"),
    "landing_site": ("landing_site", "落地站点", "落地站", "落地方", "详情站点"),
    "landing_url": (
        "landing_url", "落地地址", "落地URL", "详情地址", "页面地址", "page_url", "url",
    ),
    "attribution_site": (
        "attribution_site", "采集归属", "归属站点", "归属方", "站点归属", "collection_site",
        "site",
    ),
    "summary": (
        "summary", "页面摘要", "摘要", "原摘要", "正文摘要", "DOM摘要", "dom_summary", "body",
    ),
    "content_hash": ("content_hash", "内容指纹", "正文指纹", "摘要指纹", "dom_hash"),
    "role": ("role", "采集角色", "采集方式", "capture_role"),
    "captured_at": ("captured_at", "采集时间", "抓取时间", "timestamp"),
}

ROLE_DIRECT = "直接采集"
ROLE_JUMP = "跳转采集"


def _pick(values: dict[str, Any], key: str) -> str:
    for alias in ALIASES[key]:
        value = values.get(alias)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _site_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path.split("/")[0] or url
    return host.lower()


def _fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _content_hash(summary: str) -> str:
    return hashlib.md5(summary.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 持久化：按文件修改时间懒加载，测试清理 data/ 后不会读到旧内存。
# ---------------------------------------------------------------------------


def _captures_path() -> str:
    return persist.data_path(_SUB, _CAPTURES_FILE)


def _read_file_mtime() -> float | None:
    try:
        return os.path.getmtime(_captures_path())
    except OSError:
        return None


def _ensure_loaded() -> None:
    global _captures, _next_id, _loaded_mtime
    mtime = _read_file_mtime()
    if mtime is None:
        if _captures:
            _captures = {}
            _next_id = 1
        _loaded_mtime = None
        return
    if _loaded_mtime is not None and mtime <= _loaded_mtime:
        return
    data = persist.read_json(_SUB, _CAPTURES_FILE) or {}
    rows = data.get("captures", []) if isinstance(data, dict) else []
    _captures = {
        row.get("capture_key") or _fingerprint(
            str(row.get("run_id", "")),
            str(row.get("entry_url", "")),
            str(row.get("landing_url", "")),
        ): row
        for row in rows
        if isinstance(row, dict)
    }
    known_next = int(data.get("next_id", 1)) if isinstance(data, dict) else 1
    _next_id = max([known_next] + [int(row.get("id", 0)) + 1 for row in _captures.values()])
    _loaded_mtime = mtime


def _save() -> None:
    global _loaded_mtime
    rows = sorted(_captures.values(), key=lambda row: int(row.get("id", 0)))
    persist.write_json(_SUB, _CAPTURES_FILE, {
        "captures": rows,
        "next_id": _next_id,
        "updated_at": persist.now_iso(),
    })
    _loaded_mtime = _read_file_mtime()


def reset_state() -> None:
    """测试辅助：清空内存台账（不删除已落盘文件）。"""
    global _captures, _next_id, _loaded_mtime
    _captures = {}
    _next_id = 1
    _loaded_mtime = None


# ---------------------------------------------------------------------------
# 写入：采集登记与重送
# ---------------------------------------------------------------------------


def ingest_capture(values: dict[str, Any]) -> dict[str, Any]:
    """登记一次抓取；同一次抓取重送时只追加处理记录。

    返回结构包含 ``duplicate``、``counted`` 以及正式归属行 ``entry``，方便接口直接回包。
    """
    with _LOCK:
        _ensure_loaded()
        prepared = _prepare(values)
        if prepared["missing"]:
            missing = prepared["missing"]
            return {
                "ok": False,
                "duplicate": False,
                "counted": False,
                "missing": missing,
                "message": f"缺少必填字段：{'、'.join(missing)}",
            }

        capture_key = _fingerprint(
            prepared["run_id"], prepared["entry_url"], prepared["landing_url"])
        existing = _captures.get(capture_key)
        if existing is not None:
            return _append_processing(existing, prepared)

        global _next_id
        capture_id = _next_id
        _next_id += 1
        attribution_id = f"ATTR-{capture_id:04d}"
        record: dict[str, Any] = {
            "id": capture_id,
            "capture_id": attribution_id,
            "attribution_id": attribution_id,
            "capture_key": capture_key,
            "run_id": prepared["run_id"],
            "run_identity": prepared["run_id"],
            "entry_site": prepared["entry_site"],
            "entry_url": prepared["entry_url"],
            "landing_site": prepared["landing_site"],
            "landing_url": prepared["landing_url"],
            "site": prepared["source_site"],
            "source_site": prepared["source_site"],
            "attribution_site": prepared["source_site"],
            "primary_site": prepared["source_site"],
            "content_site": prepared["landing_site"],
            "source_sites": _unique([prepared["entry_site"], prepared["source_site"]]),
            "role": prepared["role"],
            "summary": prepared["summary"],
            "content_hash": prepared["content_hash"],
            "crawl_count": 1,
            "processing_count": 0,
            "processing_records": [],
            "first_seen_at": prepared["captured_at"],
            "last_seen_at": prepared["captured_at"],
            "status": "已归属",
            "pending": False,
            "abnormal": False,
        }
        _captures[capture_key] = _with_display_fields(record)
        processing = _record_processing(record, "首次采集", prepared["source_site"],
                                        prepared["summary"], prepared["captured_at"])
        _captures[capture_key] = _with_display_fields(record)
        _save()
        _append_manifest(record)
        return {
            "ok": True,
            "duplicate": False,
            "counted": True,
            "message": (
                f"抓取已归属{prepared['source_site']}，入口{prepared['entry_site']}，"
                f"落地{prepared['landing_site']}，运行{prepared['run_id']}"
            ),
            "capture": _with_display_fields(dict(record)),
            "entry": _with_display_fields(dict(record)),
            "processing_record": processing,
            "unique_captures": _count_run(prepared["run_id"]),
        }


def _prepare(values: dict[str, Any]) -> dict[str, Any]:
    run_id = _pick(values, "run_id")
    entry_url = _pick(values, "entry_url")
    landing_url = _pick(values, "landing_url")
    summary = _pick(values, "summary")
    content_hash = _pick(values, "content_hash")
    entry_site = _pick(values, "entry_site")
    landing_site = _pick(values, "landing_site")
    attribution_site = _pick(values, "attribution_site")
    role_hint = _pick(values, "role")
    captured_at = _pick(values, "captured_at") or persist.now_iso()

    missing: list[str] = []
    if not run_id:
        missing.append("运行身份")
    if not entry_url:
        missing.append("入口地址")
    if not landing_url:
        missing.append("落地地址")
    if not summary and not content_hash:
        missing.append("页面摘要")

    entry_site = entry_site or _site_from_url(entry_url)
    landing_site = landing_site or _site_from_url(landing_url)
    source_site = attribution_site or entry_site
    if role_hint in (ROLE_DIRECT, ROLE_JUMP):
        role = role_hint
    elif entry_url == landing_url or entry_site == landing_site:
        role = ROLE_DIRECT
    else:
        role = ROLE_JUMP
    return {
        "missing": missing,
        "run_id": run_id,
        "entry_url": entry_url,
        "entry_site": entry_site,
        "landing_url": landing_url,
        "landing_site": landing_site,
        "source_site": source_site,
        "role": role,
        "summary": summary,
        "content_hash": content_hash or _content_hash(summary),
        "captured_at": captured_at,
    }


def _append_processing(record: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    processing = _record_processing(
        record, "重送", prepared["source_site"], prepared["summary"], prepared["captured_at"])
    record["last_seen_at"] = prepared["captured_at"]
    # 重送不改变主归属，也不新增真实抓取；只把处理记录和次数往上加。
    record["crawl_count"] = 1
    record = _with_display_fields(record)
    _captures[record["capture_key"]] = record
    _save()
    return {
        "ok": True,
        "duplicate": True,
        "counted": False,
        "message": "同一次抓取已存在，本次只追加处理记录，不重复计算抓取次数",
        "capture": dict(record),
        "entry": dict(record),
        "processing_record": processing,
        "unique_captures": _count_run(str(record.get("run_id", ""))),
    }


def _record_processing(
    record: dict[str, Any],
    action: str,
    site: str,
    summary: str,
    captured_at: str,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = record.setdefault("processing_records", [])
    seq = len(records) + 1
    processing = {
        "seq": seq,
        "action": action,
        "site": site,
        "run_id": record.get("run_id"),
        "capture_id": record.get("capture_id"),
        "attribution_id": record.get("attribution_id"),
        "summary": summary or record.get("summary", ""),
        "timestamp": captured_at,
    }
    records.append(processing)
    record["processing_count"] = seq
    record["last_processing_at"] = captured_at
    persist.append_jsonl(_SUB, _PROCESSING_FILE, processing)
    return processing


def _append_manifest(record: dict[str, Any]) -> None:
    persist.append_jsonl(_SUB, _MANIFEST_FILE, {
        "attribution_id": record.get("attribution_id"),
        "run_id": record.get("run_id"),
        "entry_site": record.get("entry_site"),
        "entry_url": record.get("entry_url"),
        "landing_site": record.get("landing_site"),
        "landing_url": record.get("landing_url"),
        "primary_site": record.get("primary_site"),
        "source_site": record.get("source_site"),
        "content_hash": record.get("content_hash"),
        "timestamp": record.get("first_seen_at"),
    })


def _with_display_fields(record: dict[str, Any]) -> dict[str, Any]:
    """给前端表格补一份中文字段；接口同时保留英文键便于脚本调用。"""
    record.update({
        "归属编号": record.get("attribution_id"),
        "采集归属": record.get("source_site"),
        "归属站点": record.get("source_site"),
        "主站点": record.get("primary_site"),
        "入口站点": record.get("entry_site"),
        "入口地址": record.get("entry_url"),
        "落地站点": record.get("landing_site"),
        "落地地址": record.get("landing_url"),
        "运行身份": record.get("run_id"),
        "采集角色": record.get("role"),
        "页面摘要": record.get("summary"),
        "原摘要": record.get("summary"),
        "内容指纹": record.get("content_hash"),
        "处理次数": record.get("processing_count"),
        "抓取次数": record.get("crawl_count", 1),
        "首次采集时间": record.get("first_seen_at"),
        "最近处理时间": record.get("last_seen_at"),
    })
    return record


def _count_run(run_id: str) -> int:
    return sum(1 for row in _captures.values() if str(row.get("run_id")) == str(run_id))


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# 查询：采集台账、正式清单、入口回查
# ---------------------------------------------------------------------------


def _snapshot_rows() -> list[dict[str, Any]]:
    _ensure_loaded()
    return sorted(_captures.values(), key=lambda row: int(row.get("id", 0)))


def list_captures(
    *,
    run_id: str | None = None,
    site: str | None = None,
    entry_site: str | None = None,
    landing_site: str | None = None,
    content_site: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    """采集台账：每次真实抓取一条，重送不会增加行。``site`` 匹配持有采集归属的站点。"""
    with _LOCK:
        rows = [dict(row) for row in _snapshot_rows()]
    result: list[dict[str, Any]] = []
    for row in rows:
        if run_id and str(row.get("run_id")) != str(run_id):
            continue
        if entry_site and row.get("entry_site") != entry_site:
            continue
        if landing_site and row.get("landing_site") != landing_site:
            continue
        if content_site and row.get("content_site") != content_site:
            continue
        if role and row.get("role") != role:
            continue
        if site and not _visible_to(row, site):
            continue
        result.append(dict(row))
    return result


def list_manifest(
    *,
    run_id: str | None = None,
    site: str | None = None,
    source_site: str | None = None,
    attribution_site: str | None = None,
    entry_site: str | None = None,
    landing_site: str | None = None,
    content_site: str | None = None,
    primary_site: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    """正式站点归属清单：一次抓取一行，只记一个主站点。

    ``site`` / ``primary_site`` 过滤主站点；采集团队按入口核对用 ``entry_site``；
    正文团队按落地站点找内容来源用 ``landing_site`` / ``content_site``。
    """
    with _LOCK:
        rows = [dict(row) for row in _snapshot_rows()]
    wanted_primary = primary_site or site
    wanted_source = attribution_site or source_site
    result: list[dict[str, Any]] = []
    for row in rows:
        if run_id and str(row.get("run_id")) != str(run_id):
            continue
        if wanted_primary and row.get("primary_site") != wanted_primary:
            continue
        if wanted_source and row.get("source_site") != wanted_source:
            continue
        if entry_site and row.get("entry_site") != entry_site:
            continue
        if landing_site and row.get("landing_site") != landing_site:
            continue
        if content_site and row.get("content_site") != content_site:
            continue
        if role and row.get("role") != role:
            continue
        result.append(dict(row))
    return result


def get_capture(attribution_id: int | str) -> dict[str, Any] | None:
    with _LOCK:
        for row in _snapshot_rows():
            if str(row.get("attribution_id")) == str(attribution_id) or str(
                row.get("id")
            ) == str(attribution_id):
                return dict(row)
    return None


def visible_to(row: dict[str, Any], site: str | None) -> bool:
    return _visible_to(row, site)


def _visible_to(row: dict[str, Any], site: str | None) -> bool:
    """站点只看得到自己持有采集归属的明细（入口站点或显式归属站点）。"""
    if not site:
        return True
    return site in set(row.get("source_sites") or []) or site in {
        row.get("source_site"), row.get("entry_site"), row.get("attribution_site"),
        row.get("primary_site"),
    }


def lookup_entry(
    *,
    attribution_id: int | str | None = None,
    entry_url: str | None = None,
    run_id: str | None = None,
    site: str | None = None,
) -> dict[str, Any]:
    """入口回查：从一条带站点标识的采集归属追回入口、落地地址和原摘要。

    指定 ``site`` 时做站点隔离：该站点必须持有这条抓取的采集归属，否则视为无可见记录。
    """
    row: dict[str, Any] | None = None
    with _LOCK:
        rows = _snapshot_rows()
        if attribution_id is not None:
            row = get_capture(attribution_id)
        if row is None and entry_url:
            candidates = [
                candidate for candidate in rows
                if candidate.get("entry_url") == entry_url
                and (not run_id or str(candidate.get("run_id")) == str(run_id))
            ]
            row = dict(candidates[-1]) if candidates else None
        if row is None and run_id and not entry_url:
            candidates = [
                candidate for candidate in rows
                if str(candidate.get("run_id")) == str(run_id) and _visible_to(candidate, site)
            ]
            row = dict(candidates[0]) if len(candidates) == 1 else None

    if row is None:
        return {
            "ok": False,
            "found": False,
            "visible": False,
            "message": "未找到对应的采集归属",
        }
    if site and not _visible_to(row, site):
        return {
            "ok": False,
            "found": True,
            "visible": False,
            "message": f"站点{site}对该次抓取没有采集归属，无可见明细",
        }
    return {
        "ok": True,
        "found": True,
        "visible": True,
        "attribution_id": row.get("attribution_id"),
        "capture_id": row.get("capture_id"),
        "run_id": row.get("run_id"),
        "site": row.get("source_site"),
        "attribution_site": row.get("source_site"),
        "primary_site": row.get("primary_site"),
        "entry_site": row.get("entry_site"),
        "entry_url": row.get("entry_url"),
        "landing_site": row.get("landing_site"),
        "landing_url": row.get("landing_url"),
        "summary": row.get("summary"),
        "original_summary": row.get("summary"),
        "content_hash": row.get("content_hash"),
        "role": row.get("role"),
        "processing_count": row.get("processing_count"),
        "entry": row,
        "message": "已按采集归属回查到入口、落地地址与原摘要",
    }


def run_captures(run_identities: Iterable[str]) -> list[dict[str, Any]]:
    wanted = {str(item) for item in run_identities if str(item).strip()}
    with _LOCK:
        rows = [dict(row) for row in _snapshot_rows()]
    return [row for row in rows if str(row.get("run_id")) in wanted]


def mark_status(attribution_id: int | str, status: str) -> dict[str, Any] | None:
    with _LOCK:
        row = get_capture(attribution_id)
        if row is None:
            return None
        row["status"] = status
        row["pending"] = status not in {"已归档"}
        _captures[row["capture_key"]] = _with_display_fields(row)
        _save()
        return dict(_captures[row["capture_key"]])


def stats() -> dict[str, Any]:
    with _LOCK:
        rows = [dict(row) for row in _snapshot_rows()]
    return {
        "captures": len(rows),
        "runs": len({row.get("run_id") for row in rows}),
        "sites": sorted({str(row.get("source_site")) for row in rows}),
        "primary_sites": sorted({str(row.get("primary_site")) for row in rows}),
        "landing_sites": sorted({str(row.get("landing_site")) for row in rows}),
        "processing_records": sum(int(row.get("processing_count", 0)) for row in rows),
    }
