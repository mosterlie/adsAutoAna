# -*- coding: utf-8 -*-
"""搜索词 × SKU 相关性打分回归（T 段）。

守护 backend/term_relevance.py 的判定口径：
分数单调性、片假名复合词拆解、虚词过滤、分档边界。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.term_relevance import score_pair, normalize, tokens, covered_ratio  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


T_PICKLE = ("ピックルボールネット ピックルボールセット 標準サイズのピックルボール 大きい 頑丈 "
            "耐摩耗 屋外 運動場 庭園 公園芝生 携帯便利 組み立てが簡単 (フレーム+通常ネット,ブラック)")
T_FENCE = ("1個柵金網フェンス 菱形ワイヤーメッシュ 防獣フェンス 亜鉛めっき材 厚い手 頑丈 耐久性 "
           "耐摩耗 巻き上げ収納 庭 農場用 家畜動物保護 再利用可能 (高さ1.5m太さ3.61mm5穴長さ20m,シルバー)")
T_KAMA = ("草取り道具 立ったまま 多機能草刈りレーキ テコステンレス 便利グッズ根こそぎ草抜き "
          "家庭 松土 庭 芝生 農場用 園芸用 着脱可能 組み立てが簡単 (3段全長1.2m,A) [並行輸入品]")
T_STOVE = ("四角風炉 茶道具 木炭ストーブ 茶釜添え焚き火台 多機能ティーストーブ 耐熱ガラス "
           "火炉を観察する 耐高温 安定構造 茶屋 屋外用 キャンプ用 自宅用 (A)")

print("T1 归一化与分词")
ck("T1.1 全角转半角+小写", normalize("ＡＢＣ　１２３") == "abc123", normalize("ＡＢＣ　１２３"))
ck("T1.2 去标点", normalize("焼却炉・家庭用!") == "焼却炉家庭用", normalize("焼却炉・家庭用!"))
ck("T1.3 片假名长音符保留", normalize("メーカー") == "メーカー", normalize("メーカー"))
ck("T1.4 虚词被过滤", "の" not in tokens("庭のフェンス"), tokens("庭のフェンス"))
ck("T1.5 单字假名被过滤", tokens("ワ") == [], tokens("ワ"))

print("T2 片假名复合词拆解")
r, f = covered_ratio("ワイヤーメッシュフェンス", normalize(T_FENCE))
ck("T2.1 复合词可拆到 100%", r == 1.0, r)
ck("T2.2 拆出两个片段", len(f) >= 2, f)

print("T3 完全匹配 = 1.0")
for q, t in (("ピックルボールネット", T_PICKLE), ("金網フェンス", T_FENCE),
             ("草刈り", T_KAMA), ("ワイヤーメッシュフェンス", T_FENCE)):
    s = score_pair(q, t)
    ck(f"T3 {q} → 高度相关", s["score"] >= 0.85, f'{s["score"]} {s["level"]}')

print("T4 真实错配要能被识别出来")
s = score_pair("草刈り鎌", T_KAMA)
ck("T4.1 镰刀 vs 除草耙：鎌 未命中", "鎌" in [m[0] for m in s["missing"]], s["missing"])
ck("T4.2 词拆成 草刈り/鎌", s["query_tokens"] == ["草刈り", "鎌"], s["query_tokens"])
ck("T4.3 不判为高度相关", s["score"] < 0.85, s["score"])
s = score_pair("バイオエタノール暖炉", T_STOVE)
ck("T4.4 酒精暖炉 vs 茶道风炉：不相关", s["level"] == "不相关", f'{s["score"]} {s["level"]}')
ck("T4.5 バイオエタノール 未命中", "バイオエタノール" in [m[0] for m in s["missing"]], s["missing"])
ck("T4.6 暖炉只对上 炉（部分）", any("暖炉" == p[0] for p in s["partial"]), s["partial"])

print("T5 分数单调性与区间")
s_hi = score_pair("焼却炉 家庭用", "焼却炉 家庭用 ドラム缶焼却炉 大型耐熱 ステンレス鋼 無煙")
s_mid = score_pair("焼却炉 家庭用", "焼却炉 業務用 ドラム缶 ステンレス鋼 無煙 大型")
s_lo = score_pair("焼却炉", "ペット用防音室 犬 猫 ケージ 防音 室内 遮音 換気 ファン付き")
ck("T5.1 全命中 > 部分命中", s_hi["score"] > s_mid["score"], f'{s_hi["score"]} vs {s_mid["score"]}')
ck("T5.2 部分命中 > 无关", s_mid["score"] > s_lo["score"], f'{s_mid["score"]} vs {s_lo["score"]}')
ck("T5.3 无关分 < 0.35", s_lo["score"] < 0.35, s_lo["score"])
ck("T5.4 分数不超 1", s_hi["score"] <= 1.0, s_hi["score"])

print("T6 加权公式与依据字段")
s = score_pair("金網フェンス", T_FENCE)
exp = 0.60 * s["coverage"] + 0.25 * s["contig"] + 0.15 * s["lcs"]
ck("T6.1 三个成分齐备", all(k in s for k in ("coverage", "contig", "lcs")), list(s))
ck("T6.2 公式可复原", abs(round(exp, 3) - s["score"]) <= 0.001, f"{exp} vs {s['score']}")
ck("T6.3 给出命中片段", s["hit_fragment"] != "", s["hit_fragment"])
ck("T6.4 拆词有保留", len(s["query_tokens"]) >= 2, s["query_tokens"])

print("T7 空输入不炸")
ck("T7.1 空词", score_pair("", T_FENCE)["score"] == 0.0)
ck("T7.2 空标题", score_pair("金網", "")["score"] == 0.0)
ck("T7.3 两边都空", score_pair("", "")["score"] == 0.0)

print(f"\n{'='*46}\nT 段：{PASS} PASS / {FAIL} FAIL\n{'='*46}")
sys.exit(1 if FAIL else 0)
