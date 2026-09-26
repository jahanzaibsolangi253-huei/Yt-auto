"""
Standalone uploader — GitHub Actions (ya kisi bhi jagah) se video upload karne ke liye.
Ismein koi database nahi, sirf environment variables chahiye.

Zaroori env vars:
    YT_TOKEN_JSON   -> channel ka token JSON (GitHub Secret se)
    VIDEO_FILE      -> upload karne wali file ka path
    VIDEO_TITLE
Optional:
    VIDEO_DESC, VIDEO_TAGS (comma se), VIDEO_PRIVACY (private/unlisted/public),
    THUMB_FILE, MADE_FOR_KIDS (0/1), DELETE_PREV (0/1)
"""
import json
import os
import sys
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def get_service():
    raw = os.environ.get("YT_TOKEN_JSON", "").strip()
    if not raw:
        sys.exit("❌ YT_TOKEN_JSON secret nahi mila.")
    tok = json.loads(raw)
    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok["refresh_token"],
        token_uri=tok.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=tok["client_id"],
        client_secret=tok["client_secret"],
        scopes=tok.get("scopes", SCOPES),
    )
    if not creds.valid:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def main() -> None:
    path = os.environ.get("VIDEO_FILE", "")
    if not path or not os.path.exists(path):
        sys.exit(f"❌ Video file nahi mili: {path!r}")

    size_mb = os.path.getsize(path) / 1e6
    print(f"📼 File: {path}  ({size_mb:.1f} MB)")

    yt = get_service()
    body = {
        "snippet": {
            "title": os.environ.get("VIDEO_TITLE", "Untitled")[:100],
            "description": os.environ.get("VIDEO_DESC", "")[:5000],
            "tags": [t.strip() for t in os.environ.get("VIDEO_TAGS", "").split(",") if t.strip()][:60],
            "categoryId": os.environ.get("VIDEO_CATEGORY", "22"),
        },
        "status": {
            "privacyStatus": os.environ.get("VIDEO_PRIVACY", "private"),
            "selfDeclaredMadeForKids": os.environ.get("MADE_FOR_KIDS", "0") == "1",
        },
    }

    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/*")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response, tries, last = None, 0, -1
    while response is None:
        try:
            status, response = req.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct >= last + 10:
                    last = pct
                    print(f"   ⬆  {pct}%", flush=True)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and tries < 8:
                tries += 1
                wait = min(2 ** tries, 60)
                print(f"   retry {tries}/8 after {wait}s ({e.resp.status})")
                time.sleep(wait)
                continue
            raise

    vid = response["id"]
    print(f"✅ Upload ho gayi: https://youtu.be/{vid}")

    # Thumbnail
    thumb = os.environ.get("THUMB_FILE", "")
    if thumb and os.path.exists(thumb):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
            print("🖼  Thumbnail lag gaya.")
        except Exception as e:
            print(f"⚠  Thumbnail nahi laga (channel verify karein): {e}")

    # Purani video delete
    if os.environ.get("DELETE_PREV", "0") == "1":
        try:
            ch = yt.channels().list(part="contentDetails", mine=True).execute()
            up = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            pl = yt.playlistItems().list(part="contentDetails", playlistId=up, maxResults=5).execute()
            for it in pl.get("items", []):
                old = it["contentDetails"]["videoId"]
                if old != vid:
                    yt.videos().delete(id=old).execute()
                    print(f"🗑  Purani video delete: {old}")
                    break
        except Exception as e:
            print(f"⚠  Purani video delete nahi hui: {e}")

    # GitHub Actions summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"## ✅ Upload mukammal\n\n"
                    f"- **Title:** {body['snippet']['title']}\n"
                    f"- **Privacy:** {body['status']['privacyStatus']}\n"
                    f"- **Link:** https://youtu.be/{vid}\n")


if __name__ == "__main__":
    main()
