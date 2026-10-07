# -*- coding: utf-8 -*-
"""亚马逊搜索结果页: 用真实浏览器加载页面 -> 从渲染后的 DOM 抽取数据 -> 落库

约束与设计:
  * **不调用任何接口**。亚马逊对非浏览器请求返回 503, 需求要求"页面加载后再取"。
  * 优先连接现成调试 Chrome(CDP), 失败则自起 Chromium; DOM 渲染完成后在页面上下文抽取。
  * **频率控制**: (a) 同一搜索词在 TTL 内直接返回库中缓存, 不再打开浏览器;
                (b) 两次「真实抓取」之间强制最小间隔, 防触发风控;
                (c) 全局互斥锁串行化, 避免并发抢浏览器。
  * **落库**: 每次真实抓取写入 amz_searches(元信息) + amz_results(明细)。
"""
import os
import random
import socket
import subprocess
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import config
from backend import amazon_store
from backend import database as db

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

MARKET: Dict[str, str] = {
    "co.jp": "https://www.amazon.co.jp",
    "com": "https://www.amazon.com",
    "de": "https://www.amazon.de",
    "co.uk": "https://www.amazon.co.uk",
}

# 同一时刻只允许一个抓取任务 (共用浏览器/避免相互打断)
_LOCK = threading.Lock()
# 上一次「真实抓取」的时间戳 (用于最小间隔限流)
_LAST_REAL_FETCH = 0.0

