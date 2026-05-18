from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel

# 导入统一日志系统
from tradingagents.utils.logging_init import get_logger
logger = get_logger("cli")


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class AnalystType(str, Enum):
    MARKET = "market"
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
