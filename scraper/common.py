# -*- coding: utf-8 -*-
"""
公用模块：HTTP 抓取、场馆归一化、演出去重与标准化。

设计原则
--------
1. 只依赖 Python 标准库，零第三方包，方便在 GitHub Actions 里直接跑。
2. 所有网络请求统一走 fetch()，内置超时、重试、限速和 UA，避免给源站压力。
3. 场馆匹配集中在这里，新增数据源时不需要各写一套。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 每个域名之间的最小请求间隔（秒），礼貌抓取，避免触发风控
_MIN_INTERVAL = 0.8
_last_hit: dict[str, float] = {}


class FetchError(Exception):
    pass


def fetch(url: str, *, referer: str | None = None, timeout: int = 25,
          retries: int = 3, encoding: str | None = None) -> str:
    """抓取一个 URL 并返回解码后的文本。失败时重试，最终抛 FetchError。"""
    host = urllib.parse.urlsplit(url).netloc
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if referer:
        headers["Referer"] = referer

    last_err = None
    for attempt in range(1, retries + 1):
        # 同一域名的请求之间保持间隔
        gap = time.time() - _last_hit.get(host, 0)
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap + random.uniform(0, 0.3))

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                raw = resp.read()
            _last_hit[host] = time.time()
            enc = encoding or _sniff_encoding(raw)
            return raw.decode(enc, "ignore")
        except Exception as exc:  # noqa: BLE001
            _last_hit[host] = time.time()
            last_err = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise FetchError(f"{url} -> {last_err}")


def _sniff_encoding(raw: bytes) -> str:
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore").lower()
        if enc in ("gb2312", "gbk", "gb18030"):
            return "gb18030"
        return enc
    return "utf-8"


# ---------------------------------------------------------------- 场馆归一化

# 大运中心是唯一目标场馆，但各平台写法差异很大，统一归一到两个标准名
STADIUM = "深圳大运中心体育场"
GYMNASIUM = "深圳大运中心体育馆"
UNKNOWN = "深圳大运中心"

# 只要文本里出现这些词，就认为是大运中心的演出
_VENUE_GATE = ("大运中心", "大运体育中心", "大运场馆", "龙岗大运")
# 主体育场（露天，6 万座，大型演唱会）
_STADIUM_HINT = ("体育场", "主体育场", "田径场", "stadium")
# 室内体育馆（1.6 万座）
_GYM_HINT = ("体育馆", "室内馆", "gymnasium", "arena")


def match_venue(text: str) -> tuple[str, str] | None:
    """判断一段文本是否指向大运中心，返回 (标准场馆名, 命中依据)。

    命中依据用于回溯，方便人工核对误判。
    """
    if not text:
        return None
    low = text.lower()

    gate = next((g for g in _VENUE_GATE if g in low), None)
    if not gate:
        return None

    # 先看有没有明确的「体育场 / 体育馆」后缀
    if any(h in low for h in _STADIUM_HINT):
        return STADIUM, f"{gate}+体育场"
    if any(h in low for h in _GYM_HINT):
        return GYMNASIUM, f"{gate}+体育馆"

    # 大运中心本身就同时包含一体育场一体育馆，无法区分时归到父级
    return UNKNOWN, gate


def normalise_venue_name(raw: str) -> str:
    """把各平台写法压成统一形式，用于展示与去重。"""
    s = re.sub(r"\s+", "", raw or "")
    s = s.replace("深圳市", "深圳").replace("广东省深圳市", "深圳")
    return s


# ---------------------------------------------------------------- 日期处理

_DATE_PATTERNS = (
    r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})",
    r"(\d{4})(\d{2})(\d{2})",
)


def extract_dates(text: str) -> list[str]:
    """从任意文本里抽出所有日期，统一成 YYYY-MM-DD（升序、去重）。"""
    if not text:
        return []
    found = set()
    for pat in _DATE_PATTERNS:
        for m in re.finditer(pat, text):
            y, mo, d = (int(x) for x in m.groups())
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                found.add(f"{y:04d}-{mo:02d}-{d:02d}")
    return sorted(found)


def is_future(date_str: str, today: str | None = None) -> bool:
    today = today or time.strftime("%Y-%m-%d")
    return bool(date_str) and date_str >= today


# ---------------------------------------------------------------- 事件模型

def make_event(*, name: str, venue: str, dates: list[str], source: str,
               source_url: str = "", price: str = "", organiser: str = "",
               on_sale: str = "", raw_venue: str = "", evidence: str = "",
               **extra) -> dict:
    """构造一条标准化演出记录。"""
    dates = [d for d in dict.fromkeys(dates) if d]
    event = {
        "name": (name or "").strip(),
        "venue": venue,
        "venue_raw": raw_venue or venue,
        "dates": dates,
        "start_date": dates[0] if dates else "",
        "end_date": dates[-1] if dates else "",
        "price": (price or "").strip(),
        "organiser": (organiser or "").strip(),
        "on_sale": (on_sale or "").strip(),
        "source": source,
        "source_url": source_url,
        "evidence": evidence,
    }
    if extra:
        event.update(extra)
    event["id"] = _event_id(event)
    return event


def _event_id(event: dict) -> str:
    key = "|".join([
        _norm_key(event.get("name", "")),
        event.get("venue", ""),
        event.get("start_date", ""),
    ])
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _norm_key(name: str) -> str:
    """把演出名压成可比较的形态，用于跨平台去重。"""
    s = name or ""
    s = re.sub(r"[（(【\[].*?[)）】\]]", "", s)
    s = re.sub(r"[\s·—\-–_|丨、,，。.！!？?「」《》\"'：:]+", "", s)
    for noise in ("世界巡回演唱会", "巡回演唱会", "巡演", "演唱会",
                  "音乐会", "深圳站", "广东省营业性演出准予许可决定"):
        s = s.replace(noise, "")
    return s.lower()


def merge_events(events: list[dict]) -> list[dict]:
    """跨源合并：同一场演出只在最完整的那条上保留，并记录所有来源。

    合并键 = 归一化名称 + 场馆 + 起始日期。名称相同但场馆未定的记录，
    只要日期与已定馆记录重合，也会被合并进来。
    """
    merged: list[dict] = []
    index: dict[tuple[str, str], dict] = {}

    def score(e: dict) -> int:
        return sum(bool(e.get(k)) for k in
                   ("dates", "price", "organiser", "on_sale", "source_url"))

    for ev in events:
        if not ev.get("name"):
            continue
        key_name = _norm_key(ev["name"])
        date = ev.get("start_date") or ""
        venue = ev.get("venue") or ""

        # 先按 名称+场馆+日期 精确找
        hit = index.get((key_name, venue, date))
        # 再按 名称+日期 兜底（场馆一个源判定了、另一个没判定）
        if hit is None:
            hit = next((v for k, v in index.items()
                        if k[0] == key_name and k[2] == date), None)

        if hit is None:
            ev = dict(ev)
            ev["sources"] = [{"name": ev["source"], "url": ev.get("source_url", "")}]
            merged.append(ev)
            index[(key_name, venue, date)] = ev
            continue

        # 合并：日期取并集，空的字段互相补全，来源累加
        union_dates = sorted(set(hit.get("dates") or []) | set(ev.get("dates") or []))
        if score(ev) > score(hit):
            merged[merged.index(hit)] = ev
            ev["sources"] = hit.get("sources", [])
            hit = ev
        for field in ("price", "organiser", "on_sale", "source_url", "evidence"):
            if not hit.get(field) and ev.get(field):
                hit[field] = ev[field]
        # 演出一旦定档，各源解析出的场次可能不全（一个源给首场，另一个给全量），取并集
        if union_dates:
            hit["dates"] = union_dates
            hit["start_date"] = union_dates[0]
            hit["end_date"] = union_dates[-1]
        # 同源可能有多条公告（先审批后变更），标签里只保留一条，避免重复展示
        srcs = hit.setdefault("sources", [])
        old = next((s for s in srcs if s.get("name") == ev["source"]), None)
        if old is None:
            srcs.append({"name": ev["source"], "url": ev.get("source_url", "")})
        elif not old.get("url") and ev.get("source_url"):
            old["url"] = ev["source_url"]
        # 场馆判定从「未知」升级为具体场馆
        if hit.get("venue") == UNKNOWN and ev.get("venue") not in (None, UNKNOWN):
            hit["venue"] = ev["venue"]

    for ev in merged:
        ev["dates"] = sorted(set(ev.get("dates") or []))
        ev["start_date"] = ev["dates"][0] if ev["dates"] else ev.get("start_date", "")
        ev["end_date"] = ev["dates"][-1] if ev["dates"] else ev.get("end_date", "")
        ev["confirmed"] = ev.get("venue") != UNKNOWN

    merged.sort(key=lambda e: (e.get("start_date") or "9999", e["name"]))
    return merged


def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default
    return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
