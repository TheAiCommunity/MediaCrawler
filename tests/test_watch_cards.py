from watch_cards import cards_from_bilibili, cards_from_douyin, cards_from_xhs, classify_failure, xhs_creator


def test_bilibili_first_page_uses_list_fields():
    cards = cards_from_bilibili({"list": {"vlist": [{"bvid": "BV1", "title": "标题", "created": 1710000000}, {"title": "缺号"}]}})
    assert cards == [{"content_external_id": "BV1", "title": "标题", "url": "https://www.bilibili.com/video/BV1", "published_at_unix": 1710000000}]


def test_douyin_first_page_uses_aweme_id():
    cards = cards_from_douyin({"aweme_list": [{"aweme_id": "7", "desc": "新作品", "create_time": 1710000000}], "has_more": 1})
    assert len(cards) == 1
    assert cards[0]["url"] == "https://www.douyin.com/video/7"
    assert cards[0]["title"] == "新作品"


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
