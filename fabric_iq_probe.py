
import asyncio
import json
import os
import sys
import traceback

import msal
import httpx2

from mcp import Client
from mcp.client.streamable_http import streamable_http_client


# =====================================================
# 1. CONFIGURATION
# =====================================================

SERVER_URL = ("https://fabriciq.svc.cloud.microsoft/v1/mcp/fabriciq")

SCOPES = [
    "https://analysis.windows.net/powerbi/api/Item.Read.All",
    "https://analysis.windows.net/powerbi/api/Item.Execute.All",
]

VARIANT_HEADER = "Fabric.Routing.FabricIQ.V1"

ARTIFACT_ID = "f1e8ea4d-1f1e-4c62-aedd-60a59d3ae929"


# =====================================================
# 2. MICROSOFT ENTRA AUTHENTICATION
# =====================================================

def acquire_token():

    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    client_id = os.environ.get("ENTRA_CLIENT_ID")

    if not tenant_id or not client_id:
        raise RuntimeError(
            "Missing ENTRA_TENANT_ID or ENTRA_CLIENT_ID."
        )

    authority = (
        f"https://login.microsoftonline.com/{tenant_id}"
    )

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=authority
    )

    print("\nStarting Microsoft authentication...")

    result = app.acquire_token_interactive(
        scopes=SCOPES,
        prompt="select_account"
    )

    if "access_token" not in result:
        raise RuntimeError(
            f"{result.get('error')}: "
            f"{result.get('error_description')}"
        )

    print("Authentication successful.")

    return result["access_token"]


# =====================================================
# 3. FABRIC IQ MCP CONNECTION
# =====================================================

