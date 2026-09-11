from contextlib import asynccontextmanager
import json

import httpx
from google import genai
from google.genai import types


def sdk_fixture(post):
    @asynccontextmanager
    async def client(response_hook):
        async def handler(request):
            return await post(str(request.url), json=json.loads(request.content))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                event_hooks={"response": [response_hook]}) as transport:
            sdk = genai.Client(api_key="test-only", vertexai=False, http_options=types.HttpOptions(
                httpx_async_client=transport, retry_options=types.HttpRetryOptions(attempts=1)))
            try:
                async with sdk.aio as aio:
                    yield aio
            finally:
                sdk.close()
    return client
