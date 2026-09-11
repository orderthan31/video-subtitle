# API 초안

Base URL: `/api`

## Health

```http
GET /api/health
```

## Upload 생성

```http
POST /api/uploads
Content-Type: application/json
```

```json
{
  "filename": "movie.mp4",
  "size": 1073741824,
  "source_language": "auto",
  "target_language": "ko",
  "quality_profile": "balanced"
}
```

응답:

```json
{
  "job_id": "...",
  "status": "UPLOADING",
  "uploaded_bytes": 0,
  "expected_size": 1073741824
}
```

## Upload 상태

```http
GET /api/uploads/{job_id}
```

현재 업로드 offset을 반환한다.

## Chunk 업로드

```http
PUT /api/uploads/{job_id}/chunks?offset=0
Content-Type: application/octet-stream
```

요청 body는 raw binary chunk다. 서버는 파일 전체를 메모리에 올리지 않고 append한다.

Offset이 맞지 않으면 `409 Conflict`와 서버의 현재 offset을 반환한다.

## Upload 완료

```http
POST /api/uploads/{job_id}/complete
```

파일 크기를 검증하고 Job을 `QUEUED` 상태로 전환한다.

## Job 조회

```http
GET /api/jobs
GET /api/jobs/{job_id}
```

## Job 취소

```http
POST /api/jobs/{job_id}/cancel
```

## Job 삭제

```http
DELETE /api/jobs/{job_id}
```

Job 디렉터리를 서비스 storage root 안에서만 삭제한다.

## 결과 다운로드

```http
GET /api/jobs/{job_id}/results/final.mp4
GET /api/jobs/{job_id}/results/translated.srt
```

결과 파일은 `COMPLETED` 상태 이후 제공된다.
# 구현 보충 사항

- 업로드 도중 저장소 한도 또는 최소 여유 공간에 도달하면 `507`을 반환하고 해당 요청만 롤백한다. 이전에 성공한 업로드 부분은 유지한다.

- 같은 작업을 동시에 변경하면 `409`와 `Retry-After: 1`을 반환한다.
- 업로드 요청이 끊기거나 선언된 크기를 넘으면 해당 요청의 데이터를 롤백한다.
- 재개 offset은 `GET /api/uploads/{job_id}`로 확인한다. 서버는 실제 파일 크기를 기준으로 응답한다.
- 작업 삭제는 종료 상태에서만 가능하며, 진행 중 작업은 먼저 취소해야 한다.
- 잘못된 형식의 Job ID는 `404`로 처리한다.
- Worker는 아직 미구현이므로 업로드 완료 후 `QUEUED`에서 대기한다.
