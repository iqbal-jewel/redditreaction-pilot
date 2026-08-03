"""Pull AITA posts + their top verdict comment via Reddit's RSS feeds.

Reddit closed self-service JSON API access and blocks anonymous JSON scraping
outright (403), but the RSS/Atom feeds are still open to anonymous requests,
just tightly rate-limited (~1 request per ~20s per IP, observed via the
x-ratelimit-* response headers). That means this module paces itself and is
built to be interrupted and resumed via a checkpoint file -- a full month's
worth of candidates takes tens of minutes of real wall-clock time, almost
all of it spent waiting out the rate limit, not computing anything.

RSS does not expose score/num_comments (that's JSON-only), so filtering
relies on Reddit's own "top" sort plus a keyword/length pre-filter, not a
numeric threshold.
"""
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

NS = {"a": "http://www.w3.org/2005/Atom"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MIN_INTERVAL = 21.0  # seconds between requests; the observed reset window is ~18-20s
TIMEOUT = 30

_last_request = 0.0


class RedditError(RuntimeError):
    pass


def _throttled_get(url, params=None):
    global _last_request
    wait = MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
    _last_request = time.time()
    if r.status_code == 429:
        reset = int(r.headers.get("x-ratelimit-reset", 20)) + 2
        time.sleep(reset)
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
        _last_request = time.time()
    if not r.ok:
        raise RedditError(f"HTTP {r.status_code} for {url}")
    return r.text


def clean_html(raw: str) -> str:
    """Reddit's RSS <content> is HTML-escaped markdown-rendered HTML."""
    text = html.unescape(raw or "")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s*submitted by\s*$", "", text, flags=re.DOTALL)
    return text.strip()


def _post_id36(atom_id: str) -> str:
    # atom <id> is like "t3_1v3o0ka"
    return atom_id.split("_", 1)[-1]


def fetch_listing(subreddit: str, t: str, limit: int = 25, after: str | None = None):
    """One page of the subreddit's top listing. Returns raw post dicts.

    `after` is the Atom <id> (a fullname like "t3_abc123") of the last post
    on the previous page -- pass it straight through to page forward.
    """
    params = {"t": t, "limit": limit}
    if after:
        params["after"] = after
    xml_text = _throttled_get(f"https://www.reddit.com/r/{subreddit}/top/.rss", params)
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("a:entry", NS):
        atom_id = entry.findtext("a:id", default="", namespaces=NS)
        title = entry.findtext("a:title", default="", namespaces=NS)
        content = entry.findtext("a:content", default="", namespaces=NS)
        link_el = entry.find("a:link[@rel='alternate']", NS)
        if link_el is None:
            link_el = entry.find("a:link", NS)
        link = link_el.get("href") if link_el is not None else ""
        out.append({
            "post_id": _post_id36(atom_id),
            "atom_id": atom_id,
            "title": html.unescape(title),
            "body": clean_html(content),
            "permalink": link,
        })
    return out


def fetch_top_comments(subreddit: str, post_id36: str, limit: int = 15):
    """Comments RSS sorted by top. Entry 0 is the post itself; skip it."""
    xml_text = _throttled_get(
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id36}/.rss",
        {"sort": "top", "limit": limit},
    )
    root = ET.fromstring(xml_text)
    entries = root.findall("a:entry", NS)[1:]
    out = []
    for entry in entries:
        content = entry.findtext("a:content", default="", namespaces=NS)
        out.append(clean_html(content))
    return out


VERDICT_RE = re.compile(r"^\s*(NTA|YTA|ESH|NAH|INFO)\b", re.IGNORECASE)
VERDICT_LABEL = {
    "NTA": "Not the A**hole",
    "YTA": "You're the A**hole",
    "ESH": "Everyone Sucks Here",
    "NAH": "No A**holes Here",
    "INFO": "Not Enough Info",
}

MIN_BODY_CHARS = 150
MAX_BODY_CHARS = 1500
BAD_FLAIRS = ("update",)

BLOCKLIST = [
    r"\bsuicid\w*\b", r"\bself.?harm\b", r"\bkill (myself|herself|himself)\b",
    r"\brape\b", r"\bmolest\w*\b", r"\bpedo\w*\b", r"\bincest\b",
    r"\btrump\b", r"\bbiden\b", r"\bharris\b", r"\brepublican\b", r"\bdemocrat\b",
    r"\bnigg\w*\b", r"\bfagg\w*\b", r"\bretard\w*\b", r"\btrann\w*\b",
]
_BLOCK_RE = re.compile("|".join(BLOCKLIST), re.IGNORECASE)


