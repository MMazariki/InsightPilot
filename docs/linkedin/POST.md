# LinkedIn post: InsightPilot

Attach the four images in this order (all in this folder). Suggested alt
text is under each.

1. `1-ask-in-plain-language.png`: "InsightPilot answering 'Delivery share of
   net sales by year' with a table of shares from 14.2% in 2021 to 29.3% in
   2026, next to the embedded Power BI report."
2. `2-checked-plan-and-dax.png`: "The checked JSON query plan and the DAX
   compiled from it, shown under every answer."
3. `3-documents-with-citations.png`: "The Documents page answering from a PDF,
   with the source file and page, the passage used and a Read aloud button."
4. `4-admin-models.png`: "Admin settings: local Ollama models today, with
   OpenAI-compatible and Claude providers available."

Tip: image 1 shows the report panel before sign-in. A screenshot of your own
signed-in report with a chart drawn on the AI Canvas page would make a
stronger first image.

---

I wanted to ask my Power BI model questions in plain English, without sending business data to a cloud AI and without letting a language model write DAX that runs unchecked.

So I built InsightPilot: a local assistant that sits next to a live, embedded Power BI report.

How it works:
🔹 A local model (Qwen2.5-Coder 7B on Ollama) turns the question into a small JSON query plan, not DAX.
🔹 The plan is validated against the semantic model (tables, measures, real column values), then checked: does it actually answer what was asked? If not, the assistant asks instead of guessing.
🔹 A deterministic compiler turns the plan into DAX, run read-only through the Power BI REST API.
🔹 Each answer draws its chart on an "AI Canvas" page of the embedded report and shows the plan and DAX behind it.

What measuring it taught me (small, pre-registered test sets):
• Letting the 7B model write DAX directly: 7 of 12 holdout questions correct. Structured plan + compiler, same model: 9 of 12.
• The dangerous failures weren't errors. They were confident substitutions, like "net profit" silently answered with Net Sales, or "average order value" computed per unit. Catching those mattered more than the headline score.

Also in the app:
📄 A Documents page that answers only from PDFs (local RAG: nomic-embed-text + keyword search), citing file and page, with each folder kept separate.
🎙️ Spoken questions (local whisper.cpp) and read-aloud answers.
📈 Growth, share and Ramadan-period questions, and everyday wording ("branch", "revenue", "last month").
⚙️ An admin page to plug in OpenAI-compatible or Claude models later.

It all runs on one Windows PC with a 6 GB GPU: no cloud AI bill, and the data never leaves the machine. The sales data in the screenshots is synthetic.

Next: measuring accuracy on business users' own questions.

#PowerBI #GenerativeAI #RAG #LocalLLM #Ollama #BusinessIntelligence #DataAnalytics #Python

---

Optional line, if you want to mention how it was built:
"Built with Claude Code as my pair programmer."
