import { useEffect, useState } from 'react';
import { KeyRound, RefreshCw, Save, Settings, Trash2 } from 'lucide-react';
import { request } from './api';
import './settings.css';

type Preferences = {
  revision: number;
  transcription_model: string;
  translation_model: string;
  transcription_fallback_model: string;
  translation_fallback_model: string;
  fallback_on_error: boolean;
  fallback_on_block: boolean;
  has_api_key: boolean;
  key_source: 'registered' | 'environment' | 'none';
};

export const SettingsPage = () => {
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [key, setKey] = useState('');
  const [removeKey, setRemoveKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const modelFields = [
    ['transcription_model', '전사 모델'],
    ['transcription_fallback_model', '전사 폴백 모델'],
    ['translation_model', '번역 모델'],
    ['translation_fallback_model', '번역 폴백 모델'],
  ] as const;
  const suggestions = [
    ...new Set([...models, ...modelFields.map(([field]) => preferences?.[field] || '')]),
  ].filter(Boolean);
  const load = async () => {
    setBusy('load');
    setError('');
    try {
      setPreferences(await request<Preferences>('/settings/llm'));
      setKey('');
      setRemoveKey(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '설정을 불러오지 못했습니다.');
    } finally {
      setBusy('');
    }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!preferences || busy) {
      return;
    }
    setBusy('save');
    setError('');
    setNotice('');
    const { has_api_key: _hasKey, key_source: _source, ...values } = preferences;
    try {
      const result = await request<Preferences>('/settings/llm', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          ...(key ? { api_key: key.trim() } : {}),
          remove_api_key: removeKey,
        }),
      });
      setPreferences(result);
      setKey('');
      setRemoveKey(false);
      setNotice('저장했습니다. 다음 작업·재시도부터 적용됩니다.');
    } catch (e) {
      setKey('');
      setError(e instanceof Error ? e.message : '설정을 저장하지 못했습니다.');
    } finally {
      setBusy('');
    }
  };
  const loadModels = async () => {
    setBusy('models');
    setError('');
    try {
      const result = await request<{ models: string[] }>('/settings/llm/models');
      setModels(result.models);
      setNotice(`사용 가능한 모델 ${result.models.length}개를 불러왔습니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : '모델 조회 실패');
    } finally {
      setBusy('');
    }
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <main className="video-main settings-page">
      <div className="settings-heading">
        <h1>
          <Settings size={24} />
          설정
        </h1>
        <button
          className="icon"
          title="설정 새로 불러오기"
          aria-label="설정 새로 불러오기"
          disabled={!!busy}
          onClick={() => void load()}
        >
          <RefreshCw size={18} />
        </button>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!preferences ? (
        <p>{error ? '설정을 불러오지 못했습니다.' : '설정을 불러오는 중입니다.'}</p>
      ) : (
        <form onSubmit={(event) => void save(event)}>
          <fieldset disabled={!!busy}>
            <legend>
              <KeyRound size={18} />
              Gemini API 키
            </legend>
            <p className="key-status">
              {removeKey
                ? '등록 키 삭제 예정'
                : preferences.key_source === 'registered'
                  ? '등록한 키 사용 중'
                  : preferences.key_source === 'environment'
                    ? '서버 환경변수 키 사용 중'
                    : '등록된 키 없음'}
            </p>
            <div className="settings-key-row">
              <label>
                새 API 키
                <input
                  type="password"
                  autoComplete="new-password"
                  value={key}
                  spellCheck={false}
                  onChange={(event) => {
                    setKey(event.target.value);
                    setRemoveKey(false);
                  }}
                  placeholder="변경할 때만 입력"
                  maxLength={256}
                />
              </label>
              {preferences.key_source === 'registered' && (
                <button
                  type="button"
                  className="icon"
                  title={removeKey ? '키 삭제 취소' : '등록 키 삭제 · 저장 시 적용'}
                  aria-label="등록 키 삭제"
                  aria-pressed={removeKey}
                  onClick={() => {
                    setRemoveKey(!removeKey);
                    setKey('');
                  }}
                >
                  <Trash2 size={18} />
                </button>
              )}
            </div>
          </fieldset>
          <fieldset disabled={!!busy}>
            <legend>모델</legend>
            <button
              type="button"
              onClick={() => void loadModels()}
              disabled={!preferences.has_api_key}
            >
              <RefreshCw size={16} />
              모델 목록 조회
            </button>
            <datalist id="gemini-models">
              {suggestions.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
            <div className="settings-model-grid">
              {modelFields.map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input
                    list="gemini-models"
                    value={preferences[field]}
                    required={!field.includes('fallback')}
                    pattern="[A-Za-z0-9_.\-]+"
                    maxLength={100}
                    autoComplete="off"
                    placeholder={field.includes('fallback') ? '사용 안 함' : 'Gemini 모델명'}
                    onChange={(event) =>
                      setPreferences({ ...preferences, [field]: event.target.value })
                    }
                  />
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset disabled={!!busy}>
            <legend>폴백 조건</legend>
            <p className="key-status">폴백 요청에도 API 사용료가 발생할 수 있습니다.</p>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={preferences.fallback_on_error}
                onChange={(event) =>
                  setPreferences({ ...preferences, fallback_on_error: event.target.checked })
                }
              />
              요청 오류 시 폴백 모델 사용
            </label>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={preferences.fallback_on_block}
                onChange={(event) =>
                  setPreferences({ ...preferences, fallback_on_block: event.target.checked })
                }
              />
              콘텐츠 차단 시 폴백 모델 사용
            </label>
          </fieldset>
          <div className="settings-actions">
            <button className="primary" type="submit" disabled={!!busy}>
              <Save size={17} />
              {busy === 'save' ? '저장 중' : '설정 저장'}
            </button>
          </div>
        </form>
      )}
    </main>
  );
};
