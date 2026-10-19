---
id: <YYYYMMDD>-<type>-<slug>
type: research | trend-scan | visual-refs | strategy-refresh | growth-ideas | advisor-draft | copy-draft | script-draft | render | verify-facts
created: <ISO time> by CMO
due: <date the CMO will read it>
output: outputs/<area>/<id>.md          # exact path agy must write
effort: low | medium | high
---
## Goal
<one sentence: what decision this result feeds>

## Read first
- agents/<role>.md (act as this role)
- research/product-facts.md (only allowed claims)
- <other input paths>

## Do
1. ...
2. ...

## Output format
<sections, tables, max length; every fact with a source URL>

## Done when
The file at `output` exists and follows the format. Append one line to state/activity.log:
`<timestamp> | agy -> CMO | <id> | <output path>`
