
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
model = os.environ["FOUNDRY_MODEL"]

credential = DefaultAzureCredential()

project = AIProjectClient(
    endpoint=endpoint,
    credential=credential,
)

openai = project.get_openai_client()

response = openai.responses.create(
    model=model,
    input="Reply with: AgenticProject Foundry connection successful."
)

print(response.output_text)

openai.close()
project.close()
credential.close()
