# 설치 및 실행

## Windows 로컬

프로젝트 루트에서 실행한다. Python 3.12 이상과 Node.js가 필요하다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e packages/shared -e apps/api -e workers/media httpx
cd apps/web
npm ci
cd ../..
.\scripts\setup-ffmpeg.ps1
```

루트 `.env`에 `GEMINI_API_KEY`를 설정한다. 공유용 `.env.example`에는 실제 키를 넣지 않는다.
전사는 `GEMINI_TRANSCRIPTION_MODEL=gemini-3.5-transcribe`, 번역은
`GEMINI_TRANSLATION_MODEL=gemini-3.8-flash`를 사용한다.

각각 별도 터미널에서 실행한다.

```powershell
.\scripts\run-api.ps1
.\scripts\run-worker.ps1
.\scripts\run-web.ps1
```

웹 주소는 http://127.0.0.1:5173 이다. 포트가 사용 중이면 해당 실행 스크립트에
`-Port`를 지정할 수 있다. API 포트 변경 시 Vite proxy 대상도 함께 조정해야 한다.

GPU 사용이 불가능한 개발 환경에서는 `.env`에
`ALLOW_SOFTWARE_ENCODER_FALLBACK=true`를 설정해 소프트웨어 HEVC 전환을 허용한다.
시스템 드라이버를 자동으로 변경하지 않는다.

여러 Worker를 띄울 때 `MAX_ENCODING_JOBS`는 모든 Worker에서 같은 값으로 설정한다.
기본값 1은 인코더 검사와 실제 출력이 동시에 하나만 실행되게 한다. 슬롯을 기다리는 작업도 취소할 수 있다.

## Docker Compose

현재 작업 환경에는 Docker가 없어 아래 구성은 파일 형식 검사만 완료했다.
실제 이미지 빌드, 컨테이너 기동 및 GPU 기동 검증은 남아 있다.

Docker Engine/Compose를 준비하고 루트 `.env`에 키를 설정한 뒤 실행한다.

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 worker
```

웹 주소는 http://127.0.0.1:8080 이다. API 8000 포트를 사용하는 로컬 서버와 동시에 실행하지 않는다.
영상은 `jobs` named volume에 보관한다. 기본 구성은 GPU 접근을 요청하지 않으며,
인코더 검사 실패 시 설정에 따라 소프트웨어로 전환한다.

NVIDIA Container Toolkit 및 호환 드라이버가 준비된 호스트에서는 다음 구성을 사용한다.
GPU 구성은 fallback을 끄므로 GPU 실행 실패를 명확히 확인할 수 있다.

```sh
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

일반 종료는 `docker compose down`이다. `-v`는 영상 저장 볼륨까지 삭제하므로 보관할 결과가 있을 때 사용하지 않는다.
키는 Worker 환경에만 전달하고 빌드 컨텍스트에서는 `.env`와 실행 데이터를 제외한다.

GPU 설정 근거: [Docker 공식 GPU Compose 문서](https://docs.docker.com/compose/how-tos/gpu-support/).
