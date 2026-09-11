# 대용량 업로드 검증

2026-09-11. 외부 API 호출과 Worker 없이 실제 로컬 HTTP 업로드 경로를 검증했다. 이 검증은 전사·번역·영상 출력까지의 전체 E2E 완료를 뜻하지 않는다.

## 실행 조건

Windows에서 `scripts/smoke-large-upload.py`가 자체 FastAPI 프로세스를 루프백의 임시 포트에 실행한다. 저장소는 실행별 UUID 폴더이며 기존 작업실 데이터는 사용하지 않는다. 테스트 서버의 서비스 할당량은 32GiB, 최소 여유 공간은 1GiB이고 기존 업로드 예약 검사는 그대로 적용된다. 인증 비활성 테스트이며 계정 인증 회귀와 별개다.

영상은 FFmpeg로 생성한 1초 패턴/440Hz 테스트 톤을 stream copy로 반복한 MP4다. 30분 또는 60분 재생 시간은 FFprobe로 확인하고, 목표 파일 크기까지 MP4의 64비트 크기 `free` 박스를 덧붙인다. 패딩은 실제 바이트 쓰기와 HTTP 전송 대상이며 sparse seek로 생략하지 않는다. 따라서 아래 파일 크기가 모두 고유 영상·음성 데이터라는 의미는 아니다.

## 결과

| 항목 | 1GiB 시험 | 5GiB 시험 |
| --- | --- | --- |
| 전송 파일 크기 | 1,073,741,824 bytes | 5,368,709,120 bytes |
| FFprobe 재생 시간 | 1800.056초 | 3600.024초 |
| 인코딩 미디어 부분 | 163,672,717 bytes | 327,333,684 bytes |
| 패딩 부분 | 910,069,107 bytes | 5,041,375,436 bytes |
| API 재시작 후 재개 지점 | 536,870,912 bytes | 2,684,354,560 bytes |
| API peak working set | 56,475,648 bytes | 56,631,296 bytes |
| 클라이언트 peak working set | 80,949,248 bytes | 97,558,528 bytes |
| 최종 상태 | QUEUED | QUEUED |
| 파일 해시 일치 | 통과 | 통과 |
| 시험 후 대용량 파일·작업 삭제 | 통과 | 통과 |

메모리는 Windows `GetProcessMemoryInfo`의 최대 working set이다. 가상 메모리 예약량이나 전체 시스템 캐시 사용량을 의미하지 않는다. 시험 기준은 API/클라이언트 각각 256MiB 미만이며, 파일을 4MiB씩 읽고 전송하는 코드 경로와 함께 확인했다.

두 시험 모두 64MiB 업로드 후 4MiB Content-Length를 선언한 다음 64KiB만 보내고 소켓 연결을 끊었다. 서버의 ClientDisconnect 처리와 이전 업로드 지점으로의 롤백을 확인했다. 절반 전송 후 API를 종료·재시작하고 저장된 offset으로 재개하기 전에 **전체 저장 prefix**를 4MiB 블록 SHA-256 검증 API로 검사했다. 완료 후 서버 파일의 전체 SHA-256을 송신 파일 해시와 다시 비교했다. 마지막에 취소·삭제 API로 해당 테스트 작업을 제거했고 생성한 대용량 입력 파일도 삭제했다.

초기 5GiB 시도는 테스트 할당량을 16GiB로 지정해 HTTP 507로 거부됐다. 원본의 4배인 20GiB를 예약하는 기존 접수 검사가 동작한 결과다. 앱 코드를 완화하지 않고 테스트 서버 할당량을 32GiB로 수정한 뒤 위 시험을 통과했다. 실패 당시 생성한 대용량 파일도 정리했다.

보고서:

- 1GiB: `data/large-upload-validation/e037fdae45814c70b67fea185c2c1945/report.json`
- 5GiB 성공: `data/large-upload-validation/bf332ae80ea64c57a5c7a96b8158c1b0/report.json`
- 5GiB 예약 거부: `data/large-upload-validation/f8eb159935e343899a87cd7293bad456/report.json`

## 재현

```powershell
python scripts/smoke-large-upload.py --run-local --gib 1 --minutes 30
python scripts/smoke-large-upload.py --run-local --gib 5 --minutes 60
```

Python/FastAPI/httpx와 로컬 FFmpeg/FFprobe가 필요하다. 최대 메모리 측정은 Windows용이므로 다른 OS에서는 이 시험의 메모리 통과 기준을 충족하지 않는다. 작업 디스크는 테스트 크기의 5배+1GiB 이상 여유 공간을 요구한다. 실패 시 보고서와 진단 파일을 보존하며, 성공 시 대용량 원본과 API 작업은 제거한다. 테스트 API 프로세스는 정상/실패 모두 종료한다.

## 남은 검증

실제 1~5GiB 비패딩 음성 영상의 전체 전사·번역·인코딩·다운로드, 모바일/브라우저 업로드와 저장 완료, 느린 네트워크 및 실제 프록시, 다수 작업이 함께 있는 저장소의 성능, 60분 영상 전체 재생·seek 품질은 아직 별도 검증이 필요하다. 루프백 전송 성공을 이 항목들의 완료 근거로 사용하지 않는다.
