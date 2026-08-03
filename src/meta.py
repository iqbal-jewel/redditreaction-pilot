"""Meta Graph API calls for Facebook Page publishing -- FB only, no IG this
pilot. Trimmed from automation/wildnatureusa/src/meta.py (same Graph API
calls; credentials() and REQUIRED_SCOPES point at RedditReaction's page).
"""
import os
import time

import requests

GRAPH = "https://graph.facebook.com/v19.0"
TIMEOUT = 60


class MetaError(RuntimeError):
    pass


def _check(resp, what):
    try:
        data = resp.json()
    except ValueError:
        raise MetaError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    if isinstance(data, dict) and "error" in data:
        raise MetaError(f"{what}: {data['error']}")
    if not resp.ok:
        raise MetaError(f"{what}: HTTP {resp.status_code} {data}")
    return data


def fb_photo(page_id, token, image_path, caption):
    """Publish a photo post immediately. Returns the post id."""
    with open(image_path, "rb") as f:
        r = requests.post(f"{GRAPH}/{page_id}/photos",
                          data={"caption": caption, "access_token": token},
                          files={"source": f}, timeout=TIMEOUT)
    data = _check(r, "fb_photo")
    return data.get("post_id") or data["id"]


def fb_story_id(photo_id, token):
    """Resolve a photo id to the post id you can comment on."""
    r = requests.get(f"{GRAPH}/{photo_id}",
                     params={"fields": "page_story_id", "access_token": token},
                     timeout=TIMEOUT)
    return _check(r, "fb_story_id").get("page_story_id")


def fb_comment(object_id, token, message):
    r = requests.post(f"{GRAPH}/{object_id}/comments",
                      data={"message": message, "access_token": token},
                      timeout=TIMEOUT)
    return _check(r, "fb_comment")["id"]


def credentials():
    need = ["REDDITREACTION_PAGE_ID", "REDDITREACTION_PAGE_TOKEN"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise MetaError(f"missing environment variables: {', '.join(missing)}")
    return {
        "page_id": os.environ["REDDITREACTION_PAGE_ID"],
        "token": os.environ["REDDITREACTION_PAGE_TOKEN"],
    }


REQUIRED_SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
                   "pages_manage_engagement"]


def debug_token(token):
    r = requests.get(f"{GRAPH}/debug_token",
                     params={"input_token": token, "access_token": token},
                     timeout=TIMEOUT)
    return _check(r, "debug_token").get("data", {})


def token_expiry(token):
    expires = debug_token(token).get("expires_at")
    if not expires:
        return None
    return max(0, int((expires - time.time()) // 86400))


def scope_report(token):
    granted = set(debug_token(token).get("scopes", []))
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    return sorted(granted), missing
