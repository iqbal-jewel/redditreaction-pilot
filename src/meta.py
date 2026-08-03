"""Meta Graph API calls for Facebook Page + Instagram Business publishing.
Adapted from automation/wildnatureusa/src/meta.py; credentials() and
REQUIRED_SCOPES point at RedditReaction's page/IG account.
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


# --- Instagram ------------------------------------------------------------
def ig_image(ig_user_id, token, image_url, caption, poll_seconds=90):
    """Publish an image post. image_url must be publicly reachable by Meta."""
    r = requests.post(f"{GRAPH}/{ig_user_id}/media",
                      data={"image_url": image_url, "caption": caption,
                            "access_token": token}, timeout=TIMEOUT)
    container = _check(r, "ig_container")["id"]

    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": token},
                         timeout=TIMEOUT)
        code = _check(s, "ig_status").get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise MetaError(f"ig processing failed for container {container}")
        time.sleep(5)

    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish",
                      data={"creation_id": container, "access_token": token},
                      timeout=TIMEOUT)
    return _check(p, "ig_publish")["id"]


def ig_comment(media_id, token, message):
    """Needs the instagram_manage_comments permission."""
    r = requests.post(f"{GRAPH}/{media_id}/comments",
                      data={"message": message, "access_token": token},
                      timeout=TIMEOUT)
    return _check(r, "ig_comment")["id"]


def credentials():
    need = ["REDDITREACTION_PAGE_ID", "REDDITREACTION_PAGE_TOKEN",
            "REDDITREACTION_IG_USER_ID"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise MetaError(f"missing environment variables: {', '.join(missing)}")
    return {
        "page_id": os.environ["REDDITREACTION_PAGE_ID"],
        "token": os.environ["REDDITREACTION_PAGE_TOKEN"],
        "ig_user_id": os.environ["REDDITREACTION_IG_USER_ID"],
    }


REQUIRED_SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
                   "pages_manage_engagement", "instagram_basic",
                   "instagram_content_publish", "instagram_manage_comments"]


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
