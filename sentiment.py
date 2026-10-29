#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盘情绪收集工具 (Close Sentiment Collector)

数据源
  1) 金十数据 MCP  —— A股收盘快讯文本
  2) 东方财富妙想   —— 五大指数收盘、涨跌家数、涨跌停、主力资金

输出
  - Markdown 日报（stdout）
  - sentiment_history.jsonl（每日一行，用于趋势对比）

用法
  python3 sentiment.py            # 采集并打印日报
  python3 sentiment.py --json     # 追加结构化 JSON
  python3 sentiment.py --no-history
"""
import json
import os
import re
import subprocess
import sys
import time
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "sentiment_history.jsonl")
JIN10_SCRIPT = "/root/.openclaw/workspace/skills/jin10-mcp/jin10_mcp.py"
MX_DIR = "/root/.openclaw/workspace-task/skills/mx-finance"

POS = [
    ("涨停", 2), ("大涨", 3), ("反弹", 2), ("拉升", 2), ("走高", 1), ("领涨", 2),
    ("利好", 3), ("净流入", 2), ("突破", 2), ("创新高", 3), ("走强", 2),
    ("回暖", 2), ("反攻", 2), ("红盘", 2), ("普涨", 3), ("放量上涨", 3),
    ("活跃", 1), ("跟涨", 1), ("翻红", 2), ("逆势上涨", 3),
]
NEG = [
    ("跌停", 2), ("大跌", 3), ("跳水", 3), ("走低", 1), ("下挫", 2), ("领跌", 2),
    ("利空", 3), ("净流出", 2), ("跌破", 2), ("创新低", 3), ("回调", 1),
    ("杀跌", 3), ("绿盘", 2), ("普跌", 3), ("缩量", 1), ("震荡走低", 2),
    ("承压", 1), ("重挫", 3), ("低迷", 2), ("失守", 2), ("跟跌", 1),
]
A_SHARE_KEYS = ("沪指", "上证", "深证", "创业板", "科创", "北证", "A股", "两市",
                "沪深", "涨停", "跌停", "主力资金", "板块", "个股")

INDEX_LIST = [
    ("上证", "上证指数"),
    ("深证", "深证成指"),
    ("创业板", "创业板指"),
    ("科创", "科创50"),
    ("北证", "北证50"),
]


def run(cmd, cwd=None, timeout=120):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.stdout, p.stderr


# ---------------- 金十 ----------------

def jin10_flash(keywords):
    items, seen = [], set()
    for kw in keywords:
        try:
            out, _ = run(["python3", JIN10_SCRIPT, "call", "search_flash",
                          json.dumps({"keyword": kw}, ensure_ascii=False)])
            obj = json.loads(out)
        except Exception:
            continue
        for it in (obj.get("data") or {}).get("items") or []:
            url = it.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            items.append({"content": (it.get("content") or "").strip().replace("\n", " "),
                          "time": it.get("time") or "", "url": url})
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


def only_today(items, day):
    return [i for i in items if (i.get("time") or "").startswith(day)]


def a_share_only(items):
    return [i for i in items if any(k in i["content"] for k in A_SHARE_KEYS)]


# ---------------- 妙想 ----------------

CACHE_FILE = os.path.join(HERE, "mx_cache.json")


def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("date") == dt.date.today().isoformat():
            return c
    except Exception:
        pass
    return {"date": dt.date.today().isoformat(), "data": {}}


def _save_cache(c):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


def mx_data(query, retries=3, use_cache=True):
    """调用妙想 data 子命令，带当日缓存与 429 退避重试"""
    cache = _load_cache()
    if use_cache and query in cache["data"]:
        return cache["data"][query]
    last = None
    for i in range(retries):
        out, err = run(["node", "mx-cli.js", "data", "--query", query], cwd=MX_DIR)
        try:
            obj = json.loads(out)
        except Exception:
            obj = {"status": "ERROR", "raw": ((out or "") + (err or ""))[:300]}
        last = obj
        if obj.get("status") != "ERROR":
            cache["data"][query] = obj
            _save_cache(cache)
            return obj
        if str(obj.get("errorCode")) == "42901" or obj.get("httpStatus") == 429:
            time.sleep(3 * (i + 1))
            continue
        break
    return last or {"status": "ERROR"}


def flatten(obj):
    rows = []
    try:
        dtos = obj["upstreamResponse"]["data"]["searchDataResultDTO"]["dataTableDTOList"]
    except Exception:
        return rows
    for dto in dtos:
        nm = dto.get("nameMap") or {}
        raw = dto.get("rawTable") or {}
        tbl = dto.get("table") or {}
        row = {"_entity": dto.get("entityName") or dto.get("code")}
        for k, label in nm.items():
            if k == "headNameSub":
                continue
            show = tbl.get(k) or raw.get(k) or []
            row[label] = show[0] if show else None
        if len(row) > 1:
            rows.append(row)
    return rows


def num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None



def fetch_index_today(full, day_iso):
    """按指定日期精确取单日收盘价/涨跌幅/成交额，避免区间表错位"""
    try:
        rows = flatten(mx_data(f"{full}{day_iso}收盘价、涨跌幅、成交额"))
    except Exception:
        return None, None, None
    for r in rows:
        c, p = num(r.get("收盘价")), num(r.get("涨跌幅"))
        if c is not None and p is not None:
            return c, p, r.get("成交额")
    return None, None, None


def fetch_market():
    m = {"indices": {}}
    for short, full in INDEX_LIST:
        # 一次查询同时取「收盘价、涨跌幅」，返回多行时取同时具备两项的那行
        close = pct = amt = None
        close, pct, amt = fetch_index_today(full, dt.date.today().isoformat())
        if close is not None and pct is not None:
            m["indices"][short] = {"name": full, "close": close, "pct": pct,
                                   "amount": amt, "date": dt.date.today().isoformat()}
            continue
        try:
            rows = flatten(mx_data(f"{full}今日收盘价、涨跌幅"))
            for r in rows:
                c, p = num(r.get("收盘价")), num(r.get("涨跌幅"))
                if c is not None and p is not None:
                    close, pct = c, p
                    break
            if close is None:
                for r in rows:
                    if close is None:
                        close = num(r.get("收盘价"))
                    if pct is None:
                        pct = num(r.get("涨跌幅"))
        except Exception:
            pass
        if close is not None and pct is not None:
            m["indices"][short] = {"name": full, "close": close, "pct": pct, "amount": amt}
            continue
        try:
            series = fetch_series(full, 60)
            closes = series.get("收盘价") or []
            if len(closes) >= 2:
                day, c = closes[-1]
                prev = closes[-2][1]
                m["indices"][short] = {"name": full, "close": c,
                                       "pct": round((c / prev - 1) * 100, 2) if prev else 0.0,
                                       "amount": amt, "date": day}
        except Exception:
            pass
    try:
        for r in flatten(mx_data("今日A股上涨家数、下跌家数")):
            up, dn = num(r.get("上涨家数")), num(r.get("下跌家数"))
            if up is not None and dn is not None:
                m["up_count"], m["down_count"] = int(up), int(dn)
                break
    except Exception:
        pass
    try:
        for r in flatten(mx_data("今日A股涨停家数、跌停家数")):
            up, dn = num(r.get("涨停家数")), num(r.get("跌停家数"))
            if up is not None:
                m["limit_up"], m["limit_down"] = int(up), int(dn or 0)
                break
    except Exception:
        pass
    try:
        for r in flatten(mx_data("今日A股主力资金净流入")):
            for k, v in r.items():
                if "区间" in str(k):
                    # 返回的是区间累计值，非当日净流入，不能当当日指标用
                    continue
                if "净流入" in k and num(v) is not None:
                    m["main_flow"] = num(v)
                    m["main_flow_txt"] = str(v)
                    break
            if "main_flow" in m:
                break
    except Exception:
        pass
    return m


# ---------------- 时间序列 & 恐慌指数 ----------------

PANIC_WORDS = [
    ("恐慌", 5), ("暴跌", 4), ("跳水", 3), ("杀跌", 3), ("踩踏", 5), ("崩盘", 5),
    ("抛售", 4), ("重挫", 3), ("爆仓", 5), ("闪崩", 5), ("大跌", 2), ("跌停", 2),
    ("失守", 1), ("出逃", 3), ("恐慌性", 5), ("熔断", 5), ("全线下跌", 4),
]


def fetch_series(full, days=60):
    """返回 {指标名: [(日期, 数值), ...]} 按日期升序"""
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    q = f"{full}{start.isoformat()}至{end.isoformat()}每个交易日的收盘价和成交额"
    out = {}
    try:
        obj = mx_data(q)
        dtos = obj["upstreamResponse"]["data"]["searchDataResultDTO"]["dataTableDTOList"]
    except Exception:
        return out
    for dto in dtos:
        nm = dto.get("nameMap") or {}
        raw = dto.get("rawTable") or {}
        heads = raw.get("headName") or []
        for k, label in nm.items():
            if k == "headNameSub" or k not in raw:
                continue
            vals = raw[k]
            pairs = []
            for i, v in enumerate(vals):
                d0 = heads[i] if i < len(heads) else None
                n = num(v)
                if d0 and n is not None:
                    pairs.append((d0, n))
            pairs = [(a, b) for a, b in pairs if len(a) == 10]
            if pairs and len(pairs) > len(out.get(label, [])):
                out[label] = sorted(pairs)
    return out


def ann_vol(closes, window=20):
    """年化历史波动率(%)，closes 为按日期升序的收盘价"""
    px = [c for _, c in closes][-window - 1:]
    if len(px) < 6:
        return None
    rets = []
    for i in range(1, len(px)):
        if px[i - 1] > 0:
            rets.append((px[i] / px[i - 1]) - 1)
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (250 ** 0.5) * 100


def panic_news(items):
    hit, total = 0, 0
    words = set()
    for it in items:
        c = it["content"]
        for w, wt in PANIC_WORDS:
            n = c.count(w)
            if n:
                hit += n * wt
                words.add(w)
        total += 1
    # 每 10 条快讯里恐慌词加权命中 10 次 => 100 分
    return clip(hit / max(total, 1) * 100), sorted(words)


def build_panic(d, items, series):
    closes = series.get("收盘价") or []
    amounts = series.get("成交额") or []
    vol20 = ann_vol(closes, 20)
    d["vol20"] = round(vol20, 1) if vol20 is not None else None

    amt_change = None
    if len(amounts) >= 6:
        today_amt = amounts[-1][1] if amounts[-1][0] == d["date"] else None
        prev5 = [a for _, a in amounts[-6:-1]]
        if today_amt and prev5:
            base = sum(prev5) / len(prev5)
            if base > 0:
                amt_change = round((today_amt / base - 1) * 100, 1)
    d["amount_change"] = amt_change

    p_news, p_words = panic_news(items)
    d["panic_words"] = p_words[:8]
    d["panic_news_score"] = round(p_news, 1)

    sub = []
    if d["avg_pct"] is not None:
        sub.append(("指数杀跌", clip(50 - d["avg_pct"] * 30), 0.30))
    if d["up_count"] is not None and (d["up_count"] + d["down_count"]) > 0:
        sub.append(("下跌广度", clip(d["down_count"] / (d["up_count"] + d["down_count"]) * 100), 0.25))
    if d["limit_up"] is not None and (d["limit_up"] + d["limit_down"]) > 0:
        sub.append(("跌停压力", clip(d["limit_down"] / (d["limit_up"] + d["limit_down"]) * 100 * 2.5), 0.15))
    if vol20 is not None:
        sub.append(("历史波动率", clip((vol20 - 8) / 27 * 100), 0.20))
    if p_news > 0:
        sub.append(("快讯恐慌词", p_news, 0.10))

    d["panic_parts"] = [{"name": n, "score": round(v, 1), "weight": w} for n, v, w in sub]
    if sub:
        ws = sum(w for _, _, w in sub)
        d["panic"] = round(sum(v * w for _, v, w in sub) / ws, 1)
    else:
        d["panic"] = None
    d["panic_label"] = panic_label(d["panic"])
    d["regime"] = regime(d["score"], d["panic"])
    return d


def panic_label(p):
    if p is None:
        return "—"
    if p >= 75:
        return "极度恐慌"
    if p >= 58:
        return "恐慌升温"
    if p >= 42:
        return "情绪正常"
    if p >= 25:
        return "情绪平静"
    return "极度平静"


def regime(score, panic):
    if score is None or panic is None:
        return "—"
    hot = panic >= 58
    if score < 45:
        return "恐慌抛售" if hot else "阴跌磨人"
    if score >= 62:
        return "急涨亢奋" if hot else "温和走强"
    return "剧烈分歧" if hot else "平稳观望"


# ---------------- 打分 ----------------

def clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def news_score(items):
    pos = neg = 0
    hp, hn = set(), set()
    for it in items:
        c = it["content"]
        for w, wt in POS:
            n = c.count(w)
            if n:
                pos += n * wt
                hp.add(w)
        for w, wt in NEG:
            n = c.count(w)
            if n:
                neg += n * wt
                hn.add(w)
    total = pos + neg
    return (50.0 if total == 0 else pos / total * 100.0), pos, neg, sorted(hp), sorted(hn)


def label(score):
    if score >= 80:
        return "极度乐观"
    if score >= 62:
        return "偏乐观"
    if score >= 45:
        return "中性"
    if score >= 28:
        return "偏悲观"
    return "极度悲观"


def build():
    now = dt.datetime.now()
    day = now.strftime("%Y-%m-%d")
    raw = only_today(jin10_flash(["A股收评", "收盘", "A股"]), day)
    items = a_share_only(raw)
    m = fetch_market()

    d = {"date": day, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
         "indices": m.get("indices", {}),
         "up_count": m.get("up_count"), "down_count": m.get("down_count"),
         "limit_up": m.get("limit_up"), "limit_down": m.get("limit_down"),
         "main_flow": m.get("main_flow"), "main_flow_txt": m.get("main_flow_txt"),
         "flash_count": len(items), "flash_total": len(raw)}

    pcts = [v["pct"] for v in d["indices"].values()]
    avg = sum(pcts) / len(pcts) if pcts else None
    d["avg_pct"] = round(avg, 2) if avg is not None else None
    d["momentum_score"] = round(clip(50 + avg * 25), 1) if avg is not None else None

    if d["up_count"] is not None and (d["up_count"] + d["down_count"]) > 0:
        d["breadth_score"] = round(d["up_count"] / (d["up_count"] + d["down_count"]) * 100, 1)
    else:
        d["breadth_score"] = None

    if d["main_flow"] is not None:
        d["flow_score"] = round(clip(50 + d["main_flow"] / 10.0), 1)
    else:
        d["flow_score"] = None

    ns, pos, neg, hp, hn = news_score(items)
    d.update({"news_score": round(ns, 1), "news_pos": pos, "news_neg": neg,
              "pos_words": hp[:8], "neg_words": hn[:8]})

    parts, weights = [], []
    for key, w in (("momentum_score", 0.40), ("breadth_score", 0.25),
                   ("flow_score", 0.15), ("news_score", 0.20)):
        if d.get(key) is not None and (key != "news_score" or pos + neg > 0):
            parts.append(d[key]); weights.append(w)
    d["score"] = round(sum(p * w for p, w in zip(parts, weights)) / sum(weights), 1) if weights else 50.0
    d["label"] = label(d["score"])

    d["flash_top"] = [{"time": i["time"][11:16], "content": i["content"][:120]} for i in items[:5]]

    try:
        series = fetch_series("上证指数", 60)
    except Exception:
        series = {}
    build_panic(d, items, series)
    return d


def fmt(v, unit="", plus=False):
    if v is None:
        return "—"
    return (f"{v:+.2f}" if plus else f"{v:.2f}") + unit


def render(d, prev=None):
    L = [f"## {d['date']} 收盘情绪 · {d['score']} 分（{d['label']}）｜恐慌指数 {fmt(d.get('panic'))}（{d.get('panic_label','—')}）"]
    if prev:
        delta = d["score"] - prev["score"]
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        L.append(f"较上一交易日（{prev['date']} {prev['score']} 分）：{arrow} {abs(delta):.1f} 分")
    L += ["", "**指数收盘**", "", "| 指数 | 收盘 | 涨跌幅 | 成交额 |", "|---|---|---|---|"]
    for short, _ in INDEX_LIST:
        v = d["indices"].get(short)
        if v:
            L.append(f"| {v['name']} | {v['close']:.2f} | {v['pct']:+.2f}% | {v.get('amount') or '—'} |")
    if d["momentum_score"] is None:
        L += ["", "> 注意：行情数据缺失（非交易日或数据源未响应），本次分数主要由快讯语义决定，仅作参考。"]
    L += ["", "**情绪四维**", "", "| 维度 | 得分 | 依据 |", "|---|---|---|"]
    L.append(f"| 指数动量（40%） | {fmt(d['momentum_score'])} | 五指数均值 {fmt(d['avg_pct'],'%',True)} |")
    breadth = (f"涨 {d['up_count']} / 跌 {d['down_count']}"
               if d["up_count"] is not None else "数据缺失")
    L.append(f"| 市场宽度（25%） | {fmt(d['breadth_score'])} | {breadth} |")
    flow = d.get("main_flow_txt") or "数据缺失"
    L.append(f"| 主力资金（15%） | {fmt(d['flow_score'])} | 主力净流入 {flow} |")
    L.append(f"| 快讯语义（20%） | {fmt(d['news_score'])} | 正 {d['news_pos']} / 负 {d['news_neg']}，A股快讯 {d['flash_count']} 条 |")
    L.append("")
    L.append("**恐慌指数拆解**（越高越恐慌）")
    L.append("")
    L.append("| 分项 | 权重 | 得分 |")
    L.append("|---|---|---|")
    for p_ in d.get("panic_parts") or []:
        L.append(f"| {p_['name']} | {int(p_['weight']*100)}% | {p_['score']:.1f} |")
    ctx = []
    if d.get("vol20") is not None:
        ctx.append(f"上证 20 日年化波动率 {d['vol20']:.1f}%")
    if d.get("amount_change") is not None:
        ctx.append(f"沪市成交额较前 5 日均值 {d['amount_change']:+.1f}%")
    if ctx:
        L.append("")
        L.append("· " + "；".join(ctx))
    if d.get("panic_words"):
        L.append(f"· 恐慌词：{'、'.join(d['panic_words'])}")
    if d.get("regime"):
        L += ["", f"**组合定位：{d['regime']}**（情绪 {'偏弱' if (d['score'] or 50) < 45 else ('偏强' if (d['score'] or 50) >= 62 else '中性')} × 恐慌 {'高' if (d.get('panic') or 0) >= 58 else '低'}）"]

    L.append("")
    extras = []
    if d["limit_up"] is not None:
        extras.append(f"涨停 {d['limit_up']} 家 / 跌停 {d['limit_down']} 家")
    if extras:
        L.append("· " + "；".join(extras))
    if d["pos_words"]:
        L.append(f"· 正面词：{'、'.join(d['pos_words'])}")
    if d["neg_words"]:
        L.append(f"· 负面词：{'、'.join(d['neg_words'])}")
    if d["flash_top"]:
        L += ["", "**收盘快讯**", ""]
        for f in d["flash_top"]:
            L.append(f"- {f['time']} {f['content']}")
    return "\n".join(L)


def load_history():
    if not os.path.exists(HISTORY):
        return []
    try:
        with open(HISTORY, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except Exception:
        return []


HIST_KEYS = ("date", "score", "label", "momentum_score", "breadth_score",
             "flow_score", "news_score", "avg_pct", "up_count", "down_count",
             "limit_up", "limit_down", "main_flow", "flash_count",
             "panic", "panic_label", "regime", "vol20", "amount_change")


def main():
    d = build()
    rows = load_history()
    prev = next((r for r in reversed(rows) if r.get("date") != d["date"]), None)
    print(render(d, prev))
    if "--json" in sys.argv:
        print("\n```json\n" + json.dumps(d, ensure_ascii=False, indent=2) + "\n```")
    if "--no-history" not in sys.argv:
        rows = [r for r in rows if r.get("date") != d["date"]]
        rows.append({k: d.get(k) for k in HIST_KEYS})
        rows.sort(key=lambda r: r["date"])
        with open(HISTORY, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
