// 우측 속성 패널 384px — **두 구획으로 나뉜다** (D2 확정 스펙 1.5·7.4, 이슈 #27).
//
// 시안은 선택된 장면의 역할 표시 바로 아래에 자막 스타일·배경·볼륨을 놓았는데, 그 셋은
// 장면 속성이 아니라 `project.json`의 프로젝트 전역 값이다. 역할 표시 밑에 두면 "이 장면의
// 자막 스타일"로 읽힌다. 장면에 따라 달라질 수 있는 것은 길이·자막 문구·오버레이다.
//
// **장면 길이 편집이 #82에서, 자막 스타일·배경 프리셋 교체가 #79에서, 배경 사용자 파일이
// #80에서, 트랙 볼륨이 #81에서, 자막 문구와 텍스트 오버레이가 #83에서 들어왔다.**

import { useEffect, useState, type ReactNode } from 'react'

import type { BackgroundReject } from '../background'
import {
  TIMING_SCENE,
  voiceVolume,
  type BackgroundFileFormat, type CaptionStylePreset, type Overlay, type OverlayContract,
  type PresetsResult, type Project, type Scene
} from '../protocol'
import { cutsNarration, FIXED_DURATION_ROLES, narrationLength, overlaysFor, seconds } from '../scenes'
import { Icon } from './Icon'
import { Notice } from './Notice'
import { StaleCard } from './Stale'

/** 오버레이 편집 콜백 묶음 (#83). 값이 없으면 그 컨트롤이 열리지 않는다. */
export interface OverlayEdits {
  /** 계약 목록. 백엔드가 보낸다 — 앱은 이름도 웨이트도 적지 않는다. */
  contract: OverlayContract
  onAdd: (scene: Scene) => void
  onChange: (scene: Scene, id: string, fields: Partial<Overlay>) => void
  onRemove: (scene: Scene, id: string) => void
}

