---
name: ai-work-intensity-index-v1
description: Calculate and explain the user's AI+工作强度指数v1 from local Codex usage logs. Use when reviewing daily Codex work intensity, end-of-day retrospectives, or comparing turns, tokens, and Codex API request counts with the v1 work-intensity formula.
---

# AI+工作强度指数v1

Use this skill to produce a daily Codex work-intensity review from three local usage signals: turns, tokens, and Codex API requests.

## Formula

Use this uncapped v1 formula:

```text
工作强度指数 =
7.2 * ln(1 + turns)
+ 6.0 * ln(1 + tokens / 10000)
+ 2.4 * ln(1 + API请求数)
```

Rules:

- Do not cap the score at 100.
- Do not use a "full-load" reference day.
- Keep the relative weights equivalent to `12:10:4`, scaled by `0.6`.
- Report the score to one decimal place unless the user asks otherwise.
- Treat `tokens` as local Codex log `total_usage_tokens`, not billing-grade server usage.

Suggested interpretation:

```text
0-10    几乎没有 Codex 工作
10-25   轻量
25-40   低强度或摸鱼感明显
40-60   正常工作日
60-80   高强度
80+     很高强度
```

This scale is calibrated so the user's `2026-05-28` day with `4 turns`, `172,018 tokens`, and `48 API requests` lands below `40`.

## Workflow

1. Use `scripts/codex_work_intensity.py` for local Codex logs whenever possible.
2. Default to `Asia/Hong_Kong` style accounting by using `--tz-offset +08:00`.
3. For a daily review, run the script for today; for comparison, use `--days N`.
4. By default, exclude the current review thread so the act of reviewing does not inflate today's score. Use `--include-current-thread` if the user is reviewing inside the same thread that should be counted.
5. Summarize the table and give a short interpretation focused on relative workload, not productivity quality.

Example commands:

```bash
python scripts/codex_work_intensity.py
python scripts/codex_work_intensity.py --date 2026-05-29 --days 5
python scripts/codex_work_intensity.py --date 2026-05-29 --include-current-thread
python scripts/codex_work_intensity.py --days 7 --format json
```

## Manual Calculation

If the script cannot run, compute the score directly from known daily metrics:

```text
score = 7.2 * ln(1 + turns)
      + 6.0 * ln(1 + tokens / 10000)
      + 2.4 * ln(1 + API请求数)
```

Then explain which component drove the score:

- More turns usually means more active steering and iteration.
- More tokens usually means more context volume or task complexity.
- More API requests usually means more tool execution, verification, or retries.
