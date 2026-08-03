"""Publisher entrypoint -- Facebook only.

Posts are published live (not via Meta's native scheduler) at the moment
their slot is due, so the verdict comment can resolve the post's story id
right after. Nothing publishes unless --live is passed.

    python -m src.publish status
    python -m src.publish run --live
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import meta, render
from .state import State

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "plan" / "august_2026_plan.json"
IMAGES = ROOT / "images"
ET = ZoneInfo("America/New_York")

_NOW = None  # overridden by --now for dry-run testing


def now_et():
    return _NOW or dt.datetime.now(ET)


def log(msg):
    print(f"[{dt.datetime.now(ET):%Y-%m-%d %H:%M %Z}] {msg}", flush=True)


def image_for(post: dict) -> Path:
    """The situation card, rendering it now if a CI checkout doesn't have it."""
    img = IMAGES / post["situation_image"]
    if img.exists():
        return img
    return render.render_situation(post, img)


def load_plan(path=PLAN_PATH):
    if not path.exists():
        raise FileNotFoundError(f"{path} missing -- run python -m src.build_plan first")
    rows = json.loads(path.read_text("utf-8"))
    for r in rows:
        r["_publish_at"] = dt.datetime.fromisoformat(r["publish_at"])
        r["_comment_due_at"] = dt.datetime.fromisoformat(r["comment_due_at"])
    return rows


def cmd_run(args, plan, st):
    now = now_et()
    max_late = dt.timedelta(minutes=args.max_late)

    due = [p for p in plan if not st.is_done(p["post_id"])
           and p["_publish_at"] <= now <= p["_publish_at"] + max_late]
    stale = [p for p in plan if not st.is_done(p["post_id"])
             and p["_publish_at"] + max_late < now]

    for p in stale:
        late = int((now - p["_publish_at"]).total_seconds() // 60)
        log(f"  {'SKIP' if args.live else 'DRY  SKIP'} {p['post_id']} "
            f"missed by {late} min")
        if args.live:
            st.record_skipped(p["post_id"], "Facebook", p["publish_at"],
                              f"missed by {late} min (tolerance {args.max_late})")
    if stale and args.live:
        st.save()

    log(f"{len(due)} Facebook posts due")
    creds = meta.credentials() if args.live else None
    failures = 0

    for p in due:
        if not args.live:
            have = "img" if (IMAGES / p["situation_image"]).exists() else "render-on-publish"
            log(f"  DRY  {p['post_id']} {p['_publish_at']:%m-%d %H:%M} [{have}]")
            continue
        try:
            img = image_for(p)
            rid = meta.fb_photo(creds["page_id"], creds["token"], img, p["caption"])
            st.record_post(p["post_id"], "Facebook", rid)
            st.queue_comment(p["post_id"], p["_comment_due_at"],
                             p["comment_message"], "Facebook")
            log(f"  OK   {p['post_id']} posted -> {rid}")
        except Exception as e:
            failures += 1
            st.record_failure(p["post_id"], e)
            log(f"  FAIL {p['post_id']}: {e}")
        st.save()

    failures += _run_comments(args, st, creds)
    st.save()
    return 1 if failures else 0


def _run_comments(args, st, creds):
    due = st.due_comments()
    log(f"{len(due)} verdict comments due")
    failures = 0
    for post_id, c in due:
        if not args.live:
            log(f"  DRY  comment on {post_id}: {c['message'][:60]}")
            continue
        try:
            target = st.remote_id(post_id)
            if not target:
                raise RuntimeError("no remote id recorded for the parent post")
            story = meta.fb_story_id(target, creds["token"])
            if not story:
                raise RuntimeError("parent post is not live yet")
            rid = meta.fb_comment(story, creds["token"], c["message"])
            st.mark_comment(post_id, "posted", rid)
            log(f"  OK   comment on {post_id} -> {rid}")
        except Exception as e:
            failures += 1
            st.mark_comment(post_id, "pending", error=e)
            log(f"  FAIL comment on {post_id}: {e}")
    return failures


def cmd_status(args, plan, st):
    now = now_et()
    counts, pending = st.summary()
    upcoming = [p for p in plan if p["_publish_at"] > now]

    missing_images = [p for p in plan
                      if not (IMAGES / p["situation_image"]).exists()
                      or not (IMAGES / p["verdict_image"]).exists()]

    if plan:
        log(f"plan: {len(plan)} posts, {plan[0]['_publish_at']:%Y-%m-%d} "
            f"to {plan[-1]['_publish_at']:%Y-%m-%d}")
    log(f"upcoming: {len(upcoming)}   recorded: {sum(counts.values())} {counts}")
    log(f"verdict comments pending: {pending}")
    log(f"images pre-rendered locally: {len(plan) - len(missing_images)}/{len(plan)} "
        f"(not required -- publish renders on demand if missing)")

    for key in ("REDDITREACTION_PAGE_ID", "REDDITREACTION_PAGE_TOKEN"):
        log(f"env {key}: {'set' if os.environ.get(key) else 'MISSING'}")

    token = os.environ.get("REDDITREACTION_PAGE_TOKEN")
    if token:
        try:
            days = meta.token_expiry(token)
            log(f"token expires in: {'never' if days is None else f'{days} days'}")
            granted, missing = meta.scope_report(token)
            if missing:
                log(f"  MISSING required scopes: {', '.join(missing)}")
            else:
                log("  all required scopes present")
        except Exception as e:
            log(f"token check failed: {e}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "run"])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--max-late", type=int, default=360, dest="max_late",
                    help="minutes past its slot a post may still publish "
                         "before being recorded as skipped (default 360)")
    ap.add_argument("--plan", default=str(PLAN_PATH))
    ap.add_argument("--now", help="simulate a moment in ET, e.g. 2026-08-04T11:05 "
                                  "(dry-run testing only)")
    args = ap.parse_args(argv)
    if args.now:
        if args.live:
            ap.error("--now is for dry-run testing; refusing to combine with --live")
        globals()["_NOW"] = dt.datetime.fromisoformat(args.now).replace(tzinfo=ET)

    plan = load_plan(Path(args.plan))
    st = State()
    if not args.live and args.command != "status":
        log("DRY RUN -- pass --live to publish")

    handler = {"status": cmd_status, "run": cmd_run}
    return handler[args.command](args, plan, st)


if __name__ == "__main__":
    sys.exit(main())
