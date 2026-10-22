#!/usr/bin/env bash
# 收盘情绪收集工具 - 每日运行入口
# 数据源: 东方财富妙想 mx-cli.js / 金十数据 jin10_mcp.py
cd "$(dirname "$0")" || exit 1
exec python3 sentiment.py "$@"
