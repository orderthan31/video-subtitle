# 아키텍처

## 경계

서비스는 세 개의 실행 단위로 나눈다.

- Web: 사용자의 파일 선택, resumable upload, 진행 상태, 결과 다운로드
- API: Job 생성, chunk 수신, 상태 조회, 취소, 결과 제공
- Media Worker: FFprobe/FFmpeg, 오디오 정제, STT, 번역, 자막 생성, 인코딩, 검증, Cleanup

API는 장시간 미디어 처리나 외부 AI 호출을 직접 실행하지 않는다. API는 Job을 `QUEUED` 상태로 만들고, Worker가 이를 polling해서 처리한다.

## 현재 MVP 저장소 전략

초기 구현은 운영 DB/Queue 대신 파일시스템 기반 Job 저장소를 사용한다.

- Job record: `data/video-jobs/{job_id}/job.json`
- Queue: `status == QUEUED`인 Job을 Worker가 polling
- 장점: 개발과 로컬 E2E 검증이 단순함
- 후속 교체 후보: SQLite/PostgreSQL + Redis/RQ/Celery/Arq

공통 도메인 모델과 저장소 유틸리티는 `packages/shared`에 둔다. API와 Worker가 같은 상태 enum, timeline mapper, transcript filter를 사용해 중복 구현을 피한다.

## 개발 단계

### Phase 1: Foundation

- 모노레포 구조
- 공통 Job/Timeline/Transcript 모델
- Resumable upload API
- 파일시스템 Job repository
- Worker polling shell
- React MVP UI

### Phase 2: Media Pipeline

- FFprobe metadata
- Audio extraction
- 긴 무음 기반 conservative preprocessing
- Timeline Mapping
- SRT generation
- Burn-in + HEVC NVENC encoding
- Validation/Cleanup

### Phase 3: AI Provider

- Gemini STT adapter
- Translation adapter
- 긴 오디오 chunking과 overlap merge
- Transcript post-processing 강화
- 용어집/고유명사 일관성 처리

### Phase 4: Operations

- Queue/DB 도입
- Heartbeat와 stale job 복구
- Storage quota enforcement 강화
- GC scheduler
- 인증, 운영 모니터링 (작업 이력 스냅샷은 결과 TTL과 분리해 구현)

## 보수적 필터링 정책

Audio 단계에서는 명백한 긴 무음과 확실한 비발화 구간만 제거한다. 호흡음/기합/추임새 제거는 STT 후 transcript filter를 함께 사용한다.

실제 대사 삭제는 가장 큰 품질 리스크이므로, 애매한 Segment는 유지하는 것이 기본이다.
