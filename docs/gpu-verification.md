# 로컬 GPU 검증

2026-09-11, NVIDIA GeForce RTX 5070 Ti / 드라이버 591.86에서 확인했다.

## 호환성

- 기존 FFmpeg 9.0.1 배포 빌드는 NVENC 13.1을 요구하여 현재 드라이버에서 실행되지 않았다.
- [Gyan FFmpeg 8.0.1 배포](https://github.com/GyanD/codexffmpeg/releases/tag/8.0.1)의 `ffmpeg-8.0.1-essentials_build.zip`을 별도로 설치했다. 배포자 안내는 [빌드 페이지](https://www.gyan.dev/ffmpeg/builds/)에서 확인할 수 있다.
- ZIP 크기: 106,259,850바이트. GitHub release API의 SHA-256과 다운로드 파일을 비교했다: `e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673`.
- 128x128 사전 검사는 실제 GPU에서 최소 프레임 크기 오류가 발생했다. 640x360/30fps로 바꿔 성공했고 회귀 테스트를 추가했다.
- 기존 FFmpeg와 드라이버는 제거하거나 변경하지 않았다. 로컬 `.env`의 `FFMPEG_PATH`/`FFPROBE_PATH`로 8.0.1을 명시적으로 선택했다. 이 구버전 선택은 현재 장비의 호환성 검증용이며, 공개 배포용 최신 보안 패치 적합성을 검증한 것은 아니다.

## 실제 파이프라인

`VIDEO_ENCODER=hevc_nvenc`, `ALLOW_SOFTWARE_ENCODER_FALLBACK=false`에서 실행했다.

```powershell
python scripts/smoke-media.py --run-live --audio data/stt-smoke.wav
```

- 영어 합성 음성 약 7.17초를 사용하여 실제 Gemini 전사·한국어 번역 요청과 자막 번인 GPU 인코딩을 실행했다.
- Job `9fd8f4baeed24fbbbbd2e082baf65f1d`: `COMPLETED`, `encoder=hevc_nvenc`, 오류 없음.
- 결과: 1,070,866바이트, 7.198005초, 640x360, 30fps, HEVC `hvc1`, AAC `mp4a`.
- SRT 3개 구간 생성. 2초 프레임에서 한글 자막 번인을 눈으로 확인했다.
- 완료 후 작업 폴더에는 `output`과 `job.json`만 남았으며 `input`/`work`가 제거됐다.
- 보고서: `data/live-smoke/43f644204cd6498c85d36aa22e125ee3/report.json`.
- 프레임: `data/live-smoke/43f644204cd6498c85d36aa22e125ee3/preview.png`.

생성 미디어·도구·로컬 환경설정은 Git에 포함하지 않는다. 위 결과는 짧은 합성 영상 검증이며, 30~60분 실제 대용량 영상의 품질·압축률·자막 싱크·모바일 재생이나 Docker GPU 실행을 증명하지 않는다.
