# -*- coding: utf-8 -*-
"""亚马逊商品页: 抓取 星级评分 / 评分数 / 小类目-大类目 BSR 排名

背景: 赛狐 `product/pageList` 里 `rating`/`ratingCount`/`bsrRank` 恒为 null,
      `smallBsrRank`/`bigBsrRank` 甚至不存在 → 原站这几列也只能显示 `***`。
      唯一可行的取数路径是**用真实浏览器加载亚马逊商品页 /dp/{ASIN}, 再读渲染后的 DOM**
      (不调用任何接口)。

设计: 与 backend/amazon.py 共用全局抓取锁与最小间隔节拍;
      结果落库 amz_product_metrics, 并按 TTL 命中缓存。
"""
import os
import urllib.parse
from typing import Any, Dict, Optional

import config
from backend import amazon
from backend import database as db

MARKET = amazon.MARKET
UA = amazon.UA

SHOT_DIR = os.path.join(config.DATA_DIR, "amazon")

# 在页面上下文执行: 从「渲染后」的商品页 DOM 抽取
EXTRACT_JS = r"""
() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  const txtOf = (sel) => { const e = document.querySelector(sel); return e ? t(e.innerText || e.textContent) : ""; };
  const attrOf = (sel, a) => { const e = document.querySelector(sel); return e ? (e.getAttribute(a) || "") : ""; };

  // 1) 星级评分: "5つ星のうち4.5" -> 4.5  (或英文 "4.5 out of 5 stars")
  let star = attrOf("#acrPopover", "title") || attrOf("a[data-hook='acr-popover']", "title");
  if (!star) star = txtOf("#acrPopover span.a-icon-alt") || txtOf("[data-hook='rating-out-of-text']");
  if (!star) {
    const el = document.getElementById("averageCustomerReviews");
    const m = el ? (el.innerText.match(/[0-9.]+(?=\s*(?:つ星|out of 5))/) || []) : [];
    star = m[0] || "";
  }
  const mStar = star.match(/うち\s*([0-9]+(?:\.[0-9]+)?)/) || star.match(/^\s*([0-9]+(?:\.[0-9]+)?)\s*out of/);
  const starNum = mStar ? mStar[1] : ((star.match(/([0-9]+(?:\.[0-9]+)?)\s*$/) || [""])[0]);

  // 2) 评分数
  const cnt = txtOf("#acrCustomerReviewText") || txtOf("[data-hook='total-review-count']");
  const cntNum = cnt ? (cnt.replace(/[^0-9]/g, "") || "0") : "";

  // 3) BSR 排名: 商品信息区里的 "Amazon 売れ筋ランキング"
  const boxes = ["#detailBulletsWrapper_feature_div", "#productDetails_detailBullets_sections1",
                 "#productDetails_db_sections", "#prodDetails", "#detailBullets_feature_div",
                 "#productDetails_feature_div"];
  let rankText = "";
  for (const b of boxes) {
    const el = document.querySelector(b);
    if (el && /(売れ筋|Best Sellers)ランキング|Best Sellers Rank/.test(el.innerText)) { rankText = t(el.innerText); break; }
  }
  if (!rankText) {
    const m = t(document.body ? document.body.innerText : "").match(/(?:Amazon\s*)?売れ筋ランキング[\s\S]{0,300}/);
    if (m) rankText = m[0];
  }
  // 形如 "ホーム＆キッチン - 193位" / "カセットコンロ - 1位"
  // 注意: 连字符集合不能含日文长音符「ー」(U+30FC), 否则「ホーム」这类词会被误排除
  const DASH = "\\-–—−‐";
  const re = new RegExp("(?:^|[\\s(（])([^\\s" + DASH + "][^\\s" + DASH + "]{1,40}?)\\s*[" + DASH +
                        "]\\s*([0-9][0-9,]*)\\s*位", "g");
  const bsr = [];
  const seen = new Set();
  for (const m of rankText.matchAll(re)) {
    const cat = t(m[1]);
    if (/位|を見る|ランキング/.test(cat) || seen.has(cat)) continue;
    seen.add(cat);
    bsr.push({ category: cat, rank: m[2].replace(/,/g, "") });
  }
  // 4) 价格
  const price = txtOf("#corePrice_feature_div .a-offscreen")
    || txtOf("#corePriceDisplay_desktop_feature_div .a-offscreen")
    || txtOf("#priceblock_ourprice") || txtOf("#priceblock_dealprice")
    || txtOf("#tp_price_block_total_price_ww .a-offscreen") || txtOf("span.a-price span.a-offscreen");

  // 5) 图片: 主图 + 附图(图廊) —— 去掉尺寸后缀取原始大图
  const up = (u) => u.replace(/\._[A-Za-z0-9_,]+_\.(jpg|jpeg|png|gif)/i, ".$1");
  const pool = [];
  const push = (u, src) => { u = t(u); if (u && /^https?:/.test(u)) pool.push({ u: u, src: src }); };
  const landing = document.querySelector("#landingImage")
    || document.querySelector("#imgTagWrapperId img")
    || document.querySelector("#main-image-container img")
    || document.querySelector("#imgBlkFront");
  if (landing) {
    const dyn = landing.getAttribute("data-a-dynamic-image");
    if (dyn) { try { Object.keys(JSON.parse(dyn)).forEach((u) => push(u, "main")); } catch (e) {} }
    push(landing.getAttribute("data-old-hires"), "main");
    push(landing.getAttribute("src"), "main");
  }
  document.querySelectorAll("#altImages li img, #imageBlock_feature_div li img, #imageBlock li img")
    .forEach((im) => {
      push(im.getAttribute("src") || im.getAttribute("data-src"), "alt");
      push(im.getAttribute("data-old-hires"), "alt");
    });
  const seenImg = new Set(); const images = [];
  for (const p of pool) {
    const key = up(p.u).replace(/\?.*$/, "");
    if (seenImg.has(key)) continue;
    seenImg.add(key);
    images.push({ thumb: p.u, large: up(p.u), source: p.src });
  }

  const bodyLow = (document.body ? document.body.innerText : "").slice(0, 4000);
  return {
    title: txtOf("#productTitle"),
    price: price,
    star: star, star_num: starNum,
    review_num: cntNum,
    bsr: bsr.slice(0, 6),
    images: images.slice(0, 12),
    blocked: (bodyLow.includes("ロボットではない") || bodyLow.includes("Enter the characters")
              || bodyLow.includes("api-services-support@amazon.com")),
  };
}
"""


