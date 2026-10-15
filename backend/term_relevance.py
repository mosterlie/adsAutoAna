# -*- coding: utf-8 -*-
"""搜索词 × SKU 标题 相关性打分。

纯规则、可复现、可解释：每个分数都能拆到「哪些词命中了标题的哪一段」。
用于广告运营判断：高花费搜索词到底跟这个 SKU 是不是一回事。

打分三成分（0~1）：
  1) coverage  查询词内容词的加权命中率（主指标，权重 0.60）
               完全等同标题词 =1.0；能在标题里找到 =0.85×字符覆盖率；字符二元组 Dice≥0.5 的近似 =0.5
  2) contig    查询整串的字符有多少比例能在标题里找到连续片段（权重 0.25）
               用 greedy 最长匹配，不被标题长度稀释（dice 会因为标题长而虚低）
  3) lcs       查询整串与标题的最长公共子串 / 查询长度（权重 0.15）

分档：
  >=0.85 高度相关 | >=0.60 相关 | >=0.35 弱相关 | <0.35 不相关
"""
import re
import unicodedata

from janome.tokenizer import Tokenizer

_TOKENIZER = Tokenizer()

# 只保留这些词性参与匹配（助詞/助動詞/記号等虚词会污染分母）
KEEP_POS = ("名詞", "動詞", "形容詞", "形状詞", "未知語", "カスタム名詞")
DROP_POS = ("非自立", "代名詞", "数詞")  # 数词单独保留在 _NUM 里判断

_KEEP_ALNUM = re.compile(r"^[a-z0-9]+$")
_NUM = re.compile(r"^\d+(\.\d+)?$")

# 假名区里其实是标点的字符，必须剔掉；「ー」(U+30FC) 是构词字符，保留
_KANA_PUNCT = {"・", "゠", "ヽ", "ヾ", "ゝ", "ゞ", "゛", "゜", "ゟ", "ヿ"}


def normalize(s: str) -> str:
    """NFKC + 小写 + 去掉空白与标点，仅留假名/汉字/字母/数字。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    out = []
    for ch in s:
        if ch in _KANA_PUNCT:
            continue
        if ch.isalnum() or "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
    return "".join(out)


def tokens(s: str):
    """返回内容词列表（去重保序）。"""
    if not s:
        return []
    seen, out = set(), []
    for tk in _TOKENIZER.tokenize(str(s)):
        pos = tk.part_of_speech.split(",")
        if pos[0] not in KEEP_POS:
            continue
        if any(p in pos for p in DROP_POS):
            continue
        w = normalize(tk.surface)
        if not w:
            continue
        # 单个假名 / 单个拉丁字母噪声太大
        if len(w) == 1 and not ("\u4e00" <= w <= "\u9fff"):
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _bigrams(s: str):
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def dice(a: str, b: str) -> float:
    """字符二元组 Dice 系数。"""
    if not a or not b:
        return 0.0
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return 2 * len(A & B) / (len(A) + len(B))


def best_window_dice(needle: str, hay: str) -> float:
    """needle 在 hay 中滑动取最大 Dice（hay 可能很长，按 needle 长度开窗）。"""
    if not needle or not hay:
        return 0.0
    n = len(needle)
    if len(hay) <= n:
        return dice(needle, hay)
    best = 0.0
    step = max(1, n // 2)
    for i in range(0, len(hay) - n + 1, step):
        best = max(best, dice(needle, hay[i:i + n]))
        if best >= 0.9:
            break
    return best


def covered_ratio(needle: str, hay: str):
    """needle 有多少比例的字符能被 hay 里的连续片段覆盖（greedy 最长匹配）。

    解决 janome 把片假名复合词并成一个词的问题：
    'ワイヤーメッシュフェンス' 在标题里若拆成 'ワイヤーメッシュ'+'フェンス' 出现，
    也能拿到 1.0，而不是因为整体不等而判为不相关。

    返回 (覆盖率, 命中的片段列表)。
    """
    if not needle or not hay:
        return 0.0, []
    hit = [False] * len(needle)
    frags = []
    i = 0
    while i < len(needle):
        best_len = 0
        for j in range(len(needle), i, -1):
            if needle[i:j] in hay:
                best_len = j - i
                break
        if best_len >= 1:
            for k in range(i, i + best_len):
                hit[k] = True
            frags.append(needle[i:i + best_len])
            i += best_len
        else:
            i += 1
    return sum(hit) / len(needle), frags


def lcs_ratio(a: str, b: str) -> float:
    """最长公共连续子串长度 / len(a)。"""
    if not a or not b:
        return 0.0
    # 只需长度，用滚动 DP
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best / len(a)


def score_pair(query: str, title: str, extra_text: str = "") -> dict:
    """给一个「搜索词 vs 标题」打分，并返回完整依据。

    extra_text: 可选的补充文本（如 SKU 名/子体差异文案），仅参与命中判定，
                不参与 dice/lcs，避免长文本把相似度稀释。
    """
    nq, nt = normalize(query), normalize(title)
    ne = normalize(extra_text)
    q_toks, t_toks = tokens(query), tokens(title)
    t_set = set(t_toks)
    hay = nt + ("|" + ne if ne else "")

    matched, partial, missing = [], [], []
    wsum = 0.0
    for q in q_toks:
        if q in t_set:
            matched.append((q, "exact")); wsum += 1.0
            continue
        cr, frags = covered_ratio(q, hay)
        if cr >= 0.999 and frags:
            matched.append((q, f"标题片段「{'/'.join(frags)}」")); wsum += 0.85
            continue
        if cr >= 0.5:
            wsum += 0.85 * cr
            partial.append((q, f"部分命中 {int(cr*100)}%（{'+'.join(frags)}）"))
            continue
        d = best_window_dice(q, nt)
        if d >= 0.5:
            partial.append((q, f"近似 Dice={d:.2f}")); wsum += 0.5 * d
        else:
            missing.append((q, f"Dice={d:.2f}"))
    coverage = (wsum / len(q_toks)) if q_toks else 0.0

    contig, whole_frags = covered_ratio(nq, nt)
    d_all = dice(nq, nt)
    l_all = lcs_ratio(nq, nt)

    score = 0.60 * coverage + 0.25 * contig + 0.15 * l_all
    if score >= 0.85:
        level = "高度相关"
    elif score >= 0.60:
        level = "相关"
    elif score >= 0.35:
        level = "弱相关"
    else:
        level = "不相关"

    # 命中的标题片段（最长公共子串，>=2 才展示）
    frag = _longest_common(nq, nt)
    return {
        "score": round(score, 3),
        "level": level,
        "coverage": round(coverage, 3),
        "contig": round(contig, 3),
        "dice": round(d_all, 3),
        "lcs": round(l_all, 3),
        "query_tokens": q_toks,
        "title_tokens": t_toks,
        "matched": matched,
        "partial": partial,
        "missing": missing,
        "hit_fragment": frag if len(frag) >= 2 else "",
    }


def _longest_common(a: str, b: str) -> str:
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best_len, best_end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len, best_end = cur[j], i
        prev = cur
    return a[best_end - best_len:best_end]
