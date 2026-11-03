# -*- coding: utf-8 -*-
"""
老张的情报站 - 全球新闻采集翻译脚本
抓 RSS -> LLM 翻译成中文 -> 生成静态 index.html
"""
import os
import sys
import json
import html
import socket
import datetime
import feedparser
from dateutil import parser as dateparser
from openai import OpenAI

# 全局 10 秒超时，防止某个 RSS 源卡死整个流程
socket.setdefaulttimeout(10)

# ============ 新闻源配置（时政 / 财金 / 军事 / 科技） ============
SOURCES = {
    "时政": [
        ("BBC World",        "http://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Guardian World",   "https://www.theguardian.com/world/rss"),
        ("NPR World",        "https://feeds.npr.org/1004/rss.xml"),
        ("Al Jazeera",       "https://www.aljazeera.com/xml/rss/all.xml"),
        ("AP Top News",      "https://feeds.apnews.com/rss/apf-topnews"),
    ],
    "财金": [
        ("CNBC World",       "https://www.cnbc.com/id/100727363/device/rss/rss.html"),
        ("Financial Times",  "https://www.ft.com/?format=rss"),
        ("MarketWatch",      "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("Yahoo Finance",    "https://finance.yahoo.com/news/rssindex"),
        ("华尔街见闻",        "https://dedicated.wallstreetcn.com/rss.xml"),
    ],
    "军事": [
        ("Defense One",      "https://www.defenseone.com/rss/all/"),
        ("Breaking Defense", "https://breakingdefense.com/feed/"),
        ("The War Zone",     "https://www.twz.com/rss"),
        ("SpaceNews",        "https://spacenews.com/feed/"),
    ],
    "科技": [
        ("TechCrunch",       "https://techcrunch.com/feed/"),
        ("The Verge",        "https://www.theverge.com/rss/index.xml"),
        ("Wired",            "https://www.wired.com/feed/rss"),
        ("Ars Technica",     "https://feeds.arstechnica.com/arstechnica/index"),
        ("Hacker News",      "https://hnrss.org/frontpage"),
        ("爱范儿",           "https://www.ifanr.com/feed"),
    ],
}

MAX_PER_SOURCE = 5
MAX_PER_CATEGORY = 15

# ============ LLM 配置 ============
API_KEY   = os.environ.get("OPENAI_API_KEY", "")
BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL     = os.environ.get("OPENAI_MODEL", "deepseek-chat")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL) if API_KEY else None


