# YouTube Shorts Maker PRD

## 1. 개요

YouTube Shorts Maker는 사용자가 입력한 주제, 긴 원문, 기사 전문, 링크를 바탕으로 세로형 쇼츠 초안을 자동 생성하고, 사용자가 앱에서 직접 편집해 최종 영상을 내보낼 수 있는 로컬 우선 영상 제작 도구다. 1차 목표는 업로드 자동화가 아니라, 검수와 편집이 가능한 `1080x1920` MP4 쇼츠 파일을 안정적으로 생성하는 것이다.

초기 버전은 완전 자동 게시 시스템이 아니라 제작 보조 앱으로 설계한다. 대본, 음성, 자막, 영상 합성 결과물을 파일로 남기고, 사용자가 편집 화면에서 내용을 조정한 뒤 업로드 전에 확인할 수 있게 한다.

## 2. 목표

- 주제, 원문, 기사 전문, 링크 중 하나를 입력하면 쇼츠 영상 생성에 필요한 대본, 음성, 자막, 메타데이터, 최종 MP4를 생성한다.
- 결과물은 YouTube Shorts에 적합한 세로형 영상으로 출력한다.
- 각 생성 단계를 파일로 남겨 재실행, 검수, 수정이 가능하게 한다.
- 사용자가 생성된 초안을 앱에서 직접 편집할 수 있게 한다.
- 세로형 쇼츠 편집에 필요한 자막, 텍스트, 배경, 컷, 오디오 조정 기능을 제공한다.
- 저작권 리스크가 낮은 소스만 사용하도록 구조를 만든다.
- YouTube 업로드 자동화는 MVP 이후 단계로 분리한다.

## 3. 비목표

- MVP에서 YouTube 자동 업로드를 구현하지 않는다.
- MVP에서 트렌드 크롤링, 뉴스 수집, 대량 예약 생성은 구현하지 않는다.
- MVP에서 복잡한 멀티트랙 전문 편집기를 만들지 않는다.
- 타인의 YouTube 영상을 무단 다운로드해 재가공하는 기능은 기본 기능으로 제공하지 않는다.
- 수익화 최적화, 조회수 예측, 채널 운영 자동화는 초기 범위에 포함하지 않는다.

## 4. 대상 사용자

- 반복적으로 쇼츠를 제작하는 개인 크리에이터
- 교육, 정보, 요약형 콘텐츠를 제작하는 운영자
- 자동화 파이프라인을 직접 수정하고 싶은 개발자

## 5. 핵심 사용 시나리오

1. 사용자가 주제, 기사 전문, 긴 원문, 또는 링크를 입력한다.
2. 시스템이 입력 유형을 감지하고 원문 텍스트를 확보한다.
3. 링크 입력이면 페이지 본문을 추출하고 출처 URL을 기록한다.
4. 시스템이 원문에서 핵심 주장, 사실, 숫자, 인용 후보를 요약한다.
5. 시스템이 45~60초 분량의 쇼츠 대본을 생성한다.
6. 대본을 장면 단위로 나누고 각 장면에 필요한 시각 자료 설명을 만든다.
7. 대본을 TTS 음성으로 변환한다.
8. 음성 기준으로 자막 파일을 생성한다.
9. 배경 이미지나 영상을 세로형 캔버스에 배치한다.
10. 음성, 자막, 배경, 간단한 모션을 합성해 MP4를 만든다.
11. 앱에서 미리보기, 자막 수정, 텍스트 위치 조정, 배경 교체, 컷 순서 조정을 수행한다.
12. 제목, 설명, 태그 후보, 출처 정보를 `metadata.json`으로 저장한다.
13. 사용자가 최종 MP4를 내보낸 뒤 직접 업로드한다.

## 6. MVP 범위

### 6.1 입력

