import os
from vertexai import init
from vertexai.generative_models import GenerativeModel

project = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = os.environ.get("VERTEX_LOCATION", "us-central1")
model_id = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")

init(project=project, location=location)
model = GenerativeModel(model_id)
resp = model.generate_content("Reply with exactly: OK")
text = resp.text.strip()
print(text)
assert text == "OK", f"Unexpected response: {text!r}"
