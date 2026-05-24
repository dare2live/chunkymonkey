# perception_absorbed — Track B Perception 副本 + 优化

> Created 2026-05-24 per goal.md MASTER_SYNTHESIS Phase 3.1.
> Track A 原版 at `/Users/dp/Documents/M/stock/perception/` (sibling repo, FROZEN, data refresh only).

## Architecture differences from Track A

| Aspect | Track A (sibling repo) | Track B (perception_absorbed) |
|---|---|---|
| Location | /stock/perception/src/perception/market_perception/ | backend/services/perception_absorbed/ |
| PIT joins | None — uses latest snapshot | Phase 3.2 wires built_at columns |
| Theme history | latest only | Phase 3.4 extends concept network historical |
| Leader-follower membership | 13 days observed | Phase 3.3 historical extension |
| Unified panel join | not joinable | Phase 3.5 refactor for cross-source merge |

## What's preserved

- 7 engine modules copied as-is:
  - emotion_engine.py
  - leader_follower_engine.py
  - regime_engine.py
  - stock_context_engine.py
  - style_rotation_engine.py
  - theme_lifecycle_engine.py
  - under_reaction_engine.py
- Output mart compatibility (mart_market_perception_*)

## Phase 3 status

- 3.1 cp done 2026-05-24 (this README)
- 3.2 PIT-strict feature joins (add built_at) — pending
- 3.3 P5 LeaderFollower historical theme membership extension — pending
- 3.4 P3 ChainDiffusion concept network expansion — pending
- 3.5 P6/P7 refactor for unified panel — pending
- 3.6 Pattern 9 audit clean — pending
