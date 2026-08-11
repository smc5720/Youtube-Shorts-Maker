# 폰트 출처와 라이선스

번들 폰트는 **Pretendard 1.3.9**이고, D1 확정 스펙 9장이 지정한 세 웨이트다.

반영일: 2026-08-11 / 이슈: #38

| 파일 | 웨이트 | 사용처 (확정 스펙 5장) |
| --- | --- | --- |
| `Pretendard-ExtraBold.otf` | 800 | 정답, 카운트다운 숫자, hook, cta punch, index |
| `Pretendard-Bold.otf` | 700 | 질문, kicker, meta, cta tail |
| `Pretendard-Medium.otf` | 500 | 해설 자막 |
| `OFL.txt` | — | 라이선스 원문 (릴리스의 `LICENSE.txt` 그대로) |

- 출처: <https://github.com/orioncactus/pretendard> — 릴리스 `v1.3.9`의 `Pretendard-1.3.9.zip`
- 저작권자: Kil Hyung-jin, Reserved Font Name **Pretendard**

## 라이선스 — SIL Open Font License 1.1

`OFL.txt`가 원문이고, 이 번들에 필요한 조항은 두 개다.

- **동봉 재배포와 임베딩이 허용된다.** PERMISSION & CONDITIONS가 "to use, study, copy, merge,
  **embed**, modify, redistribute, and sell"을 허가하고, 조항 2가 "may be **bundled**,
  redistributed and/or sold with any software"로 소프트웨어 동봉을 명시한다. 저장소 clone과
  데스크톱 앱 동봉(#25 이후)이 모두 이 안이다. 조항 1이 막는 것은 **폰트 단독 판매**이고
  이 프로젝트가 하는 일이 아니다.
- **저작권 표시와 라이선스 원문을 함께 담아야 한다** — 조항 2의 "provided that each copy
  contains the above copyright notice and this license". 그래서 `OFL.txt`가 폰트와 같은
  디렉터리에 있다. **폰트 파일만 다른 곳으로 복사하지 말 것** — 앱 패키징도 이 디렉터리를
  통째로 담는다. 조항 5가 폰트 자체를 다른 라이선스로 재배포하는 것을 막지만, "does not apply
  to any document created using the Font Software"이므로 렌더 결과물(`final_short.mp4`)에는
  걸리지 않는다.
- **이름 제약**: 폰트를 수정해 배포할 때만 걸린다 (조항 3, Reserved Font Name). 여기서는
  파일을 그대로 쓰므로 해당하지 않는다.

표기 문구는 의무가 아니지만, 크레딧을 넣는다면 아래를 쓴다.

```text
Pretendard by Kil Hyung-jin, licensed under SIL Open Font License 1.1.
```

임베딩 허용은 라이선스 문서 밖에서도 확인된다 — 세 파일 모두 `OS/2` 테이블의 `fsType`이
**0**(Installable Embedding)이다. PDF·영상에 글리프를 심는 것을 폰트 자체가 막지 않는다.

## 왜 맑은 고딕을 쓰지 않는가

개발 환경에서 `C:/Windows/Fonts/malgun.ttf`로 한국어 렌더가 되는 것은 확인됐지만(#17),
Windows 동봉 폰트는 **재배포가 금지된다.** 저장소에 커밋할 수도 없고 앱에 담을 수도 없어서,
그 경로로 렌더를 맞춰 두면 배포 시점에 폰트를 갈아야 하고 그때 레이아웃 수치(#20~#22가
실측으로 맞춘 값)가 전부 흔들린다. PRD 8장이 번들 에셋에 요구하는 조건과도 어긋난다.

## 왜 `.ttf`가 아니라 `.otf`인가

확정 스펙 9장은 파일명을 `Pretendard-*.ttf`로 적었지만 **릴리스의 기본 정적 세트는
`public/static/*.otf`이고, `.ttf`는 `public/static/alternative/`에 있다.** 두 세트를 대조한
결과 골라야 할 차이가 없었다.

| | `public/static/*.otf` | `public/static/alternative/*.ttf` |
| --- | --- | --- |
| 아웃라인 | CFF | glyf |
| 파일 크기 (Bold) | 1,576,660 B | 2,661,752 B |
| `name` 테이블 | 동일 (`Pretendard` / `Bold`) | 동일 |
| `usWeightClass` · `fsType` | 700 · 0 | 700 · 0 |
| 수직 메트릭 (`hhea` asc/desc/gap) | 1950 / -494 / 0 | 동일 |
| `unitsPerEm` | 2048 | 2048 |
| 한글 음절 커버리지 | 11,172자 전체 | 11,172자 전체 |
| `drawtext` 한국어 렌더 | 정상 | 정상 |

메트릭이 같으므로 확정 스펙 2.4의 `line_spacing` 역산 결과도 갈리지 않는다. 웨이트 3개에서
**3.3MB가 작은** 쪽을 골랐다. 스펙 9장이 금지한 것은 가변 폰트(`PretendardVariable.ttf`)이고,
그 이유는 `drawtext`가 웨이트를 고를 수 없어 **파일 1개 = 웨이트 1개**여야 한다는 것이다 —
정적 OTF도 그 조건을 만족한다.

## 갱신

```bash
gh release download v<버전> --repo orioncactus/pretendard --pattern 'Pretendard-*.zip'
```

zip에서 `public/static/Pretendard-{ExtraBold,Bold,Medium}.otf`와 `LICENSE.txt`(→ `OFL.txt`)를
꺼내 이 디렉터리에 덮어쓴다. 웨이트·커버리지·`fsType`·한국어 렌더는 `tests/test_visual_assets.py`
가 커밋된 파일에서 매번 다시 확인하므로, 잘못된 파일로 교체하면 그 테스트가 걸린다.
