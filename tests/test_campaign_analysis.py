# -*- coding: utf-8 -*-
import sqlite3

import config
from openpyxl import load_workbook

from backend.ads_ops.campaign_analysis import CampaignAnalysisService
from backend.ads_ops.campaign_export import build_workbook


def _db(tmp_path, monkeypatch):
    path = tmp_path / "ads.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE dim_campaign(campaign_id TEXT,inner_id TEXT,name TEXT,
      targeting_type TEXT,status TEXT,daily_budget REAL,strategy TEXT,
      portfolio_id TEXT,portfolio_name TEXT,placement_top REAL,
      placement_rest REAL,placement_product REAL);
    CREATE TABLE dim_group(group_id TEXT,amazon_group_id TEXT,campaign_id TEXT,
      name TEXT,default_bid REAL,status TEXT);
    CREATE TABLE dim_target(target_id TEXT,group_id TEXT,campaign_id TEXT,
      target_type TEXT,target_text TEXT,match_type TEXT);
    CREATE TABLE dim_product(asin TEXT,parent_asin TEXT,title TEXT);
    CREATE TABLE fact_campaign(campaign_id TEXT,date TEXT,hour INTEGER,
      placement TEXT,impressions REAL,clicks REAL,cost REAL,orders REAL,
      sales REAL,add_to_cart REAL);
    CREATE TABLE fact_group(group_id TEXT,campaign_id TEXT,date TEXT,hour INTEGER,
      placement TEXT,impressions REAL,clicks REAL,cost REAL,orders REAL,
      sales REAL,add_to_cart REAL);
    CREATE TABLE fact_target(target_id TEXT,campaign_id TEXT,group_id TEXT,
      date TEXT,hour INTEGER,impressions REAL,clicks REAL,cost REAL,orders REAL,
      sales REAL,add_to_cart REAL);
    CREATE TABLE fact_product(asin TEXT,campaign_id TEXT,group_id TEXT,date TEXT,
      hour INTEGER,impressions REAL,clicks REAL,cost REAL,orders REAL,sales REAL,
      add_to_cart REAL);
    CREATE TABLE fact_search_term(date TEXT,scope TEXT,owner_id TEXT,
      campaign_id TEXT,query_type TEXT,term TEXT,match_type TEXT,
      impressions REAL,clicks REAL,cost REAL,orders REAL,add_to_cart REAL);
    CREATE TABLE fact_competitor(term TEXT,asin TEXT,position INTEGER,title TEXT,
      price REAL,rating REAL,reviews INTEGER,delivery TEXT,coupon TEXT,stock TEXT,
      sponsored INTEGER,url TEXT,fetched_at TEXT);
    """)
    conn.execute("INSERT INTO dim_campaign VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("c1", "ci1", "CA_测试_B0TEST1234", "auto", "enabled", 100,
                  "legacyForSales", "p1", "测试组合", 20, 10, 20))
    conn.execute("INSERT INTO dim_group VALUES (?,?,?,?,?,?)",
                 ("g1", "ag1", "c1", "GR_测试", 10, "enabled"))
    conn.execute("INSERT INTO dim_target VALUES (?,?,?,?,?,?)",
                 ("t1", "ag1", "c1", "auto", "queryHighRelMatches", ""))
    conn.execute("INSERT INTO dim_product VALUES (?,?,?)",
                 ("B0TEST1234", "B0TEST1234", "测试商品"))
    daily = ("c1", "2026-11-01", -1, "", 1000, 40, 500, 0, 0, 0)
    conn.execute("INSERT INTO fact_campaign VALUES (?,?,?,?,?,?,?,?,?,?)", daily)
    conn.execute("INSERT INTO fact_campaign VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("c1", "2026-11-01", -1, "placementProductPage", 500, 10, 100, 0, 0, 0))
    conn.execute("INSERT INTO fact_campaign VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("c1", "2026-11-01", 0, "", 200, 2, 20, 0, 0, 0))
    conn.execute("INSERT INTO fact_group VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("g1", "c1", "2026-11-01", -1, "", 1000, 40, 500, 0, 0, 0))
    conn.execute("INSERT INTO fact_target VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("t1", "c1", "ag1", "2026-11-01", -1, 1000, 40, 500, 0, 0, 0))
    conn.execute("INSERT INTO fact_product VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("B0TEST1234", "c1", "g1", "2026-11-01", -1, 1000, 40, 500, 0, 0, 0))
    conn.execute("INSERT INTO fact_search_term VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("2026-11-01", "campaign", "g1", "c1", "keyword", "零单词",
                  "close_match", 500, 15, 150, 0, 0))
    conn.execute("INSERT INTO fact_search_term VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("2026-11-01", "campaign", "g1", "c1", "keyword", "零单词",
                  "loose_match", 300, 10, 100, 0, 0))
    conn.execute("INSERT INTO fact_competitor VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("零单词", "B0COMP0001", 1, "竞品标题", 999, 4.5, 12,
                  "明日送达", "", "有货", 0, "https://example.com", "2026-11-01"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))


def test_campaign_analysis_dimensions_and_strategy(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    svc = CampaignAnalysisService()
    campaigns = svc.campaigns()
    assert campaigns[0]["has_data"] is True
    assert campaigns[0]["data_days"] == 1

    result = svc.analyze("c1", "2026-11-01", "2026-11-01")
    assert result["summary"]["clicks"] == 40
    assert result["summary"]["orders"] == 0
    assert len(result["trend"]) == 1
    assert len(result["groups"]) == 1
    assert len(result["targets"]) == 1
    assert len(result["products"]) == 1
    assert len(result["placements"]) == 1
    assert len(result["hourly"]) == 1
    assert len(result["search_terms"]) == 1
    term = result["search_terms"][0]
    assert term["clicks"] == 25
    assert len(term["match_breakdown"]) == 2
    assert len(result["competitors"]) == 1
    actions = [x["action"] for x in result["strategy"]["actions"]]
    assert "set_campaign_budget" in actions
    assert "set_group_bid" in actions
    assert "add_negative_keyword" in actions
    assert "set_campaign_placement" in actions

    wb = load_workbook(build_workbook(result))
    assert wb.sheetnames == ["汇总", "搜索词", "搜索ASIN", "竞品数据"]
    assert wb["搜索词"].max_row == 2
    assert wb["搜索ASIN"].max_row == 1
    assert "（" in str(wb["搜索词"].cell(row=2, column=7).value)
    assert wb["竞品数据"].max_row == 2
    assert wb["竞品数据"].cell(row=2, column=3).value == "B0COMP0001"
