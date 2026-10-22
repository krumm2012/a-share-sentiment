#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪看板构建脚本：采集数据 → 注入模板 → 输出单文件 dashboard.html

数据源
  1) sentiment.py   本地情绪模型（四维 + 恐慌指数 + 历史）
  2) 东方财富妙想   指数收盘 / 涨跌家数 / 涨跌停 / 成交额
  3) 金十数据 MCP   快讯热度（自定义事件的关键词提及量）、现货黄金行情
  4) 微博           热搜榜中的市场相关条目

用法
  python3 build_dashboard.py            # 采集并生成 dashboard.html
  python3 build_dashboard.py --offline  # 只用本地缓存/历史，不联网采集
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TEMPLATE = os.path.join(HERE, "dashboard_template.html")
OUTPUT = os.path.join(HERE, "dashboard.html")
EVENTS_CFG = os.path.join(HERE, "events_config.json")
JIN10 = "/root/.openclaw/workspace/skills/jin10-mcp/jin10_mcp.py"
WEIBO_DIR = "/root/.openclaw/workspace-task/skills/weibo-skill"

MARKET_KEYS = ("A股", "股市", "沪指", "上证", "深证", "创业板", "科创", "北交所",
               "涨停", "跌停", "券商", "基金", "ETF", "黄金", "美联储", "加息", "降息", "央行")
MARKET_EXCLUDE = ("海景房", "房价", "楼市", "租金")


# ---------------- 基础工具 ----------------

