# -*- coding: utf-8 -*-
"""登录 Cookie 管理 (多账号): 落盘 / 读取 / 从调试 Chrome 同步

两个站点各支持多个账号 (同一站点同一浏览器 profile 只能保一个会话, 故按
"登录一个 → 同步一次" 的方式陆续累积; 每份 cookie 独立有效):

  - sellfox    赛狐   → adsauto/data/cookies.json
      {"version":2, "default":"<key>",
       "accounts":{"<key>":{"label":"A号","cookies":{...},"updated_at":"..."}}}
  - dianxiaomi 店小秘 → ERP 库 system_settings
      dxm_http_accounts = [{"key","label","cookie","ua","updated_at"}]  (cookie 为请求头字符串)
      dxm_http_default  = "<key>"
      同时兼容写入旧单值键 dxm_http_cookie/dxm_http_cookie_at/dxm_http_ua (指向默认账号)

账号 key = 会话 cookie 的指纹: 同一账号重复同步只更新时间, 不会重复建条目。
旧格式 (扁平 {name: value}) 读取时自动视为 "默认" 账号。
"""
import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import config

# 站点 → cookie domain 关键字
SITE_DOMAINS = {"sellfox": "sellfox.com", "dianxiaomi": "dianxiaomi.com"}
# 站点 → 展示名
SITE_LABELS = {"sellfox": "赛狐", "dianxiaomi": "店小秘"}
# 站点 → 判定"已登录"的关键 cookie (任一存在即视为有会话)
SITE_SESSION_KEYS = {"sellfox": ("sf_u",), "dianxiaomi": ("dxm_i", "dxm_t")}

# ERP 数据库: 与 adsauto 同级部署 (adsauto/../data/products.db)
ERP_DB_PATH = os.path.join(os.path.dirname(config.BASE_DIR), "data", "products.db")
ERP_ACCOUNTS_KEY = "dxm_http_accounts"
ERP_DEFAULT_KEY = "dxm_http_default"
ERP_LEGACY_KEYS = ("dxm_http_cookie", "dxm_http_cookie_at", "dxm_http_ua")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────── 通用工具 ─────────────────────────

def parse_cookie_string(raw: str) -> Dict[str, str]:
    """解析浏览器里复制的 'a=1; b=2' 形式 Cookie"""
    out: Dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())


def fingerprint(site: str, cookies: Dict[str, str]) -> str:
    """按会话 cookie 生成账号指纹 (同账号重复同步命中同一条记录)"""
    keys = SITE_SESSION_KEYS.get(site) or ()
    base = "|".join(str((cookies or {}).get(k, "")) for k in keys) \
        or json.dumps(cookies or {}, sort_keys=True)
    return hashlib.sha1(f"{site}:{base}".encode("utf-8")).hexdigest()[:10]


def has_session(site: str, cookies: Dict[str, str]) -> bool:
    keys = SITE_SESSION_KEYS.get(site) or ()
    return any((cookies or {}).get(k) for k in keys)


def _count(cookies: Dict[str, str]) -> int:
    return len(cookies or {})


def _account_brief(key: str, label: str, cookies: Dict[str, str],
                   updated_at: str, is_default: bool, site: str,
                   expires_at: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label or key, "count": _count(cookies),
            "updated_at": updated_at, "has_key": has_session(site, cookies),
            "is_default": bool(is_default), "expires_at": expires_at or ""}


# ───────────────────────── 赛狐: 本地文件 ─────────────────────────

def _load_store() -> Dict[str, Any]:
    """读 data/cookies.json, 兼容旧扁平格式"""
    empty = {"version": 2, "default": "", "accounts": {}}
    if not os.path.exists(config.COOKIE_PATH):
        return empty
    try:
        with open(config.COOKIE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return empty
    if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
        data.setdefault("version", 2)
        data.setdefault("default", "")
        return data
    if isinstance(data, dict) and data:      # 旧格式: {name: value}
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(config.COOKIE_PATH)) \
                .strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            mtime = _now()
        key = fingerprint("sellfox", data)
        return {"version": 2, "default": key,
                "accounts": {key: {"label": "默认", "cookies": data, "updated_at": mtime}}}
    return empty


