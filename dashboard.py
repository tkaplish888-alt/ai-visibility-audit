"""Phase-2 dashboard (optional). Reads the SAME SQLite DB — no separate pipeline.

    streamlit run dashboard.py
"""
import pandas as pd
import streamlit as st

from aeo.config import load_config
from aeo.metrics import as_dicts, compute

cfg = load_config()
st.title("AEO visibility")

engine = st.selectbox("Engine", ["(all)"] + cfg.engines)
metrics = compute(cfg.database, engine=None if engine == "(all)" else engine)

if not metrics:
    st.info("No data yet — run `python -m aeo.cli run` first.")
else:
    df = pd.DataFrame(as_dicts(metrics))
    st.caption(f"{metrics[0].responses} responses in window")
    st.dataframe(df[["entity", "mention_rate", "citation_rate",
                     "share_of_voice", "avg_position"]], hide_index=True)
    st.bar_chart(df.set_index("entity")["share_of_voice"])
