# 전사 전 비언어 음성 필터

2026-09-11 기준 **구현 중인 실험 기능**이다. `VOCALIZATION_FILTER_ENABLED=false`가 기본값이며 실제 의미 보존 검증 전에는 활성화하지 않는다. 코드와 모의 응답 테스트 통과는 호흡·기합 제거 품질 또는 대사 보존 정확도 달성의 증거가 아니다.

## 처리 경로

Worker의 오디오 추출 뒤 PREPROCESSING_AUDIO에서 실행한다. 전사 모델은 기존 `gemini-3.5-transcribe`를 유지한다. 필터는 `GEMINI_AUDIO_FILTER_MODEL`을 사용하며 생략 시 번역 모델인 `gemini-3.8-flash`를 사용한다. Google은 [오디오 이해 API](https://ai.google.dev/gemini-api/docs/generate-content/audio)에서 오디오 분석과 구간 타임스탬프 생성을 제공한다. 이는 정확한 이벤트 경계나 필터 정확도를 보장한다는 뜻은 아니다.

1. 16kHz 모노 PCM16 WAV를 30초 담당 구간과 양쪽 최대 2초 문맥으로 읽는다. 영상 전체 또는 전체 오디오를 메모리에 적재하지 않는다.
2. 음성/호흡/기합/불확실 구간을 구조화된 JSON으로 판별한다. 진폭만으로 호흡·기합 후보를 만들지 않는다.
3. 발화·불확실 구간과 겹치거나 인접한 후보를 거부한다. 청크 겹침에서 관측한 발화도 보호한다. 매우 짧거나 8초보다 긴 삭제 후보는 적용하지 않는다.
4. 후보 양끝을 보수적 모드 0.3초, 강한 모드 0.15초 안쪽으로 줄인다. 후보의 실제 오디오와 앞뒤 2초 문맥을 별도 요청으로 다시 판별한다.
5. 두 판별의 종류가 일치하고 두 번째가 명시적으로 삭제 가능/발화 없음으로 응답한 경우만 적용한다. 불일치/발화/불확실 응답은 유지한다. `certain`은 모델의 정성적 출력이지 보정된 확률이 아니다. 같은 모델의 두 판단이 독립적인 정확도 보장을 제공하지 않는다.
6. 판별된 발화·불확실 구간 및 두 번째 판별에서 거부된 구간을 무음 제거에서도 보호한다. 따라서 모델이 포착한 긴 저음량 대사가 진폭 기반 무음 후보와 겹쳐도 남는다. 모델이 놓친 음성을 보호한다고 보장할 수는 없다.
7. 무음과 비언어 제거 구간 모두 원본 오디오 시각에서 합성하고, PCM 샘플 단위로 복사하면서 하나의 Timeline Mapping을 만든다. 최종 영상의 원본 음성은 변경하지 않는다.

`audio_filter=off`에서는 모델 호출과 무음 제거 모두 하지 않는다. 일반 텍스트 감탄사를 무조건 지우는 규칙은 추가하지 않았다. 잘못된 구조/범위/응답이나 API 실패는 기존 실패·정리 경로를 따르며, 조용히 다른 필터 결과로 성공 처리하지 않는다. API 호출은 기존 재시도/취소/Worker 종료/주기 용량 검사를 공유한다.

작업 중 `work/vocalization-analysis.json`과 `work/timeline-map.json`을 저장한다. 정상 정리 후 분석 원문은 삭제되며 작업 메타데이터에는 활성 여부와 적용 구간 수가 남는다. 필터 활성화 시 기본 30초당 1회에 후보별 확인 요청이 추가되므로 처리 시간과 API 비용이 늘어난다. 실제 비용·지연 상한은 아직 측정하지 않았다.

## 검증 현황

- 전체 Python 174개 테스트 통과. 신규 9개는 불확실/인접 대사 거부, 두 번째 판단 불일치, 잘못된 JSON/시간, off/취소 시 무호출, 청크 경계 발화 보호, 무음·비언어 구간 결합, PCM 보존/원본 시간 복원, 검출된 저음량 발화 보호를 포함한다.
- Worker 회귀에서는 필터 활성화 시 전사 전 호출 및 제거/보호 구간 전달, 적용 구간 수 기록을 검사했다. API는 모의 응답이며 품질 평가는 아니다.
- `scripts/make-speech-fixture.ps1`을 Windows 로컬 TTS로 실행해 고정 문장 `Hello. Welcome to the subtitle test. The weather is sunny today.`를 새로 생성했다. 녹음이나 개인 파일을 입력으로 사용하지 않는다.
- 공개 [ESC-50](https://github.com/karolpiczak/ESC-50)의 `1-18631-A-23.wav`를 Git 제외 `data/vocalization-fixtures`에 검증용으로 다운로드했다. 메타데이터 category는 breathing, 원본 Freesound ID는 18631이다. SHA-256: `cdbd39617f2955d86ec18a4032ecd9090369c23f3d8c6d650f62e9e1ee19ea6a`.
- ESC-50은 [데이터셋 라이선스 및 개별 출처](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)를 별도로 제공한다. CC BY-NC 데이터셋 샘플은 제품 코드·배포 이미지에 포함하지 않으며 검증 데이터로만 분리한다.
- 실제 Gemini 전송 명령은 자동 보안 검토에서 차단되어 시작되지 않았다. 기존 음성 파일 대신 출처가 확실한 새 합성 파일을 준비했으며, 테스트 음성과 공개 호흡음 전송에 대한 사용자 승인을 요청한 상태다. **실제 모델 판별/전사 결과 보고서는 아직 없다.**

## 승인 후 재현

```powershell
powershell.exe -NoProfile -File scripts/make-speech-fixture.ps1
python scripts/smoke-vocalizations.py --run-live --speech data/vocalization-fixtures/generated-speech.wav --breath data/vocalization-fixtures/1-18631-A-23.wav --expected-text "Hello Welcome to the subtitle test The weather is sunny today"
```

두 번째 명령은 실제 Gemini에 음성을 전송한다. 보통 음량 대사, 호흡음, 12초 무음, 음량을 0.02배 한 대사를 조합한다. 호흡 영역 밖 삭제 없음, 대사 구간 PCM 보존, 모든 유지 샘플 일치, 필터 후 전사 단어열과 원본 시간 복원을 보고한다. 저음량 TTS는 실제 속삭임을 대표하지 않는다. 단일 호흡 샘플과 영어 문장 검증만으로 최종 완료 판정하지 않는다.

남은 검증: 실제 독립 호흡/기합, 의미 있는 한국어 단답·감탄사, 소리 큰 경고문, 자연스러운 속삭임, 발화와 겹친 호흡, 잡음/음악, 실제 청크 경계, 실패/재시도 지연, 장시간 처리 비용. 승인 후 실제 결과를 평가하고 실패 시 경계·판별 전략을 수정한 뒤 기본 활성 여부를 결정한다.
