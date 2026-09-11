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
  "quality_profile": "balanced",
  "video_codec": "hevc",
  "subtitle_mode": "burn",
  "resolution": "original"
}
```

`subtitle_mode`는 `burn`(기본 번인) 또는 `soft`(MP4 내장 자막 트랙)다. 기존 작업은 `burn`으로 해석한다. 작업 조회에도 반환하며, 같은 `request_id`에 다른 모드를 보내면 409다. 두 모드 모두 별도 SRT/SMI 결과를 제공한다.

`resolution`은 `original`(기본), `1080p`, `720p`다. 가로 영상은 각각 최대 1920×1080/1280×720, 세로 영상은 반대 크기로 축소하며 작은 영상은 확대하지 않는다. 화면 비율을 유지하고 yuv420p에 맞춰 짝수 픽셀로 출력한다. 같은 `request_id`에서 해상도 변경은 409다. 작업 조회·업로드 재개에 저장된 값을 사용한다.

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
## 업로드 생성 재시도

`POST /api/uploads` JSON의 선택적 `request_id`는 UUIDv4다. 동일 식별자와 동일 파일명·크기·언어·품질로 재요청하면 이미 생성된 작업의 현재 상태·진행률을 반환한다(201). 기존 업로드 진행률은 초기화하지 않으며 추가 용량을 예약하지 않는다. 동일 식별자에 다른 옵션을 보내면 409를 반환한다.

식별자가 없으면 이전 API처럼 매번 새 작업을 생성한다. 식별자는 작업 메타데이터와 함께 영속화되며, 작업이 삭제되거나 TTL로 정리된 뒤에는 보존되지 않는다. 현재 익명 서비스에서는 전역 식별자이며 인증 기능 추가 시 사용자 범위로 분리해야 한다. 요청 식별자는 인증 수단이 아니다.

웹은 생성 응답을 받지 못해 다시 시작 버튼을 눌러도 같은 파일·옵션이면 동일 식별자를 사용한다. 파일 재선택 또는 옵션 변경은 새 요청으로 처리한다. 페이지 새로고침 시 클라이언트 식별자는 유지되지 않으므로 기존 작업 목록에서 재개한다. 이 기능은 생성 응답 유실 이후 중복 생성 방지이며 영상 내용 동일성 검증은 별도의 업로드 재개 SHA-256 절차가 담당한다.
## 번역 SMI

새 작업의 `metadata.result_files`에 `translated.smi`가 추가된다. `GET /api/jobs/{job_id}/results/translated.smi`는 UTF-8 파일을 `text/plain` 첨부파일로 반환하며, 기존 결과와 동일한 다운로드·삭제 경합 보호를 적용한다. 웹은 파일 목록에 있을 때만 SMI 링크를 표시한다.

SMI는 기존 번역 cue를 SAMI 형식으로 직렬화한다. `SYNC Start`는 밀리초 단위이며 종료 시각에는 `&nbsp;`로 자막을 지운다. 근거: [Microsoft SAMI 1.0](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/dnacc/understanding-sami-1.0). 태그처럼 보이는 번역 내용은 이스케이프하고 실제 줄바꿈만 `BR`로 변환한다.

업로드의 `target_language`는 `ko`, `en`, `ja`, `zh`, `es`, `pt-BR`와 같은 2~3자리 소문자 기본 언어 코드와 선택적 하위 태그 형식으로 검증한다. 언어 이름 문장이나 CSS 구문은 생성 전에 422로 거부한다. 플레이어별 문자 인코딩·스타일 차이는 별도 검증 대상이다.
## 출력 코덱 선택

업로드 생성 JSON에 `video_codec`을 `hevc`(기본) 또는 `h264`로 지정한다. 다른 값은 422로 거부한다. 작업 조회에 선택값이 반환되고, 이전 레코드에서 값이 없으면 `hevc`로 읽는다. 동일 `request_id`로 코덱을 변경하면 409다. 업로드 재개에서는 기존 작업 코덱을 그대로 유지한다.

두 코덱 모두 `final.mp4`와 기존 자막 파일을 제공한다. HEVC는 `hvc1`, H.264는 `avc1` MP4 태그와 AAC 오디오·yuv420p 비디오를 사용한다. 품질 프리셋 수치는 공통이지만 코덱 간 동일한 시각적 품질이나 용량을 의미하지 않는다.
