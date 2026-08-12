// 우측 속성 패널 340px — **두 구획으로 나뉜다** (D2 확정 스펙 1.5, 이슈 #27).
//
// 시안은 선택된 장면의 역할 표시 바로 아래에 자막 스타일·배경·볼륨을 놓았는데, 그 셋은
// 장면 속성이 아니라 `project.json`의 프로젝트 전역 값이다. 역할 표시 밑에 두면 "이 장면의
// 자막 스타일"로 읽힌다. 장면에 따라 달라질 수 있는 것은 길이 하나뿐이다.
//
// **이 이슈에서는 자리와 읽기 전용 표시까지다.** 편집 컨트롤은 #29(자막 스타일·배경·길이·
// 볼륨)와 #28(문제)이 같은 구획 안에 들어온다.

import type { ReactNode } from 'react'

import type { Project, Scene } from '../protocol'
import { seconds } from '../scenes'

export function Properties ({ project, scene, index, runDir }: {
  project: Project
  scene: Scene | null
  index: number | null
  runDir: string
}) {
  return (
    <aside className="panel panel--properties" data-testid="properties">
      <div className="panel__head">
        <span className="t-heading">속성</span>
      </div>
      <div className="panel__scroll">
        <Section title="장면" hint="선택한 장면에만 적용된다" testid="properties-scene">
          {scene && index !== null
            ? <SceneFields scene={scene} index={index} />
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

function SceneFields ({ scene, index }: { scene: Scene, index: number }) {
  return (
    <>
      <Field label="번호" value={String(index)} />
      <Field label="역할" value={scene.role} />
      <Field label="길이" value={seconds(scene.duration)} />
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
