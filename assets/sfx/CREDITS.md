# 효과음 출처와 라이선스

이 디렉터리의 효과음은 **이 저장소에서 FFmpeg 사인 합성으로 직접 생성했다.** 외부에서 받은
파일이 아니다 — 제3자 저작물이 아니므로 표기 의무도, 재배포 제한도 없다.

생성일: 2026-08-10 / 이슈: #18

---

## 왜 외부 무료 소스를 쓰지 않았는가

PRD 8장은 "사용자가 제공한 파일, **직접 생성한** 이미지, 라이선스가 명확한 무료 소스"를
허용한다. 이 파일들은 저장소에 커밋되고 이후 데스크톱 앱에 동봉되므로, 재배포 범위가
저장소 clone에서 배포 바이너리까지 넓다. 그 범위에서 가장 위험이 없는 선택은 조건이 하나도
없는 자체 생성이다. CC0 소스도 조건이 없지만, 사이트 약관과 파일별 표기가 갈리는 경우가
있고 나중에 출처가 사라지면 근거를 잃는다.

부수 효과가 두 가지 있다. 길이와 peak를 스펙 값에 정확히 맞출 수 있고
(아래 측정값), 소리를 바꾸고 싶을 때 파일을 다시 찾는 대신 파라미터를 고치면 된다.

## 라이선스

**이 저장소의 라이선스를 따른다.** 저작권자가 이 프로젝트이므로 저장소 재배포와 앱 동봉이
모두 허용된다 — 허용 여부를 제3자에게 확인할 필요가 없다는 것이 자체 생성의 요점이다.

표기 문구는 **필요하지 않다.** 그래도 밝히고 싶다면 아래를 그대로 복사해 쓸 수 있다.

```text
Sound effects generated with FFmpeg for YouTube Shorts Maker.
```

## 파일

| 파일 | 용도 | 구성 |
| --- | --- | --- |
| `beep.mp3` | 카운트다운 비프 (단발) | 880Hz + 1760Hz(-9dB), 지수 감쇠 |
| `correct.mp3` | 정답 공개 | C5-E5-G5-C6 상승 아르페지오, 90ms 간격 |

이름은 임의로 붙인 것이 아니다. **파일 stem이 `scenes.json`의 `sfx` 값과 같아야 한다** —
`types/quiz/scene_template.py`의 `COUNTDOWN_SFX` / `ANSWER_SFX`가 그 값을 정하고, 이름에서
경로를 찾는 것은 믹싱하는 #23이다. 규격은 mp3 / 48kHz / 스테레오 / 192kbps다.
`voice.mp3`는 44.1kHz(`timeline._SAMPLE_RATE`)이므로 섞기 전 리샘플이 필요하며, 낭독
세그먼트도 같은 처리를 받는다 (#23).

`tension.mp3`는 넣지 않았다. 퀴즈 스펙 7장이 "(선택)"으로 두었고 현 장면 템플릿이 그 이름을
내지 않는다 — 필요해지면 템플릿이 `sfx: "tension"`을 내도록 바뀌는 것이 먼저다.

## 재생성

```bash
python tools/generate_sfx.py
```

성분음 주파수·길이·감쇠는 그 스크립트의 `BEEP` / `CORRECT` 정의에 있다. 스크립트는 무손실로
한 번 렌더해 peak를 재고, 목표 -14 dBFS까지의 차이를 gain으로 되먹여 mp3를 뽑는다. 두 파일이
같은 목표로 정규화되므로 peak 편차 조건은 구조적으로 충족된다.

## 측정값

측정 명령과 결과다. 상한은 이슈 #18이 정했다 — peak -12 dBFS 이하, 두 파일 편차 3dB 이내,
`beep` 1.0초 이하, `correct` 1.5초 이하.

```bash
# 길이
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 assets/sfx/beep.mp3
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 assets/sfx/correct.mp3

# peak
ffmpeg -hide_banner -i assets/sfx/beep.mp3    -af volumedetect -f null -
ffmpeg -hide_banner -i assets/sfx/correct.mp3 -af volumedetect -f null -
```

| 파일 | 길이 | 상한 | peak (`max_volume`) | mean |
| --- | --- | --- | --- | --- |
| `beep.mp3` | 0.100초 | 1.0초 | **-14.3 dB** | -28.5 dB |
| `correct.mp3` | 0.680초 | 1.5초 | **-14.3 dB** | -27.2 dB |

peak 편차 **0.03 dB** (`astats`의 소수점까지: -14.262 / -14.288) — 상한 3dB 안이다.

파일 길이는 **들리는 구간에 맞춰 잘랐다.** 지수 감쇠는 성분음이 끝나기 전에 -60dBFS 아래로
내려가므로, 자르지 않으면 `correct.mp3`가 1.07초로 기록되고 그중 0.44초가 무음이 된다. 믹싱
쪽이 그 길이를 소리 나는 구간으로 읽으면 트리거 간격 판단이 어긋난다.

앞머리 무음은 없다 — 두 파일 모두 t=0에서 소리가 시작한다 (`silencedetect=noise=-60dB`로
확인). mp3가 담는 인코더 지연을 FFmpeg 디코더가 LAME 태그로 걷어내기 때문이며, 같은 사실을
#16이 `voice.mp3` 오프셋에서 확인했다.

커밋된 파일이 이 표의 조건을 지키는지는 `tests/test_sfx_assets.py`가 매번 다시 잰다. 파일을
더 좋은 소리로 교체하더라도 그 테스트를 통과해야 한다.
