#!/usr/bin/env python3
"""测试 A 股社交情绪数据接口（雪球 + 东方财富股吧）。

运行方式：
    cd /home/sun/worktree-social
    python scripts/test/test_cn_social.py
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from tradingagents.dataflows.cn_sentiment import get_cn_social_sentiment

if __name__ == "__main__":
    stock_code = sys.argv[1] if len(sys.argv) > 1 else "000001"
    print(f"正在获取 {stock_code} 的社交情绪数据...\n")
    result = get_cn_social_sentiment(stock_code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
