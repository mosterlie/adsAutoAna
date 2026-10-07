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

# 调试 Chrome (与 browser_toolkit 一致) —— 仅用于同步登录 Cookie
CDP_URL = "http://127.0.0.1:9222"
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

# 亚马逊商品页(评分/评分数/BSR 排名)抓取
# 评分与排名变化很慢, 缓存给长一点
AMAZON_PRODUCT_CACHE_TTL_SEC = 86400  # 同一 ASIN 缓存有效期(秒), 默认 1 天

# 本地 Ollama: 解析父 ASIN 标题, 提炼「这是什么」的短语(≤5 个词)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"
OLLAMA_TIMEOUT = 60                   # 单次生成超时(秒)
OLLAMA_LABEL_MAX_WORDS = 5            # 短语词数上限
