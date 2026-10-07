# -*- coding: utf-8 -*-
"""赛狐广告数据采集与展示 - 全局配置"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "ads.db")
COOKIE_PATH = os.path.join(DATA_DIR, "cookies.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 调试 Chrome (与 browser_toolkit 一致) —— 同步登录 Cookie / 抓取亚马逊都走它
CDP_URL = "http://127.0.0.1:9222"
# 抓取时若 CDP 连不上(浏览器被关了), 是否自动拉起调试 Chrome
CHROME_AUTO_START = True
CHROME_DEBUG_USER_DATA = r"C:\ChromeDebugUser"
CHROME_EXE_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
SELLFOX_PAGE = ("https://www.sellfox.com/cpc-vue3/web/cpc-vue3/ads-management"
                "?openfrom=clickmenu")

# 赛狐接口
ORIGIN = "https://www.sellfox.com"
REFERER = "https://www.sellfox.com/cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu"
GW_PREFIX = "/api/gw/sellfox/sellfox-cpc/api/sellfox/"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

# 采集默认参数
DEFAULT_START_DATE = "2026-08-01"     # 需求: 20260801 起
DEFAULT_END_DATE = None               # None = 今天
PAGE_SIZE = 200                       # 接口分页上限
AD_TYPES = ["sp", "sb", "sd"]         # 广告类型(子页签) 维度
MAX_PAGES = 200                       # 单页签单scope 最大翻页保护
REQUEST_TIMEOUT = 30
REQUEST_RETRY = 3

# 亚马逊搜索结果抓取 (浏览器加载, 非接口)
AMAZON_DOMAIN = "co.jp"               # 站点后缀 (amazon.co.jp)
AMAZON_CACHE_TTL_SEC = 1800           # 同一搜索词缓存有效期(秒): 期内直接返回库中结果
AMAZON_MIN_INTERVAL_SEC = 6           # 两次「真实抓取」之间的最小间隔(秒), 防触发风控
AMAZON_MAX_ITEMS = 0                  # 落库条数上限, 0=不限
AMAZON_PACE_JITTER_SEC = 3            # 最小间隔之上再叠加的随机抖动上下限(秒), 避免固定节拍

# 搜索页数据的「本地文件仓库」: 预抓取结果落这里; 页面查看优先读本地, 命中即不再抓取
# 可用环境变量 AMAZON_DATA_DIR 覆盖
AMAZON_DATA_DIR = os.environ.get("AMAZON_DATA_DIR") or r"E:\amazonData\adsData"

# 预抓取(批量)反爬节流: 相邻两个搜索词之间的随机等待区间(秒)
AMAZON_PREFETCH_GAP_MIN_SEC = 8
AMAZON_PREFETCH_GAP_MAX_SEC = 15
AMAZON_PREFETCH_BATCH_SIZE = 20       # 每抓 N 条后额外长休一次
AMAZON_PREFETCH_REST_SEC = 60         # 长休时长(秒)
AMAZON_PREFETCH_BLOCK_BACKOFF_SEC = 300   # 撞到验证码后的退避时长(秒)
AMAZON_PREFETCH_MAX_BLOCKED = 3       # 连续撞验证码达到该次数即中止本批次(保留已抓成果)
AMAZON_PREFETCH_MIN_CLICKS = 2        # 只预抓点击量达到该值的搜索词
AMAZON_PREFETCH_MAX_FAIL = 5          # 连续抓取失败达到该次数即中止本批次(多为网络不通)

# 竞品主图下载: 相邻两张之间的随机间隔(秒) + 单张超时
AMAZON_IMAGE_DELAY_MIN_SEC = 0.4
AMAZON_IMAGE_DELAY_MAX_SEC = 1.0
AMAZON_IMAGE_TIMEOUT_SEC = 20


def _detect_system_proxy() -> str:
    """读 Windows 系统代理设置

    浏览器(含调试 Chrome)自动走系统代理, 而 Python/requests 默认不走,
    两边不一致会导致"浏览器能打开、脚本连不上", 所以这里显式对齐。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if not enable or not server:
            return ""
        server = str(server).strip()
        if "=" in server:               # 形如 http=127.0.0.1:7892;https=127.0.0.1:7892
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or ""
        if not server:
            return ""
        return server if "://" in server else "http://" + server
    except Exception:       # noqa: BLE001  非 Windows / 无权限 → 直连
        return ""


# 亚马逊抓取用的代理: 优先环境变量 AMAZON_PROXY, 其次系统代理; 空字符串 = 直连
AMAZON_PROXY = (os.environ.get("AMAZON_PROXY")
                or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                or _detect_system_proxy())


def proxy_map():
    """requests 用的 proxies 参数(无代理返回 None)"""
    p = (AMAZON_PROXY or "").strip()
    return {"http": p, "https": p} if p else None

# 亚马逊商品页(评分/评分数/BSR 排名)抓取
# 评分与排名变化很慢, 缓存给长一点
AMAZON_PRODUCT_CACHE_TTL_SEC = 86400  # 同一 ASIN 缓存有效期(秒), 默认 1 天

# 本地 Ollama: 解析父 ASIN 标题, 提炼「这是什么」的短语(≤5 个词)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"
OLLAMA_TIMEOUT = 60                   # 单次生成超时(秒)
OLLAMA_LABEL_MAX_WORDS = 5            # 短语词数上限