export function Properties ({
  project, scene, index, runDir, presets, captionEdited, audioStale, locked,
  onDuration, onCaption, onRevertCaption, overlays,
  onVolume, onStyle, onBackground, onPickBackground, backgroundReject
}: {
  project: Project
  scene: Scene | null
  index: number | null
  runDir: string
  /** 번들 프리셋 목록 (#79). `null`이면 백엔드가 `assets/`를 읽지 못한 것이다. */
  presets: PresetsResult | null
  /** 고른 장면의 자막 문구가 고쳐졌는가 (#83). 계산된 값이고 파일에 적지 않는다. */
  captionEdited: boolean
  /** 고른 장면이 속한 문제의 낭독이 낡았는가 (#83). **S2에서는 읽기 전용이다.** */
  audioStale: boolean
  /** 장면 길이 조정 (#82). `null`이면 읽기 전용 표시만 남는다. */
  onDuration: ((scene: Scene, duration: number) => void) | null
  /** 자막 문구 편집 (#83). 빈 값은 올리지 않는다 — 되돌리는 것은 `onRevertCaption`이다. */
  onCaption: (scene: Scene, text: string) => void
  onRevertCaption: (scene: Scene) => void
  /** 텍스트 오버레이 (#83). `null`이면 계약 목록을 받지 못한 것이다. */
  overlays: OverlayEdits | null
  /** 트랙 볼륨 (#81). 받는 값은 눈금이 아니라 **선형 게인**이다. */
  onVolume: (track: 'voice_volume' | 'sfx_volume', gain: number) => void
  onStyle: (style: CaptionStylePreset) => void
  onBackground: (name: string) => void
  /** 배경 사용자 파일 고르기 (#80). 대화상자는 main이 연다. */
  onPickBackground: () => void
  /** 받지 않는 형식을 골랐을 때만 값이 있다 (#80). */
  backgroundReject: BackgroundReject | null
  /**
   * 렌더가 도는 중인가 (#30). **표시일 뿐이고 판정은 `App.patchProject`에 있다** — 화면에서만
   * 막으면 스모크와 메뉴가 그 잠금을 지나간다 (확정 스펙 3.3).
   */
  locked: boolean
}) {
  // 오버레이 색 견본이 쓰는 값 — **지금 고른 자막 스타일의 색이다.** `color: preset`이 값을
  // 복사하지 않으므로(확정 스펙 7.2) 스타일을 바꾸면 견본도 함께 바뀐다.
  const styleColors = presets?.caption_styles
    .find((style) => style.name === project.render.caption_style)?.colors ?? {}

  return (
    <aside
      className={`panel panel--properties${locked ? ' panel--locked' : ''}`}
      data-testid="properties"
      data-locked={locked}
    >
      <div className="panel__head">
        <span className="t-heading">속성</span>
      </div>
      <div className="panel__scroll">
        <Section title="장면" hint="선택한 장면에만 적용된다" testid="properties-scene">
          {scene && index !== null
            ? (
              <SceneFields
                project={project}
                scene={scene}
                index={index}
                captionEdited={captionEdited}
                audioStale={audioStale}
                onDuration={onDuration}
                onCaption={onCaption}
                onRevertCaption={onRevertCaption}
                overlays={overlays}
                styleColors={styleColors}
              />
              )
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
          {/* 볼륨 둘도 전역이다 (#81, 확정 스펙 1.5) — 장면마다 다를 수 있는 것은 길이뿐이다. */}
          <VolumeField
            label="낭독 볼륨"
            testid="voice"
            gain={voiceVolume(project)}
            onChange={(gain) => onVolume('voice_volume', gain)}
          />
          <VolumeField
            label="효과음 볼륨"
            testid="sfx"
            gain={project.audio.sfx_volume}
            onChange={(gain) => onVolume('sfx_volume', gain)}
            zeroHint="효과음 없이 렌더한다"
          />
          <Field label="낭독 트랙" value={project.audio.voice ?? '(없음)'} />
          <Field label="영상 규격" value={`${project.render.width}x${project.render.height} · ${project.render.fps}fps`} />
          <Field label="출력 파일" value={project.render.output} />
          <Field label="cta 문구" value={`${project.render.cta_punch} / ${project.render.cta_tail}`} />
          <Field label="타입" value={project.type} />
          <Field label="run 디렉터리" value={runDir} />
        </Section>
      </div>
    </aside>
  )
}

function SceneFields ({
  project, scene, index, captionEdited, audioStale,
  onDuration, onCaption, onRevertCaption, overlays, styleColors
}: {
  project: Project
  scene: Scene
  index: number
  captionEdited: boolean
  audioStale: boolean
  onDuration: ((scene: Scene, duration: number) => void) | null
  onCaption: (scene: Scene, text: string) => void
  onRevertCaption: (scene: Scene) => void
  overlays: OverlayEdits | null
  styleColors: Record<string, string>
}) {
  // **`countdown`은 고칠 수 없다.** `duration`이 `seconds`와 같아야 하고 그 값은 콘텐츠
  // 필드라 문제 편집(#28)이 소유한다 (확정 스펙 7.1). 값은 보이지만 컨트롤이 없다.
  const fixed = FIXED_DURATION_ROLES.includes(scene.role)
  return (
    <>
      {/* **낡음 표시가 편집 칸보다 먼저다** (확정 스펙 3.2의 `flagged` 카드와 같은 자리) —
          무엇이 낡았는지 모른 채 타이핑하게 두지 않는다. 둘이 함께 걸릴 수 있다. */}
      {captionEdited && (
        <StaleCard kind="captions">
          {/* **동작은 #77의 몫이다.** 자리와 상태까지가 이 이슈이고, 누를 수 없는 버튼이
              "여기서 하게 될 일"을 말한다 — 없으면 다음 사람이 다른 자리에 만든다. */}
          <div className="stale-card__actions">
            <button type="button" className="button" data-testid="regenerate-captions" disabled>
              <Icon name="refresh" />
              자막 재생성
            </button>
            <span className="t-caption">재생성은 다음 단계(#77)에서 들어온다.</span>
          </div>
        </StaleCard>
      )}
      {/* **S2에서는 읽기 전용이다** — 재생성은 문제 편집에서 한다 (확정 스펙 7.3). */}
      {audioStale && <StaleCard kind="audio" />}

      {/* **장면 구획 최상단이다** (확정 스펙 7.3). 고치는 칸이 읽는 값보다 앞에 온다. */}
      {scene.text !== undefined && (
        <CaptionField
          scene={scene}
          edited={captionEdited}
          onCaption={onCaption}
          onRevert={onRevertCaption}
        />
      )}

      <Field label="번호" value={String(index)} />
      <Field label="역할" value={scene.role} />
      {onDuration && !fixed
        ? <DurationField scene={scene} onDuration={onDuration} />
        : <Field label="길이" value={seconds(scene.duration)} />}
      {scene.kicker !== undefined && <Field label="라벨" value={scene.kicker} prose />}
      {/* **상단 문구는 열지 않는다** — 한 문제의 세 장면이 같은 값을 공유하므로 한 장면에서
          고치면 나머지가 갈린다. 열려면 문제 단위 편집이라 S3의 몫이다 (확정 스펙 7.3). */}
      {scene.heading !== undefined && <Field label="상단 문구" value={scene.heading} prose />}
      {/* 해설도 콘텐츠 필드라 문제 편집(#28)이 소유한다. */}
      {scene.caption !== undefined && <Field label="자막" value={scene.caption} prose />}
      {scene.seconds !== undefined && <Field label="세는 초" value={String(scene.seconds)} />}
      {scene.sfx !== undefined && <Field label="효과음" value={scene.sfx} />}
      {/* 세그먼트는 **장면 인덱스로** 매긴다 — 역할 이름이 아니다 (확정 스펙 1.3). */}
      {scene.audio !== undefined && <Field label="낭독 오디오" value={scene.audio} />}
      {scene.audio_duration !== undefined && (
        <Field label="낭독 길이" value={seconds(scene.audio_duration)} />
      )}

      {overlays && (
        <Overlays
          project={project}
          scene={scene}
          edits={overlays}
          styleColors={styleColors}
        />
      )}
    </>
  )
}

/**
 * 자막 문구 편집 (#83).
 *
 * **장면의 `text` 한 칸이다** (확정 스펙 7.3). 값이 사는 자리는 `project.json`의
 * `render.scene_overrides`이고 `scenes.json`은 그대로다 — `captions.srt`가 낡는 것이 그
 * 결과이고, 그것을 다시 만드는 것은 재생성(#77)이다.
 *
 * **빈 문구를 올리지 않는다.** 스키마가 빈 문자열을 거부하므로(`text()`의 `allow_empty`)
 * 올리면 저장 전체가 실패한다 — 지우는 동안 직전 값이 적용된 채로 남고, 원래 문구로 돌아가는
 * 길은 되돌리기 버튼이다. 비운 상태를 `danger`로 그리는 것은 값이 적용되지 않았다는 뜻이고
 * (확정 스펙 4장) 값이 적용되는 `warn`(짧은 장면 길이)과 갈린다.
 */
function CaptionField ({ scene, edited, onCaption, onRevert }: {
  scene: Scene
  edited: boolean
  onCaption: (scene: Scene, text: string) => void
  onRevert: (scene: Scene) => void
}) {
  const current = scene.text ?? ''
  const [draft, setDraft] = useState(current)
  // 값이 밖에서 바뀌면(장면 이동, 되돌리기) 입력이 따라간다. 같은 값을 가리키는 동안은
  // 입력한 글자를 그대로 둔다 (`DurationField`와 같은 이유다).
  useEffect(() => {
    setDraft((typed) => (typed === current ? typed : current))
  }, [current, scene.role, scene.question_id])

  const blank = draft.trim() === ''
  return (
    <div className="capfield" data-control="자막 문구">
      <div className="capfield__head">
        <span className="t-label">자막 문구</span>
        {edited && (
          <button
            type="button"
            className="button button--quiet"
            data-testid="revert-caption"
            onClick={() => onRevert(scene)}
          >
            되돌리기
          </button>
        )}
      </div>
      <textarea
        className={`input input--area${blank ? ' input--danger' : ''}`}
        data-testid="input-자막 문구"
        rows={3}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value)
          if (event.target.value.trim() !== '') onCaption(scene, event.target.value)
        }}
      />
      <div className="t-caption">
        {blank
          ? '비운 값은 적용되지 않는다. 원래 문구로 돌아가려면 되돌리기를 누른다.'
          : '자막·번인 문구만 바뀌고 낭독 음성은 그대로다.'}
      </div>
    </div>
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
 * 텍스트 오버레이 목록 (#83, 확정 스펙 7.2).
 *
 * **후보 목록이 이 파일에 없다.** 9칸·색·크기·웨이트는 백엔드가 보낸 `contract`이고
 * (`api.method_presets`), 앱이 적으면 화면이 허용하는 값과 렌더가 아는 값이 갈린다 — 웨이트에서는
 * 그 어긋남이 **화면에는 보이지 않고 렌더에서만** 드러난다 (확정 스펙 7.1-2).
 *
 * **겹침 경고를 넣지 않는다** (확정 스펙 7.5). 자막 요소의 좌표는 렌더러의 표이므로 앱이
 * 판정하려면 그것을 복제해야 하고, 겹침은 프리뷰 정지 프레임에 그대로 보인다.
 */
function Overlays ({ project, scene, edits, styleColors }: {
  project: Project
  scene: Scene
  edits: OverlayEdits
  styleColors: Record<string, string>
}) {
  const list = overlaysFor(project, scene)
  const roles = new Map(edits.contract.colors.map((entry) => [entry.name, entry.role]))
  const colorOf = (name: string) => styleColors[roles.get(name) ?? '']

  return (
    <div className="overlays" data-control="텍스트 오버레이">
      <div className="overlays__head">
        <span className="t-label">텍스트 오버레이</span>
        <span className="mono t-caption">{list.length}</span>
        <button
          type="button"
          className="button"
          data-testid="overlay-add"
          onClick={() => edits.onAdd(scene)}
        >
          <Icon name="plus" />
          추가
        </button>
      </div>
      {list.length === 0
        ? <div className="t-caption">얹은 텍스트가 없다. 추가하면 이 장면에만 그려진다.</div>
        : (
          <div className="overlays__list" data-testid="overlay-list">
            {list.map((overlay, position) => (
              <OverlayCard
                key={overlay.id}
                overlay={overlay}
                position={position}
                scene={scene}
                contract={edits.contract}
                colorOf={colorOf}
                onChange={(fields) => edits.onChange(scene, overlay.id, fields)}
                onRemove={() => edits.onRemove(scene, overlay.id)}
              />
            ))}
          </div>
          )}
    </div>
  )
}

/**
 * 오버레이 하나.
 *
 * **삭제에 확인 단계가 있고 되돌리기가 없다** (확정 스펙 7.4). 확인 전에는 목록도 프리뷰도
 * 그대로다. 확인을 카드 안에서 받는 것은 모달이 뜨면 자동 실행(스모크)이 거기서 멈추기
 * 때문이고, 대화상자는 파일·저장처럼 OS에 물어야 하는 것에만 쓴다.
 */
function OverlayCard ({ overlay, position, scene, contract, colorOf, onChange, onRemove }: {
  overlay: Overlay
  position: number
  scene: Scene
  contract: OverlayContract
  colorOf: (name: string) => string | undefined
  onChange: (fields: Partial<Overlay>) => void
  onRemove: () => void
}) {
  const [draft, setDraft] = useState(overlay.text)
  const [confirming, setConfirming] = useState(false)
  useEffect(() => {
    setDraft((typed) => (typed === overlay.text ? typed : overlay.text))
  }, [overlay.id, overlay.text])

  const blank = draft.trim() === ''
  const window = typeof overlay.timing === 'object' ? overlay.timing : null
  return (
    <div className="overlay-card" data-testid="overlay-card" data-id={overlay.id}>
      <div className="overlay-card__head">
        <span className="mono overlay-card__number">#{position + 1}</span>
        {confirming
          ? (
            <span className="overlay-card__confirm">
              <span className="t-caption">지우면 되돌릴 수 없다.</span>
              <button
                type="button" className="button button--danger"
                data-testid="overlay-remove-confirm" onClick={onRemove}
              >
                삭제
              </button>
              <button
                type="button" className="button button--quiet"
                data-testid="overlay-remove-cancel" onClick={() => setConfirming(false)}
              >
                취소
              </button>
            </span>
            )
          : (
            <button
              type="button" className="button button--quiet"
              data-testid="overlay-remove" onClick={() => setConfirming(true)}
            >
              <Icon name="close" size={14} />
              삭제
            </button>
            )}
      </div>

      <textarea
        className={`input input--area${blank ? ' input--danger' : ''}`}
        data-testid="input-오버레이 문구"
        rows={2}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value)
          // 빈 문구는 스키마가 거부한다 — 올리면 저장 전체가 실패한다 (`CaptionField`와 같다).
          if (event.target.value.trim() !== '') onChange({ text: event.target.value })
        }}
      />
      {blank && <div className="t-caption">비운 값은 적용되지 않는다.</div>}

      <PositionGrid
        positions={contract.positions}
        selected={overlay.pos}
        onPick={(pos) => onChange({ pos })}
      />

      <div className="overlay-card__row">
        <IntField
          label="x"
          value={overlay.offset.x}
          onChange={(x) => onChange({ offset: { ...overlay.offset, x } })}
        />
        <IntField
          label="y"
          value={overlay.offset.y}
          onChange={(y) => onChange({ offset: { ...overlay.offset, y } })}
        />
        <span className="t-caption">고른 모서리에서 px</span>
      </div>

      <Choice
        label="색"
        testid="overlay-color"
        options={contract.colors.map((entry) => ({
          value: entry.name,
          text: entry.name,
          // 견본 색은 **지금 자막 스타일의 값**이다 — 스타일을 바꾸면 함께 바뀐다
          // (`color: preset`이 값을 복사하지 않는 이유, 확정 스펙 7.2).
          swatch: colorOf(entry.name)
        }))}
        value={overlay.color}
        onPick={(color) => onChange({ color })}
      />
      <Choice
        label="크기"
        testid="overlay-size"
        options={contract.sizes.map((size) => ({ value: String(size), text: `${size}px` }))}
        value={String(overlay.size)}
        onPick={(value) => onChange({ size: Number(value) })}
      />
      <Choice
        label="굵기"
        testid="overlay-weight"
        options={contract.weights.map((weight) => ({ value: String(weight), text: String(weight) }))}
        value={String(overlay.weight)}
        onPick={(value) => onChange({ weight: Number(value) })}
      />
      <Choice
        label="구간"
        testid="overlay-timing"
        options={[
          { value: TIMING_SCENE, text: '장면 전체' },
          { value: 'window', text: '지정' }
        ]}
        value={window ? 'window' : TIMING_SCENE}
        onPick={(value) => onChange({
          timing: value === TIMING_SCENE
            ? TIMING_SCENE
            // 기본 창은 장면 시작부터이고 길이는 장면을 넘지 않는다. 넘겨도 렌더가 장면 끝으로
            // 자르지만(`overlay.overlay_span`), 처음 보이는 값이 사실과 달라야 할 이유가 없다.
            : { start: 0, dur: Math.min(1, Math.round(scene.duration * 10) / 10) }
        })}
      />
      {window && (
        <div className="overlay-card__row">
          <SecondsField
            label="시작"
            value={window.start}
            min={0}
            onChange={(start) => onChange({ timing: { ...window, start } })}
          />
          <SecondsField
            label="길이"
            value={window.dur}
            min={0.1}
            onChange={(dur) => onChange({ timing: { ...window, dur } })}
          />
          {/* 장면 끝을 넘으면 렌더가 자른다 — 경고가 아니라 확정 동작이다 (확정 스펙 7.2). */}
          <span className="t-caption">장면 시작 기준 초</span>
        </div>
      )}
    </div>
  )
}

