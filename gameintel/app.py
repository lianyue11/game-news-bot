from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Item:
    title: str
    link: str
    source: str
    published: datetime
    summary: str
    category: str = "其他"
    zh_summary: str = ""
    importance: int = 0


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_time(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def collect(cfg: dict) -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["lookback_hours"])
    items: list[Item] = []
    for feed in cfg["feeds"]:
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries:
            published = parse_time(entry)
            link = canonical_url(entry.get("link", ""))
            if not link or published < cutoff:
                continue
            items.append(Item(
                title=clean_text(entry.get("title", "无标题")), link=link,
                source=feed["name"], published=published,
                summary=clean_text(entry.get("summary", entry.get("description", "")))[:1200],
            ))
    return items


def classify(item: Item, cfg: dict) -> str:
    text = f"{item.title} {item.summary}".lower()
    if any(word.lower() in text for word in cfg.get("exclude_keywords", [])):
        return "排除"
    def hit(word: str) -> bool:
        word = word.lower()
        if re.fullmatch(r"[a-z0-9 $-]+", word):
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text))
        return word in text
    scores = {cat: sum(1 for word in words if hit(word))
              for cat, words in cfg["categories"].items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "其他"


def importance_score(item: Item, cfg: dict) -> int:
    text = f"{item.title} {item.summary}".lower()
    score = int(cfg.get("source_weights", {}).get(item.source, 1))
    score += 2 if item.category in {"政策监管", "行业动态"} else 1
    score += 2 if any(word.lower() in text for word in cfg.get("high_impact_keywords", [])) else 0
    score -= 3 if any(word.lower() in text for word in cfg.get("low_signal_keywords", [])) else 0
    return score


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT / "gameintel.db")
    conn.execute("CREATE TABLE IF NOT EXISTS sent (url_hash TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
    return conn


def unseen(items: list[Item]) -> list[Item]:
    conn = db()
    result = []
    for item in items:
        key = hashlib.sha256(item.link.encode()).hexdigest()
        if not conn.execute("SELECT 1 FROM sent WHERE url_hash=?", (key,)).fetchone():
            result.append(item)
    conn.close()
    return result


def mark_sent(items: list[Item]) -> None:
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany("INSERT OR IGNORE INTO sent VALUES (?, ?)",
                     [(hashlib.sha256(x.link.encode()).hexdigest(), now) for x in items])
    conn.commit()
    conn.close()


def summarize(items: list[Item]) -> None:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        for x in items:
            x.zh_summary = (x.summary or x.title)[:180]
        return
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload_items = [{"id": i, "title": x.title, "source_text": x.summary[:700]}
                     for i, x in enumerate(items)]
    prompt = ("你是全球游戏行业情报编辑。逐条用简体中文写不超过80字的客观摘要，不补充原文没有的信息。"
              "只返回JSON数组，格式为[{\"id\":0,\"summary\":\"...\"}]。材料：" +
              json.dumps(payload_items, ensure_ascii=False))
    try:
        with httpx.Client(timeout=60) as client:
            res = client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                              json={"model": os.getenv("LLM_MODEL", "gpt-4.1-mini"),
                                    "messages": [{"role": "user", "content": prompt}], "temperature": 0.2})
            res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        mapped = {int(x["id"]): x["summary"] for x in json.loads(content)}
        for i, item in enumerate(items):
            item.zh_summary = mapped.get(i, (item.summary or item.title)[:180])
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = clean_text(exc.response.text)[:500]
        print(f"AI 摘要暂不可用，改用原文摘要：{exc} {detail}")
        for item in items:
            item.zh_summary = (item.summary or item.title)[:180]


def render(items: list[Item], title: str) -> str:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    lines = [f"# {title}｜{today}", f"> 共 {len(items)} 条，经过去重与分类。"]
    current = None
    for item in items:
        if item.category != current:
            current = item.category
            lines += ["", f"## {current}"]
        lines += [f"**{item.title}**", item.zh_summary,
                  f"来源：{item.source} · [查看原文]({item.link})", ""]
    return "\n".join(lines)


def post_wecom(text: str) -> None:
    url = os.getenv("WECOM_WEBHOOK")
    if not url:
        return
    # 企业微信 markdown 单条有长度限制，按条数边界拆分会在后续版本增强。
    res = httpx.post(url, json={"msgtype": "markdown", "markdown": {"content": text[:4000]}}, timeout=30)
    res.raise_for_status()


def post_feishu(text: str) -> None:
    url = os.getenv("FEISHU_WEBHOOK")
    if not url:
        return
    payload = {"msg_type": "text", "content": {"text": re.sub(r"[#*>]", "", text)}}
    secret = os.getenv("FEISHU_SECRET")
    if secret:
        timestamp = str(int(time.time()))
        signature = base64.b64encode(hmac.new(f"{timestamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()).decode()
        payload.update({"timestamp": timestamp, "sign": signature})
    res = httpx.post(url, json=payload, timeout=30)
    res.raise_for_status()


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    if not dry_run and not (os.getenv("WECOM_WEBHOOK") or os.getenv("FEISHU_WEBHOOK")):
        raise RuntimeError("未配置 WECOM_WEBHOOK 或 FEISHU_WEBHOOK，已停止以避免误标记为已推送。")
    items = unseen(collect(cfg))
    for item in items:
        item.category = classify(item, cfg)
        item.importance = importance_score(item, cfg)
    items = [x for x in items if x.category not in {"其他", "排除"}]

    # 先选严格达到门槛的内容；数量不足时，只从可信度较高的来源补足。
    # 这样可以增加国内消息覆盖，又不会用传闻、攻略或普通评论文章凑数。
    threshold = cfg.get("min_importance_score", 4)
    min_items = cfg.get("min_items", 5)
    source_weights = cfg.get("source_weights", {})
    primary = [x for x in items if x.importance >= threshold]
    fallback = [
        x for x in items
        if x.importance == threshold - 1
        and source_weights.get(x.source, 1) >= 3
        and x not in primary
    ]
    selected = primary
    if len(selected) < min_items:
        selected += fallback[:max(0, min_items - len(selected))]

    domestic_sources = {
        "腾讯游戏官方", "网易游戏官方", "米哈游官方", "鹰角网络官方",
        "叠纸游戏官方", "国家新闻出版署", "游戏葡萄", "中国音数协游戏工委",
    }
    items = sorted(
        selected,
        key=lambda x: (x.importance, x.source in domestic_sources, x.published),
        reverse=True,
    )[:cfg["max_items"]]
    if not items:
        print("没有发现新的行业动态。")
        return
    summarize(items)
    report = render(items, cfg["title"])
    if dry_run:
        print(report)
        return
    post_wecom(report)
    post_feishu(report)
    mark_sent(items)
    print(f"已推送 {len(items)} 条行业动态。")


def main() -> None:
    parser = argparse.ArgumentParser(description="全球游戏行业情报推送器")
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("run", help="立即执行一次")
    once.add_argument("--dry-run", action="store_true", help="仅在终端预览")
    sub.add_parser("daemon", help="按 config.yaml 定时执行")
    args = parser.parse_args()
    if args.command == "run":
        run(args.dry_run)
        return
    cfg = load_config()
    scheduler = BlockingScheduler(timezone=cfg["timezone"])
    scheduler.add_job(run, CronTrigger.from_crontab(cfg["schedule"], timezone=cfg["timezone"]),
                      max_instances=1, coalesce=True)
    print(f"定时服务已启动：{cfg['schedule']} ({cfg['timezone']})")
    scheduler.start()
