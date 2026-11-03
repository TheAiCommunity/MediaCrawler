from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse


def cards_from_bilibili(payload: dict | None) -> list[dict[str, Any]]:
    videos = ((payload or {}).get("list") or {}).get("vlist") or []
    cards = []
    for video in videos:
        bvid = str(video.get("bvid") or "").strip()
        if not bvid:
            continue
        created = _unix(video.get("created"))
        cards.append(
            {
                "content_external_id": bvid,
                "title": _title(video.get("title")),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "published_at_unix": created,
            }
        )
    return cards


def cards_from_douyin(payload: dict | None) -> list[dict[str, Any]]:
    awemes = (payload or {}).get("aweme_list") or []
    cards = []
    for aweme in awemes:
        aweme_id = str(aweme.get("aweme_id") or "").strip()
        if not aweme_id:
            continue
        desc = str(aweme.get("desc") or "").strip()
        cards.append(
            {
                "content_external_id": aweme_id,
                "title": _title(desc or aweme.get("title")),
                "body": desc[:32000],
                "url": f"https://www.douyin.com/video/{aweme_id}",
                "published_at_unix": _unix(aweme.get("create_time")),
            }
        )
    return cards


def cards_from_xhs(payload: dict | None, profile_url: str) -> list[dict[str, Any]]:
    notes = (payload or {}).get("notes") or []
    token, source = _xhs_query(profile_url)
    cards = []
    for note in notes:
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            continue
        note_token = str(note.get("xsec_token") or token or "").strip()
        note_source = str(note.get("xsec_source") or source or "pc_feed").strip()
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if note_token:
            url = f"{url}?xsec_token={note_token}&xsec_source={note_source}"
        title = note.get("title") or note.get("display_title") or note.get("desc")
        cards.append(
            {
                "content_external_id": note_id,
                "title": _title(title),
                "url": url,
                "published_at_unix": _unix(note.get("time")),
                "xsec_token": note_token,
                "xsec_source": note_source or "pc_feed",
            }
        )
    return cards


def xhs_creator(profile_url: str) -> tuple[str, str, str]:
    parsed = urlparse(profile_url)
    parts = [part for part in parsed.path.split("/") if part]
    user_id = ""
    for index, part in enumerate(parts):
        if part == "profile" and index + 1 < len(parts):
            user_id = parts[index + 1]
            break
    token, source = _xhs_query(profile_url)
    return user_id, token, source


def classify_failure(platform: str, message: str) -> str | None:
    text = message or ""
    lowered = text.lower()
    if "风控" in text or "risk control" in lowered:
        return "risk_control"
    if platform == "xiaohongshu" and ("xsec" in lowered or "token" in lowered):
        return "token_expired"
    if "登录" in text or "not login" in lowered or "login required" in lowered:
        return "login_required"
    return None


def _xhs_query(profile_url: str) -> tuple[str, str]:
    query = dict(parse_qsl(urlparse(profile_url).query, keep_blank_values=False))
    return query.get("xsec_token", ""), query.get("xsec_source", "")


def needs_detail(published_at_unix: int, since_unix: int) -> bool:
    """详情和字幕只补尚未建立基线的一页，以及基线之后的新内容。"""
    if int(since_unix or 0) <= 0:
        return True
    return int(published_at_unix or 0) > int(since_unix)


def body_from_xhs_note(note: dict | None) -> str:
    return str((note or {}).get("desc") or "").strip()[:32000]


_BILI_PREFERRED = ("zh-CN", "zh-Hans", "ai-zh")


def pick_bili_subtitle(subtitles: list | None) -> str:
    chosen = ""
    chosen_rank = len(_BILI_PREFERRED) + 1
    for item in subtitles or []:
        if not isinstance(item, dict):
            continue
        lan = str(item.get("lan") or "")
        url = str(item.get("subtitle_url") or "").strip()
        if not url or not _chinese_subtitle(lan):
            continue
        rank = _BILI_PREFERRED.index(lan) if lan in _BILI_PREFERRED else len(_BILI_PREFERRED)
        if chosen and rank >= chosen_rank:
            continue
        chosen = url
        chosen_rank = rank
    return chosen


def body_from_bili_subtitle(payload: dict | None) -> str:
    lines = []
    for cue in (payload or {}).get("body") or []:
        if not isinstance(cue, dict):
            continue
        text = str(cue.get("content") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)[:32000]


def _chinese_subtitle(lan: str) -> bool:
    return lan in _BILI_PREFERRED or lan.startswith("zh")


def _title(value: Any) -> str:
    text = str(value or "").strip()
    return text[:500] or "未命名"


def _unix(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if number > 10_000_000_000:
        number //= 1000
    return number


def canonical_profile_query(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    return urlencode(dict(parse_qsl(parsed.query)))
