# -*- coding: utf-8 -*-
"""换一个模型重跑「整体相似度」, 核对已落库的 glm-5v-turbo 分数是否准确

用法:
    python verify_sim_model_compare.py [asin] [term] [top_n] [模型]

默认: asin=B0HHR6KGCV term=シフトノブ top_n=10 model=kimi-k3

做两件事:
  ① 对同一个词的前 N 条竞品, 用 kimi-k3 再判断一次 → 与库里 glm-5v-turbo 的分数逐条对照;
  ② 混入 NEG 里几组「与我的品无关」的竞品(别的搜索词的竞品)作反例:
     如果模型整体比对是有效的, 这些条必须拿低分; 若也拿高分, 说明它没在整体判断。

★ 只读不写: 结果不落 sku_similarity 表, 不会覆盖已算好的分数。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from backend import database as db                     # noqa: E402
from backend import sku_sim                            # noqa: E402

GLM = "glm-5v-turbo"
#: 反例: (父ASIN, 搜索词, 取几条) —— 家具类竞品, 与「汽车换挡杆头」毫无关系
NEG_CASES = [("B0HJPL4Y53", "こたつテーブル", 3)]
OUT = os.path.join(os.environ.get("TEMP", "."), "sim_model_compare.txt")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            pass
    asin = sys.argv[1] if len(sys.argv) > 1 else "B0HHR6KGCV"
    term = sys.argv[2] if len(sys.argv) > 2 else "シフトノブ"
    top_n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    model = sys.argv[4] if len(sys.argv) > 4 else "kimi-k3"

    my_title = db.get_parent_title(asin)
    my_image = db.get_parent_main_image(asin)
    my_b64 = None
    if my_image:
        try:
            my_b64 = sku_sim.b64_by_url(my_image)
        except Exception as e:                         # noqa: BLE001
            print("我的主图下载失败:", e)
    cached = db.get_sku_sims(asin, term)

    jobs = [(asin, term, r, True) for r in
            [x for x in db.latest_amazon_results(term, top_n) if x.get("image")][:top_n]]
    for p, q, n in NEG_CASES:
        jobs += [(p, q, r, False) for r in
                 [x for x in db.latest_amazon_results(q, n) if x.get("image")][:n]]

    def run(job):
        p, q, r, same_term = job
        row = {"pos": r.get("position"), "asin": r.get("asin"),
               "title": (r.get("title") or "")[:56], "glm": None, "k3": None,
               "reason": "", "neg": not same_term}
        if same_term:
            row["glm"] = (cached.get(str(r.get("asin") or "")) or {}).get("score")
        try:
            j = sku_sim.judge(my_title, my_b64, r.get("title") or "",
                              sku_sim.other_b64(p, q, r), model)
            row["k3"], row["reason"] = j["score"], j["reason"]
        except Exception as e:                         # noqa: BLE001
            row["reason"] = f"失败: {str(e)[:60]}"
        return row

    with ThreadPoolExecutor(max_workers=5) as ex:
        rows = list(ex.map(run, jobs))

    lines = [f"我的品({asin}): {my_title[:60]}",
             f"搜索词: {term} · 前 {top_n} 条竞品 · 对照模型: {GLM} vs {model}", ""]
    head = f"{'pos':>4} {'ASIN':<12} {'glm':>4} {'k3':>4}  理由({model})"
    lines.append(head)
    lines.append("-" * 100)
    for r in sorted(rows, key=lambda x: (x["neg"], x["pos"] or 0)):
        tag = "[反例]" if r["neg"] else "      "
        lines.append(f"{str(r['pos']):>4} {r['asin']:<12} "
                     f"{str(r['glm'] if r['glm'] is not None else '-'):>4} "
                     f"{str(r['k3'] if r['k3'] is not None else '-'):>4} "
                     f"{tag}{r['reason']}")

    main_rows = [r for r in rows if not r["neg"] and r["k3"] is not None]
    neg_rows = [r for r in rows if r["neg"] and r["k3"] is not None]
    if main_rows:
        k3_avg = sum(r["k3"] for r in main_rows) / len(main_rows)
        pairs = [(r["glm"], r["k3"]) for r in main_rows if r["glm"] is not None]
        diff = [abs(a - b) for a, b in pairs]
        lines += ["", f"前 {len(main_rows)} 条: k3 平均 {k3_avg:.1f} · "
                      f"glm 平均 {(sum(a for a, _ in pairs) / len(pairs)):.1f}"
                      if pairs else "",
                      f"两者差值: 平均 {sum(diff) / len(diff):.1f} 分 · 最大 {max(diff)} 分"
                      if diff else ""]
    if neg_rows:
        lines.append(f"反例({len(neg_rows)} 条无关竞品): k3 平均 "
                     f"{sum(r['k3'] for r in neg_rows) / len(neg_rows):.1f} 分 "
                     f"(应显著低于前 {len(main_rows)} 条, 否则说明模型没在做整体判断)")
    text = "\n".join([x for x in lines if x is not None])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("\nwrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