- CLI 인자: `--topic "주제"`, `--url "https://..."`, `--text-file input/source.txt`
- 선택 입력 파일: `input/topic.txt`, `input/source.txt`, `input/urls.txt`
- 선택 설정 파일: `config.yaml`

### 6.2 출력

```text
outputs/
  run-{timestamp}/
    script.txt
    source.json
    summary.json
    scenes.json
    voice.mp3
    captions.srt
    metadata.json
    final_short.mp4
```

### 6.3 영상 규격

- 해상도: `1080x1920`
- 비율: `9:16`
- 길이: 기본 45~60초
- 포맷: H.264 MP4
- FPS: 30fps
- 오디오: AAC

### 6.4 기본 생성 단계

- 대본 생성
- 입력 유형 감지
- 링크 본문 추출
- 원문 요약 및 사실 후보 추출
- 장면 분할
- TTS 음성 생성
- 자막 생성
- 배경 이미지/영상 선택
- 자막 번인
- 최종 MP4 렌더링
- 메타데이터 생성

### 6.5 앱 편집 기능

- 생성 결과 미리보기
- 자막 텍스트 수정
- 자막 스타일 선택
- 텍스트 오버레이 추가/수정/삭제
- 배경 이미지 또는 영상 교체
- 장면 순서 변경
- 장면 길이 조정
- 오디오 볼륨 조정
- 최종 MP4 내보내기

## 7. 기능 요구사항

### 7.1 입력 수집

- 입력은 짧은 주제, 긴 원문, 기사 전문, 링크를 모두 지원한다.
- 링크 입력은 MVP에서 단일 URL을 우선 지원한다.
- 링크에서 추출한 본문, 제목, URL, 접근 시각을 `source.json`으로 저장한다.
- 원문 직접 입력은 입력 파일 경로와 텍스트 길이를 `source.json`으로 저장한다.
- 본문 추출 실패 시 사용자에게 직접 원문 입력을 요구할 수 있도록 실패 사유를 남긴다.

### 7.2 원문 요약

- 긴 원문은 바로 쇼츠 대본으로 변환하지 않고 먼저 요약한다.
- 핵심 주장, 배경 맥락, 중요한 숫자, 고유명사, 주의해야 할 불확실성을 분리한다.
- 출처가 있는 경우 메타데이터와 설명에 포함할 출처 후보를 만든다.
- 결과를 `summary.json`으로 저장한다.

### 7.3 대본 생성

- 입력 주제 또는 요약된 원문에서 쇼츠용 내레이션 대본을 생성한다.
- 첫 3초에 후킹 문장을 포함한다.
- 문장은 TTS와 자막에 적합하도록 짧게 작성한다.
- 과장, 허위 사실, 검증 불가 주장을 줄이는 프롬프트를 사용한다.
- 원문 기반 콘텐츠는 원문에 없는 사실을 임의로 추가하지 않는다.
- 결과를 `script.txt`로 저장한다.

### 7.4 장면 분할

- 대본을 3~6개 장면으로 나눈다.
- 각 장면은 내레이션 텍스트, 예상 길이, 시각 자료 설명을 가진다.
- 결과를 `scenes.json`으로 저장한다.

### 7.5 음성 생성

- MVP 기본값은 무료 또는 로컬 실행이 쉬운 TTS provider를 사용한다.
- provider는 교체 가능하게 설계한다.
- 결과를 `voice.mp3`로 저장한다.

### 7.6 자막 생성

- MVP에서는 음성 길이와 대본 문장 기준으로 SRT를 생성한다.
- 이후 Whisper 또는 Faster Whisper 기반 word-level timestamp로 개선한다.
- 결과를 `captions.srt`로 저장한다.

### 7.7 영상 합성

- FFmpeg를 기본 렌더링 엔진으로 사용한다.
- 배경 이미지 또는 영상을 `1080x1920` 캔버스에 맞춘다.
- 자막은 모바일에서 읽기 쉬운 크기와 위치로 번인한다.
- 장면 전환은 MVP에서 단순 컷 또는 약한 zoom/pan만 사용한다.

