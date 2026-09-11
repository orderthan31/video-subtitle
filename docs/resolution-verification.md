# 해상도 선택 검증

2026-09-11, Windows / FFmpeg 8.0.1 / RTX 5070 Ti.

## 정책

- `original`은 기존 원본 유지 동작이다. `1080p`는 가로 최대 1920×1080, `720p`는 최대 1280×720이며 세로 영상은 제한 크기를 반대로 적용한다.
- 두 축 모두 제한 안에 들어오도록 축소하고 작은 영상은 확대하지 않는다. 자르기나 여백 추가는 하지 않는다.
- yuv420p 출력 크기는 짝수 픽셀로 내림한다. FFmpeg scale이 SAR를 조정해 표시 종횡비를 보존한다. 번인 자막은 축소 이후 적용한다.
- 원본 픽셀 크기에 회전 메타데이터를 반영하여 크기를 계산한다. 임의 각도 회전의 축소는 명시적 오류로 처리한다. 비정사각 픽셀 원본의 최대 크기는 표시 크기가 아닌 회전 후 픽셀 크기 기준이다.
- API 저장·조회·생성 요청 충돌 검사와 웹 업로드 재개에 선택값을 유지한다. 기존 레코드는 원본 유지다.

## 실제 검증

`python scripts/smoke-resolution.py`는 AI 호출 없이 영상을 생성하고 인코딩·FFprobe·전체 디코딩·표시 종횡비를 검사한다.

| 입력 | 옵션 | 검증 출력 |
| --- | --- | --- |
| 1920×1080 | 720p / HEVC 번인 | 1280×720 |
| 1920×1080, 90도 회전 | 720p / H.264 soft | 720×1280 |
| 2560×1440 | 1080p / HEVC soft | 1920×1080 |
| 640×360 | 1080p / H.264 번인 | 640×360 |
| 1920×800 | 720p / H.264 soft | 1280×532, DAR 12:5 |

- NVENC 보고서: `data/resolution-validation/a7e9b6eae00e438e943a8efa4fa2ec04/report.json`.
- libx265/libx264 보고서: `data/resolution-validation/48ff6d84598f425fae1143fc958c8ee6/report.json`.
- 기존 `-metadata rotate=90` 테스트 파일 생성 방식이 현재 FFmpeg에서 회전 정보를 저장하지 않는 사례를 확인했다. 두 회전 smoke 스크립트를 `-display_rotation`으로 수정하고 실제 side data 존재를 검사한다. 새로운 결과는 이 검사까지 통과했다.
- 실제 Gemini/HEVC NVENC 1080p→720p 작업 `648fc7ea62624da28fe05764e8c1153b` 완료. 보고서: `data/live-smoke/550b30a270b94041a2c1d39ee291b20a/report.json`.
- Python 124개·웹 4개 회귀 테스트와 TypeScript/Vite 빌드 통과. 데스크톱에서 해상도 선택과 배치 확인. 모바일 선택/재개와 장시간 화질 평가는 미검증이다.

실제 AI 재현 명령(기존 합성 음성 사용):

```powershell
python scripts/smoke-media.py --run-live --audio data/stt-smoke.wav --frame-size 1920x1080 --resolution 720p
```

원본·출력·보고서는 Git 제외 경로에 보관한다.
