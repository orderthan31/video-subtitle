import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.auth_routes import require_session
from app.core.config import settings
from video_service.llm_settings import LLMSettingsStore, SettingsConflict


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
async def list_models(request: Request):
    try:
        key = LLMSettingsStore(settings.storage_root).resolve(request.state.settings_owner)['api_key']
        if not key:
            raise HTTPException(409, '먼저 Gemini API 키를 등록하세요.')
        models = []
        token = None
        async with httpx.AsyncClient(timeout=15) as client:
            for _ in range(10):
                response = await client.get('https://generativelanguage.googleapis.com/v1beta/models',
                    headers={'x-goog-api-key': key}, params={'pageSize': 100, **({'pageToken': token} if token else {})})
                if response.status_code in (400, 401, 403):
                    raise HTTPException(422, 'API 키 또는 모델 조회 권한을 확인하세요.')
                response.raise_for_status()
                data = response.json()
                for item in data.get('models', []):
                    name = item['name'].removeprefix('models/')
                    if 'generateContent' in item.get('supportedGenerationMethods', []) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name):
                        models.append(name)
                token = data.get('nextPageToken')
                if not token:
                    break
        return {'models': sorted(set(models))}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Gemini 모델 목록을 가져오지 못했습니다. 모델명은 직접 입력할 수 있습니다.') from None
