import os

import streamlit as st

from powerbi_embed_component import powerbi_embed_component


# Agent selection. "foundry" (default) keeps the original Azure AI Foundry
# agent; "structured" uses the local structured-planning pipeline
# (src/structured_agent.py: local Ollama + validated plans + read-only DAX).
AGENT_MODE = os.environ.get("INSIGHTPILOT_AGENT", "foundry").strip().lower()


def create_agent():
    if AGENT_MODE == "structured":
        from src.structured_agent import StructuredPlanningAgent

        return StructuredPlanningAgent()

    from src.powerbi_agent import PowerBIAgent

    return PowerBIAgent()


st.set_page_config(
    page_title="AgenticProject",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =====================================================
# SESSION STATE
# =====================================================

if "messages" not in st.session_state:
    st.session_state.messages = []

if "agent" not in st.session_state:
    st.session_state.agent = None
if "visual_request" not in st.session_state:
    st.session_state.visual_request = None


# =====================================================
# APPLICATION HEADER
# =====================================================

st.title("AgenticProject")
st.caption(
    "AI-powered Business Intelligence | "
    + (
        "Local structured planning + Power BI"
        if AGENT_MODE == "structured"
        else "Azure AI Foundry + Power BI"
    )
)

st.divider()


# =====================================================
# MAIN LAYOUT
# =====================================================

chat_column, report_column = st.columns(
    [1, 1],
    gap="large",
)


# =====================================================
# LEFT PANEL - BI AGENT
# =====================================================

with chat_column:

    st.subheader("BI Agent")
    st.caption("Ask questions about your business data.")

    chat_container = st.container(height=620)

    with chat_container:

        if not st.session_state.messages:

            st.info(
                "Welcome to AgenticProject. "
                "Ask a business question to get started."
            )

        for message in st.session_state.messages:

            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    prompt = st.chat_input(
        "Ask a business question...",
        key="agent_chat_input",
    )

    if prompt:

        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
        })

        with st.spinner("Analyzing your data..."):

            try:

                if st.session_state.agent is None:
                    st.session_state.agent = create_agent()

                answer = st.session_state.agent.ask(prompt)
                if st.session_state.agent.pending_visual_request:
                    import uuid

                    st.session_state.visual_request = {
                        **st.session_state.agent.pending_visual_request,
                        "requestId": str(uuid.uuid4()),
                    }

                    print(
                        "[STREAMLIT] Visual request received:",
                        st.session_state.visual_request
                    )

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                })

            except Exception as exc:

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"**Error:** {exc}",
                })

        st.rerun()


# =====================================================
# RIGHT PANEL - POWER BI REPORT
# =====================================================

with report_column:

    st.subheader("Power BI Report")
    st.caption("Interactive AgenticProject semantic model.")

    powerbi_embed_component(
        key="main-powerbi-report",
        visual_request=st.session_state.visual_request,
    )
