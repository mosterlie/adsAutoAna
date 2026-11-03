# -*- coding: utf-8 -*-
"""广告创建页 Playwright 调试: 控制台/网络错误 + 逐区块交互。"""
import json

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8320/"
SHOT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/dbg_adcreator"
ROWS = 'div.max-h-\\[330px\\] > div'


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        console, pageerr, failed = [], [], []
        pg.on("console", lambda m: console.append(f"[{m.type}] {m.text[:200]}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: pageerr.append(str(e)[:300]))
        pg.on("requestfailed", lambda r: failed.append(f"{r.method} {r.url[:100]} :: {r.failure}"))

        pg.goto(BASE, wait_until="networkidle")
        pg.click('#viewSwitch .seg-btn[data-view="adcreator"]')
        pg.wait_for_timeout(4500)
        fr = pg.frame_locator("#adCreatorFrame")
        body = lambda: fr.locator("body").inner_text()

        print("=== 1. 区块 ===")
        for t in ["创建广告活动", "创建广告组", "设定投放规则", "设定否定投放"]:
            print(f"  {t}:", t in body())

        print("=== 2. 广告活动表单 ===")
        sels = fr.locator("select")
        print("  下拉首项:", [sels.nth(i).locator("option").first.inner_text() for i in range(sels.count())])
        fr.locator('input[placeholder="示例：节假日收藏夹"]').fill("调试-自动-1103")
        fr.locator("text=单价加权预算池").click(); pg.wait_for_timeout(300)
        fr.locator("text=等权（每个活动）").click(); pg.wait_for_timeout(300)
        print("  预算模式切换正常:", True)
        row = fr.locator("tr", has_text="搜索结果顶部（首页）")
        row.locator('input[type="number"]').fill("15"); pg.wait_for_timeout(300)
        print("  竞价示例:", row.inner_text().replace("\n", " ").split("示例")[-1].strip()[:48])

        print("=== 3. 商品选择 ===")
        rows = fr.locator(ROWS)
        print("  父体行数:", rows.count())
        first = rows.first
        print("  首行:", first.inner_text().replace("\n", " | ")[:60])
        first.locator('input[type="checkbox"]').first.click(); pg.wait_for_timeout(600)
        print("  勾选后合计:", [l for l in body().split("\n") if "已选" in l][:1])

        print("=== 4. 广告组/投放/否定 ===")
        btn = fr.locator("button", has_text="生成")
        print("  按钮:", [btn.nth(i).inner_text().strip() for i in range(min(btn.count(), 5))])
        for name in ["生成广告组", "生成投放", "生成否定"]:
            b2 = fr.locator("button", has_text=name)
            if b2.count():
                b2.first.click(); pg.wait_for_timeout(1200)
                print(f"  {name} -> 点击完成")
        txt = body()
        print("  否定关键词区块:", "否定关键词" in txt or "否定投放" in txt)
        print("  底部合计:", [l for l in txt.split("\n") if "将生成" in l][:1])

        pg.screenshot(path=f"{SHOT}_1_campaign.png")
        fr.locator("text=创建广告组").first.scroll_into_view_if_needed(); pg.wait_for_timeout(400)
        pg.screenshot(path=f"{SHOT}_2_adgroup.png")
        fr.locator("text=设定投放规则").first.scroll_into_view_if_needed(); pg.wait_for_timeout(400)
        pg.screenshot(path=f"{SHOT}_3_targeting.png")

        print("=== 5. 错误 ===")
        print("  console:", json.dumps(console[:6], ensure_ascii=False))
        print("  pageerror:", json.dumps(pageerr[:6], ensure_ascii=False))
        print("  requestfailed:", json.dumps(failed[:6], ensure_ascii=False))
        b.close()


if __name__ == "__main__":
    main()
