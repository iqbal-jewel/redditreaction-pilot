"""Publisher entrypoint -- Facebook and Instagram.

Both post live at slot time (no native scheduler in play here). Facebook
takes image bytes directly; Instagram needs a public URL, so images must be
committed and served via raw.githubusercontent.com (set IMAGE_BASE_URL).

Each story gets two independent state entries -- "{post_id}:fb" and
"{post_id}:ig" -- since the two platforms publish, fail, and get commented
on independently of each other.

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
    """The situation card, rendering it now if somehow missing on disk."""
    img = IMAGES / post["situation_image"]
    if img.exists():
        return img
    return render.render_situation(post, img)


def image_url(post: dict) -> str:
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError(
            "IMAGE_BASE_URL is not set. Instagram needs a public URL for the "
            "image, e.g. https://raw.githubusercontent.com/<owner>/<repo>/main/images")
    return f"{base}/{post['situation_image']}"


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

    due = [p for p in plan if p["_publish_at"] <= now <= p["_publish_at"] + max_late
           and (not st.is_done(f"{p['post_id']}:fb") or not st.is_done(f"{p['post_id']}:ig"))]
    stale = [p for p in plan if p["_publish_at"] + max_late < now
             and (not st.is_done(f"{p['post_id']}:fb") or not st.is_done(f"{p['post_id']}:ig"))]

    for p in stale:
        late = int((now - p["_publish_at"]).total_seconds() // 60)
        for platform, key in (("Facebook", "fb"), ("Instagram", "ig")):
            sid = f"{p['post_id']}:{key}"
            if st.is_done(sid):
                continue
            log(f"  {'SKIP' if args.live else 'DRY  SKIP'} {sid} missed by {late} min")
            if args.live:
                st.record_skipped(sid, platform, p["publish_at"],
                                  f"missed by {late} min (tolerance {args.max_late})")
    if stale and args.live:
        st.save()

    log(f"{len(due)} stories due")
    creds = meta.credentials() if args.live else None
    failures = 0

    for p in due:
        fb_id = f"{p['post_id']}:fb"
        ig_id = f"{p['post_id']}:ig"

        if not args.live:
            log(f"  DRY  {p['post_id']} {p['_publish_at']:%m-%d %H:%M}")
        else:
            if not st.is_done(fb_id):
                try:
                    img = image_for(p)
                    rid = meta.fb_photo(creds["page_id"], creds["token"], img, p["caption"])
                    st.record_post(fb_id, "Facebook", rid)
                    st.queue_comment(fb_id, p["_comment_due_at"], p["comment_message"], "Facebook")
                    log(f"  OK   {fb_id} posted -> {rid}")
                except Exception as e:
                    failures += 1
                    st.record_failure(fb_id, e)
                    log(f"  FAIL {fb_id}: {e}")
                st.save()

            if not st.is_done(ig_id):
                try:
                    url = image_url(p)
                    rid = meta.ig_image(creds["ig_user_id"], creds["token"], url, p["caption"])
                    st.record_post(ig_id, "Instagram", rid)
                    st.queue_comment(ig_id, p["_comment_due_at"], p["comment_message"], "Instagram")
                    log(f"  OK   {ig_id} posted -> {rid}")
                except Exception as e:
                    failures += 1
                    st.record_failure(ig_id, e)
                    log(f"  FAIL {ig_id}: {e}")
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
            if c["platform"] == "Facebook":
                # fb_photo() is called without scheduled_at (this pipeline
                # publishes live, not via Meta's scheduler), so its response
                # already carries a commentable post_id -- no page_story_id
                # resolution step needed. That resolution is only for a
                # *scheduled* photo, which only has a raw photo id until it
                # goes live (see wildnatureusa's fb_story_id, which this
                # pipeline copied from before catching the distinction).
                rid = meta.fb_comment(target, creds["token"], c["message"])
            else:
                rid = meta.ig_comment(target, creds["token"], c["message"])
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

    missing_images = [p for p in plan if not (IMAGES / p["situation_image"]).exists()]

    if plan:
        log(f"plan: {len(plan)} stories, {plan[0]['_publish_at']:%Y-%m-%d} "
            f"to {plan[-1]['_publish_at']:%Y-%m-%d} (x2 platforms = "
            f"{len(plan) * 2} posts)")
    log(f"upcoming: {len(upcoming)}   recorded: {sum(counts.values())} {counts}")
    log(f"verdict comments pending: {pending}")
    log(f"images committed: {len(plan) - len(missing_images)}/{len(plan)}")
    if missing_images:
        log(f"  MISSING for: {', '.join(p['post_id'] for p in missing_images[:5])}"
            f"{' ...' if len(missing_images) > 5 else ''} -- Instagram will 404 on these")

    for key in ("REDDITREACTION_PAGE_ID", "REDDITREACTION_PAGE_TOKEN",
                "REDDITREACTION_IG_USER_ID", "IMAGE_BASE_URL"):
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
