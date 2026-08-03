"""Run-time state, kept as JSON so it stays diffable in git.

Post_ID is the idempotency key: a post recorded here is never sent again,
even if a run is retried or overlaps. Copied verbatim from
automation/wildnatureusa/src/state.py -- the pattern is generic across
either pipeline.
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "state.json"


class State:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        if path.exists() and path.stat().st_size:
            self.data = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            self.data = {"posts": {}, "comments": {}}
        self.data.setdefault("posts", {})
        self.data.setdefault("comments", {})

    TERMINAL = ("posted", "scheduled", "skipped")

    def is_done(self, post_id) -> bool:
        return self.data["posts"].get(post_id, {}).get("status") in self.TERMINAL

    def record_skipped(self, post_id, platform, publish_at, reason):
        self.data["posts"][post_id] = {
            "platform": platform,
            "remote_id": None,
            "status": "skipped",
            "reason": reason,
            "publish_at": publish_at,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }

    def record_post(self, post_id, platform, remote_id, status="posted", **extra):
        self.data["posts"][post_id] = {
            "platform": platform,
            "remote_id": remote_id,
            "status": status,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **extra,
        }

    def remote_id(self, post_id):
        return self.data["posts"].get(post_id, {}).get("remote_id")

    def record_failure(self, post_id, error):
        entry = self.data["posts"].setdefault(post_id, {})
        entry["status"] = "failed"
        entry["error"] = str(error)[:400]
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def queue_comment(self, post_id, due_at, message, platform):
        if post_id in self.data["comments"]:
            return
        self.data["comments"][post_id] = {
            "due_at": due_at.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "platform": platform,
            "status": "pending",
        }

    def due_comments(self, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        out = []
        for post_id, c in self.data["comments"].items():
            if c["status"] != "pending":
                continue
            if dt.datetime.fromisoformat(c["due_at"]) <= now:
                out.append((post_id, c))
        return out

    def mark_comment(self, post_id, status, remote_id=None, error=None):
        c = self.data["comments"].setdefault(post_id, {})
        c["status"] = status
        if remote_id:
            c["remote_id"] = remote_id
        if error:
            c["error"] = str(error)[:400]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def summary(self):
        posts = self.data["posts"].values()
        counts = {}
        for p in posts:
            counts[p.get("status", "?")] = counts.get(p.get("status", "?"), 0) + 1
        pending = sum(1 for c in self.data["comments"].values()
                      if c["status"] == "pending")
        return counts, pending
