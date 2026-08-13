// 우측 속성 패널 340px — **두 구획으로 나뉜다** (D2 확정 스펙 1.5, 이슈 #27).
//
// 시안은 선택된 장면의 역할 표시 바로 아래에 자막 스타일·배경·볼륨을 놓았는데, 그 셋은
// 장면 속성이 아니라 `project.json`의 프로젝트 전역 값이다. 역할 표시 밑에 두면 "이 장면의
// 자막 스타일"로 읽힌다. 장면에 따라 달라질 수 있는 것은 길이 하나뿐이다.
//
// **장면 길이 편집이 #82에서, 자막 스타일·배경 프리셋 교체가 #79에서, 배경 사용자 파일이
// #80에서 들어왔다.** 나머지 편집 컨트롤은 #81과 #83이 같은 구획 안에 들어온다.

import { useEffect, useState, type ReactNode } from 'react'

import type { BackgroundReject } from '../background'
import type {
  BackgroundFileFormat, CaptionStylePreset, PresetsResult, Project, Scene
} from '../protocol'
import { cutsNarration, FIXED_DURATION_ROLES, narrationLength, seconds } from '../scenes'
import { Notice } from './Notice'

export function Properties ({
  project, scene, index, runDir, presets,
  onDuration, onStyle, onBackground, onPickBackground, backgroundReject
}: {
  project: Project
  scene: Scene | null
  index: number | null
  runDir: string
  /** 번들 프리셋 목록 (#79). `null`이면 백엔드가 `assets/`를 읽지 못한 것이다. */
  presets: PresetsResult | null
  /** 장면 길이 조정 (#82). `null`이면 읽기 전용 표시만 남는다. */
  onDuration: ((scene: Scene, duration: number) => void) | null
  onStyle: (style: CaptionStylePreset) => void
  onBackground: (name: string) => void
  /** 배경 사용자 파일 고르기 (#80). 대화상자는 main이 연다. */
  onPickBackground: () => void
  /** 받지 않는 형식을 골랐을 때만 값이 있다 (#80). */
  backgroundReject: BackgroundReject | null
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
          {presets
            ? (
              <PresetFields
                project={project}
                presets={presets}
                onStyle={onStyle}
                onBackground={onBackground}
                onPickBackground={onPickBackground}
                backgroundReject={backgroundReject}
              />
              )
            : (
              <>
                {/* 프리셋 목록을 읽지 못하면 고를 것이 없다. **값은 그대로 보인다** —
                    나머지 편집을 막을 이유가 없다 (`api.method_presets`의 `assets` 실패). */}
                <Field label="자막 스타일" value={project.render.caption_style}
                  hint="프리셋 목록을 읽지 못해 값만 보인다" />
                <Field label="배경" value={`${project.background.kind} · ${project.background.value}`} />
              </>
              )}
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
        볼륨(#81)·자막 문구(#83)는 다음 단계에서 이 자리에 들어온다.
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

/**
 * 자막 스타일과 배경 프리셋 교체 (#79).
 *
 * **이름도 색도 이 파일에 없다.** 목록은 백엔드(`api.method_presets`)가 `assets/`에서 읽어
 * 보내고, 동결 배포에서 그 디렉터리는 백엔드 실행 파일 옆이라 앱이 직접 읽을 수 없다.
 * 이름을 여기 적으면 프리셋을 하나 더할 때 화면의 목록과 렌더가 아는 목록이 갈린다.
 *
 * **둘은 독립이다** — 스타일이 배경을 끌고 오지만(기본 짝) 그 뒤 배경만 다시 고를 수 있다.
 * 차단할 조합이 없다 (D1 확정 스펙 6.3의 매트릭스).
 */
function PresetFields ({
  project, presets, onStyle, onBackground, onPickBackground, backgroundReject
}: {
  project: Project
  presets: PresetsResult
  onStyle: (style: CaptionStylePreset) => void
  onBackground: (name: string) => void
  onPickBackground: () => void
  backgroundReject: BackgroundReject | null
}) {
  const backgrounds = new Map(presets.backgrounds.map((preset) => [preset.name, preset]))
  // 프리셋이 아닌 배경(사용자 파일 #80, 색)은 프리셋 이름으로 표현되지 않는다. 그때는
  // 스타일을 골라도 배경이 따라가지 않고(`App.editStyle`) 기본 배경 문구도 띄우지 않는다 —
  // 따라가지 않는데 무엇으로 바뀐다고 적으면 그것이 거짓말이 된다.
  const isPreset = project.background.kind === 'preset'

  return (
    <>
      <PresetList
        label="자막 스타일"
        testid="caption-style"
        selected={project.render.caption_style}
        options={presets.caption_styles.map((style) => {
          const paired = backgrounds.get(style.background)
          return {
            name: style.name,
            label: style.label,
            // **무엇으로 바뀌는지 적는다.** 고르면 배경이 함께 가므로, 보이지 않으면 배경이
            // 저절로 달라진 것으로 읽힌다 (확정 스펙 1.1).
            hint: isPreset ? `기본 배경 ${paired?.label ?? style.background}` : undefined,
            swatch: (
              <Swatch
                stops={paired?.stops}
                glyph={style.colors.accent}
                outline={style.colors.border}
              />
            )
          }
        })}
        onPick={(name) => {
          const target = presets.caption_styles.find((style) => style.name === name)
          if (target) onStyle(target)
        }}
      />
      {/* **목록은 배경이 파일일 때도 그린다** (#80). 사용자 파일에서 프리셋으로 돌아오는 길이
          여기뿐이라, 숨기면 앱에서 넣은 배경을 앱에서 되돌릴 수 없다. 그때는 고른 카드가
          없을 뿐이다. */}
      <PresetList
        label="배경"
        testid="background"
        selected={isPreset ? project.background.value : ''}
        options={presets.backgrounds.map((preset) => ({
          name: preset.name,
          label: preset.label,
          swatch: <Swatch stops={preset.stops} />
        }))}
        onPick={onBackground}
      />
      <BackgroundFile
        background={project.background}
        formats={presets.background_files ?? []}
        onPick={onPickBackground}
        reject={backgroundReject}
      />
    </>
  )
}

/**
 * 배경 사용자 파일 (#80).
 *
 * **받는 형식을 앱이 정하지 않는다.** 목록은 백엔드가 보내고(`presets.background_files`,
 * 출처는 `video_renderer.BACKGROUND_FILE_KINDS`) 이 컴포넌트는 그것을 화면에 옮길 뿐이다 —
 * 앱과 렌더러가 각자 목록을 들면 앱이 받은 파일을 렌더가 거부할 수 있다 (PRD 14.1).
 *
 * 거부는 `danger`다. 낭독보다 짧은 길이(#82)는 값이 적용되는 `warn`이고 이쪽은 값이 적용되지
 * 않으므로, 두 상황에 같은 표시를 쓰면 무엇이 반영됐는지가 화면에서 갈리지 않는다
 * (확정 스펙 4장).
 */
function BackgroundFile ({ background, formats, onPick, reject }: {
  background: Project['background']
  formats: BackgroundFileFormat[]
  onPick: () => void
  reject: BackgroundReject | null
}) {
  const accepted = formats.map((format) => format.extension).join(' · ')
  const isFile = background.kind === 'image' || background.kind === 'video'
  return (
    <div className="bgfile" data-control="배경 파일">
      <div className="bgfile__row">
        <button
          type="button"
          className="button"
          data-testid="pick-background"
          onClick={onPick}
          // 형식 목록을 받지 못한 백엔드 세대에서는 무엇을 받는지 말할 수 없다. 고르게 두면
          // 화면이 아니라 렌더에서 걸린다.
          disabled={formats.length === 0}
        >
          파일 고르기…
        </button>
        <span className="t-caption">{accepted || '받는 형식을 읽지 못했다'}</span>
      </div>
      {isFile && (
        // **경로를 그대로 보여 준다.** run 디렉터리로 복사하지 않으므로 지금 어느 파일을
        // 가리키는지가 화면에 없으면, 그 파일이 사라졌을 때 원인이 프리뷰 실패에만 남는다.
        <div className="bgfile__current" data-testid="background-file">
          <span className="t-label">{background.kind}</span>
          <span className="mono bgfile__path" title={background.value}>{background.value}</span>
        </div>
      )}
      {reject && (
        <Notice kind="danger" title="받지 않는 형식이다" testid="notice-background-reject">
          {reject.path} — 배경은 그대로 두었다. 받는 형식: {reject.accepted.join(' · ')}
        </Notice>
      )}
    </div>
  )
}

interface PresetOption {
  name: string
  label: string
  hint?: string
  swatch: ReactNode
}

/** 프리셋 목록 — 견본이 있어야 이름만으로 고르지 않는다 (확정 스펙 2.1의 선택된 카드). */
function PresetList ({ label, testid, selected, options, onPick }: {
  label: string
  testid: string
  selected: string
  options: PresetOption[]
  onPick: (name: string) => void
}) {
  return (
    <div className="presets" data-control={label}>
      <div className="t-label">{label}</div>
      <div className="presets__list" data-testid={`presets-${testid}`} role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.name}
            type="button"
            className={`preset${option.name === selected ? ' preset--on' : ''}`}
            data-value={option.name}
            data-selected={option.name === selected}
            role="radio"
            aria-checked={option.name === selected}
            onClick={() => onPick(option.name)}
          >
            {option.swatch}
            <span className="preset__text">
              <span className="preset__label">{option.label}</span>
              {option.hint && <span className="t-caption">{option.hint}</span>}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

/**
 * 프리셋 견본. **색은 인라인 style로 들어간다** — 백엔드가 보낸 값이라 CSS에 적을 수 없다.
 *
 * `stops`가 1개면 단색, 2개면 위→아래 그라디언트다 (D1 확정 스펙 6.2). `glyph`가 있으면
 * 그 배경 위의 강조색과 외곽선을 함께 보여 준다 — 이 디자인에서 외곽선이 지배적 요소라
 * (확정 스펙 2.1) 강조색만으로는 스타일이 구분되지 않는다.
 */
function Swatch ({ stops, glyph, outline }: {
  stops?: string[]
  glyph?: string
  outline?: string
}) {
  const fill = stops === undefined || stops.length === 0
    ? undefined
    : stops.length === 1 ? stops[0] : `linear-gradient(${stops[0]}, ${stops[stops.length - 1]})`
  return (
    <span className="preset__swatch" style={{ background: fill }} aria-hidden="true">
      {glyph !== undefined && (
        <span
          className="preset__glyph"
          style={{ color: glyph, WebkitTextStroke: outline === undefined ? undefined : `1.5px ${outline}` }}
        >
          가
        </span>
      )}
    </span>
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
function Field ({ label, value, prose, hint }: {
  label: string
  value: string
  prose?: boolean
  /** 값이 왜 읽기 전용인지처럼 값만으로는 알 수 없는 것. 11px는 `text/2`다 (확정 스펙 1.7). */
  hint?: string
}) {
  return (
    <div className="field" data-field={label}>
      <div className="t-label field__label">{label}</div>
      <div className={prose ? 'field__value field__value--prose' : 'field__value mono'}>
        {value}
        {hint && <div className="t-caption field__hint">{hint}</div>}
      </div>
    </div>
  )
}
