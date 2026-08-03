"""Build the August schedule: collect candidates, assign slots, render cards.

One-time batch step (re-run only to extend/rebuild the plan), separate from
the hourly publish runner. Takes tens of minutes for a full month because of
Reddit's RSS rate limit -- see src/reddit.py.

    python -m src.build_plan --start 2026-08-04 --end 2026-08-31
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import reddit, render

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT = ROOT / "plan" / "candidates_raw.json"
PLAN_PATH = ROOT / "plan" / "august_2026_plan.json"
IMAGES = ROOT / "images"

ET = ZoneInfo("America/New_York")
SLOT_TIMES = [(11, 0), (18, 0)]  # 11:00 AM and 6:00 PM ET
COMMENT_DELAY_MINUTES = 60


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def build_caption(item: dict) -> str:
    return (
        f"{item['title']}\n\n"
        f"YTA or NTA? Vote in the comments ↓\n\n"
        f"Story via r/{item['subreddit']} • verdict revealed here in ~1 hour"
    )


def build_comment(item: dict) -> str:
    text = item["verdict_text"]
    if len(text) > 400:
        text = text[:397].rstrip() + "..."
    return (
        f"REDDIT'S VERDICT: {item['verdict_code']} — {item['verdict_label']}\n\n"
        f"\"{text}\"\n\n"
        f"Full thread: {item['permalink']}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD, first day to schedule")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, last day to schedule")
    ap.add_argument("--subreddits", default="AmItheAsshole,AITAH",
                    help="comma-separated, tried in order")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    days = list(daterange(start, end))
    need = len(days) * len(SLOT_TIMES)

    print(f"Need {need} candidates for {len(days)} days x {len(SLOT_TIMES)} slots/day")
    subreddits = [s.strip() for s in args.subreddits.split(",") if s.strip()]
    collector = reddit.Collector(subreddits, CHECKPOINT)
    # small buffer over the exact need, in case a render fails later
    candidates = collector.run(need=need + 6, log=print)
    if len(candidates) < need:
        print(f"WARNING: only {len(candidates)} candidates collected, "
              f"need {need}. Re-run this command later to keep collecting "
              f"(checkpoint is saved at {CHECKPOINT}).")

    plan = []
    idx = 0
    for day in days:
        for (hh, mm) in SLOT_TIMES:
            if idx >= len(candidates):
                break
            item = candidates[idx]
            idx += 1
            publish_at = dt.datetime.combine(day, dt.time(hh, mm), tzinfo=ET)
            comment_due_at = publish_at + dt.timedelta(minutes=COMMENT_DELAY_MINUTES)

            # Card content is stored here, not the rendered PNG. images/ is
            # gitignored (Facebook takes raw bytes, no URL needed -- same
            # reasoning as wildnatureusa's fact cards), and a CI runner gets
            # a fresh checkout with no local render cache. Storing the text
            # lets src.publish render on demand at publish time instead.
            plan.append({
                "post_id": item["post_id"],
                "subreddit": item["subreddit"],
                "title": item["title"],
                "body": item["body"],
                "verdict_code": item["verdict_code"],
                "verdict_label": item["verdict_label"],
                "verdict_text": item["verdict_text"],
                "permalink": item["permalink"],
                "publish_at": publish_at.isoformat(),
                "comment_due_at": comment_due_at.isoformat(),
                "situation_image": f"{item['post_id']}_situation.png",
                "verdict_image": f"{item['post_id']}_verdict.png",
                "caption": build_caption(item),
                "comment_message": build_comment(item),
            })

            # Local pre-render too, purely so `status` can report readiness
            # and so a human can eyeball the batch before it goes live --
            # not relied on by the CI runner (see publish.image_for).
            render.render_situation(item, IMAGES / plan[-1]["situation_image"])
            render.render_verdict(item, IMAGES / plan[-1]["verdict_image"])
            print(f"  scheduled {item['post_id']} -> {publish_at:%Y-%m-%d %H:%M %Z}")

    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(plan)} scheduled posts to {PLAN_PATH}")
    if len(plan) < need:
        print(f"SHORT by {need - len(plan)} -- re-run build_plan after collecting more.")


if __name__ == "__main__":
    main()
