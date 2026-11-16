"""Builds the app's agent from the admin settings (``src/settings.py``)."""

from src import llm
from src import settings as app_settings


def structured_agent_kwargs(settings, secrets=None, powerbi=None):
    """Keyword arguments for ``StructuredPlanningAgent`` from admin settings."""

    secrets = app_settings.load_secrets() if secrets is None else secrets
    planner = settings["planner"]
    verifier = app_settings.effective_verifier(settings)

    generate, chat = llm.build_callables(
        planner, app_settings.api_key_for("planner", settings, secrets))
    _, verifier_chat = llm.build_callables(
        verifier, app_settings.api_key_for("verifier", settings, secrets))

    if powerbi is None:
        from src.powerbi_query_service import PowerBIQueryService

        options = settings["powerbi"]
        powerbi = PowerBIQueryService(
            dataset_id=options["dataset_id"] or None,
            workspace_id=options["workspace_id"] or None,
            username=options["username"] or None,
            timeout_seconds=options["timeout_seconds"],
        )

    return {
        "model": planner["model"],
        "powerbi": powerbi,
        "generate": generate,
        "chat": chat,
        "options": dict(
            settings["pipeline"],
            create_visuals=settings["interface"]["create_charts"]),
        "verifier_chat": verifier_chat,
        "verifier_model": verifier["model"],
    }


def apply_foundry_overrides(settings):
    """Point the Azure AI Foundry agent at the endpoint/model from settings."""

    from src import powerbi_agent

    if settings["foundry"]["endpoint"]:
        powerbi_agent.FOUNDRY_ENDPOINT = settings["foundry"]["endpoint"]
    if settings["foundry"]["model"]:
        powerbi_agent.MODEL_NAME = settings["foundry"]["model"]
