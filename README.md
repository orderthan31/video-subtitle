# 영상 자동 번역 자막 서비스

웹에서 영상을 업로드하면 음성을 전사하고 번역한 뒤, SRT 자막과 자막이 번인된 최종 영상을 생성하는 모노레포입니다.

## 구성

- `apps/web`: React + Vite 웹 UI
- `apps/api`: FastAPI 기반 업로드, Job, 결과 API
- `workers/media`: FFmpeg, STT, 번역, 자막, 인코딩을 담당하는 별도 Worker
- `packages/shared`: API와 Worker가 공유하는 Job 상태, Timeline, Transcript, Storage 유틸리티
- `docs`: 요구사항, 아키텍처, API 문서

## 현재 개발 상태

요구사항 문서와 API 기반을 구현했습니다. 전체 영상 처리 E2E는 아직 구현되지 않았습니다.

- Resumable chunk upload API
- 파일시스템 기반 Job 저장소
- Job 상태 모델
- 요청 실패 시 chunk 롤백, 동시 변경 방지용 OS 파일 잠금
- 저장소 경로 검증 및 루트 삭제 방지
- 명시적 비발화 라벨만 제거하는 Transcript 후처리 필터
- 무음 제거 경계에서 원본 시작/종료 시간을 구분하는 Timeline Mapping
- SRT 생성
- 업로드, 취소, 삭제 API 및 핵심 처리 회귀 테스트 10개

React UI와 독립 Worker, FFmpeg 파이프라인 및 Gemini 어댑터 초안을 추가했습니다. 실제 미디어 E2E 검증과 운영 보완은 진행 중입니다. 단계별 검증 및 남은 작업은 [개발 현황](docs/development-status.md)을 기준으로 확인합니다.

## 로컬 실행

Python 의존성은 가상환경에서 설치하는 것을 권장합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e packages/shared -e apps/api httpx
```

API:

```powershell
.\scripts\run-api.ps1
```

API 문서: http://localhost:8000/docs

테스트:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

API와 Worker는 루트 `.env`를 읽습니다. 기존 프로세스 환경변수가 우선합니다. `.env.example`은 키가 없는 공유용 예시입니다. 전사 모델은 `gemini-3.5-transcribe`, 번역 모델은 `gemini-3.8-flash`입니다.

## 필수 외부 도구

- FFmpeg
- FFprobe
- NVIDIA GPU/NVENC 지원 드라이버

Windows에서는 `scripts/setup-ffmpeg.ps1`로 프로젝트 `.tools`에 FFmpeg를 준비할 수 있습니다. Worker는 PATH에 도구가 없으면 이 경로를 찾습니다. `ALLOW_SOFTWARE_ENCODER_FALLBACK=true`이면 GPU 사전 검사 실패 시 libx265로 전환합니다.

현재 API는 인증이 없는 로컬 개발용입니다. 잠금 파일은 저장소의 `.locks`에 유지되며 프로세스가 종료되면 OS 잠금은 해제됩니다. 향후 Worker도 동일한 잠금 규약을 사용해야 합니다. 저장소 용량 검사는 생성 시점 검사로, 동시 작업의 예약 용량과 미디어 임시 파일까지 보장하지 않습니다.
