# -*- coding: utf-8 -*-
"""
数据源：人工维护表（data/manual.json）。

为什么需要它
------------
公开可爬的源覆盖不全。内地艺人的演出由区级文旅部门审批，公示不上网；
售票平台（大麦/猫眼）有强反爬或按 IP 定位城市。所以留一个手工入口，
看到官方公众号/票务 App 的消息就补一条，成本很低但能兜住长尾。

字段：
  name       演出名称（必填）
  venue      "stadium" 或 "gym"，留空则自动按关键词判断
  dates      ["2026-09-26", "2026-09-27"]
  price      票价档位，字符串，随意写
  organiser  举办单位
  on_sale    开票时间，字符串
  url        购票或公告链接
  note       备注
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import (GYMNASIUM, STADIUM, load_json, make_event,  # noqa: E402
                    match_venue, extract_dates)

SOURCE_NAME = "人工整理"

_VENUE_MAP = {
    "stadium": STADIUM,
    "gym": GYMNASIUM,
    "gymnasium": GYMNASIUM,
    "体育馆": GYMNASIUM,
    "体育场": STADIUM,
}


def collect(path: str, verbose: bool = True) -> list[dict]:
    rows = load_json(path, [])
    if not isinstance(rows, list):
        return []

    events = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue

        # 场馆：优先用显式声明，其次从名称/场馆文本里猜
        key = str(row.get("venue", "")).strip().lower()
        venue = _VENUE_MAP.get(key)
        evidence = "explicit"
        if venue is None:
            guess = match_venue(" ".join([
                str(row.get("venue", "")), str(row.get("venue_raw", "")),
                row["name"], str(row.get("note", "")),
            ]))
            if guess:
                venue, evidence = guess
            else:
                venue, evidence = STADIUM if "体育场" in row["name"] else GYMNASIUM, "guessed"

        dates = list(row.get("dates") or [])
        if not dates:
            dates = extract_dates(" ".join([
                str(row.get("date_text", "")), str(row.get("note", "")),
            ]))

        events.append(make_event(
            name=row["name"],
            venue=venue,
            dates=dates,
            source=SOURCE_NAME,
            source_url=row.get("url", ""),
            price=row.get("price", ""),
            organiser=row.get("organiser", ""),
            on_sale=row.get("on_sale", ""),
            raw_venue=row.get("venue_raw", ""),
            evidence=f"manual/{evidence}",
        ))

    if verbose:
        print(f"  [manual] 读入 {len(events)} 条")
    return events