def product_url(asin: str, domain: str = "co.jp") -> str:
    base = MARKET.get(domain, MARKET["co.jp"])
    return f"{base}/dp/{urllib.parse.quote(str(asin))}?language=ja_JP"


def _live_fetch(asin: str, url: str, cdp: str, timeout_ms: int,
                screenshot: bool = False, fast: bool = False) -> Dict[str, Any]:
    """打开浏览器加载商品页并抽取

    fast=True 用于「只取附图」场景: 只等图廊出现(不等标题/BSR 等), 明显更快。
    """
    from playwright.sync_api import sync_playwright

    out: Dict[str, Any] = {"asin": asin, "url": url, "blocked": False, "via": ""}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(cdp, timeout=15000)
            via = f"cdp:{cdp}"
        except Exception:       # noqa: BLE001
            browser = pw.chromium.launch(headless=True)
            via = "headless-chromium"

        if via.startswith("cdp") and browser.contexts:
            ctx = browser.contexts[0]
        else:
            ctx = browser.new_context(locale="ja-JP", user_agent=UA,
                                      viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if fast:
                try:
                    page.wait_for_selector("#landingImage, #altImages li img, #imgTagWrapperId img",
                                           timeout=12000)
                except Exception:   # noqa: BLE001  可能遇验证码
                    pass
                page.wait_for_timeout(1200)
            else:
                try:
                    page.wait_for_selector("#productTitle", timeout=20000)
                except Exception:   # noqa: BLE001  可能遇验证码
                    pass
                page.wait_for_timeout(2500)
            data = page.evaluate(EXTRACT_JS)
            bsr = data.get("bsr") or []
            images = data.get("images") or []
            out.update({
                "title": data.get("title"),
                "price": data.get("price"),
                "rating": _f(data.get("star_num")),
                "rating_count": _i(data.get("review_num")),
                "bsr_small": None, "bsr_small_cat": None,
                "bsr_big": None, "bsr_big_cat": None,
                "bsr_all": bsr,
                # 附图(主图 + 图廊), 供父 ASIN 详情页展示
                "images": images,
                "main_image": (images[0]["large"] if images else ""),
                "blocked": bool(data.get("blocked")),
                "via": via,
            })
            if screenshot:
                os.makedirs(SHOT_DIR, exist_ok=True)
                shot = os.path.join(SHOT_DIR, f"dp_{asin}.png")
                page.screenshot(path=shot)
                out["screenshot"] = shot
        finally:
            page.close()
    return out


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> Optional[int]:
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch(asin: str, domain: Optional[str] = None, cdp: Optional[str] = None,
          timeout_ms: int = 45000, ttl: Optional[int] = None, refresh: bool = False,
          use_cache: bool = True, screenshot: bool = False) -> Dict[str, Any]:
    """取商品指标: 命中缓存直接返回; 否则限流后抓取并落库"""
    domain = domain or config.AMAZON_DOMAIN
    cdp = cdp or config.CDP_URL
    ttl = config.AMAZON_PRODUCT_CACHE_TTL_SEC if ttl is None else ttl
    asin = str(asin or "").strip()
    if not asin:
        raise ValueError("asin 不能为空")
    url = product_url(asin, domain)
    want_cache = use_cache and not refresh and ttl > 0

    if want_cache:
        hit = db.get_amz_product_metrics(asin, domain, ttl)
        if hit:
            hit["from_cache"] = True
            hit["url"] = url
            return hit

    amazon.acquire_lock()
    try:
        if want_cache:      # 双检
            hit = db.get_amz_product_metrics(asin, domain, ttl)
            if hit:
                hit["from_cache"] = True
                hit["url"] = url
                return hit
        amazon.pace()
        out = _live_fetch(asin, url, cdp, timeout_ms, screenshot=screenshot)
        out["domain"] = domain
        # 小类目 = 名次更靠前(数值更小)的那条
        cands = [x for x in (out.get("bsr_all") or []) if _i(x.get("rank"))]
        if len(cands) >= 2:
            cands.sort(key=lambda x: _i(x["rank"]))
            out["bsr_small"], out["bsr_small_cat"] = _i(cands[0]["rank"]), cands[0]["category"]
            out["bsr_big"], out["bsr_big_cat"] = _i(cands[-1]["rank"]), cands[-1]["category"]
        try:
            db.save_amz_product_metrics(out)
        except Exception:       # noqa: BLE001
            pass
        try:
            db.save_amz_product_images(asin, domain, out.get("images") or [])
        except Exception:       # noqa: BLE001
            pass
        out["fetched_at"] = db.now()
        out["from_cache"] = False
        return out
    finally:
        amazon.release_lock()


def fetch_images(asin: str, domain: Optional[str] = None, cdp: Optional[str] = None,
                 timeout_ms: int = 45000, ttl: Optional[int] = None,
                 refresh: bool = False) -> Dict[str, Any]:
    """只取商品页**图片(主图 + 附图)**, 命中缓存直接返回。

    父 ASIN 的附图 = 其子 ASIN 详情页图片的合集, 因此本函数按「单个 ASIN」粒度
    缓存, 上层 (api) 负责按父体聚合。
    """
    domain = domain or config.AMAZON_DOMAIN
    cdp = cdp or config.CDP_URL
    ttl = config.AMAZON_PRODUCT_CACHE_TTL_SEC if ttl is None else ttl
    asin = str(asin or "").strip()
    if not asin:
        raise ValueError("asin 不能为空")

    if not refresh and ttl > 0:
        hit = db.get_amz_product_images(asin, domain)
        if hit:
            return {"asin": asin, "domain": domain, "images": hit, "from_cache": True}
    else:
        hit = db.get_amz_product_images(asin, domain)
        if hit:
            return {"asin": asin, "domain": domain, "images": hit, "from_cache": True}

    amazon.acquire_lock()
    try:
        amazon.pace()
        out = _live_fetch(asin, product_url(asin, domain), cdp, timeout_ms, fast=True)
        images = out.get("images") or []
        db.save_amz_product_images(asin, domain, images)
        return {"asin": asin, "domain": domain, "images": images,
                "blocked": bool(out.get("blocked")), "via": out.get("via"),
                "from_cache": False, "fetched_at": db.now()}
    finally:
        amazon.release_lock()
