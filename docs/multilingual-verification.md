# 다중 번역 언어 검증

2026-09-11.

## 동작

- 기본 번역 언어와 최대 4개 추가 언어를 선택한다. 웹은 한국어·영어·일본어·중국어·스페인어를 제공하고, API는 기존 언어 코드 형식을 허용한다.
- 전사/원본 시간 복원/원문 SRT는 공통으로 수행한다. 언어별 번역과 자막 레이아웃을 생성하며 CJK 지역 코드에도 짧은 줄 폭을 적용한다.
- 기본 파일명은 기존 API와 호환된다. 추가 언어는 `translated.ja.srt`처럼 언어를 포함한 SRT/SMI로 제공한다.
- 번인은 기본 언어만 합성하고 추가 언어는 별도 파일이다. 언어별 번인 MP4를 여러 개 생성하는 기능은 아니다.
- soft는 한 MP4의 언어별 트랙을 생성한다. 첫 트랙만 기본 선택이다. 실제 트랙 선택 지원은 플레이어에 달려 있다.
- 추가 언어가 실패하거나 취소되면 부분 완료로 남기지 않고 전체 작업을 실패/취소 정리한다. 추가 번역은 Gemini 호출과 비용을 늘린다.

## 자동 검증

- Python 137개 테스트: 기본 요청 호환, 언어 배열 저장·조회·생성 멱등성, 중복/경로/과다 언어 거부, 전사 1회·언어별 번역 호출, 원본/기본/추가 SRT 시간과 내용, 추가 언어 실패 정리, soft 트랙 수와 default, 허용 파일만 다운로드, 만료 410.
- 웹 테스트 4개 및 TypeScript/Vite 빌드 통과.

## 실제 검증

- 기존 7.17초 합성 영어 음성을 Gemini로 전사하고 한국어·일본어·스페인어로 번역했다. HEVC NVENC soft 출력 작업 `afef5cf324a2436099485aa82a1db79e` COMPLETED 확인. 결과 파일은 8개다.
- 작업 보고서: `data/live-smoke/ec34a3451ed449ea9d2c3324c72d3f31/report.json`.
- `verify-multilingual-output.py`로 3개 MP4 트랙을 SRT로 추출해 각각의 원래 SRT와 정규화 후 텍스트·시간 일치 및 언어 라벨 확인.
- 재추출 보고서: `data/multilingual-validation/405c9ffece4f4517a177aa2e226e417d/report.json`.
- API 일본어·스페인어 SRT/SMI 총 4개 파일 HTTP 200 확인. 데스크톱 브라우저의 추가 언어 체크박스, 추가 자막 메뉴 열림 및 언어별 링크 표시 확인.
- 최대 5개 언어 실제 동시 결과, 장시간 번역 품질/용어 일관성, 모바일 트랙 선택, 브라우저 다운로드 저장 완료는 미검증이다. 이 검증은 서로 다른 언어의 전문 번역 품질을 보증하지 않는다.

## 재현

```powershell
python scripts/smoke-media.py --run-live --audio data/stt-smoke.wav --subtitle-mode soft --additional-language ja --additional-language es
python scripts/verify-multilingual-output.py --job <job-directory>
```

첫 명령은 실제 Gemini를 호출한다. 두 번째 명령은 완성된 soft 작업의 파일만 로컬에서 검사한다. 보고서/미디어는 Git에 포함하지 않는다.
