# -*- coding: utf-8 -*-
"""ads_ops 动作注册表 —— 智能体感知「能做什么」的唯一来源

设计原则
    智能体只需要知道：有哪些动作、参数是什么、限制是什么、风险几级。
    它不需要知道 URL、编码（json/form）、双 id 体系——那些都在本模块和 write.py 里。

用法
    python3 -m backend.ads_ops.registry list
    python3 -m backend.ads_ops.registry show set_target_bid
    python3 -m backend.ads_ops.registry search 调价
    python3 -m backend.ads_ops.registry describe        # 给智能体看的全量 JSON
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = os.path.join(_HERE, "actions.json")

with open(_SPEC, encoding="utf-8") as f:
    SPEC = json.load(f)

ACTIONS = SPEC["actions"]
BY_NAME = {a["name"]: a for a in ACTIONS}


def levels():
    return SPEC.get("levels", {})


def list_actions(level=None):
    return [a for a in ACTIONS if level is None or a["level"] == level]


def get(name):
    if name not in BY_NAME:
        raise KeyError(f"未知动作 {name!r}；可用：{', '.join(BY_NAME)}")
    return BY_NAME[name]


def search(word):
    """按意图/名称/说明做模糊匹配 —— 智能体用它把「用户的话」映射到「动作」"""
    w = (word or "").strip().lower()
    if not w:
        return []
    hits = []
    for a in ACTIONS:
        blob = " ".join([a["name"], a.get("desc", ""), *a.get("intent", [])]).lower()
        if w in blob:
            hits.append(a)
    return hits


def describe():
    """给智能体的机器可读清单（不含 URL/编码细节）"""
    return {
        "version": SPEC["version"],
        "shop": SPEC["shop"],
        "levels": levels(),
        "actions": [{
            "name": a["name"],
            "level": a["level"],
            "desc": a["desc"],
            "intent": a.get("intent", []),
            "params": a.get("params", {}),
            "ids": a.get("ids", {}),
            "limits": a.get("limits", {}),
            "precondition": a.get("precondition"),
            "idempotent": a.get("idempotent"),
            "rollback": a.get("rollback"),
            "verified": a.get("verified"),
        } for a in ACTIONS],
        "unverified": SPEC.get("unverified", []),
        "conflicts": SPEC.get("conflicts", []),
        "forbidden": SPEC.get("forbidden", []),
    }


def _main():
    ap = argparse.ArgumentParser(prog="ads_ops.registry")
    ap.add_argument("cmd", choices=["list", "show", "search", "describe"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()

    if a.cmd == "describe":
        print(json.dumps(describe(), ensure_ascii=False, indent=1))
        return

    if a.cmd == "list":
        lv = a.arg or ""
        print(f"{'动作':<26}{'级别':<6}{'幂等':<6}{'回滚':<22}说明")
        print("-" * 96)
        for x in list_actions(lv or None):
            print(f"{x['name']:<26}{x['level']:<6}{'是' if x.get('idempotent') else '否':<6}"
                  f"{(x.get('rollback') or '-')[:20]:<22}{x['desc'][:34]}")
        print(f"\n共 {len(list_actions(lv or None))} 个动作")

    elif a.cmd == "show":
        x = get(a.arg)
        print(json.dumps(x, ensure_ascii=False, indent=1))

    elif a.cmd == "search":
        hits = search(a.arg)
        if not hits:
            print(f"没有匹配 {a.arg!r} 的动作")
            sys.exit(1)
        for x in hits:
            print(f"{x['name']:<26}[{x['level']}] {x['desc']}")
            print(f"   触发词: {' / '.join(x.get('intent', []))}")


if __name__ == "__main__":
    _main()
