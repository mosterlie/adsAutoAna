# -*- coding: utf-8 -*-
from backend import amazon, amazon_store, daily_probe_api as daily


class _FakeLocator:
    def __init__(self, visible=True):
        self.visible = visible
        self.clicked = 0
        self.checked = False

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self.visible

    def is_checked(self):
        return self.checked

    def check(self, timeout=0):
        self.checked = True
        self.clicked += 1

    def click(self, timeout=0):
        self.clicked += 1


class _FakePage:
    def __init__(self, body, visible=True):
        self.body = body
        self.locator_obj = _FakeLocator(visible)

    def inner_text(self, selector):
        return self.body

    def locator(self, selector):
        return self.locator_obj

    def wait_for_timeout(self, ms):
        return None


def test_simple_challenge_clicks_confirmation():
    page = _FakePage("We’re noticing unusual browser activity. "
                     "Check the box to confirm you’re human. 私は人間です Continue")
    assert amazon._try_simple_challenge(page) is True
    assert page.locator_obj.clicked >= 1
    assert page.locator_obj.checked is True


def test_image_challenge_is_not_clicked():
    page = _FakePage("Enter the characters you see below")
    assert amazon._try_simple_challenge(page) is False
    assert page.locator_obj.clicked == 0


def test_global_cooldown(monkeypatch):
    monkeypatch.setattr(amazon, "_BLOCK_UNTIL", 0.0)
    amazon.mark_blocked(60)
    assert amazon.blocked_remaining() > 0
    amazon._BLOCK_UNTIL = 0.0


def test_cdp_error_detection():
    assert amazon._is_cdp_error(Exception(
        "BrowserType.connect_over_cdp: Protocol error "
        "(Browser.setDownloadBehavior): Browser context management is not supported"))
    assert not amazon._is_cdp_error(Exception("ordinary timeout"))


def test_competitor_pages_stops_on_blocked(monkeypatch):
    calls = []
    logs = []

    def fake_fetch(term, **kwargs):
        calls.append(term)
        return {"blocked": True, "items": [], "via": "test"}

    monkeypatch.setattr(amazon, "fetch", fake_fetch)
    out = daily.competitor_pages(
        ["第一个词", "第二个词"], limit=2,
        on_log=lambda level, message, detail="": logs.append(
            (level, message, detail)))

    assert calls == ["第一个词"]
    assert out == {"第一个词": []}
    assert any("人机校验" in message for _, message, _ in logs)


def test_blocked_result_is_not_cached(monkeypatch):
    saved = {"local": 0, "db": 0}

    monkeypatch.setattr(amazon, "pace", lambda: None)
    monkeypatch.setattr(amazon, "_live_fetch",
                        lambda *args, **kwargs: {
                            "query": "x", "items": [], "item_count": 0,
                            "blocked": True, "via": "test",
                        })
    monkeypatch.setattr(amazon_store, "find", lambda query: None)
    monkeypatch.setattr(amazon_store, "save_result",
                        lambda *args, **kwargs: saved.update(local=1) or {})
    monkeypatch.setattr(amazon.db, "get_cached_amazon_search",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(amazon.db, "save_amazon_search",
                        lambda *args, **kwargs: saved.update(db=1) or 1)

    result = amazon.fetch("x", parents=[], use_cache=True)
    assert result["blocked"] is True
    assert result["cache_skipped"] == "blocked"
    assert result["local_paths"] == {}
    assert result["search_id"] is None
    assert saved == {"local": 0, "db": 0}