def is_clean(text: str) -> bool:
    return not _BLOCK_RE.search(text or "")


def passes_prefilter(post: dict) -> tuple[bool, str]:
    title = post.get("title", "")
    body = post.get("body", "")
    if any(b in title.lower() for b in BAD_FLAIRS):
        return False, "flair_keyword"
    if len(body) < MIN_BODY_CHARS:
        return False, "too_short"
    if len(body) > MAX_BODY_CHARS:
        return False, "too_long"
    if not is_clean(title + " " + body):
        return False, "blocklist"
    return True, ""


def find_verdict(subreddit: str, post_id36: str):
    for body in fetch_top_comments(subreddit, post_id36):
        m = VERDICT_RE.match(body)
        if m and is_clean(body):
            code = m.group(1).upper()
            return code, VERDICT_LABEL[code], body
    return None


class Collector:
    """Resumable candidate collector, checkpointed to disk after every item
    so an interrupted run (rate limit, network blip, timeout) loses nothing.

    Paginates each (subreddit, window) pair via `after` until either a page
    yields zero unseen posts (covers both "actually exhausted" and "the
    pagination param silently isn't advancing" -- both look the same from
    here and both should stop, not spin) or a page-count safety cap is hit.
    """

    WINDOWS = ("month", "year", "all", "week", "day")
    MAX_PAGES_PER_WINDOW = 8

    def __init__(self, subreddits: list[str], checkpoint_path: Path):
        self.subreddits = subreddits
        self.path = checkpoint_path
        if self.path.exists():
            self.state = json.loads(self.path.read_text("utf-8"))
        else:
            self.state = {"accepted": [], "seen_ids": [], "rejected": {},
                          "windows_done": []}
        self.state.setdefault("windows_done", [])

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    def run(self, need: int, log=print):
        seen = set(self.state["seen_ids"])
        for subreddit in self.subreddits:
            for window in self.WINDOWS:
                if len(self.state["accepted"]) >= need:
                    return self.state["accepted"]
                key = f"{subreddit}:{window}"
                if key in self.state["windows_done"]:
                    log(f"  [skip] {key} already exhausted")
                    continue
                after = None
                for page in range(self.MAX_PAGES_PER_WINDOW):
                    if len(self.state["accepted"]) >= need:
                        return self.state["accepted"]
                    try:
                        listing = fetch_listing(subreddit, window, limit=25, after=after)
                    except RedditError as e:
                        log(f"  listing fetch failed for {key} page {page}: {e}")
                        break
                    if not listing:
                        break
                    new_posts = [p for p in listing if p["post_id"] not in seen]
                    log(f"  {key} page {page}: {len(listing)} fetched, "
                        f"{len(new_posts)} new")
                    after = listing[-1]["atom_id"]
                    if not new_posts:
                        break  # exhausted or pagination isn't advancing
                    for post in new_posts:
                        if len(self.state["accepted"]) >= need:
                            break
                        pid = post["post_id"]
                        seen.add(pid)
                        self.state["seen_ids"].append(pid)
                        ok, reason = passes_prefilter(post)
                        if not ok:
                            self.state["rejected"][pid] = reason
                            self._save()
                            continue
                        try:
                            verdict = find_verdict(subreddit, pid)
                        except RedditError as e:
                            log(f"  comments fetch failed for {pid}: {e}")
                            self.state["rejected"][pid] = f"fetch_error:{e}"
                            self._save()
                            continue
                        if not verdict:
                            self.state["rejected"][pid] = "no_verdict_found"
                            self._save()
                            continue
                        code, label, text = verdict
                        self.state["accepted"].append({
                            "post_id": pid,
                            "subreddit": subreddit,
                            "title": post["title"],
                            "body": post["body"],
                            "permalink": post["permalink"],
                            "verdict_code": code,
                            "verdict_label": label,
                            "verdict_text": text,
                        })
                        self._save()
                        log(f"  [{len(self.state['accepted'])}/{need}] accepted {pid}: "
                            f"{post['title'][:60]}")
                    if len(listing) < 25:
                        break  # last page for this window
                self.state["windows_done"].append(key)
                self._save()
        return self.state["accepted"]
