"""Answers from a folder of PDFs (retrieval-augmented generation).

Plan and design: docs/RAG_PLAN.md. Everything runs on this computer:
pypdf reads the PDFs, an Ollama embedding model turns passages into
vectors, numpy searches them, and an Ollama chat model writes the answer
from the retrieved passages only.
"""
