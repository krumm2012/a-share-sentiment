#!/usr/bin/env bash
# ============================================================
# 收盘情绪完整流水线
#   1) 采集当天收盘情绪（金十 + 妙想），写本地历史
#   2) 重建单文件看板 dashboard.html
#   3) 按日期 upsert 到飞书多维表格
#   4) 提交 dashboard / 历史 / 脚本到 GitHub 私有仓库
#
# 每一步独立判定成败，任一失败不影响后续步骤；
# 最后统一输出一行 JSON 汇总，供唤醒任务读取。
#
# 用法: bash pipeline.sh [--skip-github] [--offline]
# ============================================================
set -u
cd "$(dirname "$0")" || exit 1

SKIP_GITHUB=0
OFFLINE=""
for arg in "$@"; do
  case "$arg" in
    --skip-github) SKIP_GITHUB=1 ;;
    --offline) OFFLINE="--offline" ;;
  esac
done

step_sentiment="skip"; step_dashboard="skip"; step_feishu="skip"; step_github="skip"
report=""
detail=""

# ---------- 1) 情绪采集 ----------
if bash run_daily.sh --json > .pipeline_report.md 2>&1; then
  step_sentiment="ok"
else
  step_sentiment="fail"
fi
report="$(cat .pipeline_report.md 2>/dev/null)"

# ---------- 2) 看板重建 ----------
dash_out="$(python3 build_dashboard.py $OFFLINE 2>&1 | tail -1)"
if echo "$dash_out" | grep -q '"ok": true'; then
  step_dashboard="ok"
else
  step_dashboard="fail"
fi
detail="$(printf '%s' "$dash_out" | tr -d '\n' | head -c 300)"

# ---------- 3) 写飞书 ----------
feishu_out="$(python3 push_feishu.py 2>&1 | tail -1)"
if echo "$feishu_out" | grep -q '"ok": true'; then
  step_feishu="ok"
else
  step_feishu="fail"
fi

# ---------- 4) 提交 GitHub ----------
github_out="skip"
if [ "$SKIP_GITHUB" -eq 0 ]; then
  github_out="$(python3 push_github.py 2>&1 | tail -1)"
  if echo "$github_out" | grep -q '"ok": true'; then
    step_github="ok"
  else
    step_github="fail"
  fi
fi

# ---------- 汇总 ----------
python3 - "$step_sentiment" "$step_dashboard" "$step_feishu" "$step_github" "$detail" "$github_out" <<'PY'
import json, sys, re, os
s, d, f, g, detail, gh = sys.argv[1:7]
md = ""
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pipeline_report.md")
if os.path.exists(p):
    md = open(p, encoding="utf-8").read()
head = [l for l in md.splitlines() if l.strip()][:1]
first = head[0].lstrip("# ").strip() if head else ""
def brief(x):
    try:
        return json.loads(x)
    except Exception:
        return x[:200]
print(json.dumps({
    "ok": s == "ok",
    "steps": {"sentiment": s, "dashboard": d, "feishu": f, "github": g},
    "headline": first,
    "dashboard": brief(detail),
    "github": brief(gh),
}, ensure_ascii=False))
PY
