# 계정 및 작업 소유권

## 현재 범위

선택적 로컬 계정 인증 API와 웹 로그인/로그아웃을 구현했다. 초기 세션 확인을 마친 뒤 작업실을 표시하고, 세션이 폐기되거나 만료되면 작업실을 해제하여 업로드 요청을 중단하고 로그인 화면으로 돌아간다. 비밀번호와 CSRF 토큰은 브라우저 영속 저장소에 저장하지 않는다. 외부 공개 서버로 사용하기 전에 HTTPS 및 프록시 설정 검증이 필요하다. 공개 회원가입은 없으며 관리자가 계정을 발급한다.

실제 API 키와 비밀번호는 `.env.example`, Git, 명령행 인자에 넣지 않는다. `.env`는 Git에서 제외한다.

## 계정 관리

프로젝트 루트에서 실행한다. 비밀번호는 화면에 표시하지 않는 대화형 입력으로 두 번 확인한다. 사용자 이름은 영문/숫자로 시작하는 3~64자의 영문, 숫자, 점, 밑줄, 하이픈이며 대소문자를 구분하지 않는다. 비밀번호는 12~256자이고 임의로 잘라내거나 공백을 제거하지 않는다.

```powershell
python scripts/manage-account.py create alice
python scripts/manage-account.py reset-password alice
```

명령과 API는 동일한 `VIDEO_STORAGE_ROOT` 및 선택적 `AUTH_DATABASE_PATH`를 사용해야 한다. 기본 SQLite 파일은 `<VIDEO_STORAGE_ROOT>/.auth/accounts.sqlite3`이다. 별도 경로는 명령의 `--database`로 지정할 수 있다. 비밀번호 재설정은 해당 계정의 모든 기존 세션을 폐기한다. 계정 DB는 운영자만 읽을 수 있도록 OS 권한을 제한하고 백업도 동일하게 보호한다. 커스텀 경로를 Git 추적 디렉터리에 두지 않는다.

## 설정

- `AUTH_ENABLED=false`: 기존 로컬 개발용 익명 모드. 공개 배포용 설정이 아니다.
- `AUTH_ENABLED=true`: 세션이 없는 작업 API 요청은 401. 계정이 없어도 익명으로 우회하지 않는다.
- `AUTH_COOKIE_SECURE=true`: 기본값. HTTPS에서만 `__Host-video-session` 쿠키를 전송한다.
- `AUTH_COOKIE_SECURE=false`: 로컬 HTTP 시험 전용. `video-session` 쿠키를 사용한다.
- `AUTH_SESSION_HOURS=8`: 1~168시간의 절대 세션 수명. 활동으로 연장되지 않는다.
- `WEB_CORS_ORIGINS`: 허용 웹 Origin을 정확히 지정한다. 웹/API는 같은 사이트를 사용하고 로컬에서도 `localhost`와 `127.0.0.1`을 혼용하지 않는다.

설정 변경 후 API를 재시작한다. 위 값의 잘못된 불리언이나 수명은 시작 시 거부한다. Docker Compose도 인증 활성/쿠키/수명과 웹 Origin을 전달한다. 컨테이너 DB는 공유 작업 볼륨의 기본 `.auth/accounts.sqlite3`를 사용한다. 컨테이너 계정은 대화형 터미널에서 `docker compose exec api python scripts/manage-account.py create alice`로 발급한다. 로컬 HTTP Compose 테스트에는 `AUTH_COOKIE_SECURE=false`와 실제 웹 Origin을 설정하고, HTTPS 배포에는 Secure 기본값을 유지한다. 실제 컨테이너 실행 검증은 아직 수행하지 않았다.

## API 계약

| 요청 | 응답 및 조건 |
| --- | --- |
| `GET /api/auth/session` | `enabled`, `user`; 로그인 시 `csrf_token`, `expires_at` 포함 |
| `POST /api/auth/login` | JSON `username`, `password`; 세션 쿠키 및 사용자/CSRF/만료 정보 반환 |
| `POST /api/auth/logout` | 세션과 `X-CSRF-Token` 필요, 204 및 쿠키 제거/서버 세션 폐기 |
| `GET /api/health` | 인증 없는 상태 점검만 제공 |

인증 모드에서 GET/HEAD/OPTIONS 이외의 작업 요청은 세션의 `X-CSRF-Token`도 필요하다. 로그인 요청은 JSON만 허용하며 전달된 Origin을 허용 목록과 대조한다. 브라우저 외 클라이언트의 Origin 없는 JSON 로그인은 허용한다. 모든 API 응답은 `Cache-Control: no-store`를 사용한다. 파일 다운로드는 로그인 쿠키로 인증하고 기존 Range 및 전송 잠금을 유지한다.

웹의 로그인 후 요청에는 `X-Session-Token`도 붙인다. 값은 해당 탭이 로그인/초기 조회에서 확인한 세션의 CSRF 토큰이며 URL이나 영속 저장소에 넣지 않는다. 작업 API와 로그아웃은 이 헤더가 있는 경우 현재 쿠키 세션과 비교하고 불일치 시 401을 반환한다. 다른 탭이 공유 쿠키를 새 계정으로 바꿔도 이전 탭으로 새 계정의 작업 목록을 보내지 않는다. 이전 탭은 재로그인 화면으로 돌아가고 새 계정의 세션 자체를 폐기하지 않는다. 일반 다운로드 링크와 헤더 없는 읽기 클라이언트는 기존 쿠키 인증 및 작업 소유권 검사를 유지한다. 로그인 요청에는 이전 세션 바인딩을 보내지 않는다.

