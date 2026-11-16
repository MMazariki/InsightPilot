
import streamlit as st

out = st.components.v2.component(
    "powerbi_embed_component.powerbi_embed_component",
    js="index-*.js",
    html="""
        <div class="component-root">
            <style>
                .component-root {
                    width: 100%;
                    font-family: "Segoe UI Variable Text", "Segoe UI",
                        system-ui, -apple-system, "Helvetica Neue", Arial,
                        sans-serif;
                }

                .pbi-toolbar {
                    display: flex;
                    align-items: center;
                    gap: 14px;
                    margin-bottom: 12px;
                    flex-wrap: wrap;
                }

                .pbi-login {
                    background: #2251D1;
                    color: #fff;
                    border: 1px solid #2251D1;
                    border-radius: 10px;
                    padding: 9px 18px;
                    cursor: pointer;
                    font: inherit;
                    font-size: 14px;
                    font-weight: 600;
                    line-height: 1.4;
                    box-shadow: 0 1px 2px rgba(16, 24, 40, .08),
                        0 6px 14px -8px rgba(34, 81, 209, .7);
                    transition: background .15s, box-shadow .15s,
                        transform .15s;
                }

                .pbi-login:hover:not(:disabled) {
                    background: #1A3FA8;
                    border-color: #1A3FA8;
                    transform: translateY(-1px);
                }

                .pbi-login:focus-visible {
                    outline: none;
                    box-shadow: 0 0 0 3px rgba(34, 81, 209, .28);
                }

                .pbi-login:disabled {
                    background: #EEF1F7;
                    border-color: #E4E7EC;
                    color: #667085;
                    box-shadow: none;
                    cursor: wait;
                }

                .pbi-status {
                    font-size: 13px;
                    color: #667085;
                }

                .pbi-report {
                    width: 100%;
                    height: 760px;
                    border: 1px solid #E4E7EC;
                    border-radius: 12px;
                    background: #FAFBFD;
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
