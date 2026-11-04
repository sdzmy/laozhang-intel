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
    def extract_num_map(obj, depth=0):
        """递归从任意嵌套 JSON 中找 数字键->字符串值 的字典"""
        if depth > 5:
            return {}
        result = {}
        if isinstance(obj, dict):
            # 检查这个 dict 本身是否是 数字->字符串 映射
            if all(str(k).isdigit() and isinstance(v, str) for k, v in obj.items()):
                return {int(k): v for k, v in obj.items()}
            for v in obj.values():
                result.update(extract_num_map(v, depth + 1))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, dict):
                    result.update(extract_num_map(v, depth + 1))
                elif isinstance(v, str) and i < len(to_translate):
                    result[i + 1] = v
        return result

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
            mp = extract_num_map(data)
            if mp:
                break
        except Exception as ex:
            print(f"[WARN] 第{attempt+1}次翻译失败: {ex}", file=sys.stderr)
            time.sleep(5)
    for i, it in enumerate(to_translate):
        it["title_cn"] = mp.get(i + 1, it["title_en"])
    for it in items:
        it["summary_cn"] = ""


# ============ 市场行情（Yahoo Finance API，全球可用） ============
MARKET_INDICES = [
    # (yahoo ticker, 中文名, 市场)
    ("000001.SS", "上证指数",    "A股"),
    ("399001.SZ", "深证成指",    "A股"),
    ("399006.SZ", "创业板指",    "A股"),
    ("^HSI",      "恒生指数",    "港股"),
    ("^DJI",      "道琼斯",      "美股"),
    ("^IXIC",     "纳斯达克",    "美股"),
    ("^GSPC",     "标普500",     "美股"),
    ("^N225",     "日经225",     "日股"),
    ("CL=F",      "WTI原油",     "期货"),
    ("GC=F",      "COMEX黄金",   "期货"),
    ("SI=F",      "COMEX白银",   "期货"),
    ("^TNX",      "美债10年收益率","债市"),
]

import urllib.request
import urllib.error

def _em_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_market():
    """抓主要指数实时行情 + 30日日K线（Yahoo Finance）"""
    results = []
    for ticker, name, market in MARKET_INDICES:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2mo&interval=1d"
            data = _em_get(url, timeout=10)
            result = data["chart"]["result"][0]
            meta = result["meta"]
            price = meta["regularMarketPrice"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
            chg = price - prev
            chg_pct = (chg / prev * 100) if prev else 0
            # K线
            ts = result.get("timestamp", [])
            q = result["indicators"]["quote"][0]
            opens = q.get("open", [])
            closes = q.get("close", [])
            highs = q.get("high", [])
            lows = q.get("low", [])
            klines = []
            for i in range(len(ts)):
                if opens[i] is None or closes[i] is None:
                    continue
                d = datetime.datetime.fromtimestamp(ts[i]).strftime("%m-%d")
                klines.append([d, round(opens[i],2), round(closes[i],2), round(lows[i],2), round(highs[i],2)])
            klines = klines[-30:]  # 最近30根
            results.append({
                "name": name, "market": market, "ticker": ticker,
                "price": price, "chg": chg, "chg_pct": chg_pct,
                "klines": klines,
            })
        except Exception as ex:
            print(f"[WARN] 行情 {name} 失败: {ex}", file=sys.stderr)
    return results


def render_html(category_items, market_data, generated_at):
    """生成自包含 index.html"""
    cat_colors = {
        "时政": "#e74c3c",
        "财金": "#f39c12",
        "军事": "#9b59b6",
        "科技": "#3498db",
    }
    cat_icons = {"时政": "🌍", "财金": "💰", "军事": "🎖️", "科技": "🔬"}

    # ===== 市场行情板块 =====
    market_cards = []
    chart_scripts = []
    for idx, m in enumerate(market_data):
        up = m["chg"] >= 0
        color = "#ef5350" if up else "#26a69a"  # 红涨绿跌（A股习惯）
        arrow = "▲" if up else "▼"
        price_str = f"{m['price']:.2f}"
        chg_str = f"{arrow} {m['chg']:+.2f} ({m['chg_pct']:+.2f}%)"
        chart_id = f"kline_{idx}"
        # ECharts candle data: [date, open, close, low, high]
        kdata = json.dumps(m["klines"], ensure_ascii=False)
        market_cards.append(f"""
        <div class="mkt-card">
          <div class="mkt-head">
            <span class="mkt-name">{html.escape(m['name'])}</span>
            <span class="mkt-tag">{html.escape(m['market'])}</span>
          </div>
          <div class="mkt-price" style="color:{color}">{price_str}</div>
          <div class="mkt-chg" style="color:{color}">{chg_str}</div>
          <div id="{chart_id}" class="mkt-chart"></div>
        </div>""")
        chart_scripts.append(f"""
        (function(){{
          var el=document.getElementById('{chart_id}');
          if(!el) return;
          var chart=echarts.init(el,'dark',{{renderer:'canvas'}});
          var kd={kdata};
          chart.setOption({{
            animation:false,
            grid:{{left:5,right:5,top:5,bottom:5}},
            xAxis:{{type:'category',show:false,data:kd.map(d=>d[0])}},
            yAxis:{{scale:true,show:false}},
            tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}},backgroundColor:'#161b22',borderColor:'#30363d',textStyle:{{color:'#e6edf3',fontSize:11}}}},
            series:[{{type:'candlestick',data:kd.map(d=>[d[1],d[2],d[3],d[4]]),
              itemStyle:{{color:'#ef5350',color0:'#26a69a',borderColor:'#ef5350',borderColor0:'#26a69a'}}
            }}]
          }});
        }})();""")

    market_section = f"""
    <section class="category" id="market">
      <h2 style="border-color:#d4a017"><span style="color:#d4a017">📊</span> 市场行情 <small>主要指数 · 日K线</small></h2>
      <div class="mkt-grid">{''.join(market_cards)}</div>
    </section>"""

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
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
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
  .mkt-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:14px; }}
  .mkt-card {{
    background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px 16px;
  }}
  .mkt-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .mkt-name {{ font-weight:700; font-size:1.05em; color:#e6edf3; }}
  .mkt-tag {{ font-size:.72em; color:#8b949e; background:#21262d; padding:2px 8px; border-radius:10px; }}
  .mkt-price {{ font-size:1.5em; font-weight:800; font-variant-numeric:tabular-nums; }}
  .mkt-chg {{ font-size:.88em; margin-bottom:8px; font-weight:600; }}
  .mkt-chart {{ width:100%; height:90px; }}
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
  {market_section}
  {''.join(sections)}
</div>
<footer>老张的情报站 · 自动采集自全球主流媒体 RSS · 中文由 AI 翻译生成</footer>
<script>{''.join(chart_scripts)}</script>
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

    print("[INFO] 抓取市场行情...")
    market_data = fetch_market()
    print(f"[INFO] 行情拿到 {len(market_data)} 个指数")

    doc = render_html(all_items, market_data, now)
    out_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[OK] 已生成 {out_path}，共 {sum(len(v) for v in all_items.values())} 条")


if __name__ == "__main__":
    main()
