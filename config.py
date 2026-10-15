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

# ---- WorkBuddy 大模型 API (腾讯 copilot): 全项目共用这一套配置 ----
# 调用通道统一走 backend/vendor/workbuddy 的**透传模式**(mode="passthrough"),
# 由 backend/wb_config.py 统一取参; 品名解析 / 搜索词翻译 / 相关性语义判断 共用:
#   地址 + Key + 底座模型 + 超时温度  —— 都是同一份, 不再各功能各写一套默认值。
# Key 优先级: 环境变量 WORKBUDDY_API_KEY > 项目「配置」里已存的 workbuddy key
#             > 下面这个 .env(开发机旧路径; 文件不存在会被自动忽略, 不影响跨平台)
WB_ENV_FILE = os.environ.get("WORKBUDDY_ENV_FILE") or \
    "/Users/gx/Desktop/mypro/testApi/.env"
# 网关地址(OpenAI 兼容, 仅接受流式; 客户端内部已按流式聚合成完整结果)
WORKBUDDY_BASE = os.environ.get("WORKBUDDY_BASE") or \
    "https://copilot.tencent.com/v2/chat/completions"
WORKBUDDY_KEY = os.environ.get("WORKBUDDY_KEY") or ""     # 页面「配置」里填
# 底座模型默认值: 运行时可改(「配置管理」页 / 「配置」弹窗 改的是同一个值)
# 选 deepseek-v4.1-flash: 非推理型, 单次约 1~1.5 秒(相关性批量分析要跑几百条);
# glm-5.3-flash 是推理型, 约 8 秒/次, 只在需要更强理解时临时切换。
WORKBUDDY_MODEL = "deepseek-v4.1-flash"
# 主模型不可用时依次回退(2026-10-08 实测网关可用: glm-5.3-flash / deepseek-v4.1-flash
# / deepseek-v4-flash / glm-5.3 / kimi-k3 / minimax-m3 / hy3 ... 共 18 个)
WORKBUDDY_FALLBACK_MODELS = ["glm-5.3-flash", "deepseek-v4-flash"]
WORKBUDDY_TIMEOUT = 90                # 单次请求超时(秒)
WORKBUDDY_TEMPERATURE = 0.2           # 采样温度(品名/翻译/判定共用)
# 输出上限: 推理型模型(glm-5.3 / hy4 等)会先把 token 花在 reasoning_content 上,
# 给小了正文就是空的(实测 64 时 glm-5.3 正文为空); 非推理模型用不满, 不影响耗时
WORKBUDDY_MAX_TOKENS = 1024

# ---- 以下为历史命名(WB_*), 一律**派生**自上面这套, 不要再单独改这里 ----
WB_MODEL = WORKBUDDY_MODEL                 # 别名: 底座模型
WB_TIMEOUT = WORKBUDDY_TIMEOUT             # 别名: 超时
WB_TEMPERATURE = WORKBUDDY_TEMPERATURE     # 别名: 温度
# 相关性判定是「结构化小输出」, 用更小的上限更快(不影响品名/翻译的长输出)
WB_MAX_TOKENS = 200

# WorkBuddy 视觉模型: 深度检测(图片相似度)用, 可在「配置管理」页切换
WB_VISION_MODEL = "glm-5v-turbo"
WB_VISION_MAX_TOKENS = 200
WB_VISION_TIMEOUT = 90                                 # 视觉调用较慢(2~40s/次)
WB_VISION_IMG_MAX = 512                                # 送入模型前图片最长边(px)
WB_VISION_IMG_QUALITY = 78                             # JPEG 质量
WB_VISION_WORKERS = 5                                  # 深度检测并发数

# ---- 品名解析走哪家: 本地 Ollama 还是 workbuddy 在线网关 ----
# 页面「配置」菜单里可切换(存 app_settings, 优先级高于这里的默认值)
AI_PROVIDER = "workbuddy"             # ollama | workbuddy
