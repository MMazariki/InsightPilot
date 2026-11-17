"""Finds the page of the embedded Power BI report that charts are added to.

A page is addressed by an internal ID (for example ``2d2320465a2485b3b947``).
Publishing the report again from Power BI Desktop replaces its pages, so an
ID written into the app goes stale: the report then opens on its default page
and chart creation fails with "Target Power BI page was not found".

Instead of storing the ID, the app asks the Power BI service for the report's
current pages and picks the one named ``AI Canvas`` (or the first page). The
lookup uses the saved sign-in silently and never opens a sign-in window; on
any failure it returns ``None`` and the caller falls back to the built-in ID.
"""

import requests

REPORT_SCOPE = "https://analysis.windows.net/powerbi/api/Report.Read.All"
PREFERRED_PAGE_NAME = "AI Canvas"


def choose_page(pages, preferred=PREFERRED_PAGE_NAME):
    """Pick the page ID: the preferred display name, else the first page."""

    if not pages:
        return None
    wanted = preferred.strip().lower()
    for page in pages:
        if str(page.get("displayName", "")).strip().lower() == wanted:
            return page["name"]
    ordered = sorted(pages, key=lambda page: page.get("order", 0))
    return ordered[0]["name"]


def list_pages(report_id, workspace_id=None, timeout=15, service=None):
    """Return the report's pages as ``[{"name", "displayName", "order"}]``.

    Raises on any failure (no saved sign-in, no permission, network).
    """

    owns_service = service is None
    if owns_service:
        from src.powerbi_query_service import PowerBIQueryService

        service = PowerBIQueryService(workspace_id=workspace_id)
    try:
        accounts = service.msal_app.get_accounts()
        if not accounts:
            raise RuntimeError("No saved Power BI sign-in.")
        result = service.msal_app.acquire_token_silent(
            [REPORT_SCOPE], account=accounts[0])
        if not result or "access_token" not in result:
            raise RuntimeError("No report-reading token without sign-in.")

        base = "https://api.powerbi.com/v1.0/myorg"
        if workspace_id:
            base += f"/groups/{workspace_id}"
        response = requests.get(
            f"{base}/reports/{report_id}/pages",
            headers={"Authorization": f"Bearer {result['access_token']}"},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("value", [])
    finally:
        if owns_service:
            service.close()


def query_account(configured=None, service=None):
    """The Microsoft account the app's Power BI queries use: the one set in
    Admin settings, else the first saved sign-in. ``None`` if unknown.

    The report panel signs in separately, in the browser; this is offered
    to it as the account to pick, so both use the same one.
    """

    if configured:
        return configured
    owns_service = service is None
    try:
        if owns_service:
            from src.powerbi_query_service import PowerBIQueryService

            service = PowerBIQueryService()
        accounts = service.msal_app.get_accounts()
        return accounts[0].get("username") if accounts else None
    except Exception:
        return None
    finally:
        if owns_service and service is not None:
            try:
                service.close()
            except Exception:
                pass


def resolve_page_id(report_id, workspace_id=None, preferred=PREFERRED_PAGE_NAME):
    """The current ID of the chart page, or ``None`` if it cannot be found."""

    try:
        return choose_page(list_pages(report_id, workspace_id), preferred)
    except Exception:
        return None
