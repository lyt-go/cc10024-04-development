"""站点归属清单集成测试：覆盖跳转采集归属、重送去重、运行确认去重与站点隔离回查。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import persistence as persist

# 使用独立数据目录，避免污染开发环境里已有的 data/。
_TMP_ROOT = tempfile.mkdtemp(prefix="site_attribution_test_")
persist._DATA_ROOT = _TMP_ROOT

from app.services import capture_ledger, dedup_groups  # noqa: E402
from app.services.page_snapshot import PageSnapshotService  # noqa: E402
from app.services.run_history import RunHistoryService  # noqa: E402

RUN_ID = "RUN-ATTR-001"
OTHER_RUN = "RUN-ATTR-002"
JIA = "jia.example.com"
YI = "yi.example.com"
BING = "bing.example.com"
JIA_ENTRY = "https://jia.example.com/entry/1"
YI_ENTRY = "https://yi.example.com/entry/9"
DETAIL = "https://bing.example.com/detail/42"
SUMMARY = "丙站详情正文：同一篇共享正文"


def _fresh() -> None:
    capture_ledger.reset_state()
    dedup_groups.reset_state()
    for sub in ("attributions", "dedup", "snapshots", "reports", "runs"):
        path = os.path.join(_TMP_ROOT, sub)
        if os.path.isdir(path):
            for name in os.listdir(path):
                os.remove(os.path.join(path, name))


def _ingest(run_id: str, entry_url: str, landing_url: str, summary: str = SUMMARY) -> dict:
    return capture_ledger.ingest_capture({
        "run_id": run_id,
        "entry_url": entry_url,
        "landing_url": landing_url,
        "summary": summary,
    })


def test_three_captures_one_group_two_duplicates():
    _fresh()
    first = _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    second = _ingest(RUN_ID, YI_ENTRY, DETAIL)
    third = _ingest(RUN_ID, DETAIL, DETAIL)
    assert all(item["counted"] for item in (first, second, third))
    assert len(capture_ledger.list_captures(run_id=RUN_ID)) == 3

    result = dedup_groups.group_captures(RUN_ID)
    assert result["captures"] == 3
    assert result["groups"] == 1
    assert result["duplicates"] == 2

    group = dedup_groups.get_group(result["group_ids"][0])
    member_sites = [member["source_site"] for member in group["members"]]
    assert member_sites == [JIA, YI, BING]
    # 每条抓取保留自己的入口和落地地址，共享正文不覆盖来源。
    assert group["members"][0]["entry_url"] == JIA_ENTRY
    assert group["members"][1]["entry_url"] == YI_ENTRY
    assert all(member["landing_url"] == DETAIL for member in group["members"])
    assert all(member["summary"] == SUMMARY for member in group["members"])
    # 代表页取落地站点自己的直接采集，供复采执行。
    assert group["representative_source_site"] == BING
    assert group["representative"]["landing_url"] == DETAIL


def test_resend_only_appends_processing():
    _fresh()
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    first_resend = _ingest(RUN_ID, YI_ENTRY, DETAIL)
    second_resend = _ingest(RUN_ID, YI_ENTRY, DETAIL)
    assert first_resend["duplicate"] and not first_resend["counted"]
    assert second_resend["duplicate"] and not second_resend["counted"]
    assert second_resend["unique_captures"] == 1

    captures = capture_ledger.list_captures(run_id=RUN_ID)
    assert len(captures) == 1
    assert captures[0]["crawl_count"] == 1
    assert captures[0]["processing_count"] == 3
    assert [record["action"] for record in captures[0]["processing_records"]] == [
        "首次采集", "重送", "重送"]


def test_manifest_single_primary_site_per_capture():
    _fresh()
    _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    _ingest(RUN_ID, DETAIL, DETAIL)
    manifest = capture_ledger.list_manifest(run_id=RUN_ID)
    assert len(manifest) == 3
    assert all(row["primary_site"] for row in manifest)
    assert len({row["attribution_id"] for row in manifest}) == 3
    # 按入口核对覆盖：两条跳转采集各自归到入口站点。
    assert {row["source_site"] for row in capture_ledger.list_manifest(
        run_id=RUN_ID, entry_site=YI)} == {YI}
    # 按落地站点找内容来源：三条抓取都落在丙站。
    assert len(capture_ledger.list_manifest(run_id=RUN_ID, landing_site=BING)) == 3


def test_entry_lookup_is_site_scoped():
    _fresh()
    _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    yi_row = capture_ledger.list_manifest(run_id=RUN_ID, entry_site=YI)[0]

    located = capture_ledger.lookup_entry(
        attribution_id=yi_row["attribution_id"], site=YI)
    assert located["ok"] and located["visible"]
    assert located["entry_url"] == YI_ENTRY
    assert located["landing_url"] == DETAIL
    assert located["summary"] == SUMMARY

    denied = capture_ledger.lookup_entry(
        attribution_id=yi_row["attribution_id"], site=JIA)
    assert not denied["ok"] and not denied["visible"]


def test_group_detail_only_shows_own_attribution():
    _fresh()
    _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    _ingest(RUN_ID, DETAIL, DETAIL)
    group_id = dedup_groups.group_captures(RUN_ID)["group_ids"][0]

    yi_view = dedup_groups.get_group(group_id, site=YI)
    assert [member["source_site"] for member in yi_view["members"]] == [YI]
    assert yi_view["total_members"] == 3

    jia_recrawl = dedup_groups.recrawl_target(group_id, site=JIA)
    assert jia_recrawl["ok"]
    assert jia_recrawl["entry_url"] == JIA_ENTRY
    assert jia_recrawl["landing_url"] == DETAIL
    assert jia_recrawl["summary"] == SUMMARY


def test_run_confirm_writes_groups_and_representatives():
    _fresh()
    _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    _ingest(RUN_ID, DETAIL, DETAIL)

    svc = RunHistoryService()
    entry, missing = svc.create_entry({
        "记录编号": RUN_ID,
        "关联计划": "PLAN-ATTR-1",
        "开始时间": "2026-11-22",
    })
    assert entry is not None, missing
    confirmed, _ = svc.run_action(entry["id"], "确认完成")
    assert confirmed["status"] == "已完成"

    result = svc.dedup_result(entry["id"])
    assert result["ok"]
    assert len(result["groups"]) == 1
    assert len(result["representatives"]) == 1
    representative_record = result["representatives"][0]
    assert representative_record["landing_url"] == DETAIL
    assert representative_record["summary"] == SUMMARY

    reports = persist.read_jsonl("reports", "dedup_report.jsonl")
    triggered = [row for row in reports if row.get("run_id") == RUN_ID]
    assert triggered and triggered[-1]["trigger"] == "run_history_confirm"
    assert triggered[-1]["groups"] == 1

    group_logs = persist.read_jsonl("dedup", "groups.jsonl")
    assert group_logs and group_logs[-1]["member_count"] == 3
    reps_logs = persist.read_jsonl("dedup", "representatives.jsonl")
    assert reps_logs and reps_logs[-1]["landing_url"] == DETAIL


def test_other_run_pages_not_counted():
    _fresh()
    _ingest(RUN_ID, JIA_ENTRY, DETAIL)
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    _ingest(RUN_ID, DETAIL, DETAIL)
    _ingest(OTHER_RUN, DETAIL, DETAIL)  # 另一个运行抓到相同页面

    result = dedup_groups.group_captures(RUN_ID)
    assert result["captures"] == 3
    assert result["groups"] == 1
    group = dedup_groups.get_group(result["group_ids"][0])
    assert group["member_count"] == 3
    assert len(capture_ledger.list_captures(run_id=OTHER_RUN)) == 1


def test_snapshot_persists_entry_landing_and_run_identity():
    _fresh()
    svc = PageSnapshotService()
    snapshot = svc.capture_snapshot(
        DETAIL,
        "domhash001",
        "<html>丙站详情正文：同一篇共享正文</html>",
        entry_url=YI_ENTRY,
        run_id=RUN_ID,
    )
    assert snapshot["entry_url"] == YI_ENTRY
    assert snapshot["landing_url"] == DETAIL
    assert snapshot["run_id"] == RUN_ID
    assert snapshot["attribution_id"]

    attribution = svc.snapshot_attribution("domhash001")
    assert attribution is not None
    assert attribution["entry_site"] == YI
    assert attribution["landing_site"] == BING
    assert attribution["run_id"] == RUN_ID

    # 同一快照重送只追加处理记录，不多算一次。
    again = svc.capture_snapshot(
        DETAIL, "domhash001", "<html>丙站详情正文：同一篇共享正文</html>",
        entry_url=YI_ENTRY, run_id=RUN_ID,
    )
    assert again.get("duplicate") is True
    assert len(capture_ledger.list_captures(run_id=RUN_ID)) == 1


def test_ledger_survives_reload():
    _fresh()
    _ingest(RUN_ID, YI_ENTRY, DETAIL)
    capture_ledger.reset_state()
    rows = capture_ledger.list_manifest(run_id=RUN_ID)
    assert len(rows) == 1
    assert rows[0]["entry_url"] == YI_ENTRY
    assert rows[0]["landing_url"] == DETAIL


if __name__ == "__main__":
    tests = [
        test_three_captures_one_group_two_duplicates,
        test_resend_only_appends_processing,
        test_manifest_single_primary_site_per_capture,
        test_entry_lookup_is_site_scoped,
        test_group_detail_only_shows_own_attribution,
        test_run_confirm_writes_groups_and_representatives,
        test_other_run_pages_not_counted,
        test_snapshot_persists_entry_landing_and_run_identity,
        test_ledger_survives_reload,
    ]
    try:
        for test in tests:
            test()
            print(f"PASS: {test.__name__}")
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    print(f"\nALL {len(tests)} SITE ATTRIBUTION TESTS PASSED")
