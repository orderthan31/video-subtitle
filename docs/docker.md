# Docker 설치 및 실행

## 포함되는 도구

클론한 PC에는 Docker Engine + Compose v2 또는 Docker Desktop(Linux 컨테이너 모드)과 Git만 필요하다.
Python 3.12, Python 패키지, FFmpeg/FFprobe, libass 자막 필터, H.264/HEVC/AAC 인코더,
한글·일본어·중국어용 Noto CJK 글꼴은 백엔드 이미지에 설치된다.
프론트는 Node로 빌드한 뒤 Nginx로 제공한다. 호스트에 Python, Node, FFmpeg를 별도 설치할 필요는 없다.
첫 빌드에는 이미지/패키지 다운로드를 위한 인터넷 연결이 필요하다. Gemini 호출은 별도 과금이다.

## 첫 실행

```sh
git clone https://github.com/orderthan31/video-subtitle.git
cd video-subtitle
cp .env.docker.example .env.docker
```

Windows PowerShell에서는 마지막 줄 대신 `Copy-Item .env.docker.example .env.docker`를 사용한다.
이미 설정 파일이 있으면 덮어쓰지 않는다. `.env.docker`의 `GEMINI_API_KEY`에 본인 키를 입력한다.
이 파일은 Git/이미지 빌드에서 제외되고, 키는 실행 시 워커에만 전달된다.
명령은 모두 저장소 루트에서 실행한다.

```sh
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up --build -d --wait
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail=100 worker
```

웹: http://localhost:8080 / API 문서: http://localhost:8000/docs

기본 구성은 GPU 없이 CPU로 인코딩한다. 업로드 후 목록의 처리 시작 버튼을 눌러야 Gemini를 호출한다.
API/Web은 호스트의 127.0.0.1에만 포트를 공개한다. 포트가 사용 중이면 `.env.docker`에서
`DOCKER_WEB_PORT`, `DOCKER_API_PORT`를 바꾸고 웹 Origin도 새 포트에 맞춘다. 다른 서버를 종료할 필요는 없다.
Docker 저장소는 별도 named volume이므로 기존 Windows `data/user-preview/jobs`를 자동으로 가져오지 않는다.

기존 호스트 작업 폴더를 그대로 연결하려면 `VIDEO_HOST_JOBS_DIR`에 해당 폴더의 절대 경로를
설정하고 `-f docker-compose.host-data.yml`을 추가한다. 없는 폴더는 자동 생성하지 않는다.
VAD 모델을 포함하려면 `-f docker-compose.vad.yml`도 함께 사용한다.
**Windows 네이티브 API/워커와 컨테이너가 같은 작업 폴더에 동시에 쓰지 않도록 반드시
기존 프로세스를 중지한 뒤 전환한다.** OS가 다른 파일 잠금의 상호 운용을 가정하지 않는다.
빌드는 기존 서비스를 유지한 채 수행할 수 있지만, 컨테이너 시작은 전환 시점에 한다.
먼저 데이터 백업을 확보하고, 전환 후 기존 작업 목록·결과 다운로드·새 업로드를 검증한다.

기본 디스크 여유 공간 요구량은 50GiB, 서비스 저장 한도는 300GiB다. Docker Desktop의 디스크 이미지
용량도 충분해야 한다. 부족하면 업로드가 507로 거절될 수 있으며 `.env.docker`에서 한도를 조정할 수 있다.
원본·음성·중간 자막도 보관하므로 결과 영상 크기보다 더 많은 공간이 필요하다.

## NVIDIA GPU

호스트에 NVENC 지원 GPU와 호환 드라이버가 필요하다. Linux는 NVIDIA Container Toolkit,
Windows는 Docker Desktop WSL2 GPU 지원 환경이 필요하다. GPU 드라이버는 이미지가 설치하지 않는다.
[Docker GPU 안내](https://docs.docker.com/compose/how-tos/gpu-support/)를 참고한다.

```sh
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.gpu.yml up --build -d --wait
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.gpu.yml exec -T worker python scripts/check-container.py --gpu
```

GPU 구성은 `compute,video,utility` 기능을 전달하고 CPU fallback을 끈다. 두 번째 명령은
합성 영상 1초를 실제 NVENC로 인코딩/디코딩하며, 사용자 영상과 Gemini를 사용하지 않는다.
일반 이미지는 GPU 없는 빌드 머신에서도 만들 수 있도록 CPU로 자체 검증한다.

## 검증과 운영

이미지 빌드 마지막 단계에서 Python import, FFmpeg/FFprobe, 글꼴, 자막 번인, HEVC/AAC 인코딩,
디코딩을 검사한다. 실제 Gemini 번역 품질이나 호스트의 GPU 호환성을 검증하는 것은 아니다.
실행 중에도 `docker compose --env-file .env.docker exec -T worker python scripts/check-container.py`로 재검사할 수 있다.

완료·실패·취소만으로 파일을 삭제하지 않는다. 수동 삭제와 명시적 기간 만료 배치만 삭제한다.
기간/저장소를 확인한 다음에만 아래 정리 명령을 실행한다. 크론은 자동 등록하지 않는다.

```sh
docker compose --env-file .env.docker exec -T worker python -m media_worker.worker --collect-only
```

`docker compose --env-file .env.docker down`은 컨테이너만 종료한다.
**`down -v`는 원본·자막·결과·계정 DB가 들어 있는 볼륨까지 삭제하므로 사용에 주의한다.**
Compose 프로젝트 이름을 바꾸면 다른 볼륨을 사용한다. 백업과 데이터 이전은 별도로 수행해야 한다.

기본값은 로컬 익명 HTTP 시험용이다. 외부 공개 전에는 HTTPS와 `AUTH_ENABLED=true`,
`AUTH_COOKIE_SECURE=true`, 실제 `WEB_CORS_ORIGINS`를 설정하고 관리자 계정을 만든다.
계정 생성: `docker compose --env-file .env.docker exec api python scripts/manage-account.py create alice`.
상세 내용은 [인증 안내](authentication.md)를 참고한다. `docker compose config`는 키를 출력할 수 있으므로
공유 로그에서는 `config --quiet`만 사용한다.

## 검증 상태

2026-09-12 개발 PC에 Docker Desktop 4.90.0 (CLI 29.7.2)과 WSL 2.7.13을 설치했다.
설치 로그에서 WSL2/가상화 기능 활성화 후 Windows 재부팅이 필요하다고 확인됐다.
아직 재부팅·이미지 빌드·컨테이너 전환은 하지 않았다. 기존 Python/Node 서버와
Tailscale 연결은 유지하며, 데이터도 기존 폴더에 그대로 보관 중이다.
저장소에는 Docker CPU 빌드·기동 검증용 GitHub Actions를 포함한다. GPU 검증은 GPU 호스트에서 별도 실행한다.