async def connect_fabric_iq(access_token):

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Variants": VARIANT_HEADER
    }

    timeout = httpx2.Timeout(
        30.0,
        read=300.0
    )

    async with httpx2.AsyncClient(
        headers=headers,
        timeout=timeout
    ) as http_client:

        transport = streamable_http_client(
            SERVER_URL,
            http_client=http_client
        )

        async with Client(transport) as client:

            print("\nConnected to Fabric IQ MCP.")

            # ==========================================
            # STEP 10: RETRIEVE MCP TOOL DEFINITIONS
            # ==========================================

            response = await client.list_tools()

            tools_data = []

            print("\nAVAILABLE MCP TOOLS")
            print("=" * 65)

            for tool in response.tools:

                tool_data = tool.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True
                )

                tools_data.append(tool_data)

                print(f"\nTool: {tool.name}")
                print(f"Description: {tool.description}")
                print("\nInput Schema:")

                print(
                    json.dumps(
                        tool_data.get("inputSchema", {}),
                        indent=2,
                        ensure_ascii=False
                    )
                )

            output_file = "fabric_iq_tools.json"

            with open(
                output_file,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    tools_data,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            print("\n" + "=" * 65)
            print(f"Total tools discovered: {len(tools_data)}")
            print(f"Definitions saved: {output_file}")

            # ==========================================
            # STEP 11: DISCOVER POWER BI SEMANTIC MODEL
            # ==========================================

            print("\nDISCOVERING POWER BI SEMANTIC MODELS...")

            discovery = await client.call_tool(
                "DiscoverArtifacts",
                {
                    "searchQuery": "Retail",
                    "artifactTypes": ["SemanticModel"],
                    "maxResults": 20
                }
            )

            if discovery.is_error:
                raise RuntimeError(
                    f"Discovery failed: {discovery.content}"
                )

            discovery_data = discovery.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True
            )

            print("\nDISCOVERY RESULTS:")
            print("=" * 65)

            print(
                json.dumps(
                    discovery_data,
                    indent=2,
                    ensure_ascii=False
                )
            )

            with open(
                "fabric_iq_discovery.json",
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    discovery_data,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            print("\nDiscovery saved to fabric_iq_discovery.json")

            # ==========================================
            # STEP 12: RESOLVE SEMANTIC MODEL IDENTITY
            # ==========================================

            print("\nRESOLVING SEMANTIC MODEL...")

            resolved = await client.call_tool(
                "ResolveFabricItem",
                {
                    "fabricItemId": ARTIFACT_ID
                }
            )

            if resolved.is_error:
                raise RuntimeError(
                    f"Resolve failed: {resolved.content}"
                )

            resolved_data = resolved.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True
            )

            with open(
                "fabric_iq_resolved.json",
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    resolved_data,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            print("\nCanonical identity:")
            print(
                json.dumps(
                    resolved_data.get("structuredContent", {}),
                    indent=2
                )
            )

            # ==========================================
            # STEP 13: RETRIEVE SEMANTIC MODEL SCHEMA
            # ==========================================

            print("\nRETRIEVING SEMANTIC MODEL SCHEMA...")

            resolved_content = resolved_data.get(
                "structuredContent", {}
            )

            canonical_id = (
                resolved_content.get("fabricItemId")
                or ARTIFACT_ID
            )

            schema_result = await client.call_tool(
                "GetSemanticModelSchema",
                {
                    "artifactId": canonical_id
                }
            )

            if schema_result.is_error:
                raise RuntimeError(
                    f"Schema retrieval failed: "
                    f"{schema_result.content}"
                )

            schema_data = schema_result.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True
            )

            with open(
                "fabric_iq_schema.json",
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    schema_data,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            print("\nSCHEMA RETRIEVED SUCCESSFULLY")

            print(
                f"Schema file size: "
                f"{os.path.getsize('fabric_iq_schema.json'):,} bytes"
            )

            print("Saved: fabric_iq_schema.json")

            # ==========================================
            # STEP 14: FIRST DAX EXECUTION
            # ==========================================

            
            # ==========================================
            # STEP 14: DAX EXECUTION DIAGNOSTICS
            # ==========================================

            print("\nSTARTING DAX DIAGNOSTIC TESTS...")

            diagnostic_queries = [

                (
                    "TEST 1 - BASIC DAX",
                    """
                    EVALUATE
                    ROW("MCP_Ping", 1)
                    """
                ),

                (
                    "TEST 2 - FACT TABLE ACCESS",
                    """
                    EVALUATE
                    ROW(
                        "FactSalesRows",
                        COUNTROWS('FactSales')
                    )
                    """
                ),

                (
                    "TEST 3 - MEASURE EXECUTION",
                    """
                    EVALUATE
                    ROW(
                        "NetSales",
                        [Net Sales]
                    )
                    """
                )
            ]

            diagnostic_results = {}

            for test_name, dax in diagnostic_queries:

                print("\n" + "=" * 65)
                print(test_name)
                print("=" * 65)

                result = await client.call_tool(
                    "ExecuteQuery",
                    {
                        "artifactId": ARTIFACT_ID,
                        "daxQueries": [dax],
                        "maxRows": 10
                    }
                )

                result_data = result.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True
                )

                diagnostic_results[test_name] = result_data

                if result.is_error:

                    print("\nQUERY FAILED:")
                    print(
                        json.dumps(
                            result_data,
                            indent=2,
                            ensure_ascii=False
                        )
                    )

                    print(
                        "\nStopping diagnostics at first failure."
                    )

                    break

                print("\nQUERY SUCCEEDED:")
                print(
                    json.dumps(
                        result_data,
                        indent=2,
                        ensure_ascii=False
                    )
                )

            with open(
                "fabric_iq_diagnostics.json",
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    diagnostic_results,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            print("\nDiagnostics saved:")
            print("fabric_iq_diagnostics.json")



# =====================================================
# 4. ERROR REPORTING
# =====================================================

def show_causes(group, level=0):

    for item in group.exceptions:

        if isinstance(item, BaseExceptionGroup):
            show_causes(item, level + 1)

        else:
            print(
                "  " * level
                + f"{type(item).__name__}: {item}"
            )


# =====================================================
# 5. APPLICATION ENTRY POINT
# =====================================================

if __name__ == "__main__":

    try:

        token = acquire_token()

        asyncio.run(
            connect_fabric_iq(token)
        )

    except KeyboardInterrupt:

        print("\nOperation cancelled.")
        sys.exit(1)

    except Exception as exc:

        print("\nFULL ERROR DETAILS")
        print("=" * 65)

        traceback.print_exception(exc)

        if isinstance(exc, BaseExceptionGroup):

            print("\nROOT CAUSES:")
            show_causes(exc)

        sys.exit(1)
