# 영상 자동 번역·자막 생성 웹서비스 E2E 요구사항

## 목적

사용자가 웹에서 영상을 업로드하면 서비스가 음성을 전사하고 지정 언어로 번역한 뒤, SRT 자막과 자막이 번인된 최종 MP4 영상을 생성한다.

AI 추론은 외부 Frontier Cloud Model API를 사용하고, 영상 처리와 인코딩은 로컬 FFmpeg/NVENC를 우선 사용한다.

## 기술 스택

- Frontend: React, Vite, TypeScript
- Backend: Python, FastAPI, REST API
- Worker: Python, FFprobe, FFmpeg, NVIDIA NVENC, AI Provider Adapter
- Storage: Job 단위 로컬 파일 저장소
- 구조: Frontend, Backend, Worker, Shared package를 분리한 Monorepo

## 기본 처리 흐름

```text
영상 업로드
↓
영상 분석
↓
오디오 추출
↓
Audio Preprocessing
├─ 긴 무음 탐지/제거
├─ 불필요한 호흡음 제거
├─ 비언어 발성 제거
└─ Timeline Mapping
↓
STT
↓
원본 Timestamp 복원
↓
Transcript Post-processing
↓
번역
↓
자막 Segmentation
↓
SRT 생성
↓
Subtitle Burn-in + HEVC 인코딩 + 압축
↓
결과 검증
↓
Cleanup
↓
최종 MP4 + SRT 제공
↓
TTL 만료 후 삭제
```

## 핵심 요구사항

1. GB 단위 영상 업로드를 안정적으로 처리한다.
2. 업로드 중단 시 가능한 지점부터 Resume할 수 있어야 한다.
3. 대용량 파일을 애플리케이션 메모리에 전체 적재하지 않는다.
4. FastAPI request lifecycle 안에서 장시간 FFmpeg/AI 작업을 직접 수행하지 않는다.
5. 미디어 처리는 Job Queue 기반 Worker에서 수행한다.
6. STT에는 영상 전체가 아닌 추출된 오디오를 전달한다.
7. 긴 무음, 독립적인 호흡음, 기합, 비언어 발성을 보수적으로 제거한다.
8. 제거 구간은 Timeline Mapping으로 기록하고 최종 자막은 항상 원본 영상 시간축을 사용한다.
9. STT와 번역은 분리한다.
10. STT 후 Transcript Post-processing으로 비의미 Segment를 한 번 더 제거한다.
11. SRT를 기본 자막 포맷으로 생성한다.
12. 자막 Burn-in과 압축은 한 번의 인코딩 과정에서 처리한다.
13. HEVC NVENC를 우선 사용한다.
14. GOP, Fast Start, AAC 오디오로 모바일 재생과 Seek 성능을 고려한다.
15. 완료, 실패, 취소, 비정상 종료 후 Cleanup과 Garbage Collection을 수행한다.
16. 서비스 전용 Storage Root와 Quota, Disk Watermark, Minimum Free Space를 설정한다.

## Job 저장 구조

```text
data/video-jobs/{job_id}/
├─ job.json
├─ input/
│  └─ source.{ext}
├─ work/
│  ├─ metadata.json
│  ├─ audio.m4a
│  ├─ processed-audio.m4a
│  ├─ timeline-map.json
│  ├─ transcript.json
│  ├─ translated.json
│  └─ temp.*
└─ output/
   ├─ translated.srt
   └─ final.mp4
```

Job 간 파일은 공유하지 않는다. 원본 파일명은 표시용 메타데이터로만 보관하고 디렉터리 식별자로 사용하지 않는다.

## Job 상태

```text
UPLOADING
QUEUED
ANALYZING
EXTRACTING_AUDIO
PREPROCESSING_AUDIO
TRANSCRIBING
FILTERING_TRANSCRIPT
TRANSLATING
GENERATING_SUBTITLE
ENCODING
VALIDATING
CLEANING
COMPLETED
FAILED
CANCELLED
```

