# ai-work-intensity-index
A simple skill that calculates daily workload based on Codex usage.
The skill will read local '.codex/logs_2.sqlite'.

## This is the v1.0
- We use 'turns' 'tokens' 'API requests' three indicators.
- The default time zone is UTC+8 
- Daily Workload Index = 7.2 × ln(1 + turns) + 6.0 × ln(1 + tokens / 10000) + 2.4 × ln(1 + API request)

## Future Update Plan
- May add a scoring system to assess the complexity of specific tasks.
