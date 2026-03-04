import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["CUSTOM_API_KEY"],
    base_url="https://api.z.ai/api/paas/v4/",
)

stream = client.chat.completions.create(
    model="glm-5",
    messages=[
        {"role": "system", "content": "You are a smart and creative novelist"},
        {
            "role": "user",
            "content": "Hello. Who are you?",
        },
    ],
    stream=True,
)

for chunk in stream:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)

print()
