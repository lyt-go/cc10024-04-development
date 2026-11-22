"""按站点归属清单执行页面去重，产出正式去重分组与代表页采集来源记录。

去重只在「同一次运行」的采集台账范围内进行：另一次运行抓到的相同页面不会混进本次
分组。分组键是正文指纹（``content_hash``），因此甲站入口跳转到丙站详情页、乙站入口
跳转到同一详情页、丙站直接采集这三条真实抓取会落进同一组；组内每个成员都保留自己的
入口站点、入口地址、落地站点、落地地址和原摘要，重复正文不会抹掉真实采集来源。

每组选出一个代表页，并单独写一条「代表页采集来源记录」供复采执行。代表页优先选
直接采集的成员（正文由落地站点自己产出，回放落地地址即可复采），没有直接采集时
取该组最早的抓取。站点查询分组或发起复采时，只能看到自己持有采集归属的成员。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any, Iterable

from app import persistence as persist
from app.services import capture_ledger

_SUB = "dedup"
_GROUPS_FILE = "groups.json"
_GROUPS_LOG = "groups.jsonl"
_REPRESENTATIVES_LOG = "representatives.jsonl"

_LOCK = threading.RLock()
_groups: dict[str, dict[str, Any]] = {}
_next_id = 1
_loaded_mtime: float | None = None


def _groups_path() -> str:
    return persist.data_path(_SUB, _GROUPS_FILE)


def _read_file_mtime() -> float | None:
    import os

    try:
        return os.path.getmtime(_groups_path())
    except OSError:
        return None


def _ensure_loaded() -> None:
    global _groups, _next_id, _loaded_mtime
    mtime = _read_file_mtime()
    if mtime is None:
        _groups = {}
        _next_id = 1
        _loaded_mtime = None
        return
    if _loaded_mtime is not None and mtime <= _loaded_mtime:
        return
    data = persist.read_json(_SUB, _GROUPS_FILE) or {}
    rows = data.get("groups", []) if isinstance(data, dict) else []
    _groups = {
        row.get("group_key") or _group_key(str(row.get("run_id", "")), str(row.get("content_hash", ""))): row
        for row in rows
        if isinstance(row, dict)
    }
    known_next = int(data.get("next_id", 1)) if isinstance(data, dict) else 1
    _next_id = max([known_next] + [int(row.get("seq", 0)) + 1 for row in _groups.values()])
    _loaded_mtime = mtime


def _save() -> None:
    global _loaded_mtime
    rows = sorted(_groups.values(), key=lambda item: int(item.get("seq", 0)))
    persist.write_json(_SUB, _GROUPS_FILE, {
        "groups": rows,
        "next_id": _next_id,
        "updated_at": persist.now_iso(),
    })
    _loaded_mtime = _read_file_mtime()


def reset_state() -> None:
    """测试辅助：清空内存分组（不删除已落盘文件）。"""
    global _groups, _next_id, _loaded_mtime
    _groups = {}
    _next_id = 1
    _loaded_mtime = None


def _group_key(run_id: str, content_hash: str) -> str:
    return hashlib.sha1(f"{run_id}|{content_hash}".encode("utf-8")).hexdigest()[:16]


def _member(row: dict[str, Any]) -> dict[str, Any]:
    """组内成员：保留这次真实抓取的全部来源信息。"""
    return {
        "id": row.get("id"),
        "attribution_id": row.get("attribution_id"),
        "capture_id": row.get("capture_id"),
        "capture_key": row.get("capture_key"),
        "run_id": row.get("run_id"),
        "source_site": row.get("source_site"),
        "attribution_site": row.get("attribution_site"),
        "primary_site": row.get("primary_site"),
        "entry_site": row.get("entry_site"),
        "entry_url": row.get("entry_url"),
        "landing_site": row.get("landing_site"),
        "landing_url": row.get("landing_url"),
        "summary": row.get("summary"),
        "content_hash": row.get("content_hash"),
        "role": row.get("role"),
        "processing_count": row.get("processing_count", 0),
    }


def _pick_representative(members: list[dict[str, Any]]) -> dict[str, Any]:
    """代表页：优先直接采集，其次最早真实抓取，保证同一输入得到同一结果。"""
    direct = [item for item in members if item.get("role") == capture_ledger.ROLE_DIRECT]
    pool = direct or members
    return sorted(pool, key=_capture_order)[0] if pool else members[0]


def _capture_order(member: dict[str, Any]) -> tuple[int, str]:
    return int(member.get("id") or 0), str(member.get("attribution_id") or "")


def group_captures(
    run_id: str,
    *,
    trigger: str = "manual",
    rule_id: int | None = None,
) -> dict[str, Any]:
    """对一次运行的全部真实抓取做正文去重，落盘分组数据与代表页来源记录。"""
    captures = capture_ledger.list_captures(run_id=run_id)
    with _LOCK:
        _ensure_loaded()
        global _next_id
        buckets: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for row in captures:
            key = str(row.get("content_hash") or "")
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(row)

        produced: list[dict[str, Any]] = []
        touched_keys: set[str] = set()
        for content_hash in order:
            rows = sorted(buckets[content_hash], key=lambda item: int(item.get("id", 0)))
            group_key = _group_key(run_id, content_hash)
            touched_keys.add(group_key)
            existing = _groups.get(group_key)
            if existing is None:
                seq = _next_id
                _next_id += 1
                group_id = f"DEDUP-{seq:04d}"
            else:
                seq = int(existing.get("seq", 0))
                group_id = str(existing.get("group_id"))

            members = [_member(row) for row in rows]
            representative = _pick_representative(members)
            group = {
                "group_key": group_key,
                "group_id": group_id,
                "seq": seq,
                "run_id": run_id,
                "content_hash": content_hash,
                "rule_id": rule_id,
                "member_count": len(members),
                "members": members,
                "representative": representative,
                "representative_attribution_id": representative.get("attribution_id"),
                "representative_source_site": representative.get("source_site"),
                "source_site": representative.get("source_site"),
                "entry_sites": _unique(item.get("entry_site", "") for item in members),
                "attribution_sites": _unique(item.get("source_site", "") for item in members),
                "landing_sites": _unique(item.get("landing_site", "") for item in members),
                "status": "已分组",
                "trigger": trigger,
                "updated_at": persist.now_iso(),
            }
            group["created_at"] = (existing or {}).get("created_at", group["updated_at"])
            _groups[group_key] = group
            produced.append(group)
            persist.append_jsonl(_SUB, _GROUPS_LOG, _group_log_record(group))
            persist.append_jsonl(_SUB, _REPRESENTATIVES_LOG, _representative_record(group))

        # 同一运行里已经不再出现的正文（例如台账被清理）不再保留分组。
        stale = [
            key for key, group in _groups.items()
            if group.get("run_id") == run_id and key not in touched_keys
        ]
        for key in stale:
            _groups.pop(key, None)

        _save()

    return {
        "run_id": run_id,
        "trigger": trigger,
        "rule_id": rule_id,
        "captures": len(captures),
        "groups": len(produced),
        "unique_pages": len(produced),
        "duplicates": max(len(captures) - len(produced), 0),
        "group_ids": [group["group_id"] for group in produced],
        "representatives": [
            {
                "group_id": group["group_id"],
                "attribution_id": group["representative_attribution_id"],
                "source_site": group["representative_source_site"],
            }
            for group in produced
        ],
        "timestamp": persist.now_iso(),
    }


def _group_log_record(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "trigger": group.get("trigger"),
        "run_id": group.get("run_id"),
        "group_id": group.get("group_id"),
        "content_hash": group.get("content_hash"),
        "member_count": group.get("member_count"),
        "members": [
            {
                "attribution_id": item.get("attribution_id"),
                "source_site": item.get("source_site"),
                "entry_url": item.get("entry_url"),
                "landing_url": item.get("landing_url"),
            }
            for item in group.get("members", [])
        ],
        "representative_attribution_id": group.get("representative_attribution_id"),
        "representative_source_site": group.get("representative_source_site"),
        "timestamp": persist.now_iso(),
    }


def _representative_record(group: dict[str, Any]) -> dict[str, Any]:
    """代表页的采集来源记录：复采执行直接消费这一条。"""
    representative = group.get("representative") or {}
    return {
        "group_id": group.get("group_id"),
        "run_id": group.get("run_id"),
        "content_hash": group.get("content_hash"),
        "attribution_id": representative.get("attribution_id"),
        "source_site": representative.get("source_site"),
        "primary_site": representative.get("primary_site"),
        "entry_site": representative.get("entry_site"),
        "entry_url": representative.get("entry_url"),
        "landing_site": representative.get("landing_site"),
        "landing_url": representative.get("landing_url"),
        "summary": representative.get("summary"),
        "role": representative.get("role"),
        "member_count": group.get("member_count"),
        "timestamp": persist.now_iso(),
    }


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# 查询与复采
# ---------------------------------------------------------------------------


def _visible_group(group: dict[str, Any], site: str | None) -> dict[str, Any]:
    """按站点裁剪组内明细；共享正文时站点只看得到自己有采集归属的成员。"""
    data = dict(group)
    members = [dict(item) for item in group.get("members", [])]
    if site:
        members = [
            item for item in members
            if capture_ledger.visible_to(item, site)
        ]
    data["members"] = members
    data["visible_members"] = len(members)
    data["total_members"] = int(group.get("member_count", len(group.get("members", []))))
    if site:
        data["site"] = site
        data["visible"] = bool(members)
    return data


def list_groups(
    *,
    run_id: str | None = None,
    site: str | None = None,
    group_id: str | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        _ensure_loaded()
        rows = sorted(_groups.values(), key=lambda item: int(item.get("seq", 0)))
    result: list[dict[str, Any]] = []
    for group in rows:
        if run_id and str(group.get("run_id")) != str(run_id):
            continue
        if group_id and str(group.get("group_id")) != str(group_id):
            continue
        data = _visible_group(group, site)
        if site and not data["visible"]:
            continue
        result.append(data)
    return result


def get_group(group_id: str, *, site: str | None = None) -> dict[str, Any] | None:
    groups = list_groups(group_id=group_id, site=site)
    return groups[0] if groups else None


def recrawl_target(group_id: str, *, site: str | None = None) -> dict[str, Any]:
    """复采执行：给出可复采的内容来源记录。

    有站点时优先返回该站点自己的成员（保留它原始入口与落地地址），否则用代表页；
    站点对整组没有采集归属时拒绝返回明细。
    """
    group = get_group(group_id)
    if group is None:
        return {"ok": False, "message": f"去重分组 {group_id} 不存在"}
    members = group.get("members", [])
    own = [item for item in members if site and capture_ledger.visible_to(item, site)]
    if site and not own:
        return {
            "ok": False,
            "group_id": group_id,
            "message": f"站点{site}在该分组没有采集归属，无可见明细",
        }
    target = own[0] if own else group.get("representative")
    return {
        "ok": True,
        "group_id": group_id,
        "run_id": group.get("run_id"),
        "content_hash": group.get("content_hash"),
        "member_count": group.get("total_members"),
        "attribution_id": target.get("attribution_id"),
        "capture_id": target.get("capture_id"),
        "site": target.get("source_site"),
        "source_site": target.get("source_site"),
        "entry_site": target.get("entry_site"),
        "entry_url": target.get("entry_url"),
        "landing_site": target.get("landing_site"),
        "landing_url": target.get("landing_url"),
        "summary": target.get("summary"),
        "original_summary": target.get("summary"),
        "role": target.get("role"),
        "representative": group.get("representative"),
        "members": members,
        "message": "已生成复采来源记录",
    }


def representatives(*, run_id: str | None = None, site: str | None = None) -> list[dict[str, Any]]:
    """代表页的采集来源记录清单，供复采执行按运行或站点取用。"""
    result: list[dict[str, Any]] = []
    for group in list_groups(run_id=run_id):
        representative = group.get("representative") or {}
        if site and not capture_ledger.visible_to(representative, site):
            # 代表页不属于该站点时，改为交付该站点自己的成员作为复采来源。
            own = [item for item in group.get("members", []) if capture_ledger.visible_to(item, site)]
            if not own:
                continue
            representative = own[0]
        result.append({
            "group_id": group.get("group_id"),
            "run_id": group.get("run_id"),
            "content_hash": group.get("content_hash"),
            "attribution_id": representative.get("attribution_id"),
            "source_site": representative.get("source_site"),
            "entry_site": representative.get("entry_site"),
            "entry_url": representative.get("entry_url"),
            "landing_site": representative.get("landing_site"),
            "landing_url": representative.get("landing_url"),
            "summary": representative.get("summary"),
            "role": representative.get("role"),
            "is_representative": representative is group.get("representative"),
        })
    return result


def stats() -> dict[str, Any]:
    with _LOCK:
        _ensure_loaded()
        rows = list(_groups.values())
    return {
        "groups": len(rows),
        "runs": len({row.get("run_id") for row in rows}),
        "members": sum(int(row.get("member_count", 0)) for row in rows),
    }
