import asyncio
import os
from dotenv import load_dotenv
import openai

load_dotenv()
print('OPENAI_KEY_PRESENT=', os.getenv('OPENAI_API_KEY') is not None)
print('OPENAI_VERSION=', openai.__version__)

async def main():
    client = openai.AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    response = await client.chat.completions.create(
        model='gpt-3.5-turbo',
        messages=[{'role': 'user', 'content': 'Hello, please respond with OK.'}],
        temperature=0.0,
        max_tokens=10,
        timeout=30
    )
    print('CONTENT=', response.choices[0].message.content)
    print('TOKENS=', response.usage.total_tokens)
    await client.aclose()

asyncio.run(main())