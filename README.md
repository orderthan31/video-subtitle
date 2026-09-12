# 영상 자동 번역 자막 서비스

웹에서 영상을 업로드하면 음성을 전사하고 번역한 뒤, SRT 자막과 자막이 번인된 최종 영상을 생성하는 모노레포입니다.

## 구성

- `apps/web`: React + Vite 웹 UI
- `apps/api`: FastAPI 기반 업로드, Job, 결과 API
- `workers/media`: FFmpeg, STT, 번역, 자막, 인코딩을 담당하는 별도 Worker
- `packages/shared`: API와 Worker가 공유하는 Job 상태, Timeline, Transcript, Storage 유틸리티
- `docs`: 요구사항, 아키텍처, API 문서

## 현재 개발 상태

업로드, Gemini 전사·번역, SRT 생성 및 영상 인코딩까지 구현하고 실제 영상으로 검증 중입니다.
기능과 검증 범위는 [개발 현황](docs/development-status.md)을 참고합니다.

- Resumable chunk upload API
- 파일시스템 기반 Job 저장소
- Job 상태 모델
- 요청 실패 시 chunk 롤백, 동시 변경 방지용 OS 파일 잠금
- 저장소 경로 검증 및 루트 삭제 방지
- 명시적 비발화 라벨만 제거하는 Transcript 후처리 필터
- 무음 제거 경계에서 원본 시작/종료 시간을 구분하는 Timeline Mapping
- SRT 생성
- 직렬 업로드 큐, 수동 처리 시작, 중단 단계 재시도 및 파일 보존
- 전사·번역 3병렬 처리와 구간별 진행률
- 최대 60초 병합 전사와 업로드별 선택형 NVIDIA VAD (기본 끄기): [설정과 주의사항](docs/nvidia-vad.md)

React UI와 독립 Worker, FFmpeg 파이프라인 및 Gemini 어댑터 초안을 추가했습니다. 실제 미디어 E2E 검증과 운영 보완은 진행 중입니다. 단계별 검증 및 남은 작업은 [개발 현황](docs/development-status.md)을 기준으로 확인합니다.

## Docker로 실행

호스트에 FFmpeg/Python/Node를 설치하지 않고 실행할 수 있습니다. Docker와 Git을 준비한 뒤:

```sh
git clone https://github.com/orderthan31/video-subtitle.git
cd video-subtitle
cp .env.docker.example .env.docker
```

Windows에서는 `cp` 대신 `Copy-Item`을 사용할 수 있습니다. `.env.docker`에 본인의
`GEMINI_API_KEY`를 설정한 다음 실행합니다. 기존 설정 파일은 덮어쓰지 마세요.

```sh
docker compose --env-file .env.docker up --build -d --wait
```

웹: http://localhost:8080. 기본은 CPU 인코딩이며 GPU 설정, 포트 변경, 볼륨 보관,
인증 및 검증 범위는 [Docker 실행 가이드](docs/docker.md)에 정리했습니다.
현재 개발 PC도 Docker로 실행하며, CPU/GPU VAD와 NVIDIA 영상 인코딩까지 검증했습니다.
개발 PC의 기존 데이터/5177 포트 재기동은 `scripts/run-docker-preview.ps1`을 사용합니다.

## 로컬 실행

Worker/Web과 Docker Compose를 포함한 전체 실행 방법은 [설치 및 실행](docs/running.md)을 참고합니다.

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

API와 Worker는 루트 `.env`를 읽습니다. 기존 프로세스 환경변수가 우선합니다. `.env.example`은 키가 없는 공유용 예시입니다. 전사와 번역 모두 `gemini-3.8-flash`입니다. 전사는 단어별 시간이 아닌 문장/발화별 시작·종료·원문 JSON을 요청하고 SRT로 변환합니다.

## 필수 외부 도구

- FFmpeg
- FFprobe
- NVIDIA GPU/NVENC 지원 드라이버

Windows에서는 `scripts/setup-ffmpeg.ps1`로 프로젝트 `.tools`에 FFmpeg를 준비할 수 있습니다. Worker는 PATH에 도구가 없으면 이 경로를 찾습니다. `ALLOW_SOFTWARE_ENCODER_FALLBACK=true`이면 GPU 사전 검사 실패 시 libx265로 전환합니다.

기본 실행은 로컬 익명 모드이며 선택적 계정 인증을 지원합니다. 외부 공개 전에는 HTTPS와 인증 설정이 필요합니다. 잠금 파일은 저장소의 `.locks`에 유지되며 프로세스가 종료되면 OS 잠금은 해제됩니다. API와 Worker는 동일한 잠금 규약을 사용합니다. 접수 시 활성 작업의 예약 용량을 합산하고 Worker는 처리 중 실제 사용량을 검사합니다. 예약량은 원본의 4배 추정치이며 상세 제한은 개발 현황 문서에 기록합니다.
