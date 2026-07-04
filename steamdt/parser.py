from typing import Any, Dict, List, Optional

from .models import SteamDTRow


def _find_platform(platform_list: list, enum_val: str) -> Optional[Dict[str, Any]]:
    for p in (platform_list or []):
        if p.get("platformEnum") == enum_val:
            return p
    return None


def parse_response(result: dict, target_platform: str = "BUFF") -> List[SteamDTRow]:
    if not result.get("success"):
        return []
    data = result.get("data") or []
    rows: List[SteamDTRow] = []
    for i, item in enumerate(data, 1):
        plist = item.get("platformList") or []
        platform = _find_platform(plist, target_platform)
        steam = _find_platform(plist, "STEAM")

        if platform:
            price = platform.get("price", item.get("platformPrice", 0))
            platform_link = platform.get("linkUrl", "")
            platform_sell_rate = platform.get("sellRate")
            platform_purchase_rate = platform.get("purchaseRate")
        else:
            price = item.get("platformPrice", 0)
            platform_link = ""
            platform_sell_rate = None
            platform_purchase_rate = None

        steam_link = steam.get("linkUrl", "") if steam else ""

        sell_rate = item.get("sellRate", 0)
        purchase_rate = item.get("purchaseRate", 0)

        if platform_sell_rate is not None:
            sell_rate = platform_sell_rate
        if platform_purchase_rate is not None:
            purchase_rate = platform_purchase_rate

        rows.append(SteamDTRow(
            index=str(i),
            name=item.get("marketHashName", ""),
            volume=str(item.get("transactionCount", 0)),
            min_price=str(price),
            sell_ratio=str(sell_rate or 0),
            buy_ratio=str(purchase_rate or 0),
            safe_buy_ratio=str(purchase_rate or sell_rate or 0), 
            recent_ratio=str(sell_rate or 0),      
            platform=platform_link,
            steam_link=steam_link,
            update_time=str(item.get("updateTime", "")),
            name_cn=item.get("name", ""),
            steam_price=float(item.get("steamPrice", 0) or 0),
            profit_amount=float(item.get("profitAmount", 0) or 0),
        ))
    return rows