Frontend는 현재 상태와 진행 단계를 표시한다.

## Audio Preprocessing 원칙

삭제 판단은 Precision 우선으로 한다.

- 확실한 불필요 구간은 제거한다.
- 판단이 불확실한 Segment는 유지한다.
- 단순 amplitude threshold만으로 기합이나 호흡음을 제거하지 않는다.
- 긴 무음은 기본적으로 10초 이상인 경우 STT 입력에서 제거한다.
- 독립적인 호흡음, 기합, 비언어 발성은 오디오 단계와 Transcript 단계에서 이중 필터링한다.

## Timeline Mapping

Audio Preprocessing으로 삭제되는 모든 구간은 하나의 Timeline Mapping에 기록한다.

```json
[
  {
    "processed_start": 0,
    "processed_end": 120.4,
    "original_start": 0,
    "original_end": 120.4
  },
  {
    "processed_start": 120.4,
    "processed_end": 302.1,
    "original_start": 124.8,
    "original_end": 306.5
  }
]
```

STT 결과 Timestamp는 Mapping을 이용해 즉시 원본 영상 시간축으로 복원한다.

## 인코딩 기본 프로파일

- Container: MP4
- Video Codec: H.265 / HEVC
- Encoder: `hevc_nvenc`
- Preset: `p5`
- Rate Control: CQ 기반
- CQ: 균형 24, 고화질 20, 고압축 29
- Resolution/FPS: 원본 유지
- GOP: 약 2초
- Audio: AAC 128kbps
- MP4: `-movflags +faststart`

## 결과 검증

완료 처리 전 최소 다음을 확인한다.

- `final.mp4` 존재
- File size > 0
- Video stream 존재
- Audio stream 존재
- Duration 정상
- FFprobe 정상
- `translated.srt` 존재
- SRT 내용 존재
- 비의미 자막 Segment 잔존 여부 검사

## MVP 필수 범위

- React + Vite Web UI
- FastAPI Backend
- Python Media Worker
- Resumable Upload
- Job Queue/Polling
- FFprobe/FFmpeg
- Audio Extraction
- 긴 무음 제거
- Timeline Mapping
- Gemini Transcribe Adapter
- Translation Adapter
- Transcript Filtering
- SRT 생성
- Burn-in MP4
- HEVC NVENC 우선 인코딩
- 진행 상태, Job Cancel, 결과 다운로드
- Result Validation
- Cleanup, Result TTL, Garbage Collector
- Storage Quota, Disk Watermark

## 후속 범위

- SMI
- 원문 SRT
- Soft Subtitle
- H.264 Profile
- Resolution 선택
- 다중 번역 언어
- 자막 수정 UI
- Audio Filtering 강도 조절
- 작업 이력
- 사용자 인증

## E2E 완료 기준

30~60분, 1~5GB 수준의 외국어 영상에 대해 업로드 Resume부터 최종 MP4/SRT 제공, Cleanup, TTL 삭제까지 전체 과정이 정상 동작해야 한다.

최종 검증 항목:

- 업로드 중단 후 Resume 가능
- 실제 대사가 Audio Filtering 때문에 누락되지 않음
- 긴 무음이 STT 입력에서 제외됨
- 독립적인 호흡음과 기합이 자막으로 생성되지 않음
- 의미 있는 감탄사나 문장 일부는 과도하게 삭제되지 않음
- Audio Segment 제거 이후에도 자막 Sync 정상
- STT 및 번역 정상
- 최종 영상 정상 재생
- 모바일 Seek 성능 정상
- 원본 대비 영상 크기가 비정상적으로 증가하지 않음
- 완료, 실패, 취소 작업 Cleanup
- 비정상 종료 후 Garbage Collector가 Orphan file 제거
- Disk 임계치 초과 전 신규 Upload 차단
- TTL 만료 후 최종 결과 삭제

