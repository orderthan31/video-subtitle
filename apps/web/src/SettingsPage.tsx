import { useEffect, useState } from 'react';
import { KeyRound, RefreshCw, Save, Settings, Trash2 } from 'lucide-react';
import { request } from './api';
import './settings.css';

const providers = [
  ['gemini', 'Gemini'],
  ['openai', 'OpenAI'],
  ['xai', 'xAI (Grok)'],
  ['openrouter', 'OpenRouter'],
  ['anthropic', 'Anthropic'],
] as const;
type Provider = (typeof providers)[number][0];
type CredentialStatus = { has_api_key: boolean; key_source: 'registered' | 'environment' | 'none' };
type Preferences = CredentialStatus & {
  revision: number;
  transcription_model: string;
  translation_model: string;
  transcription_fallback_model: string;
  translation_fallback_model: string;
  translation_provider: Provider;
  translation_fallback_provider: Provider;
  fallback_on_error: boolean;
  fallback_on_block: boolean;
  credentials: Record<Provider, CredentialStatus>;
};
type ModelField =
  | 'transcription_model'
  | 'transcription_fallback_model'
  | 'translation_model'
  | 'translation_fallback_model';

export const SettingsPage = () => {
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [credentialProvider, setCredentialProvider] = useState<Provider>('gemini');
  const [keys, setKeys] = useState<Partial<Record<Provider, string>>>({});
  const [removals, setRemovals] = useState<Partial<Record<Provider, boolean>>>({});
  const [models, setModels] = useState<Partial<Record<Provider, string[]>>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const credential = preferences?.credentials[credentialProvider];
  const load = async () => {
    setBusy('load');
    setError('');
    setNotice('');
    try {
      setPreferences(await request<Preferences>('/settings/llm'));
      setKeys({});
      setRemovals({});
      setModels({});
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
    const {
      has_api_key: _hasKey,
      key_source: _source,
      credentials: _credentials,
      ...values
    } = preferences;
    const credentialUpdates = Object.fromEntries(
      providers
        .filter(([provider]) => keys[provider]?.trim() || removals[provider])
        .map(([provider]) => [
          provider,
          {
            ...(keys[provider]?.trim() ? { api_key: keys[provider]?.trim() } : {}),
            remove_api_key: !!removals[provider],
          },
        ]),
    );
    try {
      const result = await request<Preferences>('/settings/llm', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...values, credential_updates: credentialUpdates }),
      });
      setPreferences(result);
      setKeys({});
      setRemovals({});
      setModels({});
      setNotice('저장했습니다. 다음 작업·재시도부터 적용됩니다.');
    } catch (e) {
      setKeys({});
      setError(e instanceof Error ? e.message : '설정을 저장하지 못했습니다.');
    } finally {
      setBusy('');
    }
  };
  const loadModels = async () => {
    setBusy('models');
    setError('');
    try {
      const result = await request<{ models: string[] }>(
        `/settings/llm/models?provider=${credentialProvider}`,
      );
      setModels((current) => ({ ...current, [credentialProvider]: result.models }));
      setNotice(`모델 ${result.models.length}개를 불러왔습니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : '모델 조회 실패');
    } finally {
      setBusy('');
    }
  };
  const renderModel = (field: ModelField, label: string, provider: Provider) => (
    <label>
      {label}
      <input
        list={`models-${provider}`}
        value={preferences?.[field] || ''}
        required={!field.includes('fallback')}
        maxLength={200}
        autoComplete="off"
        placeholder={field.includes('fallback') ? '사용 안 함' : '모델 ID'}
        onChange={(event) => {
          if (preferences) {
            setPreferences({ ...preferences, [field]: event.target.value });
          }
        }}
      />
    </label>
  );
  useEffect(() => {
    void load();
  }, []);
  return (
    <main className="video-main settings-page">
      <div className="settings-heading">
        <h1>
          <Settings size={24} />
          AI 공급자 설정
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
          {providers.map(([provider]) => (
            <datalist key={provider} id={`models-${provider}`}>
              {(models[provider] || []).map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          ))}
          <fieldset disabled={!!busy}>
            <legend>
              <KeyRound size={18} />
              API 키
            </legend>
            <label>
              공급자
              <select
                aria-label="공급자"
                value={credentialProvider}
                onChange={(event) => setCredentialProvider(event.target.value as Provider)}
              >
                {providers.map(([provider, label]) => (
                  <option key={provider} value={provider}>
                    {label}
                    {keys[provider] || removals[provider] ? ' · 변경 대기' : ''}
                  </option>
                ))}
              </select>
            </label>
            <p className="key-status">
              {removals[credentialProvider]
                ? '등록 키 삭제 예정'
                : credential?.key_source === 'registered'
                  ? '등록한 키 사용 중'
                  : credential?.key_source === 'environment'
                    ? '서버 환경변수 키 사용 중'
                    : '등록된 키 없음'}
            </p>
            <div className="settings-key-row">
              <label>
                새 API 키
                <input
                  type="password"
                  autoComplete="new-password"
                  value={keys[credentialProvider] || ''}
                  spellCheck={false}
                  placeholder="변경할 때만 입력"
                  maxLength={512}
                  onChange={(event) => {
                    setKeys({ ...keys, [credentialProvider]: event.target.value });
                    setRemovals({ ...removals, [credentialProvider]: false });
                  }}
                />
              </label>
              {credential?.key_source === 'registered' && (
                <button
                  type="button"
                  className="icon"
                  title={
                    removals[credentialProvider] ? '키 삭제 취소' : '등록 키 삭제 · 저장 시 적용'
                  }
                  aria-label="등록 키 삭제"
                  aria-pressed={!!removals[credentialProvider]}
                  onClick={() => {
                    setRemovals({
                      ...removals,
                      [credentialProvider]: !removals[credentialProvider],
                    });
                    setKeys({ ...keys, [credentialProvider]: '' });
                  }}
                >
                  <Trash2 size={18} />
                </button>
              )}
            </div>
            <button
              type="button"
              className="settings-model-refresh"
              disabled={
                !credential?.has_api_key ||
                !!keys[credentialProvider] ||
                !!removals[credentialProvider]
              }
              onClick={() => void loadModels()}
            >
              <RefreshCw size={16} />
              모델 목록 조회
            </button>
          </fieldset>
          <fieldset disabled={!!busy}>
            <legend>전사 · Gemini</legend>
            <div className="settings-model-grid">
              {renderModel('transcription_model', '전사 모델', 'gemini')}
              {renderModel('transcription_fallback_model', '전사 폴백 모델', 'gemini')}
            </div>
          </fieldset>
          <fieldset disabled={!!busy}>
            <legend>번역</legend>
            <div className="settings-model-grid">
              <label>
                기본 공급자
                <select
                  aria-label="기본 공급자"
                  value={preferences.translation_provider}
                  onChange={(event) =>
                    setPreferences({
                      ...preferences,
                      translation_provider: event.target.value as Provider,
                      translation_model: '',
                    })
                  }
                >
                  {providers.map(([provider, label]) => (
                    <option key={provider} value={provider}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {renderModel('translation_model', '번역 모델', preferences.translation_provider)}
              <label>
                폴백 공급자
                <select
                  aria-label="폴백 공급자"
                  value={preferences.translation_fallback_provider}
                  onChange={(event) =>
                    setPreferences({
                      ...preferences,
                      translation_fallback_provider: event.target.value as Provider,
                      translation_fallback_model: '',
                    })
                  }
                >
                  {providers.map(([provider, label]) => (
                    <option key={provider} value={provider}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {renderModel(
                'translation_fallback_model',
                '번역 폴백 모델',
                preferences.translation_fallback_provider,
              )}
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
              요청 오류 시 폴백 사용
            </label>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={preferences.fallback_on_block}
                onChange={(event) =>
                  setPreferences({ ...preferences, fallback_on_block: event.target.checked })
                }
              />
              콘텐츠 차단 시 폴백 사용
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
