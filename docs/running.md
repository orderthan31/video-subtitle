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

`ORPHAN_GRACE_HOURS`는 메타데이터가 없거나 손상된 작업 폴더의 정리 유예 시간이며 기본 2시간이다.
유효한 작업의 결과 보관은 별도로 `RESULT_TTL_HOURS`(기본 24시간)를 따른다.

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
## Worker 종료 정책

Worker는 SIGTERM, SIGINT 및 Windows SIGBREAK를 받으면 새 작업을 시작하지 않는다. 현재 작업의 FFmpeg 자식 프로세스를 종료하고 종료를 기다리며, 진행 중 Gemini 요청은 로컬 요청 태스크를 취소·회수한다. 이미 원격 서비스가 수행한 처리나 과금까지 취소됨을 보장하지는 않는다.

처리 중이던 작업은 `FAILED`, `metadata.interrupted=true`, `Worker shutting down; upload again`을 기록한 뒤 미디어를 정리한다. 아직 시작하지 않은 `QUEUED` 작업은 입력 파일과 함께 보존한다. 사용자가 취소 요청도 보낸 작업은 `CANCELLED`가 우선한다. 파일 정리가 실패하면 다음 GC에서 재시도한다.

Compose의 Worker 종료 유예는 60초다. 파일시스템 응답 정지나 강제 종료(SIGKILL, Windows 강제 프로세스 종료)는 정상 정리를 보장하지 못하므로 다음 Worker 실행의 복구 GC가 필요하다. 실제 Docker 환경의 종료 테스트는 아직 수행하지 않았다.
## Gemini 재시도

연결 오류·읽기 타임아웃 등의 전송 오류와 HTTP 429/500/502/503/504는 최초 요청을 포함해 최대 3회 시도한다. 기본 재시도 대기는 1초·2초이며 서버 `Retry-After`가 더 길면 그 값을 따른다. 60초를 넘는 대기 요구는 기다림을 잘라 조기에 요청하지 않고 실패 처리한다. 재시도 대기 중에도 작업 취소와 Worker 종료를 확인한다.

인증 오류나 정상 HTTP 응답의 잘못된·불완전한 생성 결과는 재시도하지 않는다. 응답을 받지 못했어도 원격 생성이 이미 처리됐을 수 있으므로 재시도로 중복 처리·과금이 발생할 수 있다. 전송 오류 상세에는 민감 정보가 있을 수 있어 작업 오류에는 일반화된 메시지만 기록한다.
## 오디오 작업 공간 예약

신규 업로드는 원본 크기의 4배를 초기 예약한다. Worker가 영상 길이를 분석하면 `4 × 원본 바이트 + 2 × ceil(재생 초 × 16000) × 2 + 1MiB`로 예약을 확장한다. 두 PCM 사본(16kHz·모노·16비트)과 헤더·메타데이터 여유를 더한 값이며, 이미 더 큰 예약이 있으면 줄이지 않는다.

API와 Worker는 접수 잠금을 공유한다. 전체 실제 사용량에 모든 활성 작업의 미사용 예약을 더해 서비스 할당량과 최소 디스크 여유 공간을 검사한다. 실패하면 PCM 추출을 시작하지 않는다. 예약은 `metadata.reserved_bytes`, 분석 길이는 `metadata.source_duration`에 저장되며 종료 작업은 예약 합계에서 제외한다. 남은 파일은 실제 사용량에 계속 포함된다.

이는 출력 크기까지 정확히 예측하는 디스크 하드 제한이 아니다. FFmpeg 출력 증가와 파일시스템 외부 사용량 변화는 기존 주기 검사로 감시하므로 검사 사이 초과 가능성은 남아 있다.
## 결과 검증 비용

Worker는 출력 메타데이터뿐 아니라 FFmpeg 전체 디코딩을 통과한 뒤에만 작업을 완료한다. 영상·오디오를 파일에 다시 쓰지 않고 null 출력으로 읽으며 `-xerror`로 디코딩 오류를 실패 처리한다. 긴 영상에서는 추가 CPU 시간과 디스크 읽기가 발생한다. 이 단계에도 작업 취소·종료·용량 점검이 적용된다.

원본의 직각 회전 메타데이터를 반영한 해상도, HEVC/hvc1/yuv420p·AAC 형식, 재생 시간 및 자막 시간 범위를 검사한다. 이는 번역 정확도·시각적 품질·읽기 속도·정밀 프레임 타이밍이나 모든 플레이어 호환성을 보장하는 검사가 아니다.
## 인코더와 작업 코덱

작업의 `video_codec`이 출력 코덱을 결정한다. `VIDEO_ENCODER=hevc_nvenc` 또는 `h264_nvenc`는 GPU 선호 설정으로 해석하며 실제 작업에 따라 해당 NVENC 인코더를 선택한다. `libx265` 또는 `libx264`를 지정하면 작업 코덱에 맞는 소프트웨어 인코더를 선택한다. GPU 사전 검사 실패 시 소프트웨어 전환은 계속 `ALLOW_SOFTWARE_ENCODER_FALLBACK=true`일 때만 허용한다.

H.264 실제 짧은 파이프라인 재현: `python scripts/smoke-media.py --run-live --audio data/stt-smoke.wav --video-codec h264`. 이 명령은 Gemini를 호출한다. AI 없이 인코딩만 시험하려면 `scripts/smoke-output-validation.py`에 입력 영상·자막과 `--video-codec h264`를 지정한다.