/** 9칸 격자. **이름을 앱이 만들지 않는다** — 백엔드가 보낸 순서 그대로 3x3에 놓는다. */
function PositionGrid ({ positions, selected, onPick }: {
  positions: string[]
  selected: string
  onPick: (value: string) => void
}) {
  return (
    <div className="posgrid" data-testid="overlay-positions" role="radiogroup" aria-label="위치">
      {positions.map((value) => (
        <button
          key={value}
          type="button"
          className={`posgrid__cell${value === selected ? ' posgrid__cell--on' : ''}`}
          data-value={value}
          data-selected={value === selected}
          role="radio"
          aria-checked={value === selected}
          aria-label={value}
          title={value}
          onClick={() => onPick(value)}
        />
      ))}
    </div>
  )
}

/**
 * 선택지가 서넛인 값 — `controls.Segmented`의 오버레이 판이다.
 *
 * 별도로 두는 이유는 **후보가 백엔드에서 오는 문자열**이라 타입 파라미터가 좁혀지지 않고,
 * 색 견본을 함께 그려야 하기 때문이다 (`Segmented`는 텍스트만 받는다).
 */
function Choice ({ label, testid, options, value, onPick }: {
  label: string
  testid: string
  options: Array<{ value: string, text: string, swatch?: string }>
  value: string
  onPick: (value: string) => void
}) {
  return (
    <div className="field field--tight" data-field={label}>
      <div className="t-label field__label">{label}</div>
      <span className="segmented" data-testid={`segmented-${testid}`} role="group">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`segmented__option${option.value === value ? ' segmented__option--on' : ''}`}
            data-value={option.value}
            data-selected={option.value === value}
            aria-pressed={option.value === value}
            onClick={() => onPick(option.value)}
          >
            {option.swatch !== undefined && (
              <span className="segmented__swatch" style={{ background: option.swatch } } aria-hidden="true" />
            )}
            {option.text}
          </button>
        ))}
      </span>
    </div>
  )
}

