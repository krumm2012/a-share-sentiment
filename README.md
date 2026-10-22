# A股情绪模型与情绪看板

收盘情绪打分 + 恐慌指数 + 单文件情绪看板。数据来自金十数据（快讯 / 行情）、东方财富妙想（指数 / 涨跌家数 / 资金）、微博（热搜 / 智搜）。

## 目录结构

| 文件 | 作用 |
|---|---|
| `sentiment.py` | 情绪模型：采集四维数据、算情绪分与恐慌指数、生成 Markdown 日报、写历史 |
| `run_daily.sh` | 每日运行入口（包装 sentiment.py） |
| `push_feishu.py` | 把当天记录按日期 upsert 进飞书多维表格 |
| `build_dashboard.py` | 采集数据 → 注入模板 → 生成单文件 `dashboard.html` |
| `dashboard_template.html` | 看板模板（HTML + Tailwind + 内联 JS，数据以 JSON 占位） |
| `dashboard.html` | 构建产物，双击即可打开的完整看板 |
| `events_config.json` | 自定义事件追踪配置（黄金、Fed 加息预期…），加事件只改这里 |
| `daily_notes.json` | 每日人工补充的结论 / 社媒摘要，按日期 key |
| `sentiment_history.jsonl` | 每日一行历史快照 |
| `pipeline.sh` | 完整流水线：情绪采集 → 重建看板 → 写飞书 → 提交 GitHub |
| `push_github.py` | 走 Git Data API 提交到 GitHub（规避 git 直连超时） |

## 情绪模型

四个维度加权，映射到 0—100：

| 维度 | 权重 | 计算方式 |
|---|---|---|
| 指数动量 | 40% | 上证 / 深证 / 创业板 / 科创50 / 北证50 涨跌幅均值，0% 记 50 分，±1% 对应 ±25 分 |
| 市场宽度 | 25% | 上涨家数 /（上涨 + 下跌）× 100 |
| 主力资金 | 15% | 50 + 主力净流入（亿元）/ 10 |
| 快讯语义 | 20% | 正负面词典加权计数，正 /（正 + 负）× 100 |

标签区间：≥80 极度乐观 / ≥62 偏乐观 / ≥45 中性 / ≥28 偏悲观 / <28 极度悲观。

## 恐慌指数（PI，0—100，越高越恐慌）

A 股没有官方 VIX（中国波指 2018 年已停发），用可获得的横截面与时序数据合成：

| 分项 | 权重 | 计算方式 |
|---|---|---|
| 指数杀跌 | 30% | 50 − 五指数涨跌幅均值 × 30 |
| 下跌广度 | 25% | 下跌家数 /（上涨 + 下跌）× 100 |
| 跌停压力 | 15% | 跌停 /（涨停 + 跌停）× 100 × 2.5 |
| 历史波动率 | 20% | 上证 20 日年化波动率，(vol − 8) / 27 × 100 |
| 快讯恐慌词 | 10% | 恐慌 / 暴跌 / 踩踏 / 抛售 / 爆仓等词加权命中密度 |

档位：≥75 极度恐慌 / ≥58 恐慌升温 / ≥42 情绪正常 / ≥25 情绪平静 / <25 极度平静。

**组合定位**（情绪方向 × 恐慌强度）：恐慌抛售、阴跌磨人、剧烈分歧、平稳观望、温和走强、急涨亢奋。

## 看板组件

核心双仪表（情绪分 / 恐慌指数）、组合定位、情绪四维、恐慌五项拆解、五大指数卡片、情绪与恐慌近段走势、社交情绪（微博热搜 + 当日讨论摘要）、自定义事件追踪、一句话结论、数据口径。

自定义事件卡片包含：当前值（行情或静态指标）、近 7 日关键词热度柱、最新三条催化、以及「让 Agent 深挖」按钮（复制检索提问词给 Agent 做趋势追踪）。

## 用法

```bash
# 采集今日收盘情绪，写历史，输出 Markdown 日报
bash run_daily.sh
bash run_daily.sh --json        # 附结构化 JSON
bash run_daily.sh --no-history  # 只看不落历史

# 写入飞书多维表格（按日期 upsert）
python3 push_feishu.py
python3 push_feishu.py --dry-run

# 重新构建看板
python3 build_dashboard.py
python3 build_dashboard.py --offline   # 只用本地缓存/历史
```

## 完整流水线

```bash
bash pipeline.sh                 # 四步全跑
bash pipeline.sh --skip-github   # 跳过 GitHub 提交
bash pipeline.sh --offline       # 看板只用本地缓存重建
```

四步各自独立判定成败，任一步失败不影响后续；最后输出一行 JSON 汇总：

```json
{"ok": true, "steps": {"sentiment":"ok","dashboard":"ok","feishu":"ok","github":"ok"},
 "headline": "2026-10-22 收盘情绪 · 13.9 分（极度悲观）｜恐慌指数 78.00（极度恐慌）"}
```

GitHub 提交走 `api.github.com` 的 Git Data API（blob → tree → commit → 更新 ref）。
云端到 `github.com:443` 的 git 直连会超时，故不用 `git push`。

Token 读取顺序：环境变量 `GH_TOKEN` → `~/.config/a-share-sentiment/github_token`（权限 600）。
凭证不写入仓库，也不进入任何提交内容。

## 配置

- 飞书目标：环境变量 `SENTIMENT_BASE_TOKEN` / `SENTIMENT_TABLE_ID`，缺省用脚本内默认值。
- 自定义事件：编辑 `events_config.json`，`keywords` 用于金十快讯热度统计，`quote` 填金十行情代码（如 `XAUUSD`），`static` 放不能自动拉取的指标（需写 `as_of`）。
- 妙想接口有频率限制（429）。`sentiment.py` 对每个查询做当日缓存（`mx_cache.json`）并对 429 退避重试。

## 依赖

Python 3.11+、Node.js、`lark-cli`（飞书写入）、金十 MCP 脚本与本仓库外的技能目录、东方财富妙想 `mx-cli.js`。凭证均由运行环境注入，仓库不保存任何密钥。

## 声明

所有指标只做统计描述，不构成任何投资建议。
