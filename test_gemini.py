import os
from google import genai

client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
)

resp = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Say hello in one short sentence.",
)
print(resp.text)
print("Tokens used:", resp.usage_metadata)
