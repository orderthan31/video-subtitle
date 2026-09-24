import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.auth_routes import require_session
from app.core.config import settings
from video_service.llm_settings import LLMSettingsStore, SettingsConflict
from video_service.llm_providers import PROVIDERS, provider_headers, valid_model


def authorize(request: Request):
    session = require_session(request) if request.app.state.auth.store else None
    request.state.settings_owner = session['user']['id'] if session else None
    origin = request.headers.get('origin')
    allowed = [*request.app.state.cors_origins, str(request.base_url).rstrip('/')]
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in allowed):
        raise HTTPException(403, '허용되지 않은 설정 요청입니다.')


router = APIRouter(prefix='/api/settings', dependencies=[Depends(authorize)])


@router.get('/llm')
def get_settings(request: Request):
    try:
        return LLMSettingsStore(settings.storage_root).public(request.state.settings_owner)
    except Exception:
        raise HTTPException(503, '저장된 설정을 읽지 못했습니다. 서버 설정 저장소를 확인하세요.') from None


@router.put('/llm')
async def save_settings(request: Request):
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, 'JSON 요청이 필요합니다.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, '설정 요청이 너무 큽니다.')
    try:
        payload = json.loads(raw)
        return LLMSettingsStore(settings.storage_root).update(request.state.settings_owner, payload)
    except SettingsConflict:
        raise HTTPException(409, '설정이 변경되었습니다. 새로 불러온 뒤 저장하세요.') from None
    except (ValueError, TypeError):
        raise HTTPException(422, '모델명, 폴백 설정 또는 API 키 형식을 확인하세요.') from None
    except Exception:
        raise HTTPException(503, '설정을 저장하지 못했습니다.') from None


@router.get('/llm/models')
async def list_models(request: Request, provider: str = 'gemini'):
    if provider not in PROVIDERS:
        raise HTTPException(422, '지원하지 않는 공급자입니다.')
    try:
        key = LLMSettingsStore(settings.storage_root).resolve(request.state.settings_owner)['api_keys'][provider]
        if not key:
            raise HTTPException(409, '먼저 해당 공급자의 API 키를 등록하세요.')
        models = []
        token = None
        async with httpx.AsyncClient(timeout=15) as client:
            for _ in range(10):
                params = ({'pageSize': 100, **({'pageToken': token} if token else {})} if provider == 'gemini' else
                          {'limit': 100, **({'after_id': token} if token else {})} if provider == 'anthropic' else {})
                response = await client.get(PROVIDERS[provider]['models_url'],
                    headers=provider_headers(provider, key), params=params)
                if response.status_code in (400, 401, 403):
                    raise HTTPException(422, 'API 키 또는 모델 조회 권한을 확인하세요.')
                response.raise_for_status()
                data = response.json()
                for item in data.get('models' if provider == 'gemini' else 'data', []):
                    name = item.get('name', '').removeprefix('models/') if provider == 'gemini' else item.get('id', '')
                    if provider == 'gemini' and 'generateContent' not in item.get('supportedGenerationMethods', []):
                        continue
                    if provider == 'openrouter' and ('structured_outputs' not in item.get('supported_parameters', []) or
                            'text' not in item.get('architecture', {}).get('output_modalities', [])):
                        continue
                    if valid_model(provider, name):
                        models.append(name)
                token = data.get('nextPageToken') if provider == 'gemini' else data.get('last_id') if provider == 'anthropic' and data.get('has_more') else None
                if not token:
                    break
        return {'models': sorted(set(models))}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, '공급자 모델 목록을 가져오지 못했습니다. 모델명은 직접 입력할 수 있습니다.') from None
