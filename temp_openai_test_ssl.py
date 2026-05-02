import asyncio
import os
import ssl
from dotenv import load_dotenv
import openai
import httpx

load_dotenv()
print('OPENAI_KEY_PRESENT=', os.getenv('OPENAI_API_KEY') is not None)
print('OPENAI_VERSION=', openai.__version__)

async def main():
    # Create a custom HTTP client with SSL verification disabled for testing
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    async with httpx.AsyncClient(verify=False) as http_client:
        client = openai.AsyncOpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            http_client=http_client,
        )
        try:
            response = await client.chat.completions.create(
                model='gpt-3.5-turbo',
                messages=[{'role': 'user', 'content': 'Hello, please respond with OK.'}],
                temperature=0.0,
                max_tokens=10,
            )
            print('CONTENT=', response.choices[0].message.content)
            print('TOKENS=', response.usage.total_tokens)
            print('SUCCESS=True')
        except Exception as e:
            print(f'ERROR={type(e).__name__}: {e}')
            print('SUCCESS=False')

asyncio.run(main())