# 在页面上下文执行: 从「渲染后」的 DOM 抽取搜索结果
#
# 设计原则: **有啥爬啥**。
#   (1) 已知字段用专门选择器精确抽取(尽力而为, 缺就是空字符串, 不抛错);
#   (2) 再对卡片 innerText 做一次通用扫描, 把没被识别但**有信息量**的行收进
#       `extras`(带关键词归类), 这样亚马逊改版/新增标记时不会漏;
#   (3) 另存 `raw_text`(卡片全文, 截断) 作为最终兜底。
# 亚马逊日文站/英文站的关键词都兼容(co.jp 为主, 但 en/de 也不至于全空)。
EXTRACT_JS = r"""
() => {
  const out = [];
  const nodes = document.querySelectorAll(
    'div.s-main-slot div[data-component-type="s-search-result"]');

  const clean = (s) => (s || '').replace(/[\u00a0\u3000]/g, ' ').replace(/\s+/g, ' ').trim();
  const num = (s) => { const m = (s || '').replace(/[,\uFF0C]/g, '').match(/-?\d+(?:\.\d+)?/);
    return m ? parseFloat(m[0]) : null; };
  const int = (s) => { const m = (s || '').replace(/[,\uFF0C]/g, '').match(/\d+/);
    return m ? parseInt(m[0], 10) : null; };

  // 卡片内反复出现的无信息行(纯噪声), 不进 extras
  const NOISE = [
    /^スポンサー/, /^この広告は/, /^こちらへ$/, /^カートに入れる$/, /^今すぐ買う$/,
    /^詳細を見る$/, /^すべての出品を見る$/, /^在庫あり$/, /^売り切れ$/, /^在庫切れ$/,
    /^\d+(?:\.\d+)?つ星のうち/, /^[\d,.]+ stars?$/, /^\d+(?:\.\d+)?$/, /^\([\d,]+\)$/,
    /^[￥¥$€£]/, /^[\d,.]+\s*ポイント/, /^参考(?:価格)?[:：]?$/, /^Amazon\.co\.jp$/,
    /^無料配送$/, /^送料無料$/, /^配送料$/, /^新品$/, /^中古$/, /^Used$/, /^New$/,
    /^オプションを選択$/, /^その他(?:\s*\d+\s*点)?$/, /^-\s*$/, /^・$/, /^\+$/, /^[，,。.]+$/,
    /^カートに入れるためには$/, /^ログインしてください$/, /^価格、商品詳細ページ$/,
    /^獲得ポイント/, /^在庫状況/, /^出品者/, /^販売元$/,
  ];
  // 「有信息」的行 → 归类 (命中即打标签, 让 extras 可读)
  const TAG_RULES = [
    [/クーポン|Coupon/i, 'coupon'],
    [/タイムセール|Deal of the Day|日替わり/i, 'deal'],
    [/割引|OFF|off$|値引き|セール/i, 'discount'],
    [/ポイント|points?$/i, 'points'],
    [/配送|お届け|配達|delivery|Shipping|発送/i, 'delivery'],
    [/残り\s*\d+|在庫|Sold out|一時的に在庫切れ/i, 'stock'],
    [/過去\s*\d+\s*(?:か月|週間|日)|bought in the past|購入され/i, 'bought_recently'],
    [/こちらからもご購入|その他.*出品|used offers?|新品.*点|中古.*点/i, 'other_offers'],
    [/返品|保証|warranty|Returns?/i, 'returns'],
    [/サステナブル|省エネ|気候|Climate|Pledge|リサイクル/i, 'sustainability'],
    [/日本の中小企業|応援ストア|small business/i, 'program'],
    [/サイズ|色|スタイル|Style|Size|Colou?r/i, 'variant'],
    [/取り付け|設置|組立|assembly/i, 'assembly'],
    [/重量|耐荷重|寸法|サイズ\s*[:：]/i, 'spec'],
    [/限定|Limited/i, 'limited'],
    [/プライム|Prime/i, 'prime'],
    [/ベストセラー|Best Seller|ランキング|第\s*\d+\s*位/i, 'ranking'],
    [/Amazon|おすすめ|Choice/i, 'badge'],
  ];
  const tagOf = (line) => {
    for (const [re, tag] of TAG_RULES) { if (re.test(line)) return tag; }
    return '';
  };

  nodes.forEach((el, i) => {
    const asin = el.getAttribute('data-asin') || '';
    if (!asin) return;
    const q = (s) => el.querySelector(s);
    const txt = (s) => { const n = q(s); return n ? clean(n.textContent) : ''; };
    const attr = (s, a) => { const n = q(s); return n ? (n.getAttribute(a) || '') : ''; };
    const isBefore = (a, b) => {
      try { return !!(a.compareDocumentPosition(b) & 4); } catch (e) { return false; }
    };

    // ---------- 标题 / 链接 ----------
    const titleEl = q('h2 span') || q('[data-cy="title-recipe"] h2 span') || q('h2');
    const title = clean(titleEl && titleEl.textContent);
    const linkEl = q('h2 a') || q('a.a-link-normal.s-no-outline') ||
      q('a.a-link-normal.s-line-clamp-3');
    const hrefRaw = linkEl ? (linkEl.getAttribute('href') || '') : '';
    // 广告位 href 是 /sspa/click?… 跳转链, 统一用 ASIN 生成规范地址
    const url_href = hrefRaw ? (location.origin + hrefRaw).slice(0, 300) : '';
    const url = asin ? (location.origin + '/dp/' + asin) : '';

    // ---------- 图片: srcset 里取最大分辨率 ----------
    const imgEl = q('img.s-image') ||
      q('img[data-image-latency="s-product-image"]') || q('img');
    let image = imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') ||
      imgEl.getAttribute('data-image-src') || '') : '';
    let image_big = '';
    const srcset = imgEl ? (imgEl.getAttribute('srcset') || '') : '';
    if (srcset) {
      let best = 0;
      srcset.split(',').forEach((part) => {
        const seg = part.trim().split(/\s+/);
        const u = seg[0] || '';
        const d = parseFloat((seg[1] || '1x').replace('x', '')) || 1;
        if (u && d >= best) { best = d; image_big = u; }
      });
    }
    const image_alt = imgEl ? clean(imgEl.getAttribute('alt')) : '';

    // ---------- 品牌 / 副标题 ----------
    // 搜索卡里没有独立的品牌节点; 品牌藏在图片 alt 的前缀("スポンサー広告 - <品牌> <标题>")
    let brand = '';
    if (image_alt) {
      const alt = image_alt.replace(/^(?:スポンサー広告|スポンサー|Sponsored Ad|Sponsored)\s*[-–—]\s*/i, '');
      const head = title ? title.slice(0, 24) : '';
      const idx = head ? alt.indexOf(head) : -1;
      if (idx > 0) brand = clean(alt.slice(0, idx)).replace(/[-–—\s]+$/, '');
    }
    const diffEl = q('.title-differentiators');
    const differentiators = diffEl ? clean(diffEl.textContent) : '';
    if (!brand && titleEl) {           // 兜底: 标题上方那一行次级文本
      el.querySelectorAll('div.a-row.a-size-base.a-color-secondary, h2.a-size-mini, ' +
                         'span.a-size-base-plus.a-color-secondary, div.a-row.a-size-small')
        .forEach((n) => {
          if (brand || n.contains(titleEl) || (diffEl && (n === diffEl || n.contains(diffEl)))) return;
          if (!isBefore(n, titleEl)) return;
          const t = clean(n.textContent);
          if (t && t.length <= 90 && !/スポンサー|この広告は/.test(t)) brand = t;
        });
    }

    // ---------- 价格 ----------
    const price = txt('span.a-price span.a-offscreen') || txt('span.a-price');
    const price_symbol = txt('span.a-price-symbol');
    const price_whole = txt('span.a-price-whole');
    const price_fraction = txt('span.a-price-fraction');
    const list_price = txt('span.a-price.a-text-price span.a-offscreen') ||
      txt('span.basisPrice span.a-offscreen');
    const price_value = num(price);

    // ---------- 评分 / 评论数 ----------
    const rating = txt('span.a-icon-alt') || attr('i.a-icon-star-small span', 'aria-label');
    const parseRating = (s) => {
      if (!s) return null;
      let m = s.match(/うち\s*([\d.]+)/) || s.match(/^([\d.]+)\s*out of/i);
      if (!m) { const all = s.match(/[\d.]+/g); m = all && all.length ? [null, all[all.length - 1]] : null; }
      return m ? parseFloat(m[1]) : null;
    };
    const rating_value = parseRating(rating);
    // 评论数: 卡片里那个文本形如 "(1,234)" 的链接, 最稳
    let reviewRaw = '';
    const revEl = Array.prototype.find.call(el.querySelectorAll('a'), (a) =>
      /^\([\d,]+\)$/.test(clean(a.textContent)));
    if (revEl) reviewRaw = clean(revEl.textContent);
    if (!reviewRaw) {
      const rv = Array.prototype.find.call(
        el.querySelectorAll('span.a-size-base.s-underline-text, span.a-size-mini'),
        (n) => /^\([\d,]+\)$/.test(clean(n.textContent)));
      if (rv) reviewRaw = clean(rv.textContent);
    }
    if (!reviewRaw) reviewRaw = txt('span.a-size-base.s-underline-text');
    const reviews = reviewRaw;
    const review_count = int(reviewRaw);

    // ---------- 广告位 ----------
    const sponsored = !!(q('.s-sponsored-label-text') || q('.puis-sponsored-label-text') ||
      (el.textContent || '').indexOf('スポンサー') >= 0);
    let ad_label = '';
    if (sponsored) {
      ad_label = txt('.puis-sponsored-label-text') || txt('.s-sponsored-label-text') || 'スポンサー';
      // DOM 里标签是嵌套 span, textContent 会重复一份("スポンサースポンサー")
      if (ad_label.length % 2 === 0) {
        const half = ad_label.slice(0, ad_label.length / 2);
        if (half + half === ad_label) ad_label = half;
      }
    }

    // ---------- 徽标 / 榜单 ----------
    const badges = [];
    const pushBadge = (t) => {
      t = clean(t);
      if (!t || t.length > 70 || badges.indexOf(t) >= 0) return;
      if (/^(スポンサー|Amazon|おすすめ|詳細を見る)$/.test(t)) return;
      badges.push(t);
    };
    el.querySelectorAll('div.a-row.a-size-base span.s-line-clamp-1, ' +
                       'div.a-row.a-badge-region span.a-badge-label-inner, ' +
                       'div.a-row.a-badge-region span.a-badge-supplementary-text, ' +
                       'span.a-badge-supplementary-text, span.zg-badge-text, ' +
                       'span.a-size-medium-plus.a-color-base.a-text-bold')
      .forEach((n) => pushBadge(n.textContent));

    // ---------- 配送 ----------
    const delivery = txt('.udm-primary-delivery-message') ||
      txt('div.a-row.a-color-base.udm-primary-delivery-message');
    const delivery_secondary = txt('.udm-secondary-delivery-message');
    let delivery_date = '';
    const dm = el.querySelector('.udm-primary-delivery-message span.a-text-bold, ' +
                                '.udm-primary-delivery-message .a-text-bold');
    if (dm) delivery_date = clean(dm.textContent);
    const feeM = (delivery || '').match(/(?:配送料|送料|Shipping)\s*[￥¥$]?\s*[\d,]+/i);
    const delivery_fee = feeM ? feeM[0] : '';
    const free_shipping = /無料配送|送料無料|FREE delivery|Free delivery/i.test(delivery || '');

    // ---------- 从次级文本行里正则捞常用字段 ----------
    const rows = [];
    el.querySelectorAll('div.a-row.a-size-base.a-color-secondary, ' +
                       'span.a-size-base.a-color-price, div.a-row.a-color-base, ' +
                       'span.a-size-base.a-color-secondary').forEach((n) => {
      const t = clean(n.textContent);
      if (t && t.length <= 200 && rows.indexOf(t) < 0) rows.push(t);
    });
    const find = (re) => {
      for (const t of rows) { const m = t.match(re); if (m) return clean(m[0]); }
      return '';
    };
    const points = find(/[\d,]+\s*ポイント(?:\s*\([\d.]+%\))?/);
    const coupon = find(/[^\s。]{0,14}クーポン[^\s。]{0,20}/) || find(/クーポン[^\s。]{0,24}/);
    // 折扣: 只取「N%割引」所在的那一句, 并剥掉句首的积分片段
    const discount = (() => {
      for (const t of rows) {
        const m = t.match(/(\d+(?:\.\d+)?)\s*%\s*(?:割引|引き|還元|OFF|off)/);
        if (!m) continue;
        const segs = t.split(/(?<=。)/);
        const seg = segs.find((p) => p.indexOf(m[0]) >= 0) || t;
        return clean(seg.replace(/^[^、。]{0,24}?ポイント[^、。]{0,12}%\)?/, ""));
      }
      return "";
    })();
    const stock = find(/残り\s*\d+\s*点[^\s。]{0,16}/) || find(/在庫あり|残りわずか|一時的に在庫切れ/);
    const bought_recently = find(/過去\s*\d+\s*(?:か月|週間|日)[^\s。]{0,20}(?:購入|買わ)/) ||
      find(/[\d,]+\+?\s*bought in the past/i);
    const other_offers = find(/こちらからもご購入[^\n]{0,70}/) ||
      find(/その他\s*\d+\s*点の新品[^\n]{0,30}/);
    const return_policy = find(/[^\s。]{0,12}日以内[^\s。]{0,6}返品[^\s。]{0,16}/);

    // ---------- 购买按钮 / 视频 ----------
    const add_to_cart = !!(q('.atc-faceout-container') || q('.puis-atcb-add-container') ||
      q('input[name="submit.addToCart"]') || q('#add-to-cart-button') ||
      /カートに入れる/.test(el.textContent || ''));
    const has_video = !!q('video') || /動画もしくは動画撮影/.test(el.textContent || '');

    // ---------- 通用扫描: 有啥爬啥 ----------
    const known = [title, brand, differentiators, price, list_price, rating, reviews,
                   delivery, delivery_secondary, points, coupon, discount, stock,
                   bought_recently, other_offers, return_policy, image_alt]
      .filter(Boolean);
    const extras = [];
    (el.innerText || '').split('\n').forEach((raw) => {
      const line = clean(raw);
      if (!line || line.length > 140) return;
      if (line === title || badges.indexOf(line) >= 0) return;
      if (NOISE.some((re) => re.test(line))) return;
      let hit = false;
      for (const k of known) {
        if (k === line || (k.length > 8 && k.indexOf(line) >= 0)) { hit = true; break; }
      }
      if (hit) return;
      const tag = tagOf(line);
      if (!tag && line.length < 4) return;          // 太短又无归类 → 噪声
      if (!tag && !/[\u4e00-\u9faf\u30a0-\u30ff]/.test(line) && !/[A-Za-z]{3}/.test(line)) return;
      if (extras.some((x) => x.text === line)) return;
      extras.push({ tag: tag || 'other', text: line });
    });
    const raw_text = clean(el.innerText || '').slice(0, 1200);

    out.push({
      position: i + 1, asin: asin, title: title, url: url, url_href: url_href,
      image: image, image_big: image_big, image_alt: image_alt,
      price: price, price_value: price_value, price_symbol: price_symbol,
      price_whole: price_whole, price_fraction: price_fraction, list_price: list_price,
      rating: rating, rating_value: rating_value,
      reviews: reviews, review_count: review_count,
      sponsored: sponsored, ad_label: ad_label,
      brand: brand, differentiators: differentiators,
      badges: badges,
      points: points, coupon: coupon, discount: discount,
      delivery: delivery, delivery_date: delivery_date, delivery_fee: delivery_fee,
      free_shipping: free_shipping, delivery_secondary: delivery_secondary,
      stock: stock, bought_recently: bought_recently, other_offers: other_offers,
      return_policy: return_policy,
      add_to_cart: add_to_cart, has_video: has_video,
      extras: extras, raw_text: raw_text,
    });
  });
  const bodyText = (document.body ? document.body.innerText : '').slice(0, 4000);
  return { pageTitle: document.title || '', bodyText: bodyText, count: out.length, items: out };
}
"""


