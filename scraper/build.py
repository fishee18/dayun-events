# -*- coding: utf-8 -*-
"""
构建入口：跑所有数据源 → 归一化去重 → 输出 events.json → 推送更新提醒。

用法
----
    python scraper/build.py --pages 10 --since 2026-09-01
    python scraper/build.py --rebuild --no-notify     # 忽略缓存全量重抓
    python scraper/build.py --sources gd,manual,law   # 指定启用的源
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from common import is_future, load_json, merge_events, save_json  # noqa: E402
from sources import gd_culture, gd_law_publicity, manual  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
SITE_DIR = os.path.join(ROOT, "site")
EVENTS_JSON = os.path.join(DATA_DIR, "events.json")
CACHE_JSON = os.path.join(DATA_DIR, "cache.json")
NEW_JSON = os.path.join(DATA_DIR, "new_events.json")

DEFAULT_SINCE = "2026-09-01"      # 只要 2026 年 9 月以后的演出
DEFAULT_SOURCES = "gd,manual"     # law 源当前可用性见 gd_law_publicity.py 顶部说明


def banner(msg: str) -> None:
    print()
    print("-" * 62)
    print(msg)
    print("-" * 62)


def build_standalone(payload: dict) -> str | None:
    """把数据内联进 index.html，生成可双击直接打开的单文件版本。

    浏览器对 file:// 下的 fetch 有 CORS 限制，所以本地直接打开 site/index.html
    会读不到 events.json。这个版本把数据写进 window.__DATA__ 绕开该限制。
    """
    src = os.path.join(SITE_DIR, "index.html")
    if not os.path.exists(src):
        return None
    with open(src, "r", encoding="utf-8") as fh:
        html = fh.read()

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")          # 避免提前闭合 script 标签
    inject = f'<script>window.__DATA__={data};</script>\n'
    out_html = html.replace("<body>", "<body>\n" + inject, 1)

    out = os.path.join(ROOT, "大运中心演出日历.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(out_html)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取深圳大运中心演出信息并生成网站数据")
    ap.add_argument("--pages", type=int, default=10,
                    help="抓取审批公告的页数，每页约 20 条（首次建议 30 回补历史）")
    ap.add_argument("--since", default=os.environ.get("DAYUN_SINCE", DEFAULT_SINCE),
                    help=f"只保留该日期及以后的演出，默认 {DEFAULT_SINCE}")
    ap.add_argument("--rebuild", action="store_true", help="忽略 seen 缓存，全量重抓")
    ap.add_argument("--no-notify", action="store_true", help="不发送更新提醒")
    ap.add_argument("--sources", default=os.environ.get("DAYUN_SOURCES", DEFAULT_SOURCES),
                    help=f"启用的数据源，逗号分隔，默认 {DEFAULT_SOURCES}")
    ap.add_argument("--max-age-days", type=int, default=240,
                    help="公告发布日期超过该天数就跳过（回补时用来减少请求）")
    args = ap.parse_args()

    enabled = {s.strip() for s in args.sources.split(",") if s.strip()}
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 62)
    print(f"深圳大运中心演出信息更新  {started}")
    print(f"保留档期：{args.since} 起 ｜ 启用源：{','.join(sorted(enabled))}")
    print("=" * 62)

    # ---------------------------------------------------------- 1. 抓取
    collected: list[dict] = []

    # 公告缓存（URL -> 事件 / None），既做去重也做存储
    cache = {} if args.rebuild else load_json(CACHE_JSON, {})
    if isinstance(cache, list):               # 兼容早期只存 URL 列表的格式
        cache = {}
    cache.setdefault("gd_culture", {})
    if not isinstance(cache.get("gd_culture"), dict):
        cache["gd_culture"] = {}

    if "gd" in enabled:
        banner("[1/5] 广东省文旅厅 审批结果公告")
        try:
            collected += gd_culture.collect(
                cache=cache["gd_culture"],
                max_pages=args.pages,
                max_age_days=args.max_age_days,
            )
        except Exception:  # noqa: BLE001
            print("  [gd] 源异常，跳过（不影响其他源）：")
            traceback.print_exc()
    else:
        print("\n[1/5] 广东省文旅厅 —— 未启用，跳过")

    if "law" in enabled:
        banner("[2/5] 广东省行政执法信息公示平台（行政许可结果）")
        try:
            collected += gd_law_publicity.collect(since=args.since)
        except Exception:  # noqa: BLE001
            print("  [law] 源异常，跳过：")
            traceback.print_exc()
    else:
        print("\n[2/5] 行政执法信息公示平台 —— 未启用（该平台结果接口当前返回 503）")

    banner("[3/5] 人工补录表 data/manual.json")
    try:
        collected += manual.collect(os.path.join(DATA_DIR, "manual.json"))
    except Exception:  # noqa: BLE001
        print("  [manual] 读取失败：")
        traceback.print_exc()

    if not collected:
        print("\n所有数据源都没有返回记录，保留上一次的数据文件。")
        return 0

    # ------------------------------------------- 2. 按日期窗口过滤
    since = args.since
    kept, dropped = [], 0
    for ev in collected:
        dates = [d for d in (ev.get("dates") or []) if d >= since]
        if not dates:
            if ev.get("dates"):
                dropped += 1
                continue
            # 完全没解析到日期的，先留着，让人工去补
            kept.append(ev)
            continue
        ev["dates"] = dates
        kept.append(ev)
    print(f"\n日期过滤（{since} 起）：保留 {len(kept)} 条，剔除 {dropped} 条已过档期")

    # ------------------------------------------- 3. 合并去重
    banner(f"[4/5] 跨源合并去重（原始 {len(kept)} 条）")
    events = merge_events(kept)

    # 主列表只放「还没演完」的；已结束但落在保留窗口内的单列一份，便于回看
    today = time.strftime("%Y-%m-%d")
    upcoming = [e for e in events if not e.get("end_date") or e["end_date"] >= today]
    finished = [e for e in events if e.get("end_date") and e["end_date"] < today]
    print(f"合并后 {len(events)} 条：未结束 {len(upcoming)} 条，"
          f"窗口内已结束 {len(finished)} 条")

    upcoming.sort(key=lambda e: (e.get("start_date") or "9999-12-31", e["name"]))

    # ------------------------------------------- 4. 对比上次，找新增
    previous = load_json(EVENTS_JSON, {"events": []})
    prev_map = {e.get("id"): e for e in previous.get("events", [])}
    new_events = []
    for ev in upcoming:
        old = prev_map.get(ev["id"])
        if old is None:
            # 名称+日期相同但 id 变了，视作同一场，不算新增
            if not any(o.get("name") == ev["name"] and
                       o.get("start_date") == ev.get("start_date")
                       for o in previous.get("events", [])):
                new_events.append(ev)
        else:
            for f in ("price", "on_sale", "organiser", "start_date", "end_date"):
                if old.get(f) != ev.get(f) and ev.get(f):
                    ev["changed"] = True
                    break

    print(f"新增 {len(new_events)} 场，信息有变更 "
          f"{sum(1 for e in upcoming if e.get('changed'))} 场")

    # 首次运行不提醒，避免一次性刷屏
    first_run = not previous.get("events")

    # ------------------------------------------- 5. 写出数据
    banner("[5/5] 写出数据文件")
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_date": time.strftime("%Y-%m-%d"),
        "since": since,
        "venue": {"stadium": "深圳大运中心体育场",
                  "gymnasium": "深圳大运中心体育馆"},
        "count": len(upcoming),
        "new_count": len(new_events) if not first_run else 0,
        "sources": sorted(enabled),
        "events": upcoming,
        "recent_finished": finished,
    }
    for ev in upcoming:
        ev.pop("changed", None)

    save_json(EVENTS_JSON, payload)
    save_json(os.path.join(SITE_DIR, "events.json"), payload)
    save_json(CACHE_JSON, cache)
    save_json(NEW_JSON, new_events)

    standalone = build_standalone(payload)

    print(f"  data/events.json 与 site/events.json 已更新（{len(upcoming)} 条）")
    if standalone:
        print(f"  单文件版已生成：{os.path.basename(standalone)}（双击即开，无需服务器）")
    print(f"  公告缓存 {sum(len(v) for v in cache.values() if isinstance(v, dict))} 条")

    # ------------------------------------------- 6. 提醒
    if args.no_notify or first_run:
        print("\n[+] " + ("首次运行，不发送提醒（避免一次性刷屏）"
                         if first_run else "已指定 --no-notify，跳过提醒"))
    else:
        banner("[+] 发送更新提醒")
        try:
            from notify import notify
            notify(new_events)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    print("\n完成。当前收录：")
    for e in upcoming:
        when = "、".join(e.get("dates") or []) or "日期待定"
        print(f"  · {e['name']}")
        print(f"      {e.get('venue','')} | {when}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
