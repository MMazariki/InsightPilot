
import json
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from src.powerbi_query_service import PowerBIQueryService


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FOUNDRY_ENDPOINT = (
    "https://foundryagenticproject-resource.services.ai.azure.com/"
    "api/projects/FoundryAgenticProject"
)

MODEL_NAME = "gpt-5.2"

SCHEMA_FILE = PROJECT_ROOT / "fabric_iq_schema.json"

CREATE_VISUAL_TOOL = {
    "type": "function",
    "name": "create_powerbi_visual",
    "description": (
    "Create a native Power BI visual in the embedded report. "
    "For standard clustered column, clustered bar, and line charts, "
    "use categoryTable, categoryColumn, valueTable, and valueMeasure "
    "with fields set to null. "
    "For a lineClusteredColumnComboChart, set the four legacy "
    "category/value parameters to null and supply fields with "
    "these exact Power BI roles: Category for the X-axis column, "
    "Y for the column measure, and Y2 for the line measure. "
    "Example: DimDate[MonthName] as Category, "
    "FactSales[Net Sales] as Y, and "
    "FactSales[Margin %] as Y2. "
    "Each field must specify its table and either its column "
    "or measure, with the other property set to null. "
    "Only use fields that exist in the semantic model."
),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "visualType": {
                "type": "string",
                "enum": [
                    "clusteredColumnChart",
                    "clusteredBarChart",
                    "lineChart",
                    "lineClusteredColumnComboChart"
                ]
            },
            "categoryTable": {"type": ["string", "null"]},
            "categoryColumn": {"type": ["string", "null"]},
            "valueTable": {"type": ["string", "null"]},
            "valueMeasure": {"type": ["string", "null"]},
            "fields": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "table": {"type": "string"},
                        "column": {"type": ["string", "null"]},
                        "measure": {"type": ["string", "null"]}
                    },
                    "required": [
                        "role",
                        "table",
                        "column",
                        "measure"
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": [
            "visualType",
            "categoryTable",
            "categoryColumn",
            "valueTable",
            "valueMeasure",
            "fields"
        ],
        "additionalProperties": False
    }
}

TOOLS = [
    CREATE_VISUAL_TOOL,
    {
        "type": "function",
        "name": "execute_powerbi_dax",
        "description": (
            "Execute a read-only DAX query against the AgenticProject "
            "Power BI semantic model. Use this function when numerical "
            "data or analytical results are required."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "dax_query": {
                    "type": "string",
                    "description": (
                        "A valid DAX query containing an EVALUATE statement."
                    )
                }
            },
            "required": ["dax_query"],
            "additionalProperties": False
        }
    }
]


def load_semantic_schema() -> str:

    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(
            f"Semantic schema not found: {SCHEMA_FILE}"
        )

    with open(SCHEMA_FILE, "r", encoding="utf-8") as file:
        schema = json.load(file)

    return json.dumps(schema, indent=2)


def execute_powerbi_dax(
    dax_query: str,
    powerbi: PowerBIQueryService
) -> dict[str, Any]:

    # Basic validation before passing DAX to Power BI.

    if not isinstance(dax_query, str):
        raise ValueError("DAX query must be a string.")

    if "EVALUATE" not in dax_query.upper():
        raise ValueError("DAX query must contain EVALUATE.")

    if dax_query.count(";") > 0:
        raise ValueError("Multiple DAX statements are not allowed.")

    print("\n[POWER BI] Executing DAX:")
    print(dax_query)

    result = powerbi.execute_dax(dax_query)

    print(
        f"[POWER BI] Returned {result['row_count']} rows."
    )

    return result


