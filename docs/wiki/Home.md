# 赛狐 SP 广告接口 Wiki

> 由 `docs/sellfox_sp_create_api.md`（约 1500 行原始抓包记录）编译而来。
> 编译原则：**按「我要做什么」重组，不按抓包顺序搬运**；保留全部实测结论与未验证标记。

- 店铺：`711811`（金梧汇辰） ｜ 站点：`amazon.co.jp` / `A1VC38T7YXB528`
- 代码实现：`backend/ads_ops/`（动作注册表 + 后续 write.py）
- 原始记录（未删，作事实来源）：[`docs/sellfox_sp_create_api.md`](../sellfox_sp_create_api.md)

---

## 这套接口能干什么

| 能做 | 页面 |
|---|---|
| 读广告表现（活动/组/投放/产品/广告位/分时/建议价/日志/否定） | [03 读·广告表现](03-读-广告表现.md) |
| 读流量结构（搜索词/搜索ASIN/ABA排名/建议词） | [04 读·流量与词](04-读-流量与词.md) |
| 读商品与竞品（我方售价/竞品抓取/比价口径） | [05 读·商品与竞品](05-读-商品与竞品.md) |
| 写活动与广告组（建/改/暂停/归档/加组） | [06 写·活动与组](06-写-活动与组.md) |
| 写投放与广告位（调价/切状态/广告位加价/批量调价） | [07 写·投放与广告位](07-写-投放与广告位.md) |
| 写产品与否定（加产品/暂停/归档/否词/否商品） | [08 写·产品与否定](08-写-产品与否定.md) |
| 写组合与策略（组合增改移/分时预算模板） | [09 写·组合与策略](09-写-组合与策略.md) |

**做不到的**：删组合（接口是死代码）、否定词暂停（亚马逊不支持）、归档恢复、活动审核状态查询。

---

## 4 条铁律（踩过的坑，先看这里）

1. **一切广告操作走接口，禁止「文件导入亚马逊」**——Bulk Operations Excel 那条路径已废弃。
2. **`code:0` 不代表成功**。要看子响应（`campaignResp.code`）、`errMsgList`、`data` 里的字符串数组。
3. **改之前先读**。`spCampaign/update` 是全量覆盖语义，漏字段会被清空。
4. **`archive` 不可逆**。广告活动归档后只能重建。

---

## 5 分钟上手

```python
import json, requests
GW     = "https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/"
ORIGIN = "https://www.sellfox.com"
COOKIE = "; ".join(f"{k}={v}" for k, v in json.load(open("data/cookies.json")).items())

HDR = {
    "Cookie": COOKIE, "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu",
    "User-Agent": "Mozilla/5.0 ... Chrome/134 ...", "X-Language": "zh-CN",
}

# 读（JSON 编码）
r = requests.post(GW + "campaign/getAllCampaignData", json={
    "shopIdList": ["711811"], "adType": "sp",
    "startDate": "2026-11-08", "endDate": "2026-11-08",
    "pageNo": 1, "pageSize": 200, "fromSingle": True,
}, headers={**HDR, "Content-Type": "application/json"}, timeout=60)
print(r.json()["data"]["page"]["rows"][0]["name"])
```

三个必记的点：

1. **只带 Cookie 就够**，`sf-vvv-*` 签名头不需要（实测）。
2. **编码必须对**：改状态用 `form-urlencoded`，建对象用 `JSON`。错了就是 `360000 请求参数错误`，不是没权限。
3. **id 有两套**，写操作用赛狐内部 id。详见 [01 快速开始](01-快速开始.md#双-id-体系最容易错的地方)。

---

## 全接口速查

完整矩阵（读 + 写，含实测状态）：[02 接口总表](02-接口总表.md)

按业务动作查（推荐给智能体与非开发同学）：[12 动作速查](12-动作速查.md)

---

## 出问题先查这两页

- [10 错误码与排查](10-错误码与排查.md)
- [11 踩坑总表](11-踩坑总表.md) —— 按「现象」索引，不是按章节

---

## 关于这份 Wiki

- 全部结论来自真实抓包与实测，**每个接口标了「已实测 / 未实测 / 未打通」**。
- 已知文档内部矛盾统一记在 [11 踩坑总表](11-踩坑总表.md#文档自身的矛盾)，共 2 处，执行前需实测裁决。
- 若发布到 GitHub Wiki：链接去掉 `.md` 后缀即可；若本地阅读，保留 `.md` 可直接跳转。