/** 정수 하나. **빈 문자열이나 `NaN`을 올리지 않는다** (`controls.NumberInput`과 같은 이유). */
function IntField ({ label, value, onChange }: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => {
    setDraft((typed) => (Number.parseInt(typed, 10) === value ? typed : String(value)))
  }, [value])

  return (
    <label className="input input--number input--inline" data-control={label}>
      <span className="t-label">{label}</span>
      <input
        type="number"
        data-testid={`input-${label}`}
        value={draft}
        // 화살표 한 번에 4px — 1px씩 움직여 눈에 보이는 차이를 만들려면 수십 번이 필요하다.
        // 1080x1920 캔버스의 값이고 프리뷰는 축소되어 있다.
        step={4}
        onChange={(event) => {
          setDraft(event.target.value)
          const next = Number.parseInt(event.target.value, 10)
          if (Number.isFinite(next)) onChange(next)
        }}
      />
    </label>
  )
}

/**
 * 초 하나. 소수 첫째 자리를 입력하는 동안 값을 다시 쓰지 않는다 (`DurationField`와 같다).
 *
 * **하한을 인자로 받는다.** 스키마에서 `start`는 0 이상이고 `dur`은 0보다 커야 하므로
 * (`_TIMING_WINDOW`), 한 값으로 두면 둘 중 하나가 계약을 어긴 상태로 저장을 누를 수 있게 된다.
 */
