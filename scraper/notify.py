# -*- coding: utf-8 -*-
"""
更新提醒：发现新演出时推送。

支持三种通道，全部通过环境变量开启，不配就静默跳过：
  1. PushPlus（微信）   PUSHPLUS_TOKEN
  2. Server 酱（微信）  SERVERCHAN_KEY
  3. 邮件（SMTP）       SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / MAIL_TO

只依赖标准库，GitHub Actions 里可直接用。
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText


def _payload(new_events: list[dict]) -> tuple[str, str]:
    lines = [f"深圳大运中心新增 {len(new_events)} 场演出", ""]
    for e in new_events:
        when = "、".join(e.get("dates") or []) or "日期待定"
        lines.append(f"· {e['name']}")
        lines.append(f"  {e.get('venue', '')}  {when}")
        if e.get("on_sale"):
            lines.append(f"  开票：{e['on_sale']}")
        if e.get("source_url"):
            lines.append(f"  {e['source_url']}")
        lines.append("")
    body = "\n".join(lines)
    title = f"[大运中心] 新增 {len(new_events)} 场演出"
    return title, body


def _post(url: str, data: dict) -> str:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def pushplus(title: str, body: str) -> str:
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        return "skip"
    return _post("https://www.pushplus.plus/send",
                 {"token": token, "title": title, "content": body, "template": "txt"})


def serverchan(title: str, body: str) -> str:
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        return "skip"
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def email(title: str, body: str) -> str:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("MAIL_TO")
    if not all([host, user, pwd, to]):
        return "skip"
    port = int(os.environ.get("SMTP_PORT", "465"))
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = to
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=25) as s:
            s.login(user, pwd)
            s.sendmail(user, to.split(","), msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=25) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, to.split(","), msg.as_string())
    return "sent"


def notify(new_events: list[dict], verbose: bool = True) -> dict:
    if not new_events:
        if verbose:
            print("  没有新增演出，跳过提醒")
        return {}
    title, body = _payload(new_events)
    results = {}
    for name, fn in (("pushplus", pushplus), ("serverchan", serverchan), ("email", email)):
        try:
            results[name] = fn(title, body)
        except Exception as exc:  # noqa: BLE001
            results[name] = f"error: {exc}"
        if verbose:
            print(f"  [notify] {name}: {results[name]}")
    return results
