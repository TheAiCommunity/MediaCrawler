import asyncio

from watch_cards import (
    body_from_bili_subtitle,
    body_from_xhs_note,
    cards_from_bilibili,
    cards_from_douyin,
    cards_from_xhs,
    classify_failure,
    needs_detail,
    pick_bili_subtitle,
    xhs_creator,
)


def test_bilibili_first_page_uses_list_fields():
    cards = cards_from_bilibili({"list": {"vlist": [{"bvid": "BV1", "title": "标题", "created": 1710000000}, {"title": "缺号"}]}})
    assert cards == [{"content_external_id": "BV1", "title": "标题", "url": "https://www.bilibili.com/video/BV1", "published_at_unix": 1710000000}]


def test_douyin_first_page_uses_aweme_id():
    cards = cards_from_douyin({"aweme_list": [{"aweme_id": "7", "desc": "新作品", "create_time": 1710000000}], "has_more": 1})
    assert len(cards) == 1
    assert cards[0]["url"] == "https://www.douyin.com/video/7"
    assert cards[0]["title"] == "新作品"
    assert cards[0]["body"] == "新作品"


def test_douyin_empty_desc_does_not_invent_body():
    cards = cards_from_douyin({"aweme_list": [{"aweme_id": "8", "title": "只有标题", "create_time": 1}]})
    assert cards[0]["title"] == "只有标题"
    assert cards[0]["body"] == ""


def test_douyin_body_keeps_text_beyond_the_title_limit():
    desc = "字" * 600
    cards = cards_from_douyin({"aweme_list": [{"aweme_id": "9", "desc": desc, "create_time": 1}]})
    assert len(cards[0]["title"]) == 500
    assert cards[0]["body"] == desc


def test_xhs_note_body_uses_desc():
    assert body_from_xhs_note({"desc": "  笔记正文  ", "title": "标题"}) == "笔记正文"
    assert body_from_xhs_note({}) == ""


def test_bilibili_subtitle_prefers_chinese_and_joins_cues():
    url = pick_bili_subtitle([
        {"lan": "en", "subtitle_url": "https://example.com/en.json"},
        {"lan": "zh-Hant", "subtitle_url": "https://example.com/hant.json"},
        {"lan": "ai-zh", "subtitle_url": "//example.com/ai.json"},
    ])
    assert url == "//example.com/ai.json"
    assert body_from_bili_subtitle({"body": [{"content": "第一句"}, {"content": "  "}, {"content": "第二句"}]}) == "第一句\n第二句"
    assert pick_bili_subtitle([{"lan": "en", "subtitle_url": "https://example.com/en.json"}]) == ""
    assert body_from_bili_subtitle({}) == ""


def test_detail_fetch_skips_cards_already_seen():
    assert needs_detail(100, 0)
    assert needs_detail(300, 200)
    assert not needs_detail(100, 200)
    assert not needs_detail(200, 200)


def test_attach_bodies_requests_only_new_notes():
    from watch_runner import _attach_bodies

    class Client:
        def __init__(self):
            self.calls = []

        async def get_note_by_id(self, note_id, source, token):
            self.calls.append(note_id)
            return {"desc": "新笔记"}

    class Crawler:
        def __init__(self):
            self.xhs_client = Client()

    class Target:
        since_unix = 200

    crawler = Crawler()
    cards = [
        {"content_external_id": "old", "published_at_unix": 100, "xsec_source": "pc_feed", "xsec_token": "t"},
        {"content_external_id": "new", "published_at_unix": 300, "xsec_source": "pc_feed", "xsec_token": "t"},
    ]
    asyncio.run(_attach_bodies(crawler, "xiaohongshu", Target(), cards))
    assert crawler.xhs_client.calls == ["new"]
    assert "body" not in cards[0]
    assert cards[1]["body"] == "新笔记"


def test_xhs_keeps_token_and_does_not_require_detail():
    url = "https://www.xiaohongshu.com/user/profile/user1?xsec_token=tok&xsec_source=pc_feed"
    assert xhs_creator(url) == ("user1", "tok", "pc_feed")
    cards = cards_from_xhs({"notes": [{"note_id": "n1", "display_title": "笔记", "time": 1710000000000}], "has_more": True}, url)
    assert cards[0]["content_external_id"] == "n1"
    assert "xsec_token=tok" in cards[0]["url"]
    assert cards[0]["published_at_unix"] == 1710000000


def test_classify_failure():
    assert classify_failure("bilibili", "风控校验失败") == "risk_control"
    assert classify_failure("xiaohongshu", "xsec_token expired") == "token_expired"
    assert classify_failure("douyin", "请重新登录") == "login_required"
    assert classify_failure("douyin", "temporary timeout") is None
