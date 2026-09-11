# 개발 현황

기준: 2026-09-11. 전체 태스크 완료 목표는 진행 중이다.

## 구현 및 검증

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

현재 테스트는 실제 FFmpeg 실행이나 유료 Gemini 호출 성공을 증명하지 않는다.
