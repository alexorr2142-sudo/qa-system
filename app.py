"""Streamlit front end for rag.py. Run: uv run streamlit run app.py"""
import re
import streamlit as st

import rag

st.set_page_config(page_title="QA System", page_icon="📄", layout="wide")


@st.cache_resource(show_spinner="Loading index…")
def retriever(size: int) -> rag.Retriever:
    r = rag.Retriever(size)
    rag._retrievers[size] = r  # share with rag.ask()
    return r


sizes = sorted(int(m.group(1)) for p in rag.CACHE_DIR.glob("chunks_*.json") if (m := re.search(r"chunks_(\d+)", p.name)))
if not sizes:
    st.error("No index found. Run `uv run python rag.py --build --sizes 256,400,512,1024` first.")
    st.stop()

with st.sidebar:
    st.header("Settings")
    size = st.selectbox("Chunk size (tokens)", sizes, index=sizes.index(rag.DEFAULT_SIZE) if rag.DEFAULT_SIZE in sizes else 0)
    k = st.slider("Chunks retrieved (top-k)", 1, 10, rag.DEFAULT_K)
    r = retriever(size)
    st.caption(f"{len(r.chunks)} chunks · model {rag.CLAUDE_MODEL} · embeddings {rag.EMBED_MODEL.split('/')[-1]} · {len(rag.cache_load())} cached answers")
    st.markdown("**Documents**")
    for f, short in rag.SHORT_NAMES.items():
        st.caption(f"`{short}` — {f}" + (f"  \n⚠ {rag.DOC_NOTES[short]}" if short in rag.DOC_NOTES else ""))

st.title("QA System")
st.caption("Ask a question about the six documents. Answers cite `[doc, p.N]`; open a source below to check the quote.")

q = st.text_input("Question", placeholder="e.g. How much slower were developers in the METR study?")
c1, c2, _ = st.columns([1, 2, 6])
go, bypass = c1.button("Ask", type="primary"), c2.button("Re-ask (bypass cache)")

if (go or bypass) and q.strip():
    try:
        with st.spinner("Retrieving and asking Claude…"):
            res = rag.ask(q, size, k, bypass=bypass)
    except rag.LLMError as e:
        st.error(f"LLM call failed — {e}")
        res = rag.Result("", retriever(size).search(q, k), False, 0)
    if res.answer:
        st.markdown(res.answer)
        badge = f"cached · {res.latency_ms} ms" if res.cached else f"live · {res.latency_ms / 1000:.1f} s"
        st.caption(f"**{badge}** · chunk size {size} · top-{k}")
    st.subheader("Sources")
    for i, (c, s) in enumerate(res.hits, 1):
        pages = f"p.{c['page_start']}" + (f"-{c['page_end']}" if c["page_end"] != c["page_start"] else "")
        with st.expander(f"[{i}] {c['doc']} {pages} · score {s:.3f} · {c['n_tokens']} tokens"):
            st.text(c["text"])