# 统计已渲染的结果卡数量 (用于等懒渲染完成, 别只拿到前几张)
COUNT_JS = ('() => document.querySelectorAll('
            '\'div.s-main-slot div[data-component-type="s-search-result"]\').length')


def search_url(query: str, domain: str = "co.jp") -> str:
    base = MARKET.get(domain, MARKET["co.jp"])
    return f"{base}/s?k={urllib.parse.quote(query)}&language=ja_JP"


# --- 供其它亚马逊抓取模块(如商品页)复用的节流设施 ---
def acquire_lock() -> None:
    """获取全局抓取锁 (与搜索抓取共用, 保证同一时刻只有一个亚马逊抓取)"""
    _LOCK.acquire()


def release_lock() -> None:
    _LOCK.release()


def pace(min_interval: Optional[float] = None) -> None:
    """真实抓取的最小间隔限流(含随机抖动); 搜索页与商品页共用同一节拍, 更安全

    固定节拍本身也是特征, 所以在最小间隔之上再叠加一段随机等待,
    让相邻两次访问的时间间隔没有规律可循。
    """
    global _LAST_REAL_FETCH
    iv = config.AMAZON_MIN_INTERVAL_SEC if min_interval is None else min_interval
    wait = iv + random.uniform(0, max(0.0, config.AMAZON_PACE_JITTER_SEC)) \
        - (time.time() - _LAST_REAL_FETCH)
    if wait > 0:
        time.sleep(wait)
    _LAST_REAL_FETCH = time.time()