class PowerBIAgent:

    def __init__(self):

        self.credential = DefaultAzureCredential()

        self.project = AIProjectClient(
            endpoint=FOUNDRY_ENDPOINT,
            credential=self.credential
        )

        self.openai = self.project.get_openai_client()

        self.powerbi = PowerBIQueryService()

        self.schema = load_semantic_schema()
        # Visual request generated by the Foundry agent.
        self.pending_visual_request: dict[str, Any] | None = None

        self.instructions = f"""
You are a Business Intelligence analyst connected to the
AgenticProject Power BI semantic model.

Your responsibility is to answer business questions using
the actual data in the semantic model.

SEMANTIC MODEL SCHEMA:
{self.schema}

RULES:

1. Use only tables, columns, relationships and measures
   defined in the supplied schema.

2. Generate valid DAX queries.

3. Every query must contain an EVALUATE statement.

4. Use existing semantic model measures whenever appropriate.
   Do not recreate an existing measure unnecessarily.

5. Never invent numerical results.

6. When a question requires actual data, call
   execute_powerbi_dax.

7. Interpret the returned data and explain the result
   in clear business language.

8. If the question is ambiguous, ask for clarification.

9. Never claim that a query succeeded unless the function
   returned successful results.

10. Do not generate queries that modify data or model objects.
"""

    
    def ask(self, question: str) -> str:

        self.pending_visual_request = None

        response = self.openai.responses.create(
            model=MODEL_NAME,
            instructions=self.instructions,
            input=question,
            tools=TOOLS,
            tool_choice="auto"
        )

        max_rounds = 8

        for round_number in range(max_rounds):

            function_calls = [
                item for item in response.output
                if item.type == "function_call"
            ]

            if not function_calls:
                return response.output_text or (
                    "The model returned no text response."
                )

            print(
                f"\n[AGENT] Function-calling round "
                f"{round_number + 1}"
            )

            tool_outputs = []

            for call in function_calls:

                print(f"[AGENT] Requested function: {call.name}")

                if call.name == "create_powerbi_visual":

                    try:
                        arguments = json.loads(call.arguments)

                        fields = arguments.get("fields")

                        if fields is not None:
                            converted_fields = []

                            for field in fields:
                                if field["column"] is not None:
                                    target = {
                                        "table": field["table"],
                                        "column": field["column"],
                                    }
                                elif field["measure"] is not None:
                                    target = {
                                        "table": field["table"],
                                        "measure": field["measure"],
                                    }
                                else:
                                    raise ValueError(
                                        "Each visual field must specify a column or measure."
                                    )

                                converted_fields.append({
                                    "role": field["role"],
                                    "target": target,
                                })

                            self.pending_visual_request = {
                                "visualType": arguments["visualType"],
                                "fields": converted_fields,
                            }

                        else:
                            self.pending_visual_request = {
                                "visualType": arguments["visualType"],
                                "category": {
                                    "table": arguments["categoryTable"],
                                    "column": arguments["categoryColumn"],
                                },
                                "value": {
                                    "table": arguments["valueTable"],
                                    "measure": arguments["valueMeasure"],
                                },
                            }

                        print(
                            "[AGENT] Visual requested:",
                            self.pending_visual_request
                        )

                        output = json.dumps({
                            "success": True,
                            "message": "Visual request prepared for the frontend."
                        })

                    except Exception as exc:

                        output = json.dumps({
                            "success": False,
                            "error": str(exc)
                        })

                elif call.name != "execute_powerbi_dax":

                    output = json.dumps({
                        "success": False,
                        "error": f"Unsupported function: {call.name}"
                    })

                else:

                    try:

                        arguments = json.loads(call.arguments)

                        print("[AGENT] DAX arguments:")
                        print(arguments["dax_query"])

                        result = execute_powerbi_dax(
                            arguments["dax_query"],
                            self.powerbi
                        )

                        output = json.dumps(
                            result,
                            default=str
                        )

                    except Exception as exc:

                        print(f"[AGENT ERROR] {exc}")

                        output = json.dumps({
                            "success": False,
                            "error": str(exc)
                        })

                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output
                })

            response = self.openai.responses.create(
                model=MODEL_NAME,
                instructions=self.instructions,
                previous_response_id=response.id,
                input=tool_outputs,
                tools=TOOLS,
                tool_choice="auto"
            )

        return (
            "Maximum function-calling rounds reached. "
            "Inspect the function-calling log above."
        )


    def close(self):

        self.powerbi.close()
        self.openai.close()
        self.project.close()
        self.credential.close()


if __name__ == "__main__":

    agent = PowerBIAgent()

    try:

        print("\nInsightPilot BI Agent")
        print("----------------------")

        while True:

            question = input("\nYou: ").strip()

            if question.lower() in ("exit", "quit"):
                break

            if not question:
                continue

            answer = agent.ask(question)

            print("\nAgent:")
            print(answer)

    finally:
        agent.close()
