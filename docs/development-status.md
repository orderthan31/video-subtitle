# 개발 현황

기준: 2026-09-11. 전체 태스크 완료 목표는 진행 중이다.

## 구현 및 검증

- FFmpeg/FFprobe 9.0.1 프로젝트 도구 설치와 SHA256 검증 완료.
- 7.17초 합성 영상: 실제 전사/번역, HEVC 출력, 한글 번인 프레임 확인, input/work 정리 성공. 최종 MP4 770,553바이트. 실행 보고서: `data/live-smoke/3208d54604204b4281f24baff110f8fd/report.json` (Git 제외).
- GPU는 RTX 5070 Ti, 드라이버 591.86. 설치한 FFmpeg는 NVENC 13.1/드라이버 610 이상을 요구해 GPU 실행 실패. 명시적으로 허용한 libx265 fallback으로 소규모 통합 검증 완료. NVENC 경로는 미검증.
- 회귀 테스트 21개 통과. FFmpeg 실패 로그의 마지막 3KB를 오류에 보존한다.

- 단계별 로컬 Git 커밋 시작: API 기반, Worker, Web을 분리 기록.
- 실제 Gemini 번역 한 문장과 합성 영어 음성 전사 성공. 전사는 `audioTranscriptionConfig.wordTimestamp`로 받은 단어 시간을 문장 단위로 묶는다. 실제 영상 품질/E2E 검증과는 구분한다.

- API 업로드/재개/취소/삭제 및 저장소 보호 회귀 테스트 10개 통과.
- 미디어 프로세스 실행기, 긴 무음 구간 계산, PCM 구간 복사, 시간 매핑, 인코딩 명령 테스트 7개 통과.

## 구현했으나 통합 검증 필요

- 독립 Worker polling, 작업 실행 잠금, 상태 전환, heartbeat.
- FFprobe 분석, 오디오 추출, 긴 무음 제거, 원본 시간 복원.
- Gemini REST 전사/번역 어댑터: 60초 단위 오디오와 겹침 구간, 응답 검증, 재시도, 요청 취소.
- SRT 생성과 HEVC 번인 출력, 스트림/재생 시간 검증.
- 실행 중 취소 요청, 완료/실패/취소 정리, 결과 TTL, 중단 작업 처리.

## 남은 작업

- React 웹 화면 구현과 프로덕션 빌드 완료. API 연결/모바일 기본 레이아웃/필터 확인. 실제 파일 업로드·재개·다운로드 브라우저 검증은 남아 있다.
- FFmpeg/FFprobe 설치 또는 경로 지정 및 NVENC 지원 확인.
- 전사 모델 `gemini-3.5-transcribe`, 번역 모델 `gemini-3.8-flash`로 분리 설정 완료. 루트 `.env` 자동 로딩 추가. 키를 로컬 `.env`에 보관하고 `.env.example`에서는 제거했다. Google 모델 조회 API에서 두 모델 모두 HTTP 200 확인. 실제 전사/번역 생성과 품질 검증은 남아 있다.
- 오디오 단계의 비언어 발성 판별과 보수적 필터링 검증.
- 자막 segmentation, 긴 문장/줄바꿈/중첩 처리, 겹침 STT 병합 품질 검증.
- 인코더 자동 fallback, 동시 인코딩 제한, 단계 내 진행률.
- Worker 예외/잠금 경합/GC 통합 테스트 및 고아 파일 탐색.
- 예약 용량과 임시 파일을 포함한 저장소 한도 관리.
- 환경설정 자동 로딩, Docker Compose, Web 실행 스크립트.
- 30~60분/1~5GB 영상 E2E, 모바일 재생/Seek, 자막 싱크와 정리 검증.
- MVP 후속 범위는 요구사항 문서에 기재된 그대로 유지하며 아직 미구현이다.

## 참고한 공식 문서

- [FFmpeg 필터](https://ffmpeg.org/ffmpeg-filters.html)
- [Gemini 오디오](https://ai.google.dev/gemini-api/docs/audio)
- [Gemini generateContent API](https://ai.google.dev/api/generate-content)
- [전사 전용 설정과 응답](https://ai.google.dev/gemini-api/docs/generate-content/transcribe)

자동 회귀 테스트와 별도로 소규모 실제 Gemini/FFmpeg 통합 실행을 확인했다. 대용량·모바일 재생·GPU E2E는 아직 검증하지 않았다.