def cdp_alive(cdp: str = "", timeout: float = 5) -> bool:
    """调试 Chrome 的 CDP 端口通不通"""
    url = cdp or config.CDP_URL
    host, port = "127.0.0.1", 9222
    try:
        u = urlparse(url)
        host = u.hostname or host
        port = u.port or port
    except Exception:       # noqa: BLE001
        pass
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_chrome() -> str:
    """找一个可用的 Chrome 可执行文件"""
    for p in config.CHROME_EXE_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return ""


def ensure_cdp(cdp: str = "", wait: float = 30.0) -> bool:
    """确保调试 Chrome 可用: 已在跑就直接用, 没跑就自动拉起

    抓取依赖这个浏览器(真实指纹 + 已登录), 它被关掉时整批都会失败,
    所以这里做一次自愈: 按同样的参数把调试 Chrome 拉起来再连。
    """
    if cdp_alive(cdp):
        return True
    if not config.CHROME_AUTO_START:
        return False
    exe = find_chrome()
    if not exe:
        return False
    u = urlparse(cdp or config.CDP_URL)
    port = u.port or 9222
    args = [exe, f"--remote-debugging-port={port}",
            f"--user-data-dir={config.CHROME_DEBUG_USER_DATA}",
            "--no-first-run", "--no-default-browser-check"]
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception:       # noqa: BLE001
        return False
    t0 = time.time()
    while time.time() - t0 < wait:      # 等端口起来
        if cdp_alive(cdp, timeout=3):
            time.sleep(1.5)             # 再给浏览器一点初始化时间
            return True
        time.sleep(1.5)
    return False


