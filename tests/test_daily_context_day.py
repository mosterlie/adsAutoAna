# -*- coding: utf-8 -*-
from backend import daily_probe_api as daily
from backend import ads_store


def test_context_uses_latest_requested_day(tmp_path, monkeypatch):
    calls = {"context": [], "collected": []}

    monkeypatch.setattr(daily, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(daily, "_remaining_days",
                        lambda cid, days, skip: (["2026-11-03"],
                                                 ["2026-11-01", "2026-11-02",
                                                  "2026-11-04", "2026-11-05",
                                                  "2026-11-06", "2026-11-07"]))
    monkeypatch.setattr(daily, "collect_one_day",
                        lambda cid, day, opts: (
                            calls["collected"].append(day) or
                            {"date": day, "campaign": {"sum": {}, "hours": []},
                             "groups": [], "products": [], "search": {},
                             "errors": []}))
    monkeypatch.setattr(daily, "list_products", lambda cid, day: [])
    monkeypatch.setattr(daily, "list_targets", lambda cid, day: [])
    monkeypatch.setattr(daily, "product_basic_info", lambda asins: {})
    monkeypatch.setattr(daily, "suggested_bids",
                        lambda cid, day, targets: (
                            calls["context"].append(day) or {}))
    monkeypatch.setattr(ads_store, "write_day", lambda data, cid: {})

    tid = "test_context"
    daily.TASKS[tid] = {
        "taskId": tid, "status": "pending", "progress": 0,
        "doneDays": 0, "totalDays": 0, "current": "排队中",
        "errors": [], "createdAt": "",
        "campaignId": "c1", "campaignName": "test",
        "startDate": "2026-11-01", "endDate": "2026-11-07",
    }
    req = daily.StartReq(
        campaignId="c1", campaignName="test",
        startDate="2026-11-01", endDate="2026-11-07",
        skipExisting=True, competitor=False,
        productInfo=True, suggestedBid=True,
    )
    daily._run_task(tid, req)

    # 只采剩余日期 11-03，但上下文必须取请求区间最后一天 11-07
    assert calls["collected"] == ["2026-11-03"]
    assert calls["context"] == ["2026-11-07"]
    assert daily.TASKS[tid]["status"] == "done"
