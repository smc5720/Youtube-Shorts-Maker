// 우측 속성 패널 340px — **두 구획으로 나뉜다** (D2 확정 스펙 1.5, 이슈 #27).
//
// 시안은 선택된 장면의 역할 표시 바로 아래에 자막 스타일·배경·볼륨을 놓았는데, 그 셋은
// 장면 속성이 아니라 `project.json`의 프로젝트 전역 값이다. 역할 표시 밑에 두면 "이 장면의
// 자막 스타일"로 읽힌다. 장면에 따라 달라질 수 있는 것은 길이 하나뿐이다.
//
// **장면 길이 편집이 #82에서 들어왔다.** 나머지 편집 컨트롤은 #79~#81과 #83이 같은 구획 안에
// 들어온다.

import { useEffect, useState, type ReactNode } from 'react'

import type { Project, Scene } from '../protocol'
import { cutsNarration, FIXED_DURATION_ROLES, narrationLength, seconds } from '../scenes'
import { Notice } from './Notice'

export function Properties ({ project, scene, index, runDir, onDuration }: {
  project: Project
  scene: Scene | null
  index: number | null
  runDir: string
  /** 장면 길이 조정 (#82). `null`이면 읽기 전용 표시만 남는다. */
  onDuration: ((scene: Scene, duration: number) => void) | null
}) {
  return (
    <aside className="panel panel--properties" data-testid="properties">
      <div className="panel__head">
        <span className="t-heading">속성</span>
      </div>
      <div className="panel__scroll">
        <Section title="장면" hint="선택한 장면에만 적용된다" testid="properties-scene">
          {scene && index !== null
            ? <SceneFields scene={scene} index={index} onDuration={onDuration} />
            : <div className="t-caption">왼쪽에서 장면을 고른다.</div>}
        </Section>

        {/* **전역임을 머리글이 말한다.** 구획을 나누기만 하고 이름을 붙이지 않으면 경계가
            보이지 않는다 (확정 스펙 1.5). */}
        <Section title="프로젝트 전체" hint="모든 장면에 함께 적용된다" testid="properties-project">
          <Field label="자막 스타일" value={project.render.caption_style} />
          <Field label="배경" value={`${project.background.kind} · ${project.background.value}`} />
          <Field label="효과음 게인" value={String(project.audio.sfx_volume)} />
          <Field label="낭독 트랙" value={project.audio.voice ?? '(없음)'} />
          <Field label="영상 규격" value={`${project.render.width}x${project.render.height} · ${project.render.fps}fps`} />
          <Field label="출력 파일" value={project.render.output} />
          <Field label="cta 문구" value={`${project.render.cta_punch} / ${project.render.cta_tail}`} />
          <Field label="타입" value={project.type} />
          <Field label="run 디렉터리" value={runDir} />
        </Section>
      </div>
      <div className="panel__foot t-caption">
        값 편집은 다음 단계에서 이 자리에 들어온다.
      </div>
    </aside>
  )
}

function SceneFields ({ scene, index, onDuration }: {
  scene: Scene
  index: number
  onDuration: ((scene: Scene, duration: number) => void) | null
}) {
  // **`countdown`은 고칠 수 없다.** `duration`이 `seconds`와 같아야 하고 그 값은 콘텐츠
  // 필드라 문제 편집(#28)이 소유한다 (확정 스펙 7.1). 값은 보이지만 컨트롤이 없다.
  const fixed = FIXED_DURATION_ROLES.includes(scene.role)
  return (
    <>
      <Field label="번호" value={String(index)} />
      <Field label="역할" value={scene.role} />
      {onDuration && !fixed
        ? <DurationField scene={scene} onDuration={onDuration} />
        : <Field label="길이" value={seconds(scene.duration)} />}
      {scene.kicker !== undefined && <Field label="라벨" value={scene.kicker} prose />}
      {scene.heading !== undefined && <Field label="상단 문구" value={scene.heading} prose />}
      {scene.text !== undefined && <Field label="문구" value={scene.text} prose />}
      {scene.caption !== undefined && <Field label="자막" value={scene.caption} prose />}
      {scene.seconds !== undefined && <Field label="세는 초" value={String(scene.seconds)} />}
      {scene.sfx !== undefined && <Field label="효과음" value={scene.sfx} />}
      {/* 세그먼트는 **장면 인덱스로** 매긴다 — 역할 이름이 아니다 (확정 스펙 1.3). */}
      {scene.audio !== undefined && <Field label="낭독 오디오" value={scene.audio} />}
      {scene.audio_duration !== undefined && (
        <Field label="낭독 길이" value={seconds(scene.audio_duration)} />
      )}
    </>
  )
}

