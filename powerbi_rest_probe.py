
import os
import json
import msal
import requests


# ==========================================
# CONFIGURATION
# ==========================================

TENANT_ID = os.environ["ENTRA_TENANT_ID"]
CLIENT_ID = os.environ["ENTRA_CLIENT_ID"]

DATASET_ID = (
    "f1e8ea4d-1f1e-4c62-aedd-60a59d3ae929"
)

AUTHORITY = (
    f"https://login.microsoftonline.com/{TENANT_ID}"
)

SCOPES = [
    "https://analysis.windows.net/powerbi/api/Dataset.Read.All"
]

API_URL = (
    "https://api.powerbi.com/v1.0/myorg/"
    f"datasets/{DATASET_ID}/executeQueries"
)


# ==========================================
# AUTHENTICATION
# ==========================================

app = msal.PublicClientApplication(
    CLIENT_ID,
    authority=AUTHORITY
)

print("\nAuthenticating with Microsoft Entra...")

token_result = app.acquire_token_interactive(
    scopes=SCOPES,
    prompt="select_account"
)

if "access_token" not in token_result:

    print("\nAUTHENTICATION FAILED:")
    print(json.dumps(token_result, indent=2))

    raise SystemExit(1)

access_token = token_result["access_token"]

print("Authentication successful.")


# ==========================================
# EXECUTE DAX
# One query per REST API request
# ==========================================

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}


def execute_dax(query_name, dax):

    body = {
        "queries": [
            {
                "query": dax
            }
        ],
        "serializerSettings": {
            "includeNulls": True
        }
    }

    print(f"\nEXECUTING: {query_name}")
    print("=" * 60)

    response = requests.post(
        API_URL,
        headers=headers,
        json=body,
        timeout=60
    )

    print("HTTP STATUS:", response.status_code)

    try:
        result = response.json()
    except ValueError:
        result = response.text

    print("\nRESPONSE:")

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
        if isinstance(result, (dict, list))
        else result
    )

    return response.status_code, result


# ==========================================
# TEST 1: BASIC DAX
# ==========================================

status, result = execute_dax(
    "Basic DAX",
    """
    EVALUATE
    ROW(
        "MCP_Ping",
        1
    )
    """
)

if status != 200:
    print("\nBasic DAX test failed. Stopping.")
    raise SystemExit(1)


# ==========================================
# TEST 2: ACTUAL BUSINESS MEASURE
# ==========================================

status, result = execute_dax(
    "Net Sales",
    """
    EVALUATE
    ROW(
        "Net Sales SAR",
        [Net Sales]
    )
    """
)

if status == 200:
    print("\nSUCCESS: Both REST API tests passed.")
else:
    print("\nNet Sales REST API test failed.")
