# RedditReaction — August pilot plan

Zero-cost, single-format test on a new Facebook + Instagram page. Goal: decide
by Sept 1 whether "AITA verdict cards" is worth building out further, using
comments-per-post and follower growth as the only two numbers that matter.

Reuses the same architecture as `automation/wildnatureusa` (already proven):
scheduled render → publish → post verdict as first comment → idempotent
state.json. No new paid service anywhere in this pipeline.

## Content format

**Card 1 (the post):** the AITA situation as a Reddit-style card — subreddit
tag, upvote count, wrapped title/body text, "YTA or NTA? Vote in the
comments" footer. Poster is always shown as `u/OP`, never the real username
(privacy + reduces any risk of the OP objecting to being featured).

**Card 2 (posted as the first comment, 60 min later):** "Reddit's verdict:
NTA (94% upvoted)" — the top-voted judgment comment, condensed to fit the
card, using the same Card 1 layout logic already built in
`wildnatureusa/src/render.py`.

## Source & filter rules

- Pull from `old.reddit.com/r/AmItheAsshole/top.json?t=day&limit=25` — no
  auth needed for read-only at this volume.
- **Quality bar:** score > 500 and comment count > 100, so only threads with
  enough top-comment signal to extract a real verdict.
- **Hard excludes:** `over_18`, `stickied`, `locked`, flair containing
  "Update" (needs prior context we don't have), body under 150 chars (too
  thin) or over 1500 chars (won't fit a card cleanly).
- **Keyword blocklist:** slurs, self-harm/suicide mentions, named
  politicians/political parties, graphic abuse descriptions. Blocked posts
  are skipped outright, never edited to "clean" them — editing changes the
  story and the risk isn't worth it.
- **Verdict extraction:** top-level comment, sorted by best, matching
  `^(NTA|YTA|ESH|NAH|INFO)\b` — take the highest-voted match.
- Used post IDs go in `state/state.json` (same idempotency pattern as
  wildnatureusa's `Post_ID`) so nothing repeats even if a run overlaps.

## Cadence

Deliberately light for a cold-start page — 2 posts/day, not 5:
- **11:00 AM ET** — situation card (late-morning US scroll window)
- **12:00 PM ET** — verdict posted as first comment on the 11am post
- **6:00 PM ET** — second situation card (evening window)
- **7:00 PM ET** — its verdict comment

Same page for both FB and IG initially — no separate IG-only content this
month, keep the variable count low.

## What it reuses vs. what's new

| Reused from wildnatureusa | New for this pilot |
|---|---|
| `src/meta.py` (Graph API publish + comment) | Reddit pull + filter script |
| `src/state.py` (idempotent state.json) | Card template (Reddit UI look, not nature-fact look) |
| `fonts/Poppins-Bold.ttf` | Verdict-extraction regex |
| Hourly GitHub Actions cron pattern | New Page + IG Business account (needs creating) |
| System User token setup (`docs/token-setup.md`) | Keyword blocklist |

## Scope for August: Facebook only

No Instagram this month — one platform, one variable, matches the "keep the
pilot clean" reasoning above. IG can be added later once the format is
proven, at which point the fetchable-image-URL constraint from wildnatureusa
applies again.

## Access — confirmed

The Page (`RedditReaction`, id `381866474998884`) is already shared with the
same Business Manager/app that runs the other pages in
`automation/Automation/.env`. A page token was derived from
`FB_USER_TOKEN_LONG` and saved as `REDDITREACTION_PAGE_ID` /
`REDDITREACTION_PAGE_TOKEN` in that shared `.env`. Verified via
`debug_token`: `expires_at: 0` (does not expire while the page role stands)
and already carries `pages_manage_posts` + `pages_manage_engagement`, so no
extra Meta app setup or System User token needed for the FB-only pilot.

## Deciding at the end of August

Pull two numbers from Meta Insights: average comments/post and net follower
growth. No target number decided yet — the point of the pilot is to get a
real baseline before setting one. If it's clearly dead (near-zero comments,
no follower growth) after a full month, kill it before investing in the
video/TTS format discussed as a possible September upgrade.