/**
 * 장면 길이 입력 (#82).
 *
 * **낭독보다 짧아도 값을 받는다** — 경고하고 사람이 결정한다 (확정 스펙 4장: `warn`은 값이
 * 적용되는 경고이고, 값을 되돌리는 것은 `danger`다). 그래서 이 값은 `scenes.json`에 쓸 수
 * 없다 — `validate_scenes_final`이 거부한다 (PRD 14.1).
 *
 * 입력 중인 문자열을 따로 든다. `"3."`이나 빈 문자열을 프로젝트에 올리면 스키마를 어긴 상태로
 * 저장을 누를 수 있게 되고, 그때 실패하는 것은 저장 전체다 (`NumberInput`과 같은 이유다).
 */
function DurationField ({ scene, onDuration }: {
  scene: Scene
  onDuration: (scene: Scene, duration: number) => void
}) {
  const [draft, setDraft] = useState(scene.duration.toFixed(1))
  // 값이 밖에서 바뀌면(장면 이동) 입력이 따라간다. **같은 수를 가리키는 동안은 입력한 글자를
  // 그대로 둔다** — `toFixed(1)`로 다시 쓰면 `3.25`를 입력하는 순간 `3.3`으로 바뀌어 사용자가
  // 소수 둘째 자리를 넣을 수 없다.
  useEffect(() => {
    setDraft((current) =>
      Number.parseFloat(current) === scene.duration ? current : scene.duration.toFixed(1))
  }, [scene.duration, scene.role, scene.question_id])

  const narration = narrationLength(scene)
  const cuts = cutsNarration(scene, scene.duration)
  return (
    <div className="field" data-field="길이">
      <div className="t-label field__label">길이</div>
      <div className="duration">
        <div className="duration__row">
          <span className={`input input--number${cuts ? ' input--warn' : ''}`}>
            <input
              type="number"
              data-testid="input-길이"
              value={draft}
              min={0.1}
              step={0.1}
              onChange={(event) => {
                setDraft(event.target.value)
                const next = Number.parseFloat(event.target.value)
                if (Number.isFinite(next) && next > 0) onDuration(scene, next)
              }}
            />
            <span className="t-caption">초</span>
          </span>
          {/* **낭독이 없는 장면은 `—`다.** `hook`·`cta`·`countdown`에는 `audio_duration`이
              없고, 없는 값을 근거로 경고를 띄우면 고칠 것이 없는 사용자가 경고를 본다
              (확정 스펙 7.1). */}
          <span className="t-caption mono">
            낭독 {narration === null ? '—' : `${narration.toFixed(1)}초`}
          </span>
        </div>
        {cuts && narration !== null && (
          <Notice kind="warn" title="낭독보다 짧다" testid="notice-duration-warn">
            낭독 {seconds(narration)}보다 짧아 문장 끝이 잘린다. 값은 적용됐다.
          </Notice>
        )}
      </div>
    </div>
  )
}

function Section ({ title, hint, testid, children }: {
  title: string
  hint: string
  testid: string
  children: ReactNode
}) {
  return (
    <section className="section" data-testid={testid}>
      <div className="section__head">
        <span className="t-label section__title">{title}</span>
        <span className="t-caption">{hint}</span>
      </div>
      <div className="section__body">{children}</div>
    </section>
  )
}

/** 기계가 만든 값은 mono, 사람이 읽는 문장은 본문 서체다 (확정 스펙 2.2). */
function Field ({ label, value, prose }: { label: string, value: string, prose?: boolean }) {
  return (
    <div className="field" data-field={label}>
      <div className="t-label field__label">{label}</div>
      <div className={prose ? 'field__value field__value--prose' : 'field__value mono'}>{value}</div>
    </div>
  )
}