function SecondsField ({ label, value, min, onChange }: {
  label: string
  value: number
  min: number
  onChange: (value: number) => void
}) {
  const [draft, setDraft] = useState(value.toFixed(1))
  useEffect(() => {
    setDraft((typed) => (Number.parseFloat(typed) === value ? typed : value.toFixed(1)))
  }, [value])

  return (
    <label className="input input--number input--inline" data-control={label}>
      <span className="t-label">{label}</span>
      <input
        type="number"
        data-testid={`input-${label}`}
        value={draft}
        min={min}
        step={0.1}
        onChange={(event) => {
          setDraft(event.target.value)
          const next = Number.parseFloat(event.target.value)
          if (Number.isFinite(next) && next >= min) onChange(next)
        }}
      />
    </label>
  )
}

/** 눈금 100 = 게인 1.0 (D2 확정 스펙 5장). 파일에 사는 값은 눈금이 아니라 선형 게인이다. */
const UI_PER_GAIN = 100

/**
 * 슬라이더가 끌 수 있는 최대 눈금.
 *
 * **계약의 상한이 아니다.** `audio.voice_volume`·`audio.sfx_volume`에는 상한이 없고(최종
 * 오디오는 `alimiter`가 -1 dBFS에서 잡는다) 손으로 더 큰 값을 적은 `project.json`도 열린다 —
 * 그때 이 컨트롤은 값을 깎지 않고 손잡이만 끝에 두고 수치는 실제 값을 적는다. 시안이 0~100으로
 * 그렸지만 그 범위로는 **원본보다 키우는 조작이 화면에서 불가능해진다** (확정 스펙 5장이
 * "상한을 두지 않는다"고 적은 값이다).
 */
