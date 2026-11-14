
import os
import re
from pathlib import Path
from typing import Any

import msal
import requests

from msal_extensions import (
    PersistedTokenCache,
    build_encrypted_persistence,
)


# =====================================================
# POWER BI QUERY SERVICE
# =====================================================

class PowerBIQueryError(Exception):
    """Raised when Power BI DAX execution fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code


class PowerBIQueryService:

    SCOPES = [
        "https://analysis.windows.net/powerbi/api/Dataset.Read.All"
    ]

    def __init__(self):

        self.tenant_id = os.environ.get("ENTRA_TENANT_ID")
        self.client_id = os.environ.get("ENTRA_CLIENT_ID")

        self.dataset_id = os.environ.get(
            "POWERBI_DATASET_ID",
            "f1e8ea4d-1f1e-4c62-aedd-60a59d3ae929",
        )

        self.workspace_id = os.environ.get(
            "POWERBI_WORKSPACE_ID"
        )

        if not self.tenant_id or not self.client_id:
            raise RuntimeError(
                "ENTRA_TENANT_ID and ENTRA_CLIENT_ID "
                "must be configured."
            )

        # My workspace uses the /myorg endpoint.
        # Other workspaces use the /groups/{id} endpoint.

        if self.workspace_id:
            self.api_url = (
                "https://api.powerbi.com/v1.0/myorg/"
                f"groups/{self.workspace_id}/datasets/"
                f"{self.dataset_id}/executeQueries"
            )
        else:
            self.api_url = (
                "https://api.powerbi.com/v1.0/myorg/"
                f"datasets/{self.dataset_id}/executeQueries"
            )

        # ----------------------------------------------
        # Encrypted persistent token cache
        # ----------------------------------------------

        project_root = Path(__file__).resolve().parents[1]

        cache_dir = project_root / ".token_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_file = cache_dir / "msal_cache.bin"

        persistence = build_encrypted_persistence(
            str(cache_file)
        )

        token_cache = PersistedTokenCache(
            persistence
        )

        authority = (
            "https://login.microsoftonline.com/"
            f"{self.tenant_id}"
        )

        self.msal_app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=authority,
            token_cache=token_cache,
        )

        self.http = requests.Session()

    # =================================================
    # AUTHENTICATION
    # =================================================

    def _get_access_token(self) -> str:

        accounts = self.msal_app.get_accounts()

        preferred_username = os.environ.get(
            "ENTRA_USERNAME"
        )

        account = None

        if preferred_username:
            account = next(
                (
                    item for item in accounts
                    if item.get("username", "").lower()
                    == preferred_username.lower()
                ),
                None,
            )

        elif accounts:
            account = accounts[0]

        result = None

        if account:
            result = self.msal_app.acquire_token_silent(
                self.SCOPES,
                account=account,
            )

        # Interactive authentication is only required
        # when no usable cached token is available.

        if not result or "access_token" not in result:

            print("\nOpening Microsoft sign-in...")

            result = self.msal_app.acquire_token_interactive(
                scopes=self.SCOPES,
                prompt="select_account",
            )

        if "access_token" not in result:

            raise PowerBIQueryError(
                "Authentication failed: "
                + result.get(
                    "error_description",
                    str(result),
                )
            )

        return result["access_token"]

    # =================================================
    # DAX EXECUTION
    # =================================================

    def execute_dax(
        self,
        dax_query: str,
    ) -> dict[str, Any]:

        if not isinstance(dax_query, str) or not dax_query.strip():
            raise ValueError("DAX query cannot be empty.")

        if not re.search(
            r"\bEVALUATE\b",
            dax_query,
            re.IGNORECASE,
        ):
            raise ValueError(
                "A valid DAX query must contain EVALUATE."
            )

        access_token = self._get_access_token()

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        body = {
            "queries": [
                {
                    "query": dax_query.strip()
                }
            ],
            "serializerSettings": {
                "includeNulls": True
            },
        }

        try:

            response = self.http.post(
                self.api_url,
                headers=headers,
                json=body,
                timeout=60,
            )

        except requests.RequestException as exc:

            raise PowerBIQueryError(
                f"Power BI API communication failed: {exc}"
            ) from exc

        try:
            payload = response.json()

        except ValueError:
            payload = {"raw_response": response.text}

        if not response.ok:

            raise PowerBIQueryError(
                f"Power BI API returned HTTP "
                f"{response.status_code}: {payload}",
                status_code=response.status_code,
            )

        # ----------------------------------------------
        # Inspect query-level errors
        # ----------------------------------------------

        results = payload.get("results", [])

        if not results:
            raise PowerBIQueryError(
                f"No query results returned: {payload}"
            )

        query_result = results[0]

        if query_result.get("error"):
            raise PowerBIQueryError(
                f"DAX execution error: "
                f"{query_result['error']}"
            )

        tables = query_result.get("tables", [])

        if not tables:
            raise PowerBIQueryError(
                f"No result table returned: {payload}"
            )

        table = tables[0]

        if table.get("error"):
            raise PowerBIQueryError(
                f"Result table error: {table['error']}"
            )

        rows = table.get("rows", [])

        columns = (
            list(rows[0].keys())
            if rows
            else []
        )

        # ----------------------------------------------
        # Standardized response for the agent
        # ----------------------------------------------

        return {
            "success": True,
            "dataset_id": self.dataset_id,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }

    def close(self):
        self.http.close()
