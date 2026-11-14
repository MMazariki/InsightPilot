import streamlit as st
from powerbi_embed_component import powerbi_embed_component

st.set_page_config(
    page_title="AgenticProject - Power BI",
    page_icon="📊",
    layout="wide",
)

st.title("AgenticProject")
st.caption("Interactive Power BI report embedding test")

st.markdown("---")

powerbi_embed_component()