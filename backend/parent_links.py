# -*- coding: utf-8 -*-
"""父 ASIN 来源链接: 导入店小秘导出的 parents_*.csv

CSV 关键列(中文表头, utf-8-sig):
    父SKU / 父ASIN / 父标题 / 来源 / 来源链接1 / 来源链接2 / 父产品ID / 编辑链接

  · 来源链接1 通常是亚马逊商品链接 → parent_links.amazon_url
  · 来源链接2 通常是采购源链接(1688 / 拼多多 …) → parent_links.purchase_url
  · 店小秘导出并不保证顺序(实测有整反的行), 因此按域名判定:
    含 amazon. 的归 amazon_url, 其余归 purchase_url, 不看列位置。

落库规则:
  以「父ASIN」为键, 一个父 ASIN 一行, 重复导入覆盖更新(不删历史其它父 ASIN);
  只保留父 ASIN 非空的行; 同时记录来源 CSV 文件名, 便于追溯。
  data/parent_links_manual.json 里手补的链接优先于 CSV, 重新导入不会被冲掉。

关联核对:
  导入后与库内已有 ASIN 集合(在线产品 + 广告产品 + 父子映射)比对,
  给出「库里找不到的父 ASIN」清单 —— 这些通常是还没采集在线产品的新品。
"""
import csv
import glob
import json
import os
import re
from typing import Any, Dict, List

import config
from backend import database as db


def data_dir() -> str:
    return config.DATA_DIR


def latest_csv() -> str:
    """data/ 下最新的 parents_*.csv (按文件名排序, 文件名带时间戳)"""
    files = sorted(glob.glob(os.path.join(data_dir(), "parents_*.csv")))
    return files[-1] if files else ""


def _cell(row: Dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


_URL_RE = re.compile(r"^https?://", re.I)


def _clean_url(v: str) -> str:
    """链接列清洗

    店小秘导出时常在链接后面带备注(如 `https://qr.1688.com/s/7jhtNmLm CZ5967`),
    直接当 href 会打不开, 这里只取第一段; 不是 http(s) 的当没有。
    """
    u = str(v or "").strip()
    if not u:
        return ""
    u = u.split()[0]
    return u if _URL_RE.match(u) else ""


_AMAZON_RE = re.compile(r"amazon\.", re.I)


def _classify(urls: List[str]) -> Dict[str, str]:
    """两条来源链接按域名归类 → {amazon_url, purchase_url}

    亚马逊商品页(www.amazon.co.jp/dp/…) 归 amazon_url, 其余(1688/拼多多/淘宝…)归
    purchase_url。列顺序不可靠(实测有反的), 所以不按位置取。
    没有亚马逊链接时 amazon_url 留空, 避免把采购链接显示成「亚马逊链接」。
    """
    amazon = next((u for u in urls if u and _AMAZON_RE.search(u)), "")
    purchase = next((u for u in urls if u and u != amazon), "")
    return {"amazon_url": amazon, "purchase_url": purchase}


_MANUAL_FIELDS = ("amazon_url", "purchase_url", "source")


def manual_path() -> str:
    return os.path.join(data_dir(), "parent_links_manual.json")


def load_manual() -> Dict[str, Dict[str, str]]:
    """人工补录的父体链接: data/parent_links_manual.json

    店小秘导出里没有的链接(常见是采购链接整列为空)手补在这个文件里,
    导入时覆盖 CSV 的值 —— 否则每次重新导出/导入都会把补录的冲掉。
    格式: {"B0HHS1GBBL": {"purchase_url": "https://qr.1688.com/s/2YLwDpnq", "source": "1688"}}
    """
    p = manual_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[str(k).strip().upper()] = {
                kk: str(vv).strip() for kk, vv in v.items()
                if kk in _MANUAL_FIELDS and vv}
    return out


def parse_csv(path: str) -> List[Dict[str, Any]]:
    """解析 CSV → [{parent_asin, parent_sku, ...}]"""
    out: List[Dict[str, Any]] = []
    manual = load_manual()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pa = _cell(row, "父ASIN")
            if not pa:
                continue
            links = _classify([_clean_url(_cell(row, "来源链接1")),
                               _clean_url(_cell(row, "来源链接2"))])
            rec = {
                "parent_asin": pa,
                "parent_sku": _cell(row, "父SKU"),
                "parent_title": _cell(row, "父标题"),
                "source": _cell(row, "来源"),
                "amazon_url": links["amazon_url"],
                "purchase_url": links["purchase_url"],
                "product_id": _cell(row, "父产品ID"),
                "edit_url": _cell(row, "编辑链接"),
            }
            rec.update(manual.get(pa.upper()) or {})     # 人工补录优先
            out.append(rec)
    return out


def import_csv(path: str = "") -> Dict[str, Any]:
    """导入 CSV；path 为空时取 data/ 下最新的 parents_*.csv

    返回 {file, parsed, saved, with_amazon, with_purchase, unmatched, unmatched_list}
    """
    p = path or latest_csv()
    if not p or not os.path.exists(p):
        raise FileNotFoundError(f"未找到父ASIN CSV: {p or '(data/ 下没有 parents_*.csv)'}")

    db.init_db()                    # 确保 parent_links 表已建 (老库/服务未重启时)

    rows = parse_csv(p)
    if not rows:
        raise ValueError(f"CSV 里没有可用数据(缺 父ASIN 列?): {p}")
    saved = db.save_parent_links(rows, os.path.basename(p))

    known = db.known_parent_asins()
    unmatched = [r["parent_asin"] for r in rows if r["parent_asin"] not in known]
    return {
        "file": p,
        "parsed": len(rows),
        "saved": saved,
        "with_amazon": sum(1 for r in rows if r["amazon_url"]),
        "with_purchase": sum(1 for r in rows if r["purchase_url"]),
        "matched": len(rows) - len(unmatched),
        "unmatched": len(unmatched),
        "unmatched_list": unmatched[:50],
    }


def main() -> int:
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                       # noqa: BLE001
            pass
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    r = import_csv(path)
    print(f"✅ 已导入 {r['saved']} 条父 ASIN 来源链接 ← {r['file']}")
    print(f"   亚马逊链接 {r['with_amazon']} 条 · 采购源链接 {r['with_purchase']} 条")
    print(f"   与库内父ASIN匹配 {r['matched']} 条 · 未匹配 {r['unmatched']} 条")
    if r["unmatched_list"]:
        print("   未匹配(库里还没有这些 ASIN):", ", ".join(r["unmatched_list"][:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