def force_jpy(ctx, page) -> str:
    """强制亚马逊日本站按**日元**展示

    代理出口 IP 不在日本时, 亚马逊会按美元报价(实测拿到 USD 29.11),
    而运营看的是日本站真实售价(￥4,598), 所以抓取前统一把币种偏好写成 JPY。
    返回生效方式(仅用于日志)。
    """
    cookie = {"name": "i18n-prefs", "value": "JPY",
              "domain": ".amazon.co.jp", "path": "/"}
    try:
        ctx.add_cookies([cookie])
        return "cookie"
    except Exception:       # noqa: BLE001  CDP 接入的浏览器可能不允许 add_cookies
        pass
    try:
        page.context.new_cdp_session(page).send("Network.setCookie", cookie)
        return "cdp"
    except Exception:       # noqa: BLE001
        return ""


def _live_fetch(query: str, url: str, cdp: str, scrolls: int, screenshot: bool,
                timeout_ms: int, domain: str = "co.jp") -> Dict[str, Any]:
    """真正打开浏览器加载页面并抽取 (不加缓存/不落库)

    反爬要点(与限流配合):
      * 复用用户已登录的调试 Chrome, 浏览器指纹/环境与真人一致;
      * 加载后不立即取数, 先随机停顿, 再分几步随机滚动(带随机停顿),
        模拟"翻看列表"的行为, 也让懒加载卡片渲染出来;
      * 不注入脚本、不改 UA、不做高频重试。
    """
    from playwright.sync_api import sync_playwright

    out: Dict[str, Any] = {"query": query, "url": url, "items": [], "item_count": 0,
                           "blocked": False, "via": ""}
    with sync_playwright() as pw:
        browser = None
        via = ""
        if not cdp_alive(cdp):
            ensure_cdp(cdp)             # 浏览器被关了 → 自动拉起来再连
        try:
            browser = pw.chromium.connect_over_cdp(cdp, timeout=15000)
            via = f"cdp:{cdp}"
        except Exception as e:      # noqa: BLE001
            try:                    # 退路: 自起 Chromium(需已 playwright install chromium)
                browser = pw.chromium.launch(headless=True)
                via = "headless-chromium"
            except Exception as e2:     # noqa: BLE001
                raise RuntimeError(
                    f"连不上调试 Chrome({cdp}): {str(e)[:60]}; 且自带 Chromium 不可用: "
                    f"{str(e2)[:60]}。请先启动调试 Chrome"
                    f"(--remote-debugging-port=9222 --user-data-dir=C:\\ChromeDebugUser)"
                    f"后重试") from e2

        connected = via.startswith("cdp")
        if connected and browser.contexts:
            ctx = browser.contexts[0]
        else:
            ctx = browser.new_context(
                locale="ja-JP", user_agent=UA, timezone_id="Asia/Tokyo",
                viewport={"width": random.choice([1366, 1440, 1536]),
                          "height": random.choice([768, 900, 864])})
        page = ctx.new_page()
        try:
            jpy = force_jpy(ctx, page)      # 代理出口不在日本时亚马逊按美元报价, 统一改成日元
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # 落地后随机停顿, 避免"请求→立即取数"的机械节奏
            page.wait_for_timeout(random.randint(600, 1600))
            try:
                page.wait_for_selector(
                    'div.s-main-slot div[data-component-type="s-search-result"]',
                    timeout=20000)
            except Exception:   # noqa: BLE001  可能遇到验证码
                pass
            # 等卡片数量稳定: 亚马逊是懒渲染, 只等"第一张出现"就取, 会只拿到前几张
            last = -1
            for _ in range(10):             # 最多约 7 秒
                try:
                    cur = int(page.evaluate(COUNT_JS) or 0)
                except Exception:           # noqa: BLE001
                    cur = 0
                if cur and cur == last:
                    break
                last = cur
                page.wait_for_timeout(700)
            # 分几步滚动: 每步距离/停顿都随机
            for _ in range(max(0, scrolls)):
                page.mouse.wheel(0, random.randint(1600, 3200))
                page.wait_for_timeout(random.randint(500, 1300))

            data = page.evaluate(EXTRACT_JS)
            low = data.get("bodyText") or ""
            # 币种兜底: 还是美元就再补一次(页面级 cookie)并刷新一次
            if "USD" in low and "￥" not in low:
                try:
                    page.evaluate("() => { document.cookie = "
                                  "'i18n-prefs=JPY; domain=.amazon.co.jp; path=/'; }")
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(random.randint(800, 1500))
                    data = page.evaluate(EXTRACT_JS)
                    low = data.get("bodyText") or ""
                    jpy = (jpy + "+reload") if jpy else "reload"
                except Exception:       # noqa: BLE001  兜底失败就用现有结果
                    pass
            items = data.get("items") or []
            if config.AMAZON_MAX_ITEMS:
                items = items[:config.AMAZON_MAX_ITEMS]
            out.update({
                "page_title": data.get("pageTitle"),
                "item_count": data.get("count"),
                "items": items,
                "blocked": ("ロボットではない" in low or "Enter the characters" in low
                            or "api-services-support@amazon.com" in low),
                "via": via,
                "jpy": jpy,
                "usd": ("USD" in low),          # 记录是否仍是美元(便于排查)
            })
            if screenshot:
                shot = amazon_store.shot_path(query)
                page.screenshot(path=shot)
                out["screenshot"] = shot
        finally:
            page.close()
    return out


