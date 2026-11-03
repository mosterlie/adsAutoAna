# -*- coding: utf-8 -*-
"""广告创建(亚马逊 Bulk 批量广告表生成) —— 自 autoAds 平移

  parser.py         赛狐导出表自适应解析(Excel 上传的兼容入口)
  db_source.py      本项目在线产品库 → 同一份解析结构(主数据源)
  budget_engine.py  客单价中位数加权预算
  bulk_generator.py 亚马逊 Bulk Excel 生成 + 上传前自检
  translator.py     离线纯名词词库(日文标题 → 中文品名)
"""
