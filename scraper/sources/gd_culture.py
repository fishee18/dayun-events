# -*- coding: utf-8 -*-
"""
数据源：广东省文化和旅游厅「审批结果公告」。

为什么这是主力源
----------------
按《营业性演出管理条例》，涉港澳台及涉外演出必须由省级文旅部门审批并公示。
公告详情页是一张规整的表单，含：演出名称、举办单位、本地演出日期、演出场所。
所以这个源能在**开票之前**就拿到大运中心的档期，而且没有反爬、不需要登录。

入口：https://whly.gd.gov.cn/audit_newspjggg/index.html
分页：index.html / index_2.html / index_3.html ...（实测共 100+ 页）
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import (FetchError, extract_dates, fetch, make_event,  # noqa: E402
                    match_venue, normalise_venue_name)

BASE = "https://whly.gd.gov.cn/audit_newspjggg/"
SOURCE_NAME = "广东省文旅厅审批公告"

# 桌面版和移动版是同一份内容，两条路径互为回退
LIST_BASES = (
    "https://whly.gd.gov.cn/audit_newspjggg/index{page}.html",
    "https://whly.gd.gov.cn/audit_newspjggg/mindex{page}.html",
)

# 详情页里的字段标签，用于定位取值边界
LABELS = (
    "许可/备案事项", "演出名称", "举办单位", "证号", "演员人数",
    "入境停留日期", "是否属于或含外国人在中国短期工作任务",
    "本地演出日期", "演出场所",
)

_ITEM_RE = re.compile(
    r'<a\s+href="(?P<url>[^"]+?/content/post_\d+\.html)"\s+title="(?P<title>[^"]*)"'
    r'[^>]*>.*?</a>.*?<span\s+class="list_time">\s*(?P<date>[^<]+?)\s*</span>',
    re.S,
)


def list_page_url(page: int, mobile: bool = False) -> str:
    """第 1 页是 index.html，第 N 页是 index_N.html（移动版为 mindex*）。"""
    prefix = "mindex" if mobile else "index"
    return f"{BASE}{prefix}.html" if page <= 1 else f"{BASE}{prefix}_{page}.html"


def fetch_list(page: int) -> list[dict]:
    """抓一页列表，返回 [{url, title, published}]。

    桌面版取不到时自动回退到移动版（两条路径内容一致，用户实测均可访问）。
    """
    last_err = None
    for mobile in (False, True):
        try:
            html = fetch(list_page_url(page, mobile), referer=BASE)
        except FetchError as exc:
            last_err = exc
            continue
        out = []
        for m in _ITEM_RE.finditer(html):
            out.append({
                "url": m.group("url"),
                "title": m.group("title").strip(),
                "published": m.group("date").strip(),
            })
        if out:
            return out
    if last_err:
        raise last_err
    return []


def _to_lines(html: str) -> list[str]:
    """HTML 转成按行文本，保留标签边界，便于按标签取值。"""
    h = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|li|tr|td|th|h\d)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return [ln.strip() for ln in h.split("\n") if ln.strip()]


def parse_detail(html: str) -> dict:
    """从详情页抽字段。值是「标签后到下一个标签之间」的所有行拼接。"""
    lines = _to_lines(html)
    idx = {}
    for i, ln in enumerate(lines):
        if ln in LABELS and ln not in idx:
            idx[ln] = i

    def value_after(label: str) -> str:
        i = idx.get(label)
        if i is None:
            return ""
        buf = []
        for ln in lines[i + 1:]:
            if ln in LABELS or ln.startswith("备注") or "文化和旅游厅" in ln:
                break
            buf.append(ln)
            if len(buf) >= 4:      # 值不会太长，防止吃穿整个页面
                break
        return " ".join(buf).strip()

    name = value_after("演出名称")
    if not name:
        # 兜底：从标题里取括号内容
        m = re.search(r"广东省营业性演出准予许可决定[（(]([^）)]+)[）)]", html)
        name = m.group(1).strip() if m else ""

    return {
        "name": name,
        "organiser": value_after("举办单位"),
        "date_text": value_after("本地演出日期"),
        "venue_raw": value_after("演出场所"),
    }


def _pub_date(text: str) -> str:
    """"2026年08月27日" -> "2026-08-27"。"""
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text or "")
    if not m:
        return ""
    return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())


def collect(cache: dict, max_pages: int = 12, verbose: bool = True,
            max_age_days: int | None = 240) -> list[dict]:
    """抓取公告并筛出大运中心的演出。

    cache 是「公告 URL -> 解析出的事件；不是大运中心则存 None」的字典，
    由调用方持久化。它同时承担两个作用：

    * 去重：已解析过的公告不再请求详情页（首轮慢，之后每天只处理新增几条）
    * 存储：历史公告解析出的演出不会因为翻过页而丢失——缓存本身就是数据库

    返回缓存中所有有效事件（日期窗口过滤由调用方负责）。
    """
    # 兼容早期只存 URL 列表的格式
    if isinstance(cache, list):
        cache = {u: "__unknown__" for u in cache}
        cache["__migrated__"] = None

    checked = skipped_old = 0

    cutoff = ""
    if max_age_days:
        cutoff = time.strftime("%Y-%m-%d",
                               time.localtime(time.time() - max_age_days * 86400))

    for page in range(1, max_pages + 1):
        try:
            items = fetch_list(page)
        except FetchError as exc:
            if verbose:
                print(f"  [gd] 第 {page} 页抓取失败：{exc}")
            break
        if not items:
            break

        # 列表页只保留演出审批公告，文物/考古类直接跳过
        items = [it for it in items if "营业性演出准予许可决定" in it["title"]]
        if verbose:
            print(f"  [gd] 第 {page} 页：{len(items)} 条演出公告")

        for it in items:
            url = it["url"]
            if cache.get(url, "__miss__") != "__miss__":
                continue          # 已解析过，跳过
            # 公告一般提前 1-2 个月发布，发布很久以前的基本都已演完，跳过省请求
            pub = _pub_date(it["published"])
            if cutoff and pub and pub < cutoff:
                skipped_old += 1
                cache[url] = None
                continue

            try:
                html = fetch(url, referer=list_page_url(page))
            except FetchError as exc:
                if verbose:
                    print(f"    跳过（抓取失败）：{url} {exc}")
                continue          # 不写缓存，下次还会重试

            info = parse_detail(html)
            checked += 1
            v = match_venue(info["venue_raw"] or it["title"])
            if not v:
                cache[url] = None
                continue

            venue, evidence = v
            dates = extract_dates(info["date_text"]) or extract_dates(it["title"])
            event = make_event(
                name=info["name"] or it["title"],
                venue=venue,
                dates=dates,
                source=SOURCE_NAME,
                source_url=url,
                organiser=info["organiser"],
                raw_venue=normalise_venue_name(info["venue_raw"]),
                evidence=evidence,
                published=it["published"],
                date_text=info["date_text"],
            )
            cache[url] = event
            if verbose:
                print(f"    ✓ 命中：{info['name']} | {venue} | {dates}")

    # 缓存中的历史命中一并返回
    events = [v for v in cache.values() if isinstance(v, dict)]

    # 清理：已无意义的历史条目 + 控制缓存总量
    _prune(cache, cutoff)

    if verbose:
        print(f"  [gd] 新解析详情页 {checked} 个，跳过过旧公告 {skipped_old} 条，"
              f"缓存内累计命中 {len(events)} 场")
    return events


def _prune(cache: dict, cutoff: str = "", limit: int = 6000) -> None:
    """丢掉演出早已结束的条目；仍超量就按插入顺序丢最旧的。"""
    if cutoff:
        for url in [u for u, v in cache.items()
                    if isinstance(v, dict) and v.get("end_date")
                    and v["end_date"] < cutoff]:
            cache.pop(url, None)
    while len(cache) > limit:
        cache.pop(next(iter(cache)), None)