def jin10(tool, args, timeout=90):
    try:
        p = subprocess.run(["python3", JIN10, "call", tool, json.dumps(args, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout)
    except Exception:
        return {}


def weibo_search(query):
    """微博智搜：返回清洗后的摘要文本（去掉卡片块），失败返回空串"""
    try:
        obj = {}
        for _ in range(3):
            p = subprocess.run(["node", "scripts/weibo-skill.js", "search", "--query=" + query],
                               cwd=WEIBO_DIR, capture_output=True, text=True, timeout=150)
            obj = json.loads(p.stdout, strict=False).get("data") or {}
            if not obj.get("analyzing"):
                break
        msg = obj.get("msg") or ""
        msg = re.sub(r"```wbCustomBlock\{.*?\}```", "", msg, flags=re.S)
        msg = re.sub(r"<hl>|</hl>", "", msg)
        msg = re.sub(r"\s+", " ", msg).strip()
        return msg[:220]
    except Exception:
        return ""


def weibo_hot():
    try:
        p = subprocess.run(["node", "scripts/weibo-skill.js", "hot-search",
                            "--category=主榜", "--count=50"],
                           cwd=WEIBO_DIR, capture_output=True, text=True, timeout=150)
        return json.loads(p.stdout, strict=False).get("data") or {}
    except Exception:
        return {}


# ---------------- 自定义事件 ----------------

def build_events(offline=False):
    try:
        with open(EVENTS_CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []
    out = []
    for ev in cfg.get("events") or []:
        item = {
            "name": ev.get("name"),
            "subtitle": ev.get("subtitle", ""),
            "comment": ev.get("comment", ""),
            "counts": [],
            "headlines": [],
            "value": "—",
            "label": "",
            "direction": "flat",
        }

        # 行情类事件：直接取实时报价
        if ev.get("quote") and not offline:
            q = (jin10("get_quote", {"code": ev["quote"]}).get("data") or {})
            if q.get("close"):
                price = float(q["close"])
                chg = float(q.get("ups_percent") or 0)
                item["value"] = f"{price:,.2f}"
                item["label"] = f"{q.get('name', ev['name'])} · 今日 {chg:+.2f}%"
                item["spark"] = f"日内高 {float(q['high']):,.2f} / 低 {float(q['low']):,.2f}"
                item["direction"] = "up" if chg > 0 else ("down" if chg < 0 else "flat")

        if ev.get("static"):
            st = ev["static"]
            item["value"] = st.get("value", item["value"])
            item["label"] = st.get("label", item["label"])
            item["direction"] = st.get("direction", item["direction"])
            if st.get("as_of"):
                item["spark"] = f"数据时点 {st['as_of']}"

        # 关键词热度：用金十快讯逐日统计提及量
        if ev.get("keywords") and not offline:
            seen, items = set(), []
            for kw in ev["keywords"]:
                d = jin10("search_flash", {"keyword": kw})
                for it in (d.get("data") or {}).get("items") or []:
                    u = it.get("url") or ""
                    if u and u not in seen:
                        seen.add(u)
                        items.append(it)
            items.sort(key=lambda x: x.get("time") or "", reverse=True)
            bucket = {}
            for it in items:
                day = (it.get("time") or "")[:10]
                if day:
                    bucket[day] = bucket.get(day, 0) + 1
            days = sorted(bucket)[-7:]
            item["counts"] = [[d0, bucket[d0]] for d0 in days]
            item["headlines"] = [
                {"t": (it.get("time") or "")[5:16], "c": (it.get("content") or "")[:90]}
                for it in items[:3]
            ]
            if len(item["counts"]) >= 2:
                first, last = item["counts"][0][1], item["counts"][-1][1]
                if last > first * 1.3:
                    item["spark"] = (item.get("spark", "") + " · 热度抬升").strip(" ·")
                elif first > last * 1.3:
                    item["spark"] = (item.get("spark", "") + " · 热度回落").strip(" ·")
        out.append(item)
    return out


# ---------------- 社交 ----------------

def build_social(offline=False):
    hot = {} if offline else weibo_hot()
    items, rows = [], hot.get("data") or []
    for it in rows:
        word = it.get("word") or ""
        if not word or not any(k in word for k in MARKET_KEYS):
            continue
        if any(x in word for x in MARKET_EXCLUDE):
            continue
        items.append({"rank": it.get("id"), "word": word,
                      "heat": f"{int(it.get('num') or 0):,}",
                      "note": "新" if it.get("flag") == 1 else ""})
    digest = ""
    if not items and not offline:
        digest = weibo_search("今日A股大跌原因")
    if items:
        note = "热榜出现市场相关条目，说明讨论已从投资圈扩散到公共视野。"
    elif digest:
        note = "今日主榜 50 条没有 A 股词条，说明这轮下跌还没成为公共话题；下面是微博上当天讨论的归纳。"
    else:
        note = ("今日主榜 50 条没有 A 股词条，说明这轮下跌还没成为公共话题，"
                "情绪停留在行情软件和投资社区内部。")
    return {
        "source": f"微博热搜主榜 {hot.get('callTime', '—')}" if hot else "微博热搜（未取到）",
        "items": items[:8],
        "note": note,
        "digest": digest,
        "empty_text": "热榜中暂无市场相关条目",
    }


# ---------------- 组装 ----------------

def build(offline=False):
    import sentiment as S

    d = S.build() if not offline else {}
    hist = S.load_history()
    rows = [{k: r.get(k) for k in ("date", "score", "panic")} for r in hist]

    today = d.get("date") or (rows[-1]["date"] if rows else dt.date.today().isoformat())
    prev = next((r for r in reversed(rows) if r["date"] != today), None)
    delta = round(d["score"] - prev["score"], 1) if (d.get("score") is not None and prev) else None

    indices = [{"name": v["name"], "close": v["close"], "pct": v["pct"]}
               for v in (d.get("indices") or {}).values()]

    context = []
    if d.get("vol20") is not None:
        context.append(f"20 日年化波动率 {d['vol20']:.1f}%")
    if d.get("amount_change") is not None:
        context.append(f"沪市成交额较 5 日均值 {d['amount_change']:+.1f}%")
    if d.get("limit_up") is not None:
        context.append(f"涨停 {d['limit_up']} / 跌停 {d['limit_down']}")

    breadth_note = (f"涨 {d.get('up_count')} / 跌 {d.get('down_count')}"
                    if d.get("up_count") is not None else "")

    data = {
        "meta": {
            "date": today,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sources": "金十 · 东方财富妙想 · 微博",
        },
        "core": {
            "score": d.get("score"),
            "label": d.get("label"),
            "panic": d.get("panic"),
            "panic_label": d.get("panic_label"),
            "regime": d.get("regime"),
            "delta": delta,
        },
        "dims": [
            {"name": "指数动量", "weight": 0.40, "score": d.get("momentum_score")},
            {"name": "市场宽度", "weight": 0.25, "score": d.get("breadth_score")},
            {"name": "主力资金", "weight": 0.15, "score": d.get("flow_score")},
            {"name": "快讯语义", "weight": 0.20, "score": d.get("news_score")},
        ],
        "panic_parts": d.get("panic_parts") or [],
        "context": context,
        "indices": indices,
        "breadth_note": breadth_note,
        "trend": rows[-20:],
        "social": build_social(offline),
        "events": build_events(offline),
        "conclusion": "",
        "method": [
            "情绪分 = 指数动量 40% + 市场宽度 25% + 主力资金 15% + 快讯语义 20%，加权后映射到 0—100。",
            "恐慌指数 = 指数杀跌 30% + 下跌广度 25% + 跌停压力 15% + 20 日年化波动率 20% + 快讯恐慌词 10%。",
            "组合定位 = 情绪方向（偏弱/中性/偏强）× 恐慌强度（高/低），六种状态。",
            "指数与宽度来自东方财富妙想，快讯语义与事件热度来自金十，社交条目来自微博热搜。",
            "本看板只做统计描述，不构成任何投资建议。",
        ],
    }

    if d.get("score") is not None:
        data["conclusion"] = (
            f"{d.get('regime')}：情绪 {d.get('score')} 分（{d.get('label')}），恐慌 {d.get('panic')}"
            f"（{d.get('panic_label')}）。指数均值 {d.get('avg_pct'):+.2f}%，"
            f"涨 {d.get('up_count')} / 跌 {d.get('down_count')}，跌停 {d.get('limit_down')} 家，"
            f"20 日年化波动率 {d.get('vol20')}%。"
        )
    return data


def main():
    offline = "--offline" in sys.argv
    data = build(offline=offline)
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(json.dumps({"ok": True, "output": OUTPUT, "date": data["meta"]["date"],
                      "score": data["core"]["score"], "panic": data["core"]["panic"],
                      "indices": len(data["indices"]), "trend_points": len(data["trend"]),
                      "events": [e["name"] for e in data["events"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