def fetch_category(category, feeds):
    """抓一个分类下所有 RSS，返回条目列表"""
    items = []
    seen = set()
    for name, url in feeds:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:MAX_PER_SOURCE]:
                link = e.get("link", "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                title = e.get("title", "").strip()
                published = e.get("published", "") or e.get("updated", "")
                summary = e.get("summary", "").strip()[:200]
                items.append({
                    "category": category,
                    "source": name,
                    "title_en": title,
                    "link": link,
                    "published": published,
                    "summary_en": summary,
                })
        except Exception as ex:
            print(f"[WARN] 抓取 {name} 失败: {ex}", file=sys.stderr)
    return items[:MAX_PER_CATEGORY]


def has_chinese(text):
    return any('\u4e00' <= ch <= '\u9fff' for ch in text)


def translate_batch(items):
    """批量把英文标题翻译成中文，原地写回 title_cn。中文标题自动跳过。"""
    # 中文标题直接用，不翻译
    to_translate = []
    for it in items:
        if has_chinese(it["title_en"]):
            it["title_cn"] = it["title_en"]
        else:
            to_translate.append(it)

    if not to_translate:
        return
    if not client:
        for it in to_translate:
            it["title_cn"] = it["title_en"]
        return

    # 编号后让 LLM 翻译，返回 序号->中文 的字典
    numbered = "\n".join(f"{i+1}. {it['title_en']}" for i, it in enumerate(to_translate))
    prompt = (
        "把以下英文新闻标题翻译成简洁、地道的中文（时政财经军事科技类新闻，"
        "保留专有名词和机构名）。只返回一个 JSON 对象，key 是序号（字符串），value 是中文译文，不要解释。\n\n"
        + numbered
    )
    import time
    mp = {}
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=60,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            data = json.loads(raw)
            if all(isinstance(v, str) for v in data.values()):
                mp = {int(k): v for k, v in data.items()}
            else:
                for v in data.values():
                    if isinstance(v, dict):
                        mp = {int(k): str(val) for k, val in v.items()}
                        break
            if mp:
                break
        except Exception as ex:
            print(f"[WARN] 第{attempt+1}次翻译失败: {ex}", file=sys.stderr)
            time.sleep(5)
    for i, it in enumerate(to_translate):
        it["title_cn"] = mp.get(i + 1, it["title_en"])
    for it in items:
        it["summary_cn"] = ""


def render_html(category_items, generated_at):
    """生成自包含 index.html"""
    cat_colors = {
        "时政": "#e74c3c",
        "财金": "#f39c12",
        "军事": "#9b59b6",
        "科技": "#3498db",
    }
    cat_icons = {"时政": "🌍", "财金": "💰", "军事": "🎖️", "科技": "🔬"}

    sections = []
    for cat, items in category_items.items():
        color = cat_colors.get(cat, "#3498db")
        icon = cat_icons.get(cat, "📰")
        rows = []
        for it in items:
            title_cn = html.escape(it["title_cn"])
            title_en = html.escape(it["title_en"])
            link = html.escape(it["link"])
            source = html.escape(it["source"])
            rows.append(f"""
                <a class="news-item" href="{link}" target="_blank" rel="noopener">
                    <div class="news-title">{title_cn}</div>
                    <div class="news-meta">
                        <span class="src">{icon} {source}</span>
                        <span class="en">{title_en}</span>
                    </div>
                </a>""")
        sections.append(f"""
        <section class="category">
            <h2 style="border-color:{color}"><span style="color:{color}">{icon}</span> {cat} <small>{len(items)} 条</small></h2>
            <div class="news-list">{''.join(rows)}</div>
        </section>""")

    now_str = generated_at.strftime("%Y-%m-%d %H:%M")
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>老张的情报站</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif;
    background: #0d1117; color: #e6edf3; line-height:1.6;
  }}
  header {{
    text-align:center; padding:40px 20px 30px;
    background: linear-gradient(135deg,#1a1f2e 0%,#0d1117 100%);
    border-bottom: 2px solid #d4a017;
  }}
  header h1 {{
    font-size: 3.2em; font-weight:900; letter-spacing:8px;
    background: linear-gradient(90deg,#d4a017,#ffd700,#d4a017);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    text-shadow: 0 0 30px rgba(212,175,55,.3);
  }}
  header .sub {{ color:#8b949e; margin-top:10px; font-size:1.05em; }}
  header .time {{ color:#d4a017; margin-top:6px; font-size:.95em; }}
  .container {{ max-width:1200px; margin:0 auto; padding:30px 20px; }}
  .category {{ margin-bottom:40px; }}
  .category h2 {{
    font-size:1.6em; padding-bottom:10px; margin-bottom:18px;
    border-bottom:2px solid; display:flex; align-items:baseline; gap:10px;
  }}
  .category h2 small {{ color:#8b949e; font-size:.6em; font-weight:normal; }}
  .news-list {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:12px; }}
  .news-item {{
    background:#161b22; border:1px solid #30363d; border-radius:8px;
    padding:14px 16px; text-decoration:none; color:inherit;
    transition: transform .15s, border-color .15s; display:block;
  }}
  .news-item:hover {{ transform:translateY(-2px); border-color:#d4a017; }}
  .news-title {{ font-size:1.02em; font-weight:600; color:#e6edf3; margin-bottom:8px; }}
  .news-meta {{ font-size:.78em; color:#8b949e; }}
  .news-meta .src {{ color:#d4a017; margin-right:10px; }}
  .news-meta .en {{ display:block; margin-top:3px; font-style:italic; opacity:.7; }}
  footer {{ text-align:center; padding:30px; color:#6e7681; font-size:.85em; border-top:1px solid #21262d; }}
</style>
</head>
<body>
<header>
  <h1>老张的情报站</h1>
  <div class="sub">全球时政 · 财金 · 军事 · 科技 每日情报速递</div>
  <div class="time">更新时间：{now_str}（北京时间）</div>
</header>
<div class="container">
  {''.join(sections)}
</div>
<footer>老张的情报站 · 自动采集自全球主流媒体 RSS · 中文由 AI 翻译生成</footer>
</body>
</html>"""
    return html_doc


def main():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)  # 北京时间
    all_items = {}
    for cat, feeds in SOURCES.items():
        print(f"[INFO] 抓取 {cat} ...")
        items = fetch_category(cat, feeds)
        print(f"[INFO] {cat} 拿到 {len(items)} 条，开始翻译...")
        translate_batch(items)
        all_items[cat] = items

    doc = render_html(all_items, now)
    out_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[OK] 已生成 {out_path}，共 {sum(len(v) for v in all_items.values())} 条")


if __name__ == "__main__":
    main()
