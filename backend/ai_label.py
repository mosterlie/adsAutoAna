# -*- coding: utf-8 -*-
"""父 SKU/ASIN 品名解析: 商品标题 → 中文短语(≤5 个词)

支持两家 provider, 可在页面「配置」菜单里切换:
  * ollama    —— 本地 Ollama(/api/generate)
  * workbuddy —— workbuddy 在线网关, 统一走 backend/vendor/workbuddy 的**透传模式**
                 (mode="passthrough", 提示词原样下发), 与相关性语义判断同一个通道;
                 参数(地址/Key/模型/超时)统一由 backend/wb_config.py 解析,
                 主模型不可用时按配置依次回退到其它模型

其它要点:
- 结果按 ASIN 缓存到 ol_ai_labels 表; 标题变了(哈希不同)才重新解析
- 一页可能有几十个 ASIN, 逐个跑模型较慢 → 后台线程串行生成, 前端轮询结果
- 本机 Ollama 强制绕过系统代理 (否则 Clash 之类会把请求拦成 502; 网关侧由客户端处理)
- 网关的失败原因经常被吞成 HTTP 500, 换模型重试才有效, 所以「模型不存在/未授权/
  无权限」也一并按可回退处理(见 _retryable)
"""
import hashlib
import json
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import config
from backend import database as db
from backend import wb_config
from backend.vendor.workbuddy import WorkBuddyError

_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}

# 全绕过系统代理: 本地 Ollama 强制直连
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SETTING_KEY = wb_config.AI_SETTING_KEY      # app_settings 里的键
PROVIDERS = ("ollama", "workbuddy")

_PROMPT = (
    "你是电商标题解析助手。请判断下面这个亚马逊商品标题里，"
    "这个商品**本身是什么**（品类/主体），用不超过{maxw}个中文词回答。\n"
    "要求：只描述商品本体，不要罗列用途/场景/卖点；只输出短语本身，"
    "不要标点、不要引号、不要解释。\n\n"
    "示例：\n"
    "标题：水道ホースリール 巻き取りやすい 洗車 庭 園芸 農場 屋外\n"
    "短语：水管卷盘\n"
    "标题：四角型工具バケツ 電工バケツ 荷揚げ袋 道具袋 帆布製 吊り下げフック付き\n"
    "短语：帆布工具袋\n"
    "标题：バクテリア培養リング ろ材 水槽用濾材 多孔質構造 魚缸用 水槽用品\n"
    "短语：细菌培养环\n\n"
    "标题：{title}\n"
    "短语："
)


class ProviderDown(RuntimeError):
    """上游不可用(5xx/超时) → 可以换下一个模型重试"""


# ---------------------------------------------------------------------------
# 配置: 默认值(config) + 页面里改过的(app_settings)
# ---------------------------------------------------------------------------
def default_config() -> Dict[str, Any]:
    return {
        "provider": config.AI_PROVIDER,
        "ollama_url": config.OLLAMA_URL,
        "ollama_model": config.OLLAMA_MODEL,
        "workbuddy_base": config.WORKBUDDY_BASE,
        "workbuddy_key": config.WORKBUDDY_KEY,
        "workbuddy_model": config.WORKBUDDY_MODEL,
        "workbuddy_fallback": list(config.WORKBUDDY_FALLBACK_MODELS),
    }


def get_config() -> Dict[str, Any]:
    cfg = default_config()
    try:
        saved = db.get_setting(SETTING_KEY) or {}
    except Exception:       # noqa: BLE001
        saved = {}
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in cfg and v not in (None, ""):
                cfg[k] = v
    if cfg.get("provider") not in PROVIDERS:
        cfg["provider"] = "ollama"
    # 底座模型统一以 wb_config 的解析为准(「配置管理」页改的也是它), 避免两处值打架
    cfg["workbuddy_model"] = wb_config.model("text")
    return cfg


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """保存配置(只认识的键会落库); Key 传 __KEEP__ 表示不改"""
    cfg = get_config()
    for k, v in (patch or {}).items():
        if k not in cfg:
            continue
        if k == "workbuddy_key" and v == "__KEEP__":
            continue
        if k == "workbuddy_fallback" and isinstance(v, str):
            v = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
        cfg[k] = v
    db.set_setting(SETTING_KEY, cfg)
    new_model = (patch or {}).get("workbuddy_model")
    if new_model:       # 同步到另一个存储, 让「配置管理」页也看到同一个模型
        wb_config.set_model(new_model, "text")
    return cfg


def public_config() -> Dict[str, Any]:
    """给前端的配置(不回传明文 Key)"""
    cfg = get_config()
    key = cfg.get("workbuddy_key") or ""
    out = {k: v for k, v in cfg.items() if k != "workbuddy_key"}
    out["workbuddy_key_set"] = bool(key)
    out["workbuddy_key_tail"] = key[-4:] if key else ""
    return out


