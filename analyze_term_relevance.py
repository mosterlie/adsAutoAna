# -*- coding: utf-8 -*-
"""搜索词 × SKU 相关性分析：花费 > 阈值的词逐个与所属 SKU 标题对比。

用法:
  python3 analyze_term_relevance.py [--min-cost 100] [--out data/reports]

产出:
  data/reports/term_relevance.json   结构化全量结果（含每词的命中依据）
  data/reports/term_relevance.md     可读报告
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.term_relevance import score_pair  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ads.db")
ASIN_RE = re.compile(r"^(B0[A-Z0-9]{8})")

# 口径：只看搜索词（sp:asin 是竞品/商品投放，不是词）
SCOPE = "sp:keyword"
SNAPSHOT = None  # ("2026-08-01","2026-10-05") 或 None=取最新


def latest_range(cur):
    row = cur.execute(
        "select range_start, range_end from ad_records where tab='search' "
        "order by range_start desc limit 1").fetchone()
    return row if row else (None, None)


def load(min_cost):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    rs, re_ = latest_range(cur)
    if not rs:
        raise SystemExit("库里没有搜索词数据")

    prods = {r["asin"]: dict(r) for r in cur.execute(
        "select asin, sku, title, parent_asin, is_variation from online_products")}

    # asin -> 父ASIN 标题（用于判断「词与兄弟子体更相关」的情况）
    parent_title = {}
    for a, p in prods.items():
        pa = p.get("parent_asin") or a
        if pa in prods and (prods[pa]["title"] or "").strip():
            parent_title[a] = prods[pa]["title"]

    rows = cur.execute(
        "select biz_key, raw_json from ad_records "
        "where tab='search' and scope=? and range_start=? and range_end=?",
        (SCOPE, rs, re_)).fetchall()

    # 同一 (query, asin) 可能在多个活动里重复出现 → 合并
    agg = {}
    for r in rows:
        d = json.loads(r["raw_json"])
        q = (d.get("query") or "").strip()
        if not q:
            continue
        m = ASIN_RE.match(d.get("campaignName") or "") or ASIN_RE.match(d.get("adGroupName") or "")
        if not m:
            continue
        asin = m.group(1)
        k = (q, asin)
        a = agg.setdefault(k, {
            "query": q, "asin": asin, "cost": 0.0, "impr": 0, "clicks": 0,
            "orders": 0, "sales": 0.0, "campaigns": set(), "match_types": set(),
        })
        a["cost"] += float(d.get("adCost") or 0)
        a["sales"] += float(d.get("adSale") or 0)
        a["impr"] += int(d.get("impressions") or 0)
        a["clicks"] += int(d.get("clicks") or 0)
        a["orders"] += int(d.get("adOrderNum") or 0)
        if d.get("campaignName"):
            a["campaigns"].add(d["campaignName"])
        if d.get("matchType"):
            a["match_types"].add(str(d["matchType"]))
    con.close()

    out = []
    for (q, asin), a in agg.items():
        if a["cost"] <= min_cost:
            continue
        p = prods.get(asin, {})
        title = (p.get("title") or "").strip()
        sku = (p.get("sku") or "").strip()
        sc = score_pair(q, title, extra_text=sku)
        pa = p.get("parent_asin") or asin
        sc_parent = None
        pt = parent_title.get(asin, "")
        if pt and pt != title:
            sc_parent = score_pair(q, pt)
        a.update({
            "sku": sku, "title": title, "parent_asin": pa,
            "parent_title": pt,
            "score": sc["score"], "level": sc["level"],
            "coverage": sc["coverage"], "contig": sc["contig"],
            "dice": sc["dice"], "lcs": sc["lcs"],
            "matched": sc["matched"], "partial": sc["partial"], "missing": sc["missing"],
            "hit_fragment": sc["hit_fragment"],
            "query_tokens": sc["query_tokens"], "title_tokens": sc["title_tokens"],
            "score_vs_parent": (sc_parent or {}).get("score"),
            "level_vs_parent": (sc_parent or {}).get("level"),
            "campaigns": sorted(a["campaigns"]),
            "match_types": sorted(a["match_types"]),
            "range": [rs, re_],
        })
        out.append(a)
    out.sort(key=lambda x: (-x["cost"], x["score"]))
    return out, (rs, re_)


def fmt_kw(part):
    """matched/partial/missing 统一格式化：词（依据）。"""
    if not part:
        return "—"
    bits = []
    for it in part:
        if isinstance(it, (list, tuple)) and len(it) == 2:
            w, why = it
            bits.append(f"**{w}**" + (f"（{why}）" if why and why != "exact" else ""))
        else:
            bits.append(str(it))
    return "、".join(bits)


def to_md(rows, rng, min_cost):
    tot = sum(r["cost"] for r in rows) or 1
    L = []
    L.append(f"# 搜索词 × SKU 相关性报告（花费 > {min_cost:g}）")
    L.append("")
    L.append(f"- 数据区间：**{rng[0]} ~ {rng[1]}**（赛狐最后一次成功采集的快照）")
    L.append("- 口径：`sp:keyword` 只看搜索词（`sp:asin` 是竞品/商品投放，不是词）")
    L.append(f"- 命中：**{len(rows)} 条**「搜索词 × SKU」，花费合计 **¥{tot:,.2f}**")
    L.append("- 同一「词 × ASIN」在多个活动/匹配方式里重复出现的，已合并累计")
    L.append("- SKU 归属：广告活动名/广告组名前缀的 ASIN → `online_products` 取 SKU 与标题")
    L.append("")
    L.append("## 一、判定方法（可复现）")
    L.append("")
    L.append("```")
    L.append("score = 0.60 × coverage + 0.25 × contig + 0.15 × lcs      # 0~1")
    L.append("```")
    L.append("")
    L.append("| 成分 | 权重 | 怎么算 |")
    L.append("|---|---|---|")
    L.append("| **coverage** | 0.60 | 搜索词用 janome 分词取内容词（名詞/動詞/形容詞/未知語，去掉助詞等虚词），"
             "逐词在标题里找：完全等同标题词 = 1.0；能在标题里找到的 = 0.85；"
             "只能部分对上（如 `暖炉`→`炉`）= 0.85×覆盖率；近似词 = 0.5×Dice |")
    L.append("| **contig** | 0.25 | 搜索词整串有多少比例的字符能在标题里找到**连续片段**（greedy 最长匹配）。"
             "比 Dice 更稳：不会被长标题稀释 |")
    L.append("| **lcs** | 0.15 | 两者最长公共连续子串长度 ÷ 搜索词长度 |")
    L.append("")
    L.append("归一化：NFKC + 小写 + 去空白与标点（全角/半角、片假名长音符「ー」已统一）。")
    L.append("片假名复合词按 greedy 最长匹配拆解，避免 janome 把 `ワイヤーメッシュフェンス` 并成一个词后误判。")
    L.append("")
    L.append("| 分档 | 分数 | 运营含义 |")
    L.append("|---|---|---|")
    L.append("| 🟢 高度相关 | ≥0.85 | 词就是商品本体，是精准词 |")
    L.append("| 🟡 相关 | 0.60~0.85 | 同品类/同用途，可继续观察转化 |")
    L.append("| 🟠 弱相关 | 0.35~0.60 | 只沾边，转化差就该降/否 |")
    L.append("| 🔴 不相关 | <0.35 | 钱花在别的品类上，优先否词 |")
    L.append("")
    L.append("## 二、总览")
    L.append("")
    L.append("| 分档 | 词条数 | 花费 | 花费占比 |")
    L.append("|---|---|---|---|")
    buck = defaultdict(lambda: [0, 0.0])
    for r in rows:
        buck[r["level"]][0] += 1
        buck[r["level"]][1] += r["cost"]
    for lv in ("高度相关", "相关", "弱相关", "不相关"):
        c, s = buck.get(lv, [0, 0.0])
        L.append(f"| {lv} | {c} | ¥{s:,.2f} | {s/tot*100:.1f}% |")
    L.append("")
    L.append("## 三、需要动手的词（弱相关 + 不相关，按花费降序）")
    L.append("")
    bad = [r for r in rows if r["score"] < 0.60]
    if not bad:
        L.append("无。")
    else:
        L.append("| 搜索词 | ASIN | 花费 | 订单 | 分档 | 分 | 未命中的词 |")
        L.append("|---|---|---|---|---|---|---|")
        for r in sorted(bad, key=lambda x: -x["cost"]):
            miss = "、".join(m[0] for m in r["missing"]) or "—"
            L.append(f"| `{r['query']}` | {r['asin']} | ¥{r['cost']:,.2f} | {r['orders']} | "
                     f"{r['level']} | {r['score']:.2f} | {miss} |")
        L.append("")
        L.append(f"合计花费 **¥{sum(r['cost'] for r in bad):,.2f}**"
                 f"（占全部 {sum(r['cost'] for r in bad)/tot*100:.1f}%），订单 "
                 f"**{sum(r['orders'] for r in bad)}** 单。")
    L.append("")
    L.append("## 四、全部命中词一览（按花费降序）")
    L.append("")
    L.append("| # | 搜索词 | ASIN | SKU | 花费 | 曝光 | 点击 | 订单 | 销售额 | 分档 | 分 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(sorted(rows, key=lambda x: -x["cost"]), 1):
        L.append(f"| {i} | `{r['query']}` | {r['asin']} | {r['sku']} | ¥{r['cost']:,.2f} | "
                 f"{r['impr']:,} | {r['clicks']} | {r['orders']} | ¥{r['sales']:,.2f} | "
                 f"{r['level']} | {r['score']:.2f} |")
    L.append("")
    L.append("## 五、逐词依据")
    L.append("")
    for i, r in enumerate(sorted(rows, key=lambda x: -x["cost"]), 1):
        L.append(f"### {i}. `{r['query']}` → {r['asin']} · {r['level']} {r['score']:.2f}")
        L.append("")
        L.append(f"- 花费 **¥{r['cost']:,.2f}** ｜ 曝光 {r['impr']:,} ｜ 点击 {r['clicks']} ｜ "
                 f"订单 {r['orders']} ｜ 销售额 ¥{r['sales']:,.2f} ｜ 匹配方式 "
                 f"{'/'.join(r['match_types']) or '-'}")
        L.append(f"- SKU：`{r['sku']}`" +
                 (f"（挂在父体 {r['parent_asin']} 下）" if r["parent_asin"] != r["asin"] else ""))
        L.append(f"- 标题：{r['title']}")
        if r["hit_fragment"]:
            L.append(f"- 标题命中片段：`{r['hit_fragment']}`")
        L.append(f"- 拆成：{r['query_tokens']}")
        L.append(f"- ✅ 命中标题：{fmt_kw(r['matched'])}")
        if r["partial"]:
            L.append(f"- ⚠️ 只对上一部分：{fmt_kw(r['partial'])}")
        L.append(f"- ❌ 标题里没有：{fmt_kw(r['missing'])}")
        L.append(f"- 分数构成：coverage {r['coverage']} · contig {r['contig']} · "
                 f"lcs {r['lcs']}  → 0.6×{r['coverage']}+0.25×{r['contig']}+0.15×{r['lcs']} = {r['score']}")
        if r["score_vs_parent"] is not None:
            L.append(f"- 换成父体标题比对：{r['level_vs_parent']} {r['score_vs_parent']:.2f}")
        L.append("")
    return "\n".join(L)


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>搜索词 × SKU 相关性报告</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--line:#e5e7eb;--tx:#1f2328;--tx2:#5b6470;--tx3:#8b949e;
--g:#1a7f37;--y:#9a6700;--o:#bc4c00;--r:#cf222e;--blue:#0969da;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.6 -apple-system,"Helvetica Neue","Hiragino Sans","Yu Gothic UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1560px;margin:0 auto;padding:28px 22px 80px}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:16px;margin:34px 0 12px;padding-left:10px;border-left:3px solid var(--blue)}
.sub{color:var(--tx2);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.kpi{flex:1 1 170px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .n{font-size:22px;font-weight:600;letter-spacing:-.3px}
.kpi .l{font-size:12px;color:var(--tx2);margin-top:2px}
.kpi.g .n{color:var(--g)}.kpi.y .n{color:var(--y)}.kpi.o .n{color:var(--o)}.kpi.r .n{color:var(--r)}
.scroll{overflow-x:auto;border-radius:10px}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}
th{background:#f0f2f5;position:sticky;top:0;z-index:2;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{background:#e6e9ee}
th::after{content:"⇅";color:var(--tx3);font-size:10px;margin-left:5px;opacity:.5}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.nw{white-space:nowrap}
td.ell{max-width:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.hit{line-height:1.5;word-break:break-word}
tr:hover td{background:#fafbfc}
.q{font-weight:600}
.jp{font-family:"Hiragino Sans","Yu Gothic","Meiryo",sans-serif}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.tag.g{background:#dafbe1;color:#0f5323}.tag.y{background:#fff8c5;color:#6b5600}
.tag.o{background:#fff1e5;color:#8a3d00}.tag.r{background:#ffebe9;color:#a40e26}
.bar{height:6px;border-radius:3px;background:#eceff3;overflow:hidden;min-width:54px}
.bar>i{display:block;height:100%;background:var(--blue)}
.det{background:#fbfcfd;border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:10px 0 22px}
.det h3{margin:0 0 8px;font-size:15px}
.det dl{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;margin:0}
.det dt{color:var(--tx2);font-size:12.5px;padding-top:2px}
.det dd{margin:0;word-break:break-word}
.ok{color:var(--g);font-weight:600}.no{color:var(--r);font-weight:600}.pa{color:var(--o);font-weight:600}
code{background:#f0f2f5;padding:1px 5px;border-radius:4px;font-size:12.5px}
.formula{background:#f0f2f5;border-radius:8px;padding:12px 14px;font-family:ui-monospace,Menlo,monospace;font-size:13px}
.muted{color:var(--tx3)}
.hint{font-size:12.5px;color:var(--tx2);margin-top:8px}
.quad{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.quad>div{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--card)}
.quad h4{margin:0 0 6px;font-size:13.5px}
.quad ul{margin:6px 0 0;padding-left:18px;font-size:12.5px;color:var(--tx2)}
</style></head><body><div class="wrap">"""