const SLIDER_MAX_UI = 200

/**
 * 트랙 볼륨 하나 (#81).
 *
 * **입력 중인 문자열을 들지 않는다** — `DurationField`와 달리 슬라이더는 중간 상태가 없다.
 * 값이 곧 눈금이고, 눈금은 정수라 `"3."` 같은 것이 나올 자리가 없다.
 */
function VolumeField ({ label, testid, gain, onChange, zeroHint }: {
  label: string
  testid: string
  gain: number
  onChange: (gain: number) => void
  /** 0의 뜻이 트랙마다 다르다 — 효과음은 아예 만들지 않고, 낭독은 트랙이 흐르되 들리지 않는다. */
  zeroHint?: string
}) {
  const ui = Math.round(gain * UI_PER_GAIN)
  return (
    <div className="field" data-field={label}>
      <div className="t-label field__label">{label}</div>
      <div className="volume">
        <div className="volume__row">
          <input
            type="range"
            className="volume__slider"
            data-testid={`slider-${testid}`}
            min={0}
            max={SLIDER_MAX_UI}
            step={1}
            // 범위 밖 값(손으로 고친 파일)에서는 손잡이가 끝에 선다. 값을 깎지 않는 것이
            // 요점이다 — 화면에 들어오는 순간 `project.json`이 조용히 달라지면 안 된다.
            value={Math.min(ui, SLIDER_MAX_UI)}
            aria-label={label}
            onChange={(event) => onChange(Number(event.target.value) / UI_PER_GAIN)}
          />
          <span className="mono volume__value" data-testid={`volume-${testid}`}>{ui}</span>
        </div>
        {/* 100이 원본 레벨이라는 것과, 넘겨도 되는 이유를 값 옆에 둔다. `text/2`다 — 읽는
            11px 문구에 `text/3`을 쓰면 소형 텍스트 AA에 미달한다 (확정 스펙 1.7). */}
        <div className="t-caption">
          {ui === 0
            ? zeroHint ?? '들리지 않는다'
            : ui > UI_PER_GAIN
              ? '100이 원본 레벨 · 넘는 만큼은 리미터가 -1 dBFS에서 잡는다'
              : '100이 원본 레벨'}
        </div>
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
