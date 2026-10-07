#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量预抓取: 「点击量达标」的搜索词 → 亚马逊搜索结果页 → 按父 ASIN 存到本地

落盘结构 (config.AMAZON_DATA_DIR, 默认 E:\\amazonData\\adsData):
    <父ASIN>/json/<搜索词>.json              搜索结果完整数据
    <父ASIN>/主图/<搜索词>/<顺位>_<ASIN>.jpg  结果页里各商品的主图

为什么预抓取: 页面点开搜索词才现抓要等十几秒, 且每次访问都在增加被风控的概率。
先把数据抓到本地, 之后页面查看直接读本地, 一次请求都不发。

反爬: 串行 + 词间随机间隔(默认 8~15s) + 每 20 条长休 + 撞验证码长退避并中止;
      本地已有的搜索词自动跳过(只缺主图则只补图片, 不重开页面), 中断后重跑即续抓。

用法:
    python prefetch_amazon.py                     # 点击 >= 2 的搜索词(默认)
    python prefetch_amazon.py --min-clicks 1      # 放宽到点击 >= 1
    python prefetch_amazon.py --limit 20          # 只抓前 20 个
    python prefetch_amazon.py --dry-run           # 只看要抓哪些、归属哪个父 ASIN
    python prefetch_amazon.py --gap 12 20         # 自定义随机间隔区间(秒)
    python prefetch_amazon.py --no-images         # 只抓数据, 不下竞品主图
    python prefetch_amazon.py --refresh           # 已抓过的也重抓
    python prefetch_amazon.py --wait-net 60       # 网络不通时每 60s 探测, 通了自动开跑
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config                                        # noqa: E402
from backend import amazon_prefetch, amazon_store, amazon_targets   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-clicks", type=int, default=config.AMAZON_PREFETCH_MIN_CLICKS,
                    help=f"只抓点击量 >= 该值的搜索词, 默认 {config.AMAZON_PREFETCH_MIN_CLICKS}")
    ap.add_argument("--limit", type=int, default=0, help="最多抓多少个搜索词, 0=全部")
    ap.add_argument("--gap", nargs=2, type=float, default=None, metavar=("MIN", "MAX"),
                    help="相邻两个搜索词的随机间隔区间(秒), 默认 8 15")
    ap.add_argument("--refresh", action="store_true", help="忽略本地文件, 全部重抓")
    ap.add_argument("--no-images", action="store_true", help="不下载竞品主图")
    ap.add_argument("--image-limit", type=int, default=0,
                    help="每个搜索词最多下多少张主图, 0=全部")
    ap.add_argument("--wait-net", type=int, default=0, metavar="秒",
                    help="网络不通时先等着: 每 N 秒探测一次, 通了自动开跑(0=不等待)")
    ap.add_argument("--redo-parents", action="store_true",
                    help="已标记完成的父 ASIN 也重抓(默认整组跳过)")
    ap.add_argument("--dry-run", action="store_true", help="只列出待抓搜索词")
    args = ap.parse_args()

    gap_min, gap_max = (args.gap if args.gap else
                        (config.AMAZON_PREFETCH_GAP_MIN_SEC,
                         config.AMAZON_PREFETCH_GAP_MAX_SEC))

    items = amazon_targets.build(args.min_clicks)
    sm = amazon_targets.summary(items)
    todo = [t for t in items if args.refresh or not amazon_store.exists(t["query"])]
    groups = amazon_targets.group_by_parent(items)
    pstate = amazon_store.parents_state()

    print(f"本地目录: {amazon_store.root()}")
    print(f"点击 >= {args.min_clicks} 的搜索词 {sm['terms']} 个 · "
          f"归属父 ASIN {sm['parents']} 个 · 合计花费 {sm['adCost']}")
    print(f"父 ASIN 已标记完成 {len(pstate)} 个 | 待抓取搜索词 {len(todo)} 个 | "
          f"本地已有 {len(items) - len(todo)} 个")
    if sm["unmapped_asins"]:
        print(f"提示: {sm['unmapped_asins']} 个 ASIN 没有变体关系(在线产品未采集到), "
              f"已回退为自身作为父 ASIN")

    if args.dry_run:
        print(f"\n抓取顺序(按父 ASIN 逐个抓, 组内点击降序):")
        for i, g in enumerate(groups[:20], 1):
            mark = "✔已完成" if g["parent"] in pstate else "待抓"
            print(f"  {i:>2}. {mark} {g['parent']}: {len(g['terms'])} 个词, "
                  f"点击合计 {g['clicks']}, 花费 {g['adCost']:.0f}")
        if len(groups) > 20:
            print(f"  ... 其余 {len(groups) - 20} 个父 ASIN")
        avg = (gap_min + gap_max) / 2
        print(f"\n预计耗时约 {len(todo) * avg / 60:.1f} 分钟 "
              f"(按每条 {avg:.1f}s 估算, 不含长休; 主图另算)")
        return

    if not todo:
        print("本地已全部缓存, 无需抓取。")
        return

    # 网络不通时先等着(可选): 通了再自动开跑, 免得白跑一批失败
    if args.wait_net and not amazon_prefetch.net_ok():
        print(f"当前连不上 www.amazon.co.jp, 每 {args.wait_net}s 探测一次, "
              f"通了自动开跑(Ctrl+C 退出)…", flush=True)
        while not amazon_prefetch.net_ok():
            time.sleep(max(10, args.wait_net))
        print("网络已恢复, 开跑。", flush=True)

    try:
        st = amazon_prefetch.run_blocking(
            min_clicks=args.min_clicks, limit=args.limit, refresh=args.refresh,
            gap_min=gap_min, gap_max=gap_max, images=not args.no_images,
            image_limit=args.image_limit, skip_done_parents=not args.redo_parents)
    except KeyboardInterrupt:       # Ctrl+C: 已抓成果保留, 重跑自动续抓
        print("\n已中断(Ctrl+C), 已抓到的文件都保留在本地, 重跑会自动跳过。")
        return

    print("\n" + "=" * 70)
    print(f"完成: 新抓 {st['ok']} · 跳过 {st['skipped']} · 失败 {st['failed']}")
    print(f"主图: 新增 {st['img_new']} 张 · 失败 {st['img_failed']} 张")
    print(f"本地: {st['local']['terms']} 词 / {st['local']['parents']} 父ASIN / "
          f"{st['local']['images']} 张主图 / {st['local']['size_mb']} MB")
    print(f"目录: {st['local']['root']}")
    if st.get("error"):
        print(f"异常: {st['error']}")


if __name__ == "__main__":
    main()