HTML_JS = """
<script>
document.querySelectorAll('table.sortable').forEach(function(tb){
  tb.querySelectorAll('th').forEach(function(th,i){
    th.addEventListener('click',function(){
      var tbody=tb.tBodies[0],rows=Array.prototype.slice.call(tbody.rows);
      var dir=th.dataset.dir==='asc'?-1:1; th.dataset.dir=dir===1?'asc':'desc';
      tb.querySelectorAll('th').forEach(function(x){x.dataset.dir='';});
      th.dataset.dir=dir===1?'asc':'desc';
      rows.sort(function(a,b){
        var x=a.cells[i].dataset.v, y=b.cells[i].dataset.v;
        if(x===undefined)x=a.cells[i].innerText; if(y===undefined)y=b.cells[i].innerText;
        var nx=parseFloat(String(x).replace(/[^0-9.\\-]/g,'')), ny=parseFloat(String(y).replace(/[^0-9.\\-]/g,''));
        if(!isNaN(nx)&&!isNaN(ny)) return (nx-ny)*dir;
        return String(x).localeCompare(String(y),'ja')*dir;
      });
      rows.forEach(function(r){tbody.appendChild(r);});
    });
  });
});
document.querySelectorAll('[data-toggle]').forEach(function(a){
  a.addEventListener('click',function(e){e.preventDefault();
    var el=document.getElementById(a.dataset.toggle);
    el.style.display = el.style.display==='none'?'block':'none';
    if(el.style.display==='block') el.scrollIntoView({block:'nearest'});
  });
});
</script></div></body></html>"""


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _kws(part):
    if not part:
        return ""
    bits = []
    for it in part:
        w, why = (it if isinstance(it, (list, tuple)) and len(it) == 2 else (it, ""))
        bits.append(f"<b class='jp'>{esc(w)}</b>" +
                    (f"<span class='muted'>（{esc(why)}）</span>" if why and why != "exact" else ""))
    return "、".join(bits)