잘못된 자격 증명은 계정 존재 여부와 관계없이 같은 401 메시지다. 연결 주소별 15분에 최대 20회 로그인 시도를 SQLite에 기록하며 초과 시 429와 `Retry-After`를 반환한다. 성공도 횟수에 포함한다. 프록시의 전달 IP 신뢰 설정은 운영자가 제한해야 하며 앱만으로 분산 공격 방어를 제공하지 않는다. scrypt 메모리 사용은 프로세스당 한 번으로 제한하지만 여러 API 프로세스의 총 메모리와 동시 요청 수는 배포 측에서 제한해야 한다.

## 소유권 및 이전 데이터

서버가 업로드 생성 시 `metadata.owner_id`를 세션의 사용자 ID로 기록한다. 클라이언트가 소유자를 선택하지 않는다. 목록, 조회, 업로드 청크/검증/완료, 검토/승인, 취소/삭제, 모든 결과 다운로드는 동일한 소유권 검사를 거친다. 타인 소유 작업 ID는 없는 작업과 같은 404를 반환한다. 업로드 `request_id` 재사용 범위도 사용자별이다.

익명 작업의 소유자는 `null`이다. 인증을 켜면 기존 익명 작업을 누구에게도 자동으로 배정하지 않으며 인증을 꺼도 계정 소유 작업은 익명 사용자에게 공개하지 않는다. 현재 소유권 이전 명령은 없다. 운영 중 `job.json`을 직접 변경하지 않는다. Worker와 GC는 신뢰된 로컬 서비스로 모든 작업을 처리하며 사용자 계정 없이도 정리 정책을 수행한다.

비밀번호는 scrypt N=131072, r=8, p=1 및 개별 무작위 salt로 저장하고 세션 원문은 DB에 저장하지 않는다. 쿠키는 HttpOnly, SameSite=Strict, Path=/로 설정한다. 사용자당 최신 유효 세션 최대 20개를 유지한다. 이 설계는 [OWASP 비밀번호 저장 지침](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)과 [세션 지침](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)을 참고했으며, 외부 보안 감사나 MFA/OIDC 지원 완료를 의미하지 않는다.

## 검증

`python -m unittest discover -s tests -p test_auth.py -q`는 실제 scrypt/SQLite 및 FastAPI TestClient로 비밀번호 검증, 대소문자 중복, 토큰 해시 저장, 만료/로그아웃/비밀번호 재설정 폐기, 로그인 제한, Secure 쿠키, Origin/CSRF, 두 계정 간 API 전체 작업 경로 격리, 사용자별 멱등성, 소유 파일 Range, 익명 전환 격리를 검증한다. 테스트 파일은 짧은 임의 바이트이며 미디어 재생 검증은 아니다.

2026-09-11 전체 Python 165개, 웹 7개 테스트 및 TypeScript/Vite 빌드 통과. 웹 테스트는 쿠키/CSRF 전송, 세션 만료 통지, 지연된 401 응답이 새 로그인을 폐기하지 않는 것을 검사한다.

후속 다중 탭 검증: 변경 전 회귀 테스트에서 Alice 탭의 세션 정보와 Bob의 공유 쿠키 조합으로 Bob 작업 목록이 200 응답에 포함되는 문제를 재현했다. 세션 바인딩 추가 후 목록/업로드 생성/로그아웃은 모두 401, 새 계정 세션과 작업은 유지됨을 확인했다. 전체 Python 175개·웹 7개·빌드 통과. 실제 두 브라우저 탭에서도 Alice 로그인 후 다른 탭에서 Bob으로 로그인하면 Alice 탭만 로그인 화면으로 복귀하고 Bob 탭은 작업실을 유지했다. 기존 소유권 검사는 다른 계정 소유 ID의 다운로드를 계속 차단한다.

별도 테스트 저장소 `data/auth-browser/bf82951e71d3407290929fdc6ef8244c`와 API 8004/웹 5177에서 실제 데스크톱 브라우저 로그인 실패 안내, 성공, 새로고침 유지, 로그아웃, 두 계정 목록 분리, 비밀번호 재설정 뒤 로그인 화면 복귀를 확인했다. 실제 HTTP 클라이언트로 767481바이트 MP4를 로그인/CSRF와 함께 업로드하여 `7e9585df5cb94eb4b1cb8b6528dba47e` 작업을 QUEUED로 만들고, 브라우저 작업 취소 후 저장된 CANCELLED 상태를 확인했다. 테스트 계정은 별도 저장소에만 생성했다. 이 시험은 Gemini/인코딩을 실행하지 않았으며 인증 상태의 브라우저 파일 선택/업로드와 파일 저장 완료, 모바일, 프록시/HTTPS는 아직 검증하지 않았다.
