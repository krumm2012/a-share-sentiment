#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把收盘情绪记录写入飞书多维表格（按日期 upsert：存在则更新，不存在则新增）

用法:
  python3 push_feishu.py                # 同步 sentiment_history.jsonl 中最新一天
  python3 push_feishu.py --date 2026-10-21
  python3 push_feishu.py --dry-run      # 只打印将要写入的内容

依赖:
  lark-cli 已登录（user 身份），且对目标 Base 有编辑权限
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "sentiment_history.jsonl")
NOTES = os.path.join(HERE, "daily_notes.json")

BASE_TOKEN = os.environ.get("SENTIMENT_BASE_TOKEN", "W16UbempIaizcZsX2ZWcRJTYnDf")
TABLE_ID = os.environ.get("SENTIMENT_TABLE_ID", "tblyRk05A0LoGHle")
BASE_URL = f"https://lcndzjuf7bxz.feishu.cn/base/{BASE_TOKEN}"


def lark(args, timeout=90):
    p = subprocess.run(["lark-cli"] + args, capture_output=True, text=True, timeout=timeout)
    return p.stdout, p.stderr, p.returncode


def load_row(day=None):
    if not os.path.exists(HISTORY):
        return None
    rows = []
    with open(HISTORY, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        return None
    if day:
        hit = [r for r in rows if r.get("date") == day]
        return hit[-1] if hit else None
    return rows[-1]


def auto_conclusion(r):
    bits = [f"{r.get('regime') or '—'}"]
    bits.append(f"情绪 {r.get('score')} 分（{r.get('label')}）")
    if r.get("panic") is not None:
        bits.append(f"恐慌 {r.get('panic')}（{r.get('panic_label')}）")
    if r.get("avg_pct") is not None:
        bits.append(f"五指数均值 {r['avg_pct']:+.2f}%")
    if r.get("up_count") is not None:
        bits.append(f"涨 {r.get('up_count')} / 跌 {r.get('down_count')}")
    if r.get("main_flow") is not None:
        bits.append(f"主力净流入 {r.get('main_flow')} 亿")
    if r.get("vol20") is not None:
        bits.append(f"20 日年化波动率 {r.get('vol20')}%")
    if r.get("amount_change") is not None:
        bits.append(f"成交额较 5 日均值 {r['amount_change']:+.1f}%")
    return "；".join(bits) + "。"


def build_fields(r):
    f = {
        "日期": r.get("date"),
        "情绪分": r.get("score"),
        "恐慌指数": r.get("panic"),
        "指数动量分": r.get("momentum_score"),
        "市场宽度分": r.get("breadth_score"),
        "主力资金分": r.get("flow_score"),
        "快讯语义分": r.get("news_score"),
        "五指数均值涨跌幅": r.get("avg_pct"),
        "涨家数": r.get("up_count"),
        "跌家数": r.get("down_count"),
        "涨停家数": r.get("limit_up"),
        "跌停家数": r.get("limit_down"),
        "主力净流入亿": r.get("main_flow"),
        "20日年化波动率": r.get("vol20"),
        "成交额较5日均值": r.get("amount_change"),
        "结论": r.get("conclusion") or auto_conclusion(r),
    }
    if r.get("label"):
        f["情绪标签"] = [r["label"]]
    if r.get("panic_label"):
        f["恐慌档位"] = [r["panic_label"]]
    if r.get("regime"):
        f["组合定位"] = [r["regime"]]
    if r.get("social"):
        f["社媒情绪"] = r["social"]
    return {k: v for k, v in f.items() if v is not None}


def find_existing(day):
    filt = json.dumps({"logic": "and", "conditions": [["日期", "==", day]]}, ensure_ascii=False)
    out, err, rc = lark(["base", "+record-list", "--base-token", BASE_TOKEN,
                         "--table-id", TABLE_ID, "--filter-json", filt,
                         "--field-id", "日期", "--limit", "20", "--format", "json", "--as", "user"])
    try:
        obj = json.loads(out)
        ids = (obj.get("data") or {}).get("record_id_list") or []
    except Exception:
        ids = []
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    r = load_row(args.date)
    if r:
        try:
            with open(NOTES, encoding="utf-8") as f:
                note = (json.load(f) or {}).get(r["date"]) or {}
            for k in ("conclusion", "social"):
                if note.get(k) and not r.get(k):
                    r[k] = note[k]
        except Exception:
            pass
    if not r:
        print(json.dumps({"ok": False, "error": "no local record for given date"}, ensure_ascii=False))
        return 1

    fields = build_fields(r)
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "fields": fields}, ensure_ascii=False, indent=2))
        return 0

    ids = find_existing(r["date"])
    if ids:
        payload = json.dumps({"update_records": {rid: fields for rid in ids}}, ensure_ascii=False)
        out, err, rc = lark(["base", "+record-batch-update", "--base-token", BASE_TOKEN,
                             "--table-id", TABLE_ID, "--json", payload, "--as", "user"])
        action = "updated"
    else:
        payload = json.dumps({"create_records": [fields]}, ensure_ascii=False)
        out, err, rc = lark(["base", "+record-batch-create", "--base-token", BASE_TOKEN,
                             "--table-id", TABLE_ID, "--json", payload, "--as", "user"])
        action = "created"

    ok = False
    detail = {}
    try:
        detail = json.loads(out)
        ok = bool(detail.get("ok"))
    except Exception:
        detail = {"raw": (out or "")[:400], "stderr": (err or "")[:400]}

    print(json.dumps({"ok": ok, "action": action, "date": r["date"],
                      "record_ids": ids, "url": BASE_URL, "detail": detail},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
