import {createContext, useContext, useEffect, useState, type ReactNode} from 'react';
import {Captions, LogIn, LogOut, RefreshCw} from 'lucide-react';
import {ApiError, request} from './api';
import {sessionEvents, setCsrfToken} from './auth-session';

type Session = {enabled: boolean; user: {id: string; username: string} | null; csrf_token?: string};
const AuthContext = createContext<{session: Session; logout: () => void; busy: boolean} | null>(null);

export function AccountMenu() {
  const auth = useContext(AuthContext);
  if (!auth?.session.user) return null;
  return <div className="account-menu"><span>{auth.session.user.username}</span>
    <button className="icon" title="로그아웃" aria-label="로그아웃" disabled={auth.busy} onClick={auth.logout}><LogOut size={18}/></button></div>;
}

export function AuthGate({children}: {children: ReactNode}) {
  const [session, setSession] = useState<Session | null>(null);
  const [username, setUsername] = useState(''), [password, setPassword] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [attempt, setAttempt] = useState(0);
  function accept(value: Session) {
    setCsrfToken(value.csrf_token);
    setSession(value);
  }
  useEffect(() => {
    const abort = new AbortController();
    setError('');
    request<Session>('/auth/session', {signal: abort.signal}).then(value => {
      if (!abort.signal.aborted) accept(value);
    }).catch(e => {
      if (!abort.signal.aborted) setError(e instanceof Error ? e.message : '서버에 연결할 수 없습니다.');
    });
    const expired = () => {
      setSession({enabled: true, user: null});
      setPassword('');
      setError('세션이 만료되었습니다. 다시 로그인하세요.');
    };
    sessionEvents.addEventListener('expired', expired);
    return () => {abort.abort(); sessionEvents.removeEventListener('expired', expired);};
  }, [attempt]);

  async function login() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const result = await request<Session>('/auth/login', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password})});
      setPassword(''); accept(result);
    } catch (e) {
      setPassword(''); setError(e instanceof Error ? e.message : '로그인에 실패했습니다.');
    } finally {setBusy(false);}
  }
  async function logout() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await request('/auth/logout', {method: 'POST'});
      accept({enabled: true, user: null});
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) accept({enabled: true, user: null});
      else setError(e instanceof Error ? e.message : '로그아웃에 실패했습니다.');
    } finally {setBusy(false);}
  }

  if (session && (!session.enabled || session.user)) {
    return <AuthContext.Provider value={{session, logout: () => void logout(), busy}}>
      {error && <div className="alert" role="alert">{error}</div>}{children}
    </AuthContext.Provider>;
  }
  return <><header><a className="brand" href="/"><Captions size={27}/><span>장면</span></a></header>
    <main className="auth-page"><h1>로그인</h1>
      {error && <div className="alert" role="alert">{error}</div>}
      {!session ? error ? <button className="primary" onClick={() => setAttempt(n => n + 1)}><RefreshCw size={18}/>다시 연결</button>
        : <p role="status">연결 중...</p>
        : <form onSubmit={e => {e.preventDefault(); void login();}}>
          <label>사용자 이름<input autoFocus autoComplete="username" name="username" required maxLength={64} value={username}
            disabled={busy} onChange={e => setUsername(e.target.value)}/></label>
          <label>비밀번호<input type="password" autoComplete="current-password" name="password" required maxLength={256} value={password}
            disabled={busy} onChange={e => setPassword(e.target.value)}/></label>
          <button className="primary" disabled={busy}><LogIn size={18}/>{busy ? '로그인 중...' : '로그인'}</button>
        </form>}
    </main></>;
}
