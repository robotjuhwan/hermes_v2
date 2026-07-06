---
skill_id: catalyst_calendar
name: Catalyst Calendar
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md
required_outputs:
  - catalyst_event
  - expected_impact
  - event_risk
  - follow_up_check
max_prompt_chars: 1500
---
# Catalyst Calendar

Jue must connect price action to scheduled or plausible catalysts. For KIS, include earnings, sector policy, supply chain news, institutional/foreign flow, index/ETF rotation, and macro events. For Binance, include funding, unlocks, listing news, ETF/market structure, major protocol events, and volatility regime changes.

For each event, record:
- event date or expected window
- affected symbols or sectors
- impact level: high, medium, low
- expected direction or uncertainty
- pre-event positioning idea
- post-event review requirement

Archive past catalysts with the actual outcome so memory can learn which catalyst classes have worked.
