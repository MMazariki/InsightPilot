
import streamlit as st

out = st.components.v2.component(
    "powerbi_embed_component.powerbi_embed_component",
    js="index-*.js",
    html="""
        <div class="component-root">
            <style>
                .component-root {
                    width: 100%;
                    font-family: Arial, sans-serif;
                }

                .pbi-toolbar {
                    display: flex;
                    align-items: center;
                    gap: 14px;
                    margin-bottom: 12px;
                    flex-wrap: wrap;
                }

                .pbi-login {
                    background: #0078d4;
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 10px 18px;
                    cursor: pointer;
                    font-size: 14px;
                }

                .pbi-login:disabled {
                    opacity: 0.6;
                    cursor: wait;
                }

                .pbi-status {
                    font-size: 13px;
                    color: #666;
                }

                .pbi-report {
                    width: 100%;
                    height: 760px;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    overflow: hidden;
                }
            </style>

            <div class="pbi-toolbar">
                <button class="pbi-login">
                    Sign in and load Power BI
                </button>
                <span class="pbi-status">
                    Sign in to display your report.
                </span>
            </div>

            <div class="pbi-report"></div>
        </div>
    """,
)


def powerbi_embed_component(
    key="powerbi-report",
    visual_request=None,
):
    """Display the authenticated Power BI report and accept visual requests."""

    return out(
        key=key,
        data={
            "clientId": "3a9f7d57-1c7c-4c6a-9ca7-f2b4189c257e",
            "tenantId": "ed1297fe-5664-4236-a596-1c89e6f498b7",
            "redirectUri": "http://localhost:8501",
            "reportId": "59c7816e-28a0-489c-ac5d-54c2df8250c3",
            "pageId": "84e6ab1c3d09669a719b",
            "visualRequest": visual_request,
        },
    )
