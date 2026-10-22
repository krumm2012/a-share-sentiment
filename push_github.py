#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把工作区文件提交到 GitHub 仓库（走 Git Data API，规避云端到 github.com:443 的 git 通道不通）

用法
  python3 push_github.py                 # 提交所有纳入版本管理的文件
  python3 push_github.py --dry-run       # 只打印将要提交的文件
  python3 push_github.py --message "..." # 自定义提交信息

Token 读取优先级
  1. 环境变量 GH_TOKEN
  2. ~/.config/a-share-sentiment/github_token（权限 600，不随仓库提交）
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OWNER = os.environ.get("GH_OWNER", "krumm2012")
REPO = os.environ.get("GH_REPO", "a-share-sentiment")
BRANCH = os.environ.get("GH_BRANCH", "main")
TOKEN_FILE = os.path.expanduser("~/.config/a-share-sentiment/github_token")
API = "https://api.github.com"

INCLUDE = ["README.md", "sentiment.py", "build_dashboard.py", "push_feishu.py",
           "push_github.py", "pipeline.sh", "run_daily.sh", "dashboard.html",
           "dashboard_template.html", "events_config.json", "daily_notes.json",
           "sentiment_history.jsonl", ".gitignore"]

SKIP_SUFFIX = (".pyc",)
SKIP_NAMES = {"mx_cache.json"}


def token():
    t = os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def call(tk, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tk)
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:400]}
    except Exception as e:
        return {"_error": "net", "_body": str(e)[:200]}


def main():
    tk = token()
    if not tk:
        print(json.dumps({"ok": False, "error": "no github token"}, ensure_ascii=False))
        return 1

    files = []
    for name in INCLUDE:
        p = os.path.join(HERE, name)
        if not os.path.isfile(p) or name in SKIP_NAMES or name.endswith(SKIP_SUFFIX):
            continue
        files.append(name)
    if "--dry-run" in sys.argv:
        print(json.dumps({"ok": True, "dry_run": True, "files": files}, ensure_ascii=False))
        return 0

    # 当前分支头
    ref = call(tk, "GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    if "_error" in ref:
        print(json.dumps({"ok": False, "step": "get-ref", "detail": ref}, ensure_ascii=False))
        return 1
    parent = ref["object"]["sha"]
    parent_commit = call(tk, "GET", f"/repos/{OWNER}/{REPO}/git/commits/{parent}")
    if "_error" in parent_commit:
        print(json.dumps({"ok": False, "step": "get-parent", "detail": parent_commit}, ensure_ascii=False))
        return 1
    base_tree = parent_commit["tree"]["sha"]

    tree = []
    for name in files:
        with open(os.path.join(HERE, name), "rb") as f:
            content = base64.b64encode(f.read()).decode()
        b = call(tk, "POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                 {"content": content, "encoding": "base64"})
        if "_error" in b:
            print(json.dumps({"ok": False, "step": "blob", "file": name, "detail": b}, ensure_ascii=False))
            return 1
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": b["sha"]})

    t = call(tk, "POST", f"/repos/{OWNER}/{REPO}/git/trees",
             {"base_tree": base_tree, "tree": tree})
    if "_error" in t:
        print(json.dumps({"ok": False, "step": "tree", "detail": t}, ensure_ascii=False))
        return 1

    msg = None
    for i, a in enumerate(sys.argv):
        if a == "--message" and i + 1 < len(sys.argv):
            msg = sys.argv[i + 1]
    if not msg:
        import datetime as dt
        msg = f"chore: 更新 {dt.date.today().isoformat()} 收盘数据与看板"

    c = call(tk, "POST", f"/repos/{OWNER}/{REPO}/git/commits",
             {"message": msg, "tree": t["sha"], "parents": [parent]})
    if "_error" in c:
        print(json.dumps({"ok": False, "step": "commit", "detail": c}, ensure_ascii=False))
        return 1

    upd = call(tk, "PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
               {"sha": c["sha"], "force": True})
    if "_error" in upd:
        print(json.dumps({"ok": False, "step": "update-ref", "detail": upd}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, "commit": c["sha"][:10], "files": len(files),
                      "repo": f"{OWNER}/{REPO}",
                      "url": f"https://github.com/{OWNER}/{REPO}"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