### 7.8 메타데이터 생성

- 제목 후보 3개, 설명, 태그 후보를 생성한다.
- 링크나 원문 기반 콘텐츠는 출처 URL 또는 원문 출처 정보를 포함한다.
- 결과를 `metadata.json`으로 저장한다.
- 업로드는 하지 않는다.

### 7.9 편집 앱

- 사용자는 생성된 프로젝트를 앱에서 열 수 있다.
- 앱은 세로형 캔버스 미리보기를 제공한다.
- 앱은 장면 목록을 보여주고, 장면별 텍스트, 자막, 배경, 길이를 수정할 수 있게 한다.
- 편집 내용은 프로젝트 파일로 저장한다.
- 미리보기 렌더링은 빠른 프리뷰와 최종 렌더링을 분리한다.
- 최종 내보내기는 FFmpeg 렌더링 엔진을 사용한다.

### 7.10 프로젝트 파일

- 편집 가능한 상태는 `project.json`으로 저장한다.
- `project.json`은 장면, 자막, 텍스트 오버레이, 배경 소스, 오디오 소스, 렌더링 설정을 포함한다.
- 앱은 `project.json`을 기준으로 미리보기와 최종 렌더링을 수행한다.

## 8. 안전장치 및 정책 고려

- 기본 소스는 사용자가 제공한 파일, 직접 생성한 이미지, 라이선스가 명확한 무료 소스만 사용한다.
- 링크 입력은 본문 요약과 변환 용도로 사용하되, 원문을 그대로 긴 분량 복제하지 않는다.
- 기사 기반 콘텐츠는 출처를 기록하고, 사실 여부가 중요한 내용은 검수 단계를 거친다.
- 업로드 자동화는 YouTube Data API v3 정책과 OAuth 설정을 별도 문서로 검토한 뒤 구현한다.
- 대량 생성/대량 업로드 기능은 기본값으로 제공하지 않는다.
- 생성된 대본과 메타데이터는 업로드 전 사람이 검수할 수 있어야 한다.
- 음악은 기본 제공하지 않거나, 사용자가 라이선스를 확인한 파일만 사용한다.

## 9. 참고 오픈소스

