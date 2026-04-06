# Podruff

Marketing ops monorepo for CrowdTamers. Handles social media scheduling, video pipeline, hook detection, reporting, and the public site.

## Structure

- `site/` — Public marketing site (static HTML landing page)
- `video-pipeline/` — Drive-native video processing, short clip extraction, subtitle generation, engagement scoring (replacing Descript)
- `social-engine/` — Social content generation, copywriting, image rendering, Notion integration for client onboarding
- `social-scheduler/` — Polls approved posts from Notion, schedules to Later/PostBridge platforms
- `hook-detector/` — Live call hook assistant, real-time transcription, hook/topic/followup suggestion generation during recordings
- `heygen-pipeline/` — HeyGen avatar video generation, node-based workflow with templates
- `orchestrator/` — Pipeline coordination, workflow routing, agent dispatcher for multi-step tasks
- `reporting/` — Client reporting with analytics aggregation (Google Ads, Meta Ads, LinkedIn integration stubs)

## Backlog

Uses a unified BACKLOG.md with `#podruff/<subproject>` tags to route items to sub-projects (e.g., `#podruff/video`, `#podruff/site`, `#podruff/hooks`, `#podruff/social-engine`, `#podruff/social-scheduler`, `#podruff/reporting`, `#podruff/orchestrator`, `#podruff/heygen`).

## Backlog Convention

When adding items to BACKLOG.md, always include:

- Clear description of what's wrong or what's needed
- The relevant `#podruff/<subproject>` tag (one of: site, video, social-engine, social-scheduler, hooks, heygen, orchestrator, reporting)
- `[done-when::...]` with specific, verifiable acceptance criteria
- `[priority::low|medium|high]` if not medium (default is medium)
- `[needs-ui::true]` if it involves visible UI changes
- `[due::YYYY-MM-DD]` if there's a deadline
- `[added::YYYY-MM-DD]` to track when the item was created

Example:
```
- [ ] Social poller skips posts with missing image URLs #podruff/social-scheduler [added::2026-04-06] [done-when::Poller handles missing images gracefully, logs warning, still schedules text-only post] [priority::medium]
```

Another example with more metadata:
```
- [ ] Add aspect ratio variants to clip export #podruff/video [added::2026-04-06] [priority::high] [due::2026-04-20] [done-when::Video clips export in 1:1, 9:16, and 16:9 aspect ratios with smart face-centered crop; tests pass; clients can select variant in Notion form]
```
