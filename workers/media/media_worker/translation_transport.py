"""Text-only translation transports. Retries remain owned by the segment queue."""
import asyncio
import json
import os
import re
import time

import httpx
from video_service.llm_providers import PROVIDERS, provider_headers
from .content_block import ContentBlockedError
from .llm_diagnostics import emit, exception_details
from .llm_trace import begin_call, finish_call
from .transcription_queue import RetryableTranscriptionError
from .worker_storage import disk_call


class ModelRequestError(RuntimeError):
    pass


def json_schema(schema):
    result = dict(schema)
    if 'type' in result:
        result['type'] = result['type'].lower()
    if 'items' in result:
        result['items'] = json_schema(result['items'])
    if 'properties' in result:
        result['properties'] = {key: json_schema(value) for key, value in result['properties'].items()}
        result['additionalProperties'] = False
        result['required'] = list(result['properties'])
    return result


def request_payload(provider, model, parts, schema):
    if any(set(part) != {'text'} for part in parts):
        raise ValueError('Translation transport accepts text only')
    wrapped = {'type': 'object', 'properties': {'translations': json_schema(schema)},
               'required': ['translations'], 'additionalProperties': False}
    messages = [
        {'role': 'system', 'content': 'Translate subtitles using the supplied instructions. '
         'Return a JSON object with a translations array containing the requested entries.'},
        {'role': 'user', 'content': '\n'.join(part['text'] for part in parts)},
    ]
    if provider == 'anthropic':
        return {'model': model, 'max_tokens': 8192, 'system': messages[0]['content'],
                'messages': messages[1:], 'output_config': {'format': {'type': 'json_schema', 'schema': wrapped}}}
    payload = {'model': model, 'messages': messages,
               'response_format': {'type': 'json_schema', 'json_schema': {
                   'name': 'subtitle_translation', 'strict': True, 'schema': wrapped}}}
    if provider == 'openai':
        payload.update(max_completion_tokens=16384, store=False)
    else:
        payload['max_tokens'] = 8192
    if provider == 'openrouter':
        # Never silently drop schema requirements or change models at the gateway.
        payload['provider'] = {'require_parameters': True, 'allow_fallbacks': False}
    return payload


def parse_response(provider, data):
    if not isinstance(data, dict):
        raise ValueError('Provider returned malformed response')
    if data.get('error'):
        code = data['error'].get('code') if isinstance(data['error'], dict) else None
        if code in ('content_filter', 'content_policy_violation'):
            raise ContentBlockedError('CONTENT_FILTER', provider)
        raise ModelRequestError(f'{provider} returned an error response')
    if provider == 'anthropic':
        if data.get('stop_reason') == 'refusal':
            raise ContentBlockedError('REFUSAL', provider)
        if data.get('stop_reason') != 'end_turn':
            raise ValueError('Provider returned incomplete output')
        blocks = data.get('content')
        if not isinstance(blocks, list) or any(not isinstance(item, dict) for item in blocks):
            raise ValueError('Provider returned malformed content')
        texts = [item.get('text') for item in blocks if item.get('type') == 'text']
        if any(not isinstance(text, str) for text in texts):
            raise ValueError('Provider returned malformed content')
        text = ''.join(texts)
    else:
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError('Provider returned malformed choices')
        choice = choices[0]
        message = choice.get('message') or {}
        if not isinstance(message, dict):
            raise ValueError('Provider returned malformed message')
        if choice.get('finish_reason') == 'content_filter' or message.get('refusal'):
            raise ContentBlockedError('REFUSAL' if message.get('refusal') else 'CONTENT_FILTER', provider)
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Provider returned incomplete output')
        text = message.get('content')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Provider returned empty output')
    try:
        result = json.loads(text)
    except ValueError:
        raise ValueError('Provider returned invalid JSON') from None
    if not isinstance(result, dict) or not isinstance(result.get('translations'), list):
        raise ValueError('Provider returned invalid translations envelope')
    return result['translations']


def usage_details(provider, data):
    usage = data.get('usage') or {}
    names = ('input_tokens', 'output_tokens') if provider == 'anthropic' else ('prompt_tokens', 'completion_tokens')
    if not isinstance(usage, dict):
        usage = {}
    def count(name):
        value = usage.get(name)
        return value if type(value) is int and value >= 0 else None
    return {'input_tokens': count(names[0]), 'output_tokens': count(names[1]),
            'cache_read_input_tokens': count('cache_read_input_tokens'),
            'cache_creation_input_tokens': count('cache_creation_input_tokens')}


async def request_translation(provider, key, model, parts, schema, check, attempt, retry_delay):
    if os.getenv('PAID_LLM_ENABLED', 'false').lower() != 'true':
        raise RuntimeError('Paid LLM calls are disabled (PAID_LLM_ENABLED=false)')
    if not key:
        raise RuntimeError(f'{provider} API key is not configured')
    payload = request_payload(provider, model, parts, schema)
    check()
    trace = await disk_call(begin_call, model, attempt or 1, payload, key, provider=provider)
    started = time.monotonic()
    response = None
    emit('request_started', provider=provider, model=model, request_attempt=attempt or 1,
         trace_id=trace.name if trace else None, http_timeout_seconds=120)
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            async with asyncio.timeout(125):
                task = asyncio.create_task(client.post(PROVIDERS[provider]['url'],
                    headers=provider_headers(provider, key), json=payload))
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=.25)
                        check()
                    response = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        status = response.status_code
        identifiers = {name: value for name in ('x-request-id', 'request-id', 'retry-after')
                       if (value := response.headers.get(name)) and key not in value
                       and re.fullmatch(r'[A-Za-z0-9_.:/;, =+-]{1,200}', value)}
        emit('http_headers', provider=provider, model=model, status=status,
             response_identifiers=identifiers,
             category='remote_rate_limit' if status == 429 else 'remote_http_error' if status >= 500 else
             'authentication_or_permission_rejected' if status in (401, 403) else
             'http_request_rejected' if status >= 400 else 'http_response_received')
        if status == 429 or status >= 500:
            raise RetryableTranscriptionError(f'{provider} HTTP {status}', retry_delay(response, 0), shared_cooldown=status == 429)
        if status >= 300:
            # Classify explicit structured policy codes only, not message substrings.
            try:
                data = response.json()
            except ValueError:
                data = {}
            if isinstance(data, dict) and isinstance(data.get('error'), dict) and data['error'].get('code') in ('content_filter', 'content_policy_violation'):
                raise ContentBlockedError('CONTENT_FILTER', provider)
            if status in (401, 403):
                raise RuntimeError(f'{provider} authentication or permission rejected (HTTP {status})')
            raise ModelRequestError(f'{provider} request failed (HTTP {status})')
        try:
            data = response.json()
        except ValueError:
            raise ValueError('Provider returned invalid response JSON') from None
        if isinstance(data, dict):
            emit('token_usage', provider=provider, model=model, **usage_details(provider, data))
        result = parse_response(provider, data)
        emit('request_parsed', provider=provider, model=model, elapsed_seconds=round(time.monotonic() - started, 3))
        return result
    except httpx.TransportError as error:
        emit('request_failed', provider=provider, model=model, **exception_details(error))
        raise RetryableTranscriptionError(f'{provider} transport failure') from None
    except Exception as error:
        emit('request_failed', provider=provider, model=model, **exception_details(error))
        raise
    finally:
        await disk_call(finish_call, trace, key, provider=provider, model=model,
                        status=response.status_code if response is not None else None,
                        body=response.text if response is not None else None)
