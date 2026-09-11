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
# 업로드 재개 내용 검증

`POST /api/uploads/{job_id}/verify`

- JSON: `uploaded_bytes`(상태 조회 시 서버 크기), `offset`, `length`(1~4,194,304), `sha256`(소문자 64자리).
- 지정 블록의 서버 파일 SHA-256과 비교한다. 성공 204, 파일 내용 불일치 422, 상태/크기 변경 또는 잠금 경합 409, 범위 초과 400.
- 웹은 재개 및 재시도 시 이미 업로드된 모든 바이트를 블록별로 검증한 후 추가 데이터를 보낸다. 아직 업로드하지 않은 부분은 비교 대상이 아니다.
- 검증 요청은 파일을 수정하지 않는다. 이 절차는 웹 재개 시 실수로 다른 파일을 선택하는 것을 방지하며 인증 또는 악의적 API 호출 방어를 대체하지 않는다.
- 검증은 업로드된 크기에 비례한 디스크 읽기와 왕복 요청을 필요로 한다. 클라이언트 메모리는 블록 크기로 제한한다. 웹 SHA-256은 localhost 또는 HTTPS 보안 컨텍스트에서 실행한다.
# 다운로드와 삭제 경합

결과 응답은 전송 시작 전 작업 상태와 결과 파일을 다시 검사하며, 마지막 응답 블록까지 다운로드 잠금을 유지한다. MP4와 SRT를 포함한 동일 작업의 여러 다운로드는 동시에 가능하다.

다운로드 중 작업 삭제는 HTTP 409와 `Retry-After: 1`을 반환한다. GC는 해당 작업을 건너뛰고 다음 순회에서 TTL을 다시 검사한다. 연결 취소·응답 예외 시 잠금을 해제하며, API 프로세스가 비정상 종료해도 OS 잠금이 풀려 다음 정리에서 잔여 잠금 파일을 회수할 수 있다.

기존 Range 요청(206/416)은 유지한다. 파일 경로만 ASGI 서버에 넘기는 `pathsend` 최적화는 전송보다 잠금이 먼저 풀리지 않도록 사용하지 않는다. 잠금 보호는 개별 HTTP 전송에 적용되며, 다운로드 요청 사이 또는 이후 재생 세션 전체의 TTL 연장을 의미하지 않는다.
## 원문 SRT

새 완료 작업은 `final.mp4`, `translated.srt`, `original.srt`를 `metadata.result_files`에 제공한다. 원문은 필터링된 전사 결과에 원본 영상 시간을 복원하고 자막 줄바꿈을 적용한 UTF-8 SRT이며, 번역 문자열로 덮어쓰지 않는다.

`GET /api/jobs/{job_id}/results/original.srt`는 기존 결과와 동일한 완료 상태 검사·Range·다운로드 잠금 보호를 적용한다. 원문 파일이 없는 이전 작업은 404를 반환하며, 웹은 `result_files`에 있는 경우에만 원문 링크를 표시한다. 작업 삭제 및 TTL 정리는 세 파일 모두에 적용된다.
