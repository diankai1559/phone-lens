# phonelens

输入一个机型，从 B 站抓评测视频，抽成带出处的 claim，再生成一份可追溯的报告。不同测试条件下的数据不会被合并。

## 安装

```bash
pip install -e .
# 或者带网页界面
pip install -e ".[web]"
```

需要两个环境变量：

```bash
DEEPSEEK_API_KEY=sk-...        # claim 抽取、对话
SILICONFLOW_API_KEY=sk-...     # 可选，视频无字幕时做 ASR
```

B 站登录态从 `~/.bilibili-cli/credential.json` 读取（bilibili-cli 生成）。没有也能跑，只是拿不到 AI 字幕。

## 用法

```bash
phonelens probe                                      # 检查信源和 key
phonelens agent redmik100 --top 4                    # 生成购买参考（推荐）
phonelens agent "红米K60至尊版" --top 3               # 老机型会自动回退参数库
phonelens report redmik100 --top 5 --out out/k100.md  # 流程版报告（调试用）
phonelens chat --from-json out/k100.json              # 基于结果追问
phonelens serve --port 8000                           # 网页界面
```

`agent` 会自己决定是否补采视频、先取官网参数再校准；`report` 保留原来的固定流水线，便于排查。网页界面左边是阶段进度，右边分「报告 / 证据 / 对话」三栏。

## 工作方式

```
机型对齐 → 官网/参数库 → 选视频 → 采集 → 抽取 → 官网校准 → 冲突检测 → 写报告 → 校验
                ↑____________________证据不足时自动补采____________________|
```

几条规则：

- 规格以官网为准（官网页面下线时用 ZOL 参数页，报告里标 `†`）。视频与官网冲突的规格项直接按官网校准，不再当成「分歧」。
- claim 是 `(机型, 指标, 值, 测试条件, 出处)`，报告只引用 claim id。
- 抽取结果的 `quote` 必须能在原文里找到，找不到就丢弃并计数。
- 条件不同（亮度、网络、充电区间）标 `condition_mismatch`，不取平均；同条件数值差才叫 `value_gap`。
- 采集链按 字幕 → B 站 AI 摘要 → ASR 降级，每一步都写进报告。
- 数值有合理区间校验，超出（比如亮屏 62h）会被降权。

## 机型

`phonelens/data/devices.json` 里每个机型是一条记录，新机型加一行就行：

```json
"Apple iPhone 18": {
  "brand": "Apple",
  "aliases": ["iphone18", "苹果18"],
  "variants": ["标准版", "Air", "Pro", "Pro Max"]
}
```

没收录的走规则解析，比如 `redminote15pro` → `Redmi Note 15 Pro`、`find x9 ultra` → `OPPO Find X9 Ultra`。

```bash
phonelens catalog
phonelens catalog add --name "Redmi K110" --brand Redmi \
  --aliases "redmik110,k110,红米k110" --variants "标准版,Pro,Pro Max"
```

## 实测

K100：官网参数 + 3 个视频，118 条 claim，22 项按官网校准，3 组同口径冲突，7/7 校验通过。
K60 至尊版（官网已下线）：参数库 9 项，41 条 claim，7/7 通过。
机型对齐 24/24。

```bash
python scripts/bench.py --queries "redmik100,小米17" --top 3
```

完整数据在 [docs/metrics.md](docs/metrics.md)。

## 限制

- 官网参数依赖 Playwright（首次需要本机有 Chromium），没装会退到参数库或视频口径。
- 小米只保留在售机型的参数页，老机型（如 K60 至尊版）只能用参数库，来源会在报告里标出。
- B 站字幕是 AI 字幕，会有同音错字（`8E5` 变 `815`），SoC 做了归一，其他数字仍需多源核对。
- 引用校验只检查 id 是否存在、值是否出现在回答里，不保证语义归属正确。
- ASR 依赖 SiliconFlow，没有 key 时只走字幕和摘要。

## License

MIT
