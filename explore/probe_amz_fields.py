# -*- coding: utf-8 -*-
"""探针: 用新的 EXTRACT_JS 实时抓一次搜索结果, 打印每张卡抽到的字段覆盖情况。

不落库(直接调 _live_fetch)。用法:
    python3 explore/probe_amz_fields.py "金網フェンス" [滚动次数]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import amazon as az          # noqa: E402

FIELDS = ["brand", "differentiators", "price", "price_value", "list_price", "rating",
          "rating_value", "reviews", "review_count", "points", "coupon", "discount",
          "delivery", "delivery_date", "delivery_fee", "free_shipping",
          "delivery_secondary", "stock", "bought_recently", "other_offers",
          "return_policy", "badges", "extras", "add_to_cart", "has_video",
          "image_big", "raw_text"]


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "金網フェンス"
    scrolls = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    url = az.search_url(query, "co.jp")
    print("URL:", url)
    az.acquire_lock()
    try:
        az.pace(0)
        data = az._live_fetch(query, url, az.config.CDP_URL, scrolls, False, 45000)
    finally:
        az.release_lock()

    items = data.get("items") or []
    print(f"\nvia={data.get('via')} 卡片数={len(items)} blocked={data.get('blocked')}")
    if not items:
        print("没抓到结果"); return

    print("\n=== 字段覆盖率 ===")
    for f in FIELDS:
        n = sum(1 for it in items if it.get(f) not in (None, "", [], 0, False))
        print(f"  {f:20s} {n:>3}/{len(items)}")

    print("\n=== 前 3 张卡全部抽到的内容 ===")
    for it in items[:3]:
        print("\n" + "-" * 72)
        for k, v in it.items():
            if k in ("raw_text",):
                v = (v or "")[:160] + "…"
            print(f"  {k:20s} = {v!r}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amz_fields_sample.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(f"\n完整样本 -> {out}")

    print("\n=== extras 抽取示例(第 4~8 张卡) ===")
    for it in items[3:8]:
        print(f"\n  #{it['position']} {it['asin']}")
        for e in (it.get("extras") or [])[:8]:
            print(f"     [{e.get('tag')}] {e.get('text')[:100]}")


if __name__ == "__main__":
    main()
