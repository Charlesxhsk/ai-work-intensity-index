# ai-work-intensity-index
A skill plugin that calculates daily workload based on Codex usage.

## This is the v1.0
We use 'turns' 'tokens' 'API requests' three indicators.

Workload index = 7.2 × ln(1 + turns) + 6.0 × ln(1 + tokens / 10000) + 2.4 × ln(1 + API request)