# ---------------------------------------------------------------------------
# 模型调用: ollama / workbuddy
# ---------------------------------------------------------------------------
def _clean_label(text: str, max_words: int = 5) -> str:
    """把模型输出规整成短标签"""
    raw = (text or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    line = re.sub(r'^(短?语|答案|商品|标签)\s*[:：]\s*', "", line)
    line = line.strip('"\'“”‘’「」『』【】《》 \t')
    line = re.sub(r"[，。、,\.；;：:！!？?~～\-—|/\\]+", " ", line)
    tokens = [t for t in line.split() if t]
    if not tokens:
        return ""
    label = " ".join(tokens[:max_words]) if len(tokens) > 1 else tokens[0]
    return label[:24]


def _ollama_generate(prompt: str, cfg: Dict[str, Any]) -> str:
    url = (cfg.get("ollama_url") or config.OLLAMA_URL).rstrip("/") + "/api/generate"
    body = json.dumps({
        "model": cfg.get("ollama_model") or config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 32, "top_p": 0.9},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=config.OLLAMA_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise ProviderDown(f"Ollama 不可用: {str(e)[:80]}") from e
    return data.get("response") or ""


def _retryable(err: WorkBuddyError) -> bool:
    """判断该错误是否「换个模型就好」(决定要不要回退到下一个模型)

    网关的错误经常被包装成 HTTP 500, 里面可能是:
      * 业务码 11102「model [...] service info not found / only available for authorized users」
      * 「您暂无权限使用该模型」(本 Key 未开通该模型)
      * 裸的 internal_server_error(网关把上游原因吞了)
    这些以及 5xx/连不上都属于「换模型有效」; 其余(如 Key 无效)直接失败。
    """
    status = getattr(err, "status", None) or 0
    code = getattr(err, "code", None)
    msg = str(err)
    if status >= 500 or code in (11101, 11102):
        return True
    return "权限" in msg or "请求上游失败" in msg


def _workbuddy_once(model: str, prompt: str, cfg: Dict[str, Any]) -> str:
    """单模型单次调用(透传模式), 返回正文(推理内容由客户端丢弃)

    客户端由 backend/wb_config.py 统一构造; 这里把「配置」弹窗里(可能还没保存)的
    Key/地址/超时传进去, 好让「测试」按钮用表单值直接试。
    """
    try:
        client = wb_config.client(model, "text",
                                  timeout_sec=cfg.get("timeout") or None,
                                  key=cfg.get("workbuddy_key") or "",
                                  base=cfg.get("workbuddy_base") or "")
    except Exception as e:      # noqa: BLE001  配置不全(如没配 Key)
        raise RuntimeError(f"workbuddy 未就绪: {str(e)[:140]}") from e
    try:
        r = client.chat_raw(
            prompt,
            temperature=float(cfg.get("temperature") or config.WORKBUDDY_TEMPERATURE),
            max_tokens=int(cfg.get("max_tokens") or config.WORKBUDDY_MAX_TOKENS))
    except WorkBuddyError as e:
        # 保留原有的可回退语义: 该换模型的换模型, 不该换的直接失败
        raise (ProviderDown(str(e)[:200]) if _retryable(e)
               else RuntimeError(str(e)[:200])) from e
    except Exception as e:      # noqa: BLE001  其它(超时/解析等)按可回退处理
        raise ProviderDown(f"网关调用失败: {str(e)[:160]}") from e
    return (r.content or "").strip()


def _workbuddy_generate(prompt: str, cfg: Dict[str, Any]) -> str:
    key = wb_config.api_key(cfg.get("workbuddy_key") or "")
    if not key:
        raise RuntimeError("未配置 workbuddy API Key(在页面「配置」里填)")
    main = cfg.get("workbuddy_model") or wb_config.model("text")
    models = [main] + [m for m in (cfg.get("workbuddy_fallback") or [])
                       if m and m != main]
    errs: List[str] = []
    for m in models:
        try:
            return _workbuddy_once(m, prompt, cfg)
        except ProviderDown as e:
            errs.append(f"{m}: {e}")
    raise RuntimeError("workbuddy 调用失败(" + "; ".join(errs) + ")")


def _generate(title: str) -> Tuple[str, str]:
    """返回 (标签, 实际使用的模型名)"""
    cfg = get_config()
    prompt = _PROMPT.format(maxw=config.OLLAMA_LABEL_MAX_WORDS,
                            title=(title or "")[:300])
    if cfg.get("provider") == "workbuddy":
        raw = _workbuddy_generate(prompt, cfg)
        model = cfg.get("workbuddy_model") or config.WORKBUDDY_MODEL
    else:
        raw = _ollama_generate(prompt, cfg)
        model = cfg.get("ollama_model") or config.OLLAMA_MODEL
    return _clean_label(raw, config.OLLAMA_LABEL_MAX_WORDS), model


def available(provider: str = "") -> bool:
    """某 provider 现在能不能用(workbuddy 只校验有没有配 Key)"""
    cfg = get_config()
    p = provider or cfg.get("provider") or "ollama"
    if p == "workbuddy":
        return bool((cfg.get("workbuddy_key") or "").strip())
    try:
        url = (cfg.get("ollama_url") or config.OLLAMA_URL).rstrip("/") + "/api/tags"
        with _OPENER.open(url, timeout=3) as r:
            return r.status == 200
    except Exception:       # noqa: BLE001
        return False


def test_provider(patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """测试配置能否出结果(「配置」菜单的测试按钮): 不发缓存、不改配置"""
    cfg = get_config()
    for k, v in (patch or {}).items():
        if k in cfg and v not in (None, "", "__KEEP__"):
            cfg[k] = v
    prompt = _PROMPT.format(maxw=config.OLLAMA_LABEL_MAX_WORDS,
                            title="ガスコンロ プロパンガス 2口 卓上 家庭用 炎調節")
    try:
        if cfg.get("provider") == "workbuddy":
            raw = _workbuddy_generate(prompt, cfg)
            model = cfg.get("workbuddy_model")
        else:
            raw = _ollama_generate(prompt, cfg)
            model = cfg.get("ollama_model")
        return {"ok": True, "provider": cfg.get("provider"), "model": model,
                "raw": (raw or "")[:120], "sample": _clean_label(raw) or "(空)"}
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "provider": cfg.get("provider"),
                "model": cfg.get("workbuddy_model") if cfg.get("provider") == "workbuddy"
                else cfg.get("ollama_model"),
                "error": str(e)[:300]}


# ---------------------------------------------------------------------------
# 缓存 + 后台生成
# ---------------------------------------------------------------------------
def ensure_labels(asins: List[str], refresh: bool = False) -> Dict[str, Any]:
    """返回已缓存的标签; 缺失的启动后台生成"""
    items = [str(a).strip() for a in (asins or []) if str(a).strip()]
    items = list(dict.fromkeys(items))
    if not items:
        return {"labels": {}, "pending": [], "no_title": [], "job": None}

    titles = db.get_online_titles(items)              # asin -> {title, title_hash}
    cached = {} if refresh else db.get_ai_labels(items)

    labels: Dict[str, str] = {}
    pending: List[str] = []
    no_title: List[str] = []          # 库里没有该 ASIN 的标题 → 无从解析, 不用等
    for a in items:
        t = titles.get(a) or {}
        th = t.get("title_hash") or ""
        hit = cached.get(a)
        if hit and hit.get("label") and (not th or hit.get("title_hash") == th):
            labels[a] = hit["label"]
        elif t.get("title"):
            pending.append(a)
        else:
            no_title.append(a)

    job = None
    if pending:
        job = _start(items, pending, titles)
    return {"labels": labels, "pending": pending, "no_title": no_title, "job": job}


def _start(all_asins: List[str], pending: List[str],
           titles: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """启动后台生成

    key 必须用**请求的完整列表**(而不是待生成的子集), 否则前端拿整页
    ASIN 列表来查 job_state 会因 key 不一致永远查到 running=False。
    """
    key = _key_of(all_asins)
    with _LOCK:
        cur = JOBS.get(key)
        if cur and cur.get("running"):
            return {"running": True, "done": cur["done"], "total": cur["total"]}
        JOBS[key] = {"running": True, "done": 0, "total": len(pending),
                     "current": "", "ok": 0, "failed": 0, "error": ""}
    t = threading.Thread(target=_work, args=(key, pending, titles), daemon=True)
    t.start()
    return {"running": True, "done": 0, "total": len(pending)}


def _key_of(asins: List[str]) -> str:
    return "|".join(sorted(str(a).strip() for a in (asins or []) if str(a).strip()))


def job_state(asins: List[str]) -> Dict[str, Any]:
    with _LOCK:
        j = JOBS.get(_key_of(asins))
        return dict(j) if j else {"running": False, "done": 0, "total": 0}


def _work(key: str, asins: List[str], titles: Dict[str, Dict[str, str]]) -> None:
    for i, a in enumerate(asins, 1):
        title = (titles.get(a) or {}).get("title") or ""
        with _LOCK:
            if key in JOBS:
                JOBS[key]["current"] = a
        try:
            label, model = _generate(title)
            if label:
                db.save_ai_label(a, label, model,
                                 (titles.get(a) or {}).get("title_hash") or "")
                # 同步镜像到统一的 translations 表(kind=product_name):
                # 「翻译过的父SKU品名要落库」—— ol_ai_labels 之外再留一份统一台账
                db.save_translation("product_name", a, label, model)
                with _LOCK:
                    JOBS[key]["ok"] += 1
            else:
                with _LOCK:
                    JOBS[key]["failed"] += 1
        except Exception as e:      # noqa: BLE001
            with _LOCK:
                JOBS[key]["failed"] += 1
                JOBS[key]["error"] = str(e)[:140]
        with _LOCK:
            JOBS[key]["done"] = i
    with _LOCK:
        if key in JOBS:
            JOBS[key]["running"] = False
            JOBS[key]["current"] = ""


def title_hash(title: Optional[str]) -> str:
    return hashlib.md5((title or "").strip().encode("utf-8")).hexdigest()[:16]
