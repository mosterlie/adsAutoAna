#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计从赛狐(sellfox.com)一共要爬多少个 URL

分两个口径:
  1) **独立端点**  —— 去重后的接口地址个数(URL 种类)
  2) **实际请求数** —— 一次「全量采集」真正发出的 HTTP 请求数(含分页/汇总/多维度)

做法: 真实发起请求, 但把落库全部改成 no-op —— 只计数, 不写库。
（跑完不会改动 data/ads.db 里的任何数据）

用法:
    python3 count_sellfox_urls.py            # 广告管理 + 在线产品
    python3 count_sellfox_urls.py --ads      # 只统计广告管理
    python3 count_sellfox_urls.py --online   # 只统计在线产品
"""
import argparse
import collections
import sys
from typing import Any, Dict, List

import requests

sys.path.insert(0, ".")

import config                                        # noqa: E402
from backend import database as db                   # noqa: E402
from backend import online_product as op             # noqa: E402
from backend.cookies import load_cookies             # noqa: E402
from backend.sellfox_client import TAB_DEFS, TAB_ORDER, SellfoxClient   # noqa: E402

HITS: List[Dict[str, Any]] = []          # 逻辑调用 (含分页/汇总/维度)
RAW: List[str] = []                      # 真实 HTTP 请求 (含失败重试)


def _record(group: str, url: str, kind: str) -> None:
    HITS.append({"group": group, "url": url, "kind": kind})


# 真实 HTTP 层计数: 失败重试也会被记到, 得到「实际发出多少个请求」
_orig_session_post = requests.Session.post


def _counting_session_post(self, url, *a, **kw):
    if "sellfox.com" in str(url):
        RAW.append(str(url))
    return _orig_session_post(self, url, *a, **kw)


# ---------------------------------------------------------------------------
# 广告管理: 拦 SellfoxClient._post
# ---------------------------------------------------------------------------
_orig_post = SellfoxClient._post


def _counting_post(self, api: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = config.ORIGIN + config.GW_PREFIX + api
    # 汇总接口不带 pageNo/pageSize, 用这个区分「列表页」与「汇总」请求
    kind = "列表(分页)" if "pageNo" in payload else "汇总"
    _record("广告管理", url, kind)
    return _orig_post(self, api, payload)


def count_ads() -> None:
    cookies = load_cookies()
    if not cookies:
        raise SystemExit("未找到登录 Cookie, 先点「同步登录态」")
    client = SellfoxClient(cookies)

    # 1) 店铺 + 广告组合
    sp = client.get_shops()
    rows = sp.get("rows") or []
    shop_ids = sorted({r["shopId"] for r in rows if r.get("shopId")}) or [None]
    start = config.DEFAULT_START_DATE
    from backend.crawler import today_str
    end = today_str()

    total_rows = 0
    for tab in TAB_ORDER:
        for sid in shop_ids:
            for scope in TAB_DEFS[tab]["scopes"]:
                try:
                    recs = client.fetch_list(tab, [sid], start, end, scope)
                    total_rows += len(recs)
                except Exception as e:      # noqa: BLE001
                    print(f"  ⚠ {tab} {scope}: {str(e)[:80]}")
                try:
                    client.fetch_aggregate(tab, [sid], start, end, scope)
                except Exception:           # noqa: BLE001
                    pass
    print(f"  (本次实际取到 {total_rows} 条记录)")


# ---------------------------------------------------------------------------
# 在线产品: 拦 online_product._post
# ---------------------------------------------------------------------------
_orig_op_post = op._post


def _counting_op_post(sess, url, payload, json_body=False, retry=3):
    kind = "汇总" if str(payload.get("defaultPageType")) == "2" else "列表(分页)"
    if url.endswith("getHeadField.json"):
        kind = "字段目录"
    elif url.endswith("getAllShopSite.json"):
        kind = "店铺"
    _record("在线产品", url, kind)
    return _orig_op_post(sess, url, payload, json_body=json_body, retry=retry)


def count_online() -> None:
    sess = op.make_session()
    op.fetch_head_fields(sess)
    op.fetch_shops(sess)
    for pt in op.PAGE_TYPES:
        op.fetch_all(sess, page_type=pt)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def report() -> None:
    for group in ("广告管理", "在线产品"):
        hits = [h for h in HITS if h["group"] == group]
        if not hits:
            continue
        urls = collections.Counter(h["url"] for h in hits)
        print(f"\n===== {group} =====")
        print(f"独立端点 {len(urls)} 个 / 实际请求 {len(hits)} 次")
        print(f"{'请求数':>6}  {'端点':<10} URL")
        for url, n in urls.most_common():
            kinds = {h["kind"] for h in hits if h["url"] == url}
            print(f"{n:>6}  {'/'.join(sorted(kinds)):<10} {url}")

    all_urls = {h["url"] for h in HITS}
    raw_urls = set(RAW)
    print("\n" + "=" * 74)
    print(f"总计: 独立端点 {len(all_urls)} 个")
    print(f"      逻辑调用 {len(HITS)} 次 (一次完整采集的接口调用次数)")
    print(f"      真实 HTTP 请求 {len(RAW)} 次 (含失败重试与分页)")
    print(f"      实际命中过的 URL {len(raw_urls)} 个")
    by_group = collections.Counter(h["group"] for h in HITS)
    for g, n in by_group.items():
        print(f"        {g}: {n} 次调用")
    retry_extra = len(RAW) - len(HITS)
    if retry_extra:
        print(f"      (其中失败重试多发出 {retry_extra} 次请求)")
    print("=" * 74)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ads", action="store_true", help="只统计广告管理")
    ap.add_argument("--online", action="store_true", help="只统计在线产品")
    a = ap.parse_args()
    only = a.ads or a.online

    # 落库全部 no-op —— 只计数, 不写库
    for name in ("save_shops", "save_portfolios", "upsert_records", "save_aggregate",
                 "create_run", "finish_run", "save_online_meta", "save_online_products"):
        setattr(db, name, lambda *args, **kw: 0)

    SellfoxClient._post = _counting_post
    op._post = _counting_op_post
    requests.Session.post = _counting_session_post

    if not a.online:
        print("▶ 正在实测「广告管理」...")
        count_ads()
    if not a.ads:
        print("▶ 正在实测「在线产品」...")
        count_online()
    report()
    _ = only


if __name__ == "__main__":
    main()
