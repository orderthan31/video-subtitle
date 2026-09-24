"""Allowlisted provider destinations; user settings never supply an API URL."""
import re


PROVIDERS = {
    'gemini': {'label': 'Gemini', 'env': 'GEMINI_API_KEY',
               'models_url': 'https://generativelanguage.googleapis.com/v1beta/models'},
    'openai': {'label': 'OpenAI', 'env': 'OPENAI_API_KEY',
               'models_url': 'https://api.openai.com/v1/models',
               'url': 'https://api.openai.com/v1/chat/completions'},
    'xai': {'label': 'xAI (Grok)', 'env': 'XAI_API_KEY',
            'models_url': 'https://api.x.ai/v1/models',
            'url': 'https://api.x.ai/v1/chat/completions'},
    'openrouter': {'label': 'OpenRouter', 'env': 'OPENROUTER_API_KEY',
                   'models_url': 'https://openrouter.ai/api/v1/models',
                   'url': 'https://openrouter.ai/api/v1/chat/completions'},
    'anthropic': {'label': 'Anthropic', 'env': 'ANTHROPIC_API_KEY',
                  'models_url': 'https://api.anthropic.com/v1/models',
                  'url': 'https://api.anthropic.com/v1/messages'},
}


def valid_model(provider, model):
    pattern = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}'
    if provider == 'openrouter':
        pattern = r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?(?::[A-Za-z0-9_-]+)?'
    return isinstance(model, str) and len(model) <= 200 and '..' not in model and bool(re.fullmatch(pattern, model))


def provider_headers(provider, key):
    if provider == 'gemini':
        return {'x-goog-api-key': key}
    if provider == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    return {'Authorization': f'Bearer {key}'}