def _save_store(store: Dict[str, Any]) -> None:
    with open(config.COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _sellfox_accounts() -> Tuple[List[Dict[str, Any]], str]:
    store = _load_store()
    accounts = store.get("accounts") or {}
    default = store.get("default") or ""
    items = [{"key": k, **v} for k, v in accounts.items()]
    return items, default


def _upsert_sellfox(cookies: Dict[str, str], label: str = "",
                    dedupe_label: bool = False, expires_at: str = "") -> Dict[str, Any]:
    store = _load_store()
    key = fingerprint("sellfox", cookies)
    accounts = store.setdefault("accounts", {})
    old = accounts.get(key) or {}
    entry = {"label": (label or old.get("label") or f"账号{len(accounts) + 1}"),
             "cookies": cookies, "updated_at": _now(),
             "expires_at": expires_at or old.get("expires_at") or ""}
    accounts[key] = entry
    # 同一账号重复登录会产生新的会话指纹 → 自动识别出相同账号名时清掉旧条目
    if dedupe_label and entry["label"]:
        for k in [k for k, v in accounts.items()
                  if k != key and v.get("label") == entry["label"]]:
            accounts.pop(k, None)
            if store.get("default") == k:
                store["default"] = key
    if not store.get("default"):
        store["default"] = key
    _save_store(store)
    return _account_brief(key, entry["label"], cookies, entry["updated_at"],
                          store["default"] == key, "sellfox", entry["expires_at"])


# ───────────────────────── 店小秘: ERP 数据库 ─────────────────────────

def _erp_read(keys: List[str]) -> Dict[str, str]:
    import sqlite3
    if not os.path.exists(ERP_DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(f"file:{ERP_DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            marks = ",".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT key, value_json FROM system_settings WHERE key IN ({marks})",
                list(keys)).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for k, v in rows:
        try:
            out[k] = str(json.loads(v or '""'))
        except Exception:
            out[k] = ""
    return out


def _erp_write(values: Dict[str, str]) -> str:
    """写 ERP 设置; 返回空串=成功, 否则错误信息"""
    import sqlite3
    if not os.path.exists(ERP_DB_PATH):
        return f"未找到 ERP 数据库: {ERP_DB_PATH}"
    now = _now()
    try:
        conn = sqlite3.connect(ERP_DB_PATH, timeout=10)
        try:
            for key, val in values.items():
                payload = json.dumps(val, ensure_ascii=False)
                cur = conn.execute(
                    "UPDATE system_settings SET value_json = ?, updated_at = ? WHERE key = ?",
                    (payload, now, key))
                if (cur.rowcount or 0) == 0:
                    conn.execute(
                        "INSERT INTO system_settings (key, value_json, updated_at) VALUES (?,?,?)",
                        (key, payload, now))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:      # noqa: BLE001
        return f"写入 ERP 失败: {e}"
    return ""


def _dxm_state() -> Tuple[List[Dict[str, Any]], str, str]:
    """读店小秘账号列表与默认账号; 无多账号结构时回落旧单值键"""
    kv = _erp_read([ERP_ACCOUNTS_KEY, ERP_DEFAULT_KEY, *ERP_LEGACY_KEYS])
    accounts: List[Dict[str, Any]] = []
    try:
        raw = kv.get(ERP_ACCOUNTS_KEY) or "[]"
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list):
            accounts = [a for a in parsed if isinstance(a, dict) and a.get("key")]
    except Exception:
        accounts = []
    default = kv.get(ERP_DEFAULT_KEY) or ""
    if not accounts and kv.get("dxm_http_cookie"):
        legacy_cookies = parse_cookie_string(kv["dxm_http_cookie"])
        if legacy_cookies:
            key = fingerprint("dianxiaomi", legacy_cookies)
            try:
                ts = int(kv.get("dxm_http_cookie_at") or 0)
                ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            except Exception:
                ts_str = ""
            accounts = [{"key": key, "label": "默认", "cookie": kv["dxm_http_cookie"],
                         "ua": kv.get("dxm_http_ua") or "", "updated_at": ts_str}]
            default = key
    return accounts, default, ""


def _dxm_write(accounts: List[Dict[str, Any]], default: str) -> str:
    """回写账号列表; 同时把默认账号同步到旧单值键 (兼容旧版 ERP)"""
    default_acc = next((a for a in accounts if a["key"] == default), None)
    values = {
        ERP_ACCOUNTS_KEY: json.dumps(accounts, ensure_ascii=False),
        ERP_DEFAULT_KEY: default,
    }
    if default_acc:
        try:
            ts = datetime.strptime(default_acc.get("updated_at") or "", "%Y-%m-%d %H:%M:%S")
            values["dxm_http_cookie_at"] = str(int(ts.timestamp()))
        except Exception:
            values["dxm_http_cookie_at"] = str(int(datetime.now().timestamp()))
        values["dxm_http_cookie"] = default_acc.get("cookie") or ""
        values["dxm_http_ua"] = default_acc.get("ua") or ""
    return _erp_write(values)


def _upsert_dxm(cookies: Dict[str, str], label: str = "", ua: str = "",
                dedupe_label: bool = False, expires_at: str = "") -> Dict[str, Any]:
    accounts, default, _err = _dxm_state()
    key = fingerprint("dianxiaomi", cookies)
    header = cookie_header(cookies)
    old = next((a for a in accounts if a["key"] == key), None)
    entry = {"key": key,
             "label": (label or (old or {}).get("label") or f"账号{len(accounts) + 1}"),
             "cookie": header, "ua": ua or (old or {}).get("ua") or "",
             "updated_at": _now(),
             "expires_at": expires_at or (old or {}).get("expires_at") or ""}
    if old:
        accounts = [entry if a["key"] == key else a for a in accounts]
    else:
        accounts.append(entry)
    # 同一账号重复登录: 自动识别出相同账号名时清掉旧条目
    if dedupe_label and entry["label"]:
        dropped = [a["key"] for a in accounts
                   if a["key"] != key and a.get("label") == entry["label"]]
        if dropped:
            accounts = [a for a in accounts if a["key"] not in dropped]
            if default in dropped:
                default = key
    if not default:
        default = key
    err = _dxm_write(accounts, default)
    if err:
        return {"key": key, "label": entry["label"], "count": _count(cookies),
                "updated_at": entry["updated_at"], "has_key": has_session("dianxiaomi", cookies),
                "is_default": default == key, "error": err,
                "expires_at": entry.get("expires_at", "")}
    return _account_brief(key, entry["label"], cookies, entry["updated_at"],
                          default == key, "dianxiaomi", entry.get("expires_at", ""))


# ───────────────────────── 对外: 账号管理 ─────────────────────────

def list_accounts(site: str) -> Dict[str, Any]:
    """账号列表: {"accounts":[{key,label,count,updated_at,has_key,is_default}], "default": key}"""
    if site == "sellfox":
        items, default = _sellfox_accounts()
        return {"accounts": [
            _account_brief(a["key"], a.get("label", ""), a.get("cookies") or {},
                           a.get("updated_at", ""), a["key"] == default, site,
                           a.get("expires_at", ""))
            for a in items], "default": default}
    if site == "dianxiaomi":
        accounts, default, err = _dxm_state()
        return {"accounts": [
            _account_brief(a["key"], a.get("label", ""),
                           parse_cookie_string(a.get("cookie") or ""),
                           a.get("updated_at", ""), a["key"] == default, site,
                           a.get("expires_at", ""))
            for a in accounts], "default": default, "error": err}
    return {"accounts": [], "default": ""}


def get_account(site: str, key: str) -> Optional[Dict[str, Any]]:
    """单个账号的完整 cookie: {"key","label","cookies","updated_at","ua"}"""
    if site == "sellfox":
        items, _default = _sellfox_accounts()
        for a in items:
            if a["key"] == key:
                return {"key": key, "label": a.get("label", ""),
                        "cookies": a.get("cookies") or {},
                        "updated_at": a.get("updated_at", ""), "ua": "",
                        "expires_at": a.get("expires_at", "")}
        return None
    if site == "dianxiaomi":
        accounts, _default, _err = _dxm_state()
        for a in accounts:
            if a["key"] == key:
                return {"key": key, "label": a.get("label", ""),
                        "cookies": parse_cookie_string(a.get("cookie") or ""),
                        "updated_at": a.get("updated_at", ""), "ua": a.get("ua") or "",
                        "expires_at": a.get("expires_at", "")}
    return None


def get_default_account(site: str) -> Optional[Dict[str, Any]]:
    res = list_accounts(site)
    key = res.get("default") or ""
    if not key and res.get("accounts"):
        key = res["accounts"][0]["key"]
    return get_account(site, key) if key else None


def get_default_cookies(site: str) -> Dict[str, str]:
    acc = get_default_account(site)
    return (acc or {}).get("cookies") or {}


def upsert_account(site: str, cookies: Dict[str, str], label: str = "",
                   ua: str = "", dedupe_label: bool = False,
                   expires_at: str = "") -> Dict[str, Any]:
    """保存/更新一个账号 (按会话 cookie 指纹幂等)

    dedupe_label=True: 当账号名重复 (同一账号重新登录产生新会话) 时删除旧条目
    expires_at: 关键 cookie 的过期时间 (会话型 cookie 传空串)
    """
    if site == "sellfox":
        return _upsert_sellfox(cookies, label, dedupe_label, expires_at)
    if site == "dianxiaomi":
        return _upsert_dxm(cookies, label, ua, dedupe_label, expires_at)
    raise ValueError(f"未知站点: {site}")


def set_default_account(site: str, key: str) -> Dict[str, Any]:
    if site == "sellfox":
        store = _load_store()
        if key not in (store.get("accounts") or {}):
            raise ValueError("账号不存在")
        store["default"] = key
        _save_store(store)
        return {"ok": True, "default": key}
    if site == "dianxiaomi":
        accounts, _default, _err = _dxm_state()
        if key not in [a["key"] for a in accounts]:
            raise ValueError("账号不存在")
        err = _dxm_write(accounts, key)
        if err:
            raise RuntimeError(err)
        return {"ok": True, "default": key}
    raise ValueError(f"未知站点: {site}")


def rename_account(site: str, key: str, label: str) -> Dict[str, Any]:
    label = (label or "").strip()[:40]
    if not label:
        raise ValueError("备注不能为空")
    if site == "sellfox":
        store = _load_store()
        acc = (store.get("accounts") or {}).get(key)
        if not acc:
            raise ValueError("账号不存在")
        acc["label"] = label
        _save_store(store)
        return {"ok": True}
    if site == "dianxiaomi":
        accounts, default, _err = _dxm_state()
        hit = False
        for a in accounts:
            if a["key"] == key:
                a["label"] = label
                hit = True
        if not hit:
            raise ValueError("账号不存在")
        err = _dxm_write(accounts, default)
        if err:
            raise RuntimeError(err)
        return {"ok": True}
    raise ValueError(f"未知站点: {site}")


def delete_account(site: str, key: str) -> Dict[str, Any]:
    if site == "sellfox":
        store = _load_store()
        accounts = store.get("accounts") or {}
        if key not in accounts:
            raise ValueError("账号不存在")
        accounts.pop(key)
        if store.get("default") == key:
            store["default"] = next(iter(accounts), "")
        _save_store(store)
        return {"ok": True, "default": store.get("default", "")}
    if site == "dianxiaomi":
        accounts, default, _err = _dxm_state()
        if key not in [a["key"] for a in accounts]:
            raise ValueError("账号不存在")
        accounts = [a for a in accounts if a["key"] != key]
        if default == key:
            default = accounts[0]["key"] if accounts else ""
        err = _dxm_write(accounts, default)
        if err:
            raise RuntimeError(err)
        return {"ok": True, "default": default}
    raise ValueError(f"未知站点: {site}")


# ───────────────────────── 兼容旧调用 (默认账号) ─────────────────────────

def load_cookies() -> Optional[Dict[str, str]]:
    """默认账号的赛狐 cookie (旧调用方统一走这里)"""
    cookies = get_default_cookies("sellfox")
    return cookies or None


def save_cookies(cookies: Dict[str, str]) -> None:
    """把手动粘贴的 cookie 存为账号 (旧调用方兼容)"""
    if cookies:
        upsert_account("sellfox", cookies)


# ───────────────────────── 调试 Chrome 同步 ─────────────────────────

def _cdp_all_cookies(cdp_url: str = config.CDP_URL) -> Tuple[List[Dict[str, Any]], str]:
    """读调试 Chrome 的全部 cookie 与 UA (页面级 CDP Network.getAllCookies)"""
    import urllib.request

    import websocket

    base = cdp_url.rstrip("/")
    ua = ""
    try:
        info = json.load(urllib.request.urlopen(f"{base}/json/version", timeout=5))
        ua = info.get("User-Agent") or ""
    except Exception:
        pass
    pages = json.load(urllib.request.urlopen(f"{base}/json", timeout=5))
    target = next((p for p in pages if p.get("type") == "page"
                   and "sellfox.com" in (p.get("url") or "")), None) \
        or next((p for p in pages if p.get("type") == "page"
                 and p.get("webSocketDebuggerUrl")), None)
    temp_id = ""
    if not target:
        req = urllib.request.Request(f"{base}/json/new?about:blank", method="PUT")
        target = json.load(urllib.request.urlopen(req, timeout=5))
        temp_id = target.get("id") or ""
    ws_url = (target or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("调试 Chrome 没有可用页面，请启动 9222 并登录赛狐")
    cookies: List[Dict[str, Any]] = []
    try:
        ws = websocket.create_connection(ws_url, max_size=16 * 1024 * 1024, timeout=10)
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            for _ in range(30):
                msg = json.loads(ws.recv())
                if msg.get("id") != 1:
                    continue
                if msg.get("error"):
                    raise RuntimeError(f"读取 Cookie 失败: {msg['error']}")
                cookies = ((msg.get("result") or {}).get("cookies")) or []
                break
        finally:
            try:
                ws.close()
            except Exception:
                pass
    finally:
        if temp_id:
            try:
                urllib.request.urlopen(f"{base}/json/close/{temp_id}", timeout=5).read()
            except Exception:
                pass
    return cookies, ua


def _filter_domain(raw: List[Dict[str, Any]], domain: str) -> Dict[str, str]:
    return {str(c.get("name")): str(c.get("value") or "")
            for c in (raw or [])
            if c.get("name") and domain in str(c.get("domain") or "")}


def _expiry_for(raw: List[Dict[str, Any]], domain: str, names) -> str:
    """关键 cookie 的最晚过期时间; 会话型 cookie(无 expires) 返回空串"""
    exp = 0.0
    keys = set(names or ())
    for c in raw or []:
        if domain not in str(c.get("domain") or ""):
            continue
        if keys and str(c.get("name")) not in keys:
            continue
        try:
            e = float(c.get("expires") or -1)
        except (TypeError, ValueError):
            continue
        if e > 0:
            exp = max(exp, e)
    return datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S") if exp else ""


def sync_sites_from_browser(sites: Optional[List[str]] = None,
                            cdp_url: str = config.CDP_URL
                            ) -> Tuple[Dict[str, Dict[str, str]], str, Dict[str, str]]:
    """一次读取调试 Chrome, 按站点分流

    返回 ({site: {name: value}}, UA, {site: 关键cookie过期时间})
    """
    raw, ua = _cdp_all_cookies(cdp_url)
    out: Dict[str, Dict[str, str]] = {}
    expiries: Dict[str, str] = {}
    for site in (sites or list(SITE_DOMAINS)):
        domain = SITE_DOMAINS.get(site)
        if domain:
            out[site] = _filter_domain(raw, domain)
            exp = _expiry_for(raw, domain, SITE_SESSION_KEYS.get(site, ()))
            if exp:
                expiries[site] = exp
    return out, ua, expiries


def sync_from_browser(cdp_url: str = config.CDP_URL) -> Dict[str, str]:
    """只同步赛狐并落盘 (兼容旧调用)"""
    sites, _ua, exp = sync_sites_from_browser(["sellfox"], cdp_url)
    out = sites.get("sellfox") or {}
    if not out:
        raise RuntimeError("调试 Chrome 未读取到赛狐 Cookie，请先在调试 Chrome 登录赛狐")
    if not has_session("sellfox", out):
        raise RuntimeError("调试 Chrome 当前未登录赛狐（缺少 sf_u），请先在调试 Chrome 登录赛狐")
    upsert_account("sellfox", out, expires_at=exp.get("sellfox", ""))
    return out


def open_debug_browser(open_sites: bool = True) -> Dict[str, Any]:
    """打开带 9222 调试端口的 Chrome (独立 profile)

    - 未运行: 复用 amazon.ensure_cdp 拉起 (与其他抓取共用同一 profile/端口)
    - 已运行: 直接复用, 不重复启动
    - 两个站点页面已存在时不再重复开标签, 只补开缺失的那个
    """
    import urllib.parse
    import urllib.request

    from backend.amazon import cdp_alive, ensure_cdp, find_chrome

    base = config.CDP_URL.rstrip("/")
    started = False
    if not cdp_alive(base):
        if not find_chrome():
            return {"ok": False, "running": False, "started": False,
                    "message": "未找到 Chrome，可在 config.CHROME_EXE_CANDIDATES 配置路径"}
        if not ensure_cdp(config.CDP_URL, wait=20):
            return {"ok": False, "running": False, "started": False,
                    "message": "已尝试启动调试 Chrome，但 9222 未就绪，请手动检查"}
        started = True

    opened: List[str] = []
    if open_sites:
        try:
            pages = json.load(urllib.request.urlopen(f"{base}/json", timeout=5))
            open_urls = [str(p.get("url") or "") for p in pages]
        except Exception:       # noqa: BLE001
            open_urls = []
        for url in ("https://www.sellfox.com", "https://www.dianxiaomi.com"):
            host = url.split("//")[-1]
            if any(host in u for u in open_urls):
                continue
            try:
                req = urllib.request.Request(
                    f"{base}/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
                urllib.request.urlopen(req, timeout=5).read()
                opened.append(host)
            except Exception:   # noqa: BLE001  开页面失败不影响浏览器本身
                pass

    head = "调试 Chrome 已启动" if started else "调试 Chrome 已在运行 (9222)"
    tail = f"，已打开 {'/'.join(opened)}" if opened else ""
    return {"ok": True, "running": True, "started": started, "message": head + tail}


# ───────────────────────── 自动识别登录账号 ─────────────────────────
# 站点 → (用户信息接口, Referer)
ACCOUNT_APIS = {
    "sellfox": ("https://www.sellfox.com/api/account/user.json", "https://www.sellfox.com/"),
    "dianxiaomi": ("https://www.dianxiaomi.com/api/userIn.json",
                   "https://www.dianxiaomi.com/web/home"),
}


def detect_account_label(site: str, cookies: Dict[str, str], timeout: int = 8) -> str:
    """用 cookie 调站点「当前用户」接口, 返回可读的账号名 (失败返回空串)

    - 赛狐: /api/account/user.json → data.user.nickname / accountName
    - 店小秘: /api/userIn.json → data.account
    """
    url_ref = ACCOUNT_APIS.get(site)
    if not url_ref or not cookies:
        return ""
    url, referer = url_ref
    import requests
    try:
        r = requests.get(url, headers={
            "Cookie": cookie_header(cookies),
            "User-Agent": config.USER_AGENT,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        }, timeout=timeout)
        body = r.json()
    except Exception:       # noqa: BLE001  网络/解析失败按"未识别"处理
        return ""
    if str(body.get("code")) not in ("0", "200"):
        return ""
    data = body.get("data") or {}
    if site == "sellfox":
        # 赛狐登录账号就是手机号 (user.mobile), 优先用它; 逐级回退唯一账号名/昵称/ID
        user = data.get("user") or {}
        return str(user.get("mobile") or user.get("accountName")
                   or user.get("nickname") or user.get("id") or "").strip()
    if site == "dianxiaomi":
        return str(data.get("account") or data.get("id") or "").strip()
    return ""