def fetch(query: str, domain: str = "co.jp", cdp: Optional[str] = None,
          scrolls: int = 1, screenshot: bool = False, timeout_ms: int = 45000,
          ttl: Optional[int] = None, refresh: bool = False,
          use_cache: bool = True, parents: Optional[List[str]] = None) -> Dict[str, Any]:
    """取搜索结果: 本地文件/库缓存命中则直接返回; 否则限流+抓取+落本地+落库。

    读取优先级(命中任一层都**不再访问亚马逊**):
      1) 本地文件仓库 <AMAZON_DATA_DIR>/<父ASIN>/json/<搜索词>.json  → 永久有效;
      2) SQLite 里 TTL 内的近一次抓取                                  → 兼容历史数据;
      3) 都没有才真正打开浏览器抓取, 抓完同时写本地文件与库。

    parents: 该搜索词归属的父 ASIN 列表(落本地时按父 ASIN 分目录, 每个父目录各一份)
    ttl:     库缓存有效期(秒), None 取 config.AMAZON_CACHE_TTL_SEC
    refresh: True 强制重新抓取(忽略本地文件与库缓存)
    """
    ttl = config.AMAZON_CACHE_TTL_SEC if ttl is None else ttl
    cdp = cdp or config.CDP_URL
    url = search_url(query, domain)
    want_cache = use_cache and not refresh

    if parents is None:                 # 未指定时自行解析归属父 ASIN
        try:
            from backend import amazon_targets
            parents = amazon_targets.parents_of(query)
        except Exception:               # noqa: BLE001
            parents = []

    if want_cache:
        hit = amazon_store.find(query)
        if hit:
            return hit
        if ttl > 0:
            cached = db.get_cached_amazon_search(query, domain, ttl)
            if cached:
                cached["from_local"] = False
                return cached

    with _LOCK:
        # 双检: 等锁期间可能已被其它请求抓取并落盘
        if want_cache:
            hit = amazon_store.find(query)
            if hit:
                return hit
            if ttl > 0:
                cached = db.get_cached_amazon_search(query, domain, ttl)
                if cached:
                    cached["from_local"] = False
                    return cached

        pace()                  # 最小间隔 + 随机抖动
        out = _live_fetch(query, url, cdp, scrolls, screenshot, timeout_ms, domain)
        out["fetched_at"] = db.now()
        out["from_cache"] = False
        out["from_local"] = False

        try:                    # 先落本地文件(预抓取的成果), 再落库
            saved = amazon_store.save_result(query, parents or [], out)
            out["local_paths"] = saved
            out["local_path"] = next(iter(saved.values()), None)
            out["parents"] = parents or []
        except Exception:       # noqa: BLE001  落本地失败不影响返回
            out["local_path"] = None
        try:
            out["search_id"] = db.save_amazon_search(query, domain, out)
        except Exception:       # noqa: BLE001  落库失败不影响返回
            out["search_id"] = None
        return out