- [Verticals v3 / youtube-shorts-pipeline](https://github.com/rushindrasinha/youtube-shorts-pipeline): topic-to-short 전체 파이프라인 참고
- [Viral Faceless Shorts Generator](https://github.com/Dark2C/Viral-Faceless-Shorts-Generator): 수동 승인 단계, TTS, 자막 정렬, FFmpeg 합성 참고
- [ClippedAI](https://github.com/Shaarav4795/ClippedAI): 긴 영상 하이라이트 추출, 9:16 크롭, 동적 자막 참고
- [AI-Youtube-Shorts-Generator](https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator): Whisper 전사, LLM 하이라이트 탐지, 자동 크롭 참고
- [ffmpeg-ai](https://hackaday.io/project/205368-ffmpeg-ai-fully-free-ai-video-cli-pipeline): 가벼운 CLI 기반 topic-to-video MVP 참고

## 10. 제안 기술 스택

- 언어: Python 3.11+
- 앱: Electron + React 또는 Tauri + React
- 렌더링 백엔드: Python 3.11+
- CLI: argparse
- 설정: YAML
- 영상 처리: FFmpeg
- Python 보조 라이브러리: moviepy는 선택 사용
- TTS: Edge TTS, OpenAI TTS, ElevenLabs, Kokoro 중 provider 방식으로 교체 가능하게 설계
- 자막: SRT, 이후 ASS 스타일 자막 지원
- 전사/정렬: MVP 이후 Faster Whisper 검토
- 업로드: MVP 이후 YouTube Data API v3 검토

## 11. 초기 프로젝트 구조

```text
Youtube-Shorts-Maker/
  docs/
    PRD.md
  app/
    package.json
    src/
      App.tsx
      editor/
      components/
  input/
    topic.txt
    source.txt
    urls.txt
  assets/
    backgrounds/
    music/
  outputs/
  src/
    main.py
    config.py
    source_loader.py
    article_extractor.py
    summarizer.py
    script_generator.py
    scene_planner.py
    tts.py
    captions.py
    video_renderer.py
    metadata_generator.py
    project.py
    api.py
  tests/
```

## 12. 구현 마일스톤

### Milestone 1: CLI 골격

- 프로젝트 구조 생성
- `--topic` 입력 처리
- `--url`, `--text-file` 입력 처리
- run output directory 생성
- 설정 파일 로드

### Milestone 2: 프로젝트 파일과 앱 골격

- `project.json` 스키마 정의
- React 앱 생성
- 세로형 캔버스 미리보기 화면
- 장면 목록 UI
- 프로젝트 열기/저장 흐름

### Milestone 3: 텍스트 산출물

- 입력 수집 모듈
- 원문 요약 모듈
- 대본 생성 모듈
- 장면 분할 모듈
- 메타데이터 생성 모듈
- 파일 저장 포맷 확정

### Milestone 4: 오디오와 자막

- TTS provider 인터페이스
- 기본 TTS 구현
- SRT 생성
- 음성/자막 길이 검증

### Milestone 5: 편집 기능

- 자막 텍스트 수정
- 자막 스타일 변경
- 텍스트 오버레이 편집
- 배경 교체
- 장면 순서 변경
- 장면 길이 조정

### Milestone 6: 영상 렌더링

- FFmpeg 기반 세로 영상 합성
- 배경 이미지/영상 적용
- 자막 번인
- 최종 MP4 출력

### Milestone 7: 품질 개선

- ASS 자막 스타일
- 장면별 zoom/pan
- 배경음악 ducking
- 렌더링 실패 처리

### Milestone 8: 업로드 자동화 검토

- YouTube Data API v3 OAuth 문서화
- private 업로드 기능
- 업로드 전 검수 플래그
- 일일 업로드 제한과 쿼터 처리

## 13. 성공 기준

- 하나의 주제, 원문 파일, 또는 링크 입력으로 `final_short.mp4`가 생성된다.
- 생성 결과를 앱에서 열고 자막, 텍스트, 배경, 장면 순서를 수정할 수 있다.
- 편집 상태가 `project.json`으로 저장되고 다시 열 수 있다.
- 출력 영상은 `1080x1920`, H.264 MP4, 30fps로 렌더링된다.
- 원문 정보, 요약, 대본, 장면 정보, 음성, 자막, 메타데이터가 모두 저장된다.
- 같은 입력으로 실패 없이 반복 실행할 수 있다.
- 업로드 전 사람이 검수할 수 있는 산출물이 남는다.

## 14. 미해결 결정사항

- 기본 TTS provider를 무엇으로 할지 결정해야 한다.
- LLM provider를 로컬/외부 API 중 무엇으로 시작할지 결정해야 한다.
- 기본 배경 소스를 사용자가 직접 넣는 방식으로 할지, 무료 이미지 API를 붙일지 결정해야 한다.
- MVP에서 MoviePy를 사용할지, FFmpeg 명령 생성만 사용할지 결정해야 한다.
- 앱 프레임워크를 Electron으로 할지 Tauri로 할지 결정해야 한다.
- Python 백엔드와 앱 프론트엔드를 어떻게 연결할지 결정해야 한다.
- 한국어 쇼츠를 1차 타깃으로 할지, 다국어 구조를 처음부터 열어둘지 결정해야 한다.
- 링크 본문 추출을 어떤 라이브러리로 시작할지 결정해야 한다.
- 유료 언론사, 로그인 필요 페이지, 동적 렌더링 페이지를 MVP에서 제외할지 결정해야 한다.
