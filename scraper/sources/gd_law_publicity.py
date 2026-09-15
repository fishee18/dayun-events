# -*- coding: utf-8 -*-
"""
数据源：广东省行政执法信息公示平台 —— 行政执法事后公开 · 执法结果（行政许可）
http://www.xzzfxxgs.gdsf.gov.cn/ApprLawPublicity/result.html

接口已完整逆向出来（下面常量就是浏览器实际发出的请求），但有三点限制，
所以本模块**默认不启用**，等平台恢复了把 --sources 里加上 law 即可：

1. **平台当前不可用（实测）**
   `GET /appr-law-datacenter-service/law/datacenter/result/list` 返回 503，
   而且在真实浏览器里也一样、页面显示「暂无数据」。不是被反爬拦截，是它自己的
   数据服务挂着。同站的 `/appr-law-publicity-service/...` 接口正常返回 200，
   说明只有 datacenter 这一组接口故障。

2. **请求带加密参数**
   浏览器请求里有个 `decryptText`，值形如 `fAJHc2ChItUCczyUVEkyW9szokYO...`，
   由前端 AES 加密生成；密钥线索在 /ApprLawPublicity/js/def.js 的
   `_d / _e / _f` 三个 base64 常量里。要完整复现需要把这段加密逻辑移植过来。
   由于接口一直 503，无法做对照实验，这里先留了 hook（`_decrypt_text`）。

3. **字段侧重不同**
   该平台公示的是「许可决定」记录：决定书文号、案件名称、行政相对人、公示日期、执法主体。
   通常**不含演出档期和演出场所**，所以即便恢复，它的定位是「发现线索」而不是
   「拿到完整场次」。因此本模块只在案件名称里出现「大运」时才产出一条待确认记录。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import UA, UNKNOWN, make_event  # noqa: E402

SOURCE_NAME = "广东省行政执法公示平台"

ROOT = "http://www.xzzfxxgs.gdsf.gov.cn"
# 浏览器实际请求：/appr-law-datacenter-service/law/datacenter/result/list
RESULT_PATH = "/appr-law-datacenter-service/law/datacenter/result/list"
REFERER = ROOT + "/ApprLawPublicity/result.html"

ITEM_TYPE_LICENSE = 4      # 4=行政许可 1=行政处罚 2=行政强制 5=行政检查
AREA_GUANGDONG = "440000"

# 注意：http 站点，标准库默认 SSL 上下文不影响；这里只需要普通 http 请求
_TIMEOUT = 25


def _decrypt_text(payload: dict) -> str:
    """预留 hook：把请求参数按 def.js 里的密钥加密成 decryptText。

    目前返回空串。平台的 datacenter 接口恢复后，若发现必须带该参数，
    在这里补上 AES 加密实现（密钥见 /ApprLawPublicity/js/def.js）。
    """
    return ""


def query(*, item_type: int = ITEM_TYPE_LICENSE, belong_area: str = AREA_GUANGDONG,
          case_name: str = "", legal_dep_name: str = "",
          page_index: int = 1, page_row_num: int = 50) -> dict | None:
    """调用结果列表接口。返回解析后的 JSON，失败返回 None。"""
    params = {
        "r": random.random() * 1000,
        "flag": "",
        "pageIndex": page_index,
        "pageRowNum": page_row_num,
        "itemType": item_type,
        "belongArea": belong_area,
        "decisionNum": "",
        "legalDepName": legal_dep_name,
        "caseName": case_name,
        "decryptText": _decrypt_text({}),
    }
    url = ROOT + RESULT_PATH + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": REFERER,
        "Origin": ROOT,
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        return None


def _rows(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("status")) != "200":
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("dataList", "list", "records", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    return []


def collect(since: str = "", verbose: bool = True, **_) -> list[dict]:
    """查询行政许可结果，挑出与演出审批有关的记录。"""
    # 先用「演出」做关键词查询，缩小结果集
    payload = query(case_name="演出", page_row_num=50)

    if payload is None:
        if verbose:
            print("  [law] 接口无响应（平台该服务当前返回 503），跳过")
        return []

    rows = _rows(payload)
    if verbose:
        print(f"  [law] 接口返回 {len(rows)} 条记录")

    if not rows and verbose:
        print(f"  [law] 状态 {payload.get('status')} / {payload.get('desc')}"
              f" —— 平台侧暂无可查数据")

    events: list[dict] = []
    for row in rows:
        name = " ".join(str(row.get(k, "")) for k in
                        ("caseName", "decisionNum", "itemName") if row.get(k))
        if "大运" not in name:
            continue
        events.append(make_event(
            name=row.get("caseName") or "（行政许可记录）",
            venue=UNKNOWN,
            dates=[],
            source=SOURCE_NAME,
            source_url=REFERER,
            organiser=row.get("partyName") or row.get("relativePerson") or "",
            evidence="执法公示/待确认",
            decision_num=row.get("decisionNum", ""),
            public_date=row.get("publicDate", ""),
            legal_dep=row.get("legalDepName", ""),
        ))
        if verbose:
            print(f"    · 线索：{row.get('caseName')} | {row.get('legalDepName','')}")

    if verbose:
        print(f"  [law] 命中大运中心线索 {len(events)} 条")
    return events