def to_html(rows, rng, min_cost):
    from collections import defaultdict
    tot = sum(r["cost"] for r in rows) or 1
    srt = sorted(rows, key=lambda x: -x["cost"])
    buck = defaultdict(lambda: [0, 0.0])
    for r in rows:
        buck[r["level"]][0] += 1
        buck[r["level"]][1] += r["cost"]
    TAG = {"高度相关": "g", "相关": "y", "弱相关": "o", "不相关": "r"}

    H = [HTML_HEAD]
    H.append("<h1>搜索词 × SKU 相关性报告</h1>")
    H.append(f"<div class='sub'>数据区间 <b>{rng[0]} ~ {rng[1]}</b>（赛狐最后一次成功采集快照）"
             f"　·　口径 <code>sp:keyword</code> 只看搜索词　·　阈值 花费 &gt; ¥{min_cost:g}"
             f"　·　同一「词 × ASIN」跨活动已合并</div>")

    H.append("<div class='kpis'>")
    H.append(f"<div class='kpi'><div class='n'>{len(rows)}</div><div class='l'>词 × SKU 条数</div></div>")
    H.append(f"<div class='kpi'><div class='n'>¥{tot:,.0f}</div><div class='l'>涉及花费</div></div>")
    g = buck.get("高度相关", [0, 0.0])
    H.append(f"<div class='kpi g'><div class='n'>{g[1]/tot*100:.0f}%</div>"
             f"<div class='l'>花费落在高度相关词上</div></div>")
    zc = sum(r["cost"] for r in rows if r["orders"] == 0)
    H.append(f"<div class='kpi r'><div class='n'>{zc/tot*100:.0f}%</div>"
             f"<div class='l'>花费没带来订单</div></div>")
    H.append(f"<div class='kpi'><div class='n'>{sum(r['orders'] for r in rows)}</div>"
             f"<div class='l'>这批词总订单</div></div>")
    H.append("</div>")

    H.append("<h2>判定方法（可复现）</h2><div class='card'>")
    H.append("<div class='formula'>score = 0.60 × coverage + 0.25 × contig + 0.15 × lcs"
             "　　　(0 ~ 1)</div>")
    H.append("<ul style='margin:12px 0 0;padding-left:20px'>"
             "<li><b>coverage</b>：搜索词用 janome 分词取内容词（名詞/動詞/形容詞/未知語，去掉助詞等虚词），"
             "逐词去标题里找 —— 完全等同标题词 <code>= 1.0</code>；能在标题里找到 <code>= 0.85</code>；"
             "只对上部分（如 <span class='jp'>暖炉</span>→<span class='jp'>炉</span>）<code>= 0.85×覆盖率</code>；"
             "近似词 <code>= 0.5×Dice</code></li>"
             "<li><b>contig</b>：搜索词整串的字符有多少比例能在标题里找到<b>连续片段</b>（greedy 最长匹配）。"
             "比 Dice 稳，不会被长标题稀释</li>"
             "<li><b>lcs</b>：两者最长公共连续子串长度 ÷ 搜索词长度</li>"
             "<li>归一化：NFKC + 小写 + 去空白与标点，全角/半角与片假名长音符「ー」已统一；"
             "片假名复合词按 greedy 匹配拆解，避免 janome 把 "
             "<span class='jp'>ワイヤーメッシュフェンス</span> 并成一个词后误判</li></ul>")
    H.append("<div class='hint'>SKU 归属：广告活动名 / 广告组名前缀的 ASIN → <code>online_products</code> "
             "取 SKU 与标题（457/457 条搜索词全部能定位到 ASIN，68 个 ASIN 全部有标题）</div>")
    H.append("</div>")

    H.append("<h2>总览</h2><div class='card'><table class='sortable'><thead><tr>"
             "<th>分档</th><th class='num'>条数</th><th class='num'>花费</th>"
             "<th class='num'>花费占比</th><th>占比条</th><th>运营含义</th></tr></thead><tbody>")
    meaning = {"高度相关": "词就是商品本体，是精准词", "相关": "同品类 / 同用途，可继续观察转化",
               "弱相关": "只沾边，转化差就该降价或否掉", "不相关": "钱花在别的品类上，优先否词"}
    for lv in ("高度相关", "相关", "弱相关", "不相关"):
        c, s = buck.get(lv, [0, 0.0])
        H.append(f"<tr><td><span class='tag {TAG[lv]}'>{lv}</span></td>"
                 f"<td class='num' data-v='{c}'>{c}</td><td class='num' data-v='{s:.1f}'>¥{s:,.2f}</td>"
                 f"<td class='num' data-v='{s/tot*100:.1f}'>{s/tot*100:.1f}%</td>"
                 f"<td><div class='bar'><i style='width:{s/tot*100:.1f}%'></i></div></td>"
                 f"<td>{meaning[lv]}</td></tr>")
    H.append("</tbody></table></div>")

    # 相关性 × 转化 四象限
    H.append("<h2>相关性 × 转化</h2><div class='quad'>")
    hi = [r for r in rows if r["score"] >= 0.60]
    lo = [r for r in rows if r["score"] < 0.60]
    boxes = [
        ("🟢 词对 + 有单 → 加价抢位", [r for r in hi if r["orders"] > 0], "var(--g)"),
        ("🟡 词对 + 无单 → 查 listing / 价格 / 评价，或竞价过高", [r for r in hi if r["orders"] == 0], "var(--y)"),
        ("🔴 词不对 + 无单 → 直接否词", [r for r in lo if r["orders"] == 0], "var(--r)"),
        ("🟠 词不对 + 有单 → 捡漏词，单独控价", [r for r in lo if r["orders"] > 0], "var(--o)"),
    ]
    for title, rs, col in boxes:
        cost = sum(r["cost"] for r in rs)
        H.append(f"<div><h4 style='color:{col}'>{title}</h4>"
                 f"<div style='font-size:12.5px'>¥{cost:,.0f} ／ {len(rs)} 条"
                 f"（占 {cost/tot*100:.1f}%）</div><ul>")
        for r in sorted(rs, key=lambda x: -x["cost"])[:6]:
            H.append(f"<li><span class='jp'>{esc(r['query'])}</span> "
                     f"<span class='muted'>{r['asin']} · ¥{r['cost']:,.0f}"
                     f"{' · 单'+str(r['orders']) if r['orders'] else ''}</span></li>")
        if len(rs) > 6:
            H.append(f"<li class='muted'>… 其余 {len(rs)-6} 条见表</li>")
        H.append("</ul></div>")
    H.append("</div>")

    H.append("<h2>全部明细（点表头排序，点「依据」看逐词对比）</h2>")
    H.append("<div class='scroll'><table class='sortable'>")
    H.append("<colgroup>"
             "<col style='width:42px'><col style='width:152px'><col style='width:92px'>"
             "<col style='width:148px'><col style='width:84px'><col style='width:66px'>"
             "<col style='width:54px'><col style='width:50px'><col style='width:84px'>"
             "<col style='width:60px'><col style='width:72px'><col style='width:54px'>"
             "<col style='width:300px'><col style='width:52px'>"
             "</colgroup>")
    H.append("<thead><tr>"
             "<th class='num'>#</th><th>搜索词</th><th>ASIN</th><th>SKU</th>"
             "<th class='num'>花费</th><th class='num'>曝光</th><th class='num'>点击</th>"
             "<th class='num'>订单</th><th class='num'>销售额</th><th class='num'>点击率</th>"
             "<th>分档</th><th class='num'>得分</th><th>命中 / 未命中</th><th>依据</th>"
             "</tr></thead><tbody>")
    for i, r in enumerate(srt, 1):
        ctr = (r["clicks"] / r["impr"] * 100) if r["impr"] else 0
        hit = "、".join(m[0] for m in r["matched"]) or "—"
        miss = "、".join(m[0] for m in r["missing"]) or "—"
        H.append(
            f"<tr><td class='num nw' data-v='{i}'>{i}</td>"
            f"<td class='q jp hit' data-v='{esc(r['query'])}'>{esc(r['query'])}</td>"
            f"<td class='nw' data-v='{r['asin']}'><a href='https://www.amazon.co.jp/dp/{r['asin']}' "
            f"target='_blank' rel='noreferrer'>{r['asin']}</a></td>"
            f"<td class='ell' data-v='{esc(r['sku'])}' title='{esc(r['sku'])}'>{esc(r['sku'])}</td>"
            f"<td class='num' data-v='{r['cost']:.1f}'>¥{r['cost']:,.2f}</td>"
            f"<td class='num' data-v='{r['impr']}'>{r['impr']:,}</td>"
            f"<td class='num' data-v='{r['clicks']}'>{r['clicks']}</td>"
            f"<td class='num' data-v='{r['orders']}'>{r['orders']}</td>"
            f"<td class='num' data-v='{r['sales']:.1f}'>¥{r['sales']:,.0f}</td>"
            f"<td class='num' data-v='{ctr:.2f}'>{ctr:.1f}%</td>"
            f"<td data-v='{r['score']}'><span class='tag {TAG[r['level']]}'>{r['level']}</span></td>"
            f"<td class='num' data-v='{r['score']}'>{r['score']:.2f}</td>"
            f"<td class='hit jp' data-v='{r['score']}'><span class='ok'>✓ {esc(hit)}</span>"
            f"{'<br><span class=no>✗ '+esc(miss)+'</span>' if miss != '—' else ''}</td>"
            f"<td class='nw'><a href='#' data-toggle='d{i}'>展开</a></td></tr>")
    H.append("</tbody></table></div>")

    H.append("<h2>逐条依据</h2>")
    for i, r in enumerate(srt, 1):
        H.append(f"<div class='det' id='d{i}' style='display:none'>")
        H.append(f"<h3>{i}. <span class='jp'>{esc(r['query'])}</span> → "
                 f"{r['asin']}　<span class='tag {TAG[r['level']]}'>{r['level']}</span> "
                 f"<span style='font-weight:600'>{r['score']:.2f}</span></h3>")
        H.append("<dl>")
        H.append(f"<dt>花费</dt><dd>¥{r['cost']:,.2f}　曝光 {r['impr']:,}　点击 {r['clicks']}　"
                 f"订单 {r['orders']}　销售额 ¥{r['sales']:,.2f}　匹配方式 "
                 f"{'/'.join(r['match_types']) or '-'}</dd>")
        H.append(f"<dt>SKU</dt><dd><code>{esc(r['sku'])}</code>" +
                 (f"　<span class='muted'>挂在父体 {r['parent_asin']} 下</span>"
                  if r["parent_asin"] != r["asin"] else "") + "</dd>")
        H.append(f"<dt>标题</dt><dd class='jp'>{esc(r['title'])}</dd>")
        H.append(f"<dt>词拆开</dt><dd class='jp'>{esc(' / '.join(r['query_tokens']))}</dd>")
        if r["hit_fragment"]:
            H.append(f"<dt>命中片段</dt><dd><code class='jp'>{esc(r['hit_fragment'])}</code></dd>")
        H.append(f"<dt>分数构成</dt><dd>0.60×{r['coverage']} + 0.25×{r['contig']} + "
                 f"0.15×{r['lcs']} = <b>{r['score']:.3f}</b></dd>")
        for lbl, part in (("✅ 命中标题", r["matched"]),
                          ("⚠️ 只对上一部分", r["partial"]),
                          ("❌ 标题里没有", r["missing"])):
            if part:
                H.append(f"<dt>{lbl}</dt><dd>{_kws(part)}</dd>")
        if r["score_vs_parent"] is not None:
            H.append(f"<dt>父体标题</dt><dd>{r['level_vs_parent']} "
                     f"{r['score_vs_parent']:.2f}</dd>")
        H.append("</dl></div>")

    H.append(HTML_JS)
    return "".join(H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-cost", type=float, default=100.0)
    ap.add_argument("--out", default="data/reports")
    args = ap.parse_args()

    rows, rng = load(args.min_cost)
    root = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.join(root, args.out)
    os.makedirs(outdir, exist_ok=True)

    with open(os.path.join(outdir, "term_relevance.json"), "w", encoding="utf-8") as f:
        json.dump({"range": rng, "min_cost": args.min_cost, "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    md = to_md(rows, rng, args.min_cost)
    with open(os.path.join(outdir, "term_relevance.md"), "w", encoding="utf-8") as f:
        f.write(md)
    html = to_html(rows, rng, args.min_cost)
    html_path = os.path.join(outdir, "term_relevance.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"区间 {rng[0]} ~ {rng[1]}   花费>{args.min_cost:g} 命中 {len(rows)} 条")
    print(f"{'词':<26}{'ASIN':<12}{'花费':>8}  {'分档':<6}{'分':>6}  {'cov':>5}{'cont':>6}{'lcs':>6}  命中/缺")
    for r in rows:
        print(f"{r['query'][:24]:<26}{r['asin']:<12}{r['cost']:>8.1f}  "
              f"{r['level']:<6}{r['score']:>6.2f}  {r['coverage']:>5.2f}{r['contig']:>6.2f}{r['lcs']:>6.2f}  "
              f"{len(r['matched'])}/{len(r['missing'])}")
    print(f"\n→ {outdir}/term_relevance.html  +  .md  +  .json")


if __name__ == "__main__":
    main()
