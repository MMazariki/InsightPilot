import pytest

from src import report_pages


def test_choose_page_prefers_the_ai_canvas_page():
    pages = [
        {"name": "aaa", "displayName": "Overview", "order": 0},
        {"name": "bbb", "displayName": "ai canvas ", "order": 1},
    ]
    assert report_pages.choose_page(pages) == "bbb"


def test_choose_page_falls_back_to_the_first_page_in_order():
    pages = [
        {"name": "second", "displayName": "Details", "order": 1},
        {"name": "first", "displayName": "Overview", "order": 0},
    ]
    assert report_pages.choose_page(pages) == "first"
    assert report_pages.choose_page([]) is None


class FakeMsal:
    def __init__(self, accounts, token):
        self.accounts, self.token, self.scopes = accounts, token, None

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent(self, scopes, account):
        self.scopes = scopes
        return self.token


class FakeService:
    def __init__(self, accounts=({"username": "me"},), token=None):
        self.msal_app = FakeMsal(list(accounts), token)
        self.closed = False

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise report_pages.requests.HTTPError(str(self.status))

    def json(self):
        return self.payload


def test_list_pages_uses_a_silent_report_token(monkeypatch):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers))
        return FakeResponse({"value": [
            {"name": "2d23", "displayName": "AI Canvas", "order": 0}]})

    monkeypatch.setattr(report_pages.requests, "get", fake_get)
    service = FakeService(token={"access_token": "TOKEN"})
    pages = report_pages.list_pages("REPORT", service=service)

    assert pages[0]["name"] == "2d23"
    assert service.msal_app.scopes == [report_pages.REPORT_SCOPE]
    assert calls[0][0] == "https://api.powerbi.com/v1.0/myorg/reports/REPORT/pages"
    assert calls[0][1] == {"Authorization": "Bearer TOKEN"}
    assert service.closed is False  # a caller-supplied service is left open


def test_list_pages_in_a_workspace(monkeypatch):
    urls = []
    monkeypatch.setattr(
        report_pages.requests, "get",
        lambda url, headers, timeout: urls.append(url) or FakeResponse({"value": []}))
    report_pages.list_pages("R", workspace_id="W",
                            service=FakeService(token={"access_token": "T"}))
    assert urls == ["https://api.powerbi.com/v1.0/myorg/groups/W/reports/R/pages"]


@pytest.mark.parametrize("service", [
    FakeService(accounts=()),                       # nobody signed in
    FakeService(token=None),                        # sign-in needed
    FakeService(token={"error": "interaction_required"}),
])
def test_list_pages_never_falls_back_to_interactive_sign_in(service):
    with pytest.raises(RuntimeError):
        report_pages.list_pages("REPORT", service=service)


def test_resolve_page_id_returns_none_on_any_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(report_pages, "list_pages", boom)
    assert report_pages.resolve_page_id("REPORT") is None

    monkeypatch.setattr(report_pages, "list_pages", lambda *a, **k: [
        {"name": "x1", "displayName": "AI Canvas", "order": 0}])
    assert report_pages.resolve_page_id("REPORT") == "x1"


class _AccountsService:
    def __init__(self, accounts):
        self.msal_app = type("App", (), {"get_accounts": lambda _self: accounts})()

    def close(self):
        pass


def test_query_account_prefers_the_configured_account():
    from src.report_pages import query_account
    service = _AccountsService([{"username": "cached@example.com"}])
    assert query_account("admin@example.com", service) == "admin@example.com"
    assert query_account(None, service) == "cached@example.com"
    assert query_account(None, _AccountsService([])) is None
