// 타입 전용 편집기 레지스트리 (이슈 #28).
//
// **셸은 콘텐츠의 필드를 모른다.** `quiz.json`의 `question` · `answer` · `explanation`을
// 아는 것은 아래에 등록된 모듈뿐이고, 셸은 `ContentItem` 다섯 칸과 폼 컴포넌트만 본다 —
// 퀴즈 스펙 1.1이 그은 선이 앱 쪽에서 이 파일이다. 백엔드도 같은 모양이다: `api.py`는
// 파일명과 검증을 레지스트리(`ShortsType.content_schema`)에서 받는다.
//
// **등록되지 않은 타입은 편집 없이 읽기 전용이다.** 동결 배포에서 앱과 백엔드의 세대가
// 갈릴 수 있으므로(스파이크 5.1) 모르는 타입이 화면을 막지 않아야 한다.

import type { ReactElement } from 'react'

import type { Content } from '../protocol'
import { quiz } from './quiz'

/** 확정 스펙 4장의 세 상태. 색·아이콘·문구 셋으로 갈린다. */
export type VerifyStatus = 'verified' | 'flagged' | 'unverified'

/** 편집 단위 하나를 셸이 읽는 모양. */
export interface ContentItem {
  /** `scenes.json`의 `question_id`와 같은 값. 확인 기록과 낡음 표시가 이것을 가리킨다. */
  id: number
  /** 목록 행의 제목. */
  title: string
  status: VerifyStatus
  /** `unverified`는 값이 없다 — 자리에 `—`를 쓴다 (확정 스펙 4장). */
  confidence: number | null
  /** 검증 근거. 검증기가 대지 못한 경우가 있어 없을 수 있다. */
  source: string | null
  /** 목록 행 오른쪽의 짧은 값 (퀴즈는 난이도). */
  tag: string
  /**
   * **낭독으로 가는** 문구 전부를 이어 붙인 값.
   *
   * 이것이 바뀌면 **음성까지 낡는다** — `voice.mp3`를 다시 만들어야 한다. 어느 필드가 낭독으로
   * 가는지는 타입의 지식이라 셸이 알지 않는다 (`narrate: true` 장면의 `text`만 TTS를 지난다) —
   * 셸은 값이 달라졌는지만 본다.
   */
  narration: string
  /**
   * **자막에 가는** 문구 전부를 이어 붙인 값 (#83).
   *
   * `narration`을 포함한다 — 낭독 문구는 자막에도 나오기 때문이다. 이것만 바뀌면
   * **자막만 낡는다**: 음성은 그대로 쓰고 `captions.srt`만 다시 만들면 된다 (확정 스펙 7.3).
   *
   * **두 값을 나눈 것이 #83이다.** 낡음이 한 종류일 때는 해설이 `narration`에 섞여 있어도
   * 결과가 같았지만, 갈린 뒤에는 해설만 고친 경우가 "음성까지 낡음"으로 잘못 표시된다.
   */
  captions: string
}

export interface EditorProps {
  content: Content
  item: ContentItem
  acknowledged: boolean
  /** 음성까지 낡았다 (#83). */
  stale: boolean
  /** 자막만 낡았다 (#83). 둘이 함께 참일 수 있고 그때는 강한 쪽만 그린다. */
  captionsStale: boolean
  onChange: (next: Content) => void
  onAcknowledge: () => void
}

export interface ContentModule {
  /** 콘텐츠 → 편집 단위 목록. **순서가 곧 장면 순서다.** */
  items: (content: Content) => ContentItem[]
  /** 항목을 `delta`만큼 옮긴 콘텐츠. 범위를 벗어나면 그대로 돌려준다. */
  move: (content: Content, id: number, delta: number) => Content
  /**
   * 항목을 끝에 하나 더한 콘텐츠와 그 `id`.
   *
   * **`reserved`의 번호를 새 항목에 주지 않는다.** 콘텐츠에서 지웠어도 `scenes.json`이
   * 아직 그 번호로 장면을 들고 있으면, 새 항목이 그 번호를 가져가는 순간 옛 장면들이 새
   * 항목의 것으로 읽힌다 — 검증 배지도 재생성 표시도 번호를 따라가기 때문이다.
   */
  add: (content: Content, reserved: number[]) => { content: Content, id: number }
  remove: (content: Content, id: number) => Content
  Editor: (props: EditorProps) => ReactElement
}

const MODULES: Record<string, ContentModule> = { quiz }

export function contentModule (type: string): ContentModule | null {
  return MODULES[type] ?? null
}
