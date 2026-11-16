"""Structured-planning BI agent (pipeline v6) for the Streamlit app.

Drop-in alternative to ``PowerBIAgent`` (same ``ask`` / ``pending_visual_request``
/ ``close`` interface) that runs locally and never executes model-written DAX:

    request guard -> local Ollama planner (plan or clarification)
    -> alias normalisation + semantic checks + schema/model validation
    -> at most one repair turn with the rejection message
    -> deterministic DAX compiler -> read-only Power BI ExecuteQueries

Selected in ``src/main.py`` with ``INSIGHTPILOT_AGENT=structured``. The model
defaults to ``qwen2.5-coder:7b`` (``INSIGHTPILOT_PLANNER_MODEL`` overrides).
Logs carry a per-question trace ID and pipeline stages only -- no tokens,
credentials or result rows.
"""

import json
import logging
import os
import uuid
from typing import Any, Callable

from evaluation.ollama_query_plan_adapter import (
    build_instructions,
    generate_messages,
    generate_plan,
)
from evaluation.planner_response import parse_planner_response
from evaluation.query_plan_compiler import compile_query_plan
from evaluation.request_guard import screen_request
from evaluation.run_fresh_evaluation import REPAIR_INSTRUCTION_V6

LOGGER = logging.getLogger("insightpilot.structured_agent")

PIPELINE_VERSION = "v6"
DEFAULT_MODEL = "qwen2.5-coder:7b"
MAX_TABLE_ROWS = 50


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:,.4f}" if abs(value) < 1 else f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value).replace("|", "\\|")


def _column_label(name: str) -> str:
    # "DimStore[Region]" -> "Region", "[Net Sales]" -> "Net Sales"
    if name.endswith("]") and "[" in name:
        return name[name.rindex("[") + 1:-1]
    return name


def format_result_table(columns: list[str], rows: list[dict]) -> str:
    if not rows:
        return "_The query returned no rows._"

    header = "| " + " | ".join(_column_label(c) for c in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_format_value(row.get(c)) for c in columns) + " |"
        for row in rows[:MAX_TABLE_ROWS]
    ]
    table = "\n".join([header, divider, *body])
    if len(rows) > MAX_TABLE_ROWS:
        table += f"\n\n_Showing the first {MAX_TABLE_ROWS} of {len(rows)} rows._"
    return table


class StructuredPlanningAgent:
    """Local structured-planning agent with the ``PowerBIAgent`` interface."""

    def __init__(
        self,
        model: str | None = None,
        powerbi: Any | None = None,
        generate: Callable[[str, str, str], str] | None = None,
        chat: Callable[[str, list[dict]], str] | None = None,
    ):
        self.model = model or os.environ.get(
            "INSIGHTPILOT_PLANNER_MODEL", DEFAULT_MODEL)
        if powerbi is None:
            from src.powerbi_query_service import PowerBIQueryService

            powerbi = PowerBIQueryService()
        self.powerbi = powerbi
        self.generate = generate or generate_plan
        self.chat = chat or generate_messages
        self.instructions = build_instructions(PIPELINE_VERSION)
        # This agent never authors visuals; the embedded report is untouched.
        self.pending_visual_request: dict[str, Any] | None = None
        self.last_trace: dict[str, Any] | None = None

    # -------------------------------------------------------------
    # Planning
    # -------------------------------------------------------------

    def _parse(self, raw: str) -> dict:
        return parse_planner_response(raw, semantic_checks=PIPELINE_VERSION)

    def plan(self, question: str, trace: dict) -> dict:
        """Return a parsed response: plan or clarification. Raises
        ``ValueError`` if no valid response is obtained after one repair."""

        blocked = screen_request(question)
        if blocked is not None:
            trace["stages"].append("request_guard:blocked")
            detail = blocked["clarification"]
            return {"kind": "clarification", "reason": detail["reason"],
                    "message": detail["message"]}

        raw = self.generate(self.model, question, self.instructions)
        trace["stages"].append("planner")
        try:
            return self._parse(raw)
        except ValueError as first_error:
            trace["stages"].append("rejected")
            trace["first_error"] = str(first_error)
            LOGGER.info("[%s] plan rejected, repairing: %s",
                        trace["trace_id"], first_error)

        messages = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": question},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_INSTRUCTION_V6.format(
                error=trace["first_error"])},
        ]
        repaired = self.chat(self.model, messages)
        trace["stages"].append("repair")
        return self._parse(repaired)

    # -------------------------------------------------------------
    # Public interface (matches PowerBIAgent)
    # -------------------------------------------------------------

    def ask(self, question: str) -> str:
        self.pending_visual_request = None
        trace = {"trace_id": uuid.uuid4().hex[:12], "stages": [],
                 "pipeline": PIPELINE_VERSION, "model": self.model}
        self.last_trace = trace
        LOGGER.info("[%s] question received", trace["trace_id"])

        try:
            response = self.plan(question, trace)
        except ValueError as exc:
            trace["stages"].append("failed_validation")
            trace["error"] = str(exc)
            LOGGER.info("[%s] no valid plan: %s", trace["trace_id"], exc)
            return (
                "I couldn't turn that question into a valid query against "
                "the semantic model, so nothing was executed. Try naming "
                "the measure and the breakdown you want (for example "
                "*net sales by store region*).\n\n"
                f"_Reason: {exc}_\n\n_Trace: `{trace['trace_id']}`_"
            )

        if response["kind"] == "clarification":
            trace["stages"].append(f"clarification:{response['reason']}")
            LOGGER.info("[%s] clarification (%s)", trace["trace_id"],
                        response["reason"])
            return (
                f"{response['message']}\n\n"
                f"_No query was executed. Trace: `{trace['trace_id']}`_"
            )

        plan = response["plan"]
        dax = compile_query_plan(plan)
        trace["stages"].append("compiled")
        if response.get("normalizations"):
            trace["normalizations"] = response["normalizations"]

        try:
            result = self.powerbi.execute_dax(dax)
        except Exception as exc:  # surfaced to the user, logged without data
            trace["stages"].append("execution_error")
            LOGGER.warning("[%s] execution failed: %s", trace["trace_id"],
                           type(exc).__name__)
            return (
                f"The query was valid but Power BI returned an error: {exc}\n\n"
                f"```dax\n{dax}\n```\n\n_Trace: `{trace['trace_id']}`_"
            )

        trace["stages"].append("executed")
        trace["row_count"] = result["row_count"]
        LOGGER.info("[%s] executed, %d rows", trace["trace_id"],
                    result["row_count"])

        return "\n\n".join([
            format_result_table(result["columns"], result["rows"]),
            "**Query plan and DAX**\n\n"
            f"```json\n{json.dumps(plan, indent=2)}\n```\n\n"
            f"```dax\n{dax}\n```",
            f"_Structured pipeline {PIPELINE_VERSION} · {self.model} · "
            f"{' → '.join(trace['stages'])} · Trace: `{trace['trace_id']}`_",
        ])

    def close(self):
        self.powerbi.close()
