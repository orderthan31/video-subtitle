# 소프트 자막 검증

2026-09-11, Windows / FFmpeg 8.0.1 / RTX 5070 Ti.

## 동작

- 웹 `자막 방식`에서 번인 또는 소프트 자막을 선택한다. 기존 작업과 옵션 생략 요청은 번인이다.
- soft는 영상 프레임에 글자를 합성하지 않고, MP4에 별도 `mov_text` 자막 트랙을 넣는다. 영상·음성은 기존 코덱/품질 정책으로 한 번 인코딩한다.
- 자막은 default 트랙이며 UI 제공 언어는 ISO 639-2 언어 코드로 기록한다. 다른 언어는 `und`와 원래 언어 코드의 handler name으로 기록한다.
- 별도 번역 SRT/SMI와 원문 SRT도 그대로 제공한다. 자막이 먼저 끝나도 영상/음성을 자르지 않는다.
- 내장 자막의 표시·켜기/끄기 지원은 플레이어에 달려 있다. 브라우저 및 모바일 플레이어별 동작은 아직 보장하지 않는다.

## 검증 기록

- 119개 Python 회귀 테스트, 웹 테스트 4개, TypeScript/Vite 빌드 통과.
- 실제 Gemini 전사·번역과 HEVC NVENC soft 작업 `643dba162ac047e6b9eca1bae19f48c7` 완료. 보고서: `data/live-smoke/d94485b5b1524447a49d169d0d9bad8d/report.json`.
- HEVC/H.264 GPU 출력의 전체 영상·음성 디코딩, 단일 기본 mov_text 트랙/kor 언어 코드, 재추출한 SRT 텍스트·타임코드 일치 확인. 보고서: `data/soft-subtitle-validation/3fbe76c88fdc405090a3fd28cb6ed52b/report.json`.
- libx265/libx264로 동일 검사 통과. 보고서: `data/soft-subtitle-validation/33cd510fdac5420e85fb543be263eee0/report.json`.
- 데스크톱 브라우저에서 soft 선택 후 실제 업로드한 작업 `5d3b9ab9c62b4cae9006f3adddb587aa`의 API `subtitle_mode=soft` 확인. 동일 작업을 실제 Gemini/HEVC NVENC Worker로 처리하여 COMPLETED 확인. MP4 Range 요청은 HTTP 206 및 요청한 1,024바이트를 반환했다.
- 7초 합성 영어 음성/한국어 번역 검증이다. 장시간 대화, 특수 스타일, 모바일 자막 토글/Seek은 미검증이다. 결과 파일과 보고서는 Git에 포함하지 않는다.

## 재현

```powershell
python scripts/smoke-media.py --run-live --audio data/stt-smoke.wav --subtitle-mode soft
python scripts/smoke-soft-subtitles.py --source <source.mp4> --subtitle <translated.srt>
```

첫 명령만 Gemini를 호출한다. 두 번째 명령은 HEVC/H.264를 각각 출력하고 FFmpeg로 SRT를 추출해 정규화한 원래 SRT와 비교한다. 기본 환경은 NVENC이며 `VIDEO_ENCODER=libx264`로 설정하면 두 코덱 모두 대응 소프트웨어 인코더로 검사한다.

구현 참고: [FFmpeg mov_text 인코더](https://www.ffmpeg.org/doxygen/8.0/movtextenc_8c_source.html).
