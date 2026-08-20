"""설정 로더 검증 — 이슈 #6의 완료 조건에 대응한다."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shorts_maker.assets import AssetError
from shorts_maker.config import (
    DEFAULT_CONFIG_FILENAME,
    RUN_CONFIG_FILENAME,
    SPEC,
    Config,
    ConfigError,
    Setting,
    defaults,
    load_config,
    load_run_config,
    serialize_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config.example.yaml"


def write_config(directory: Path, body: str, name: str = DEFAULT_CONFIG_FILENAME) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def flat_keys(config: Config) -> list[str]:
    return [key for key, _ in config.flatten()]


# --- 파일이 없을 때 --------------------------------------------------------


def test_runs_on_defaults_without_config_file(tmp_path: Path) -> None:
    config = load_config(search_from=tmp_path)

    assert config.source is None
    assert config.data == defaults()
    assert config.get("tts.voice") == "ko-KR-SunHiNeural"


def test_default_file_is_picked_up_when_present(tmp_path: Path) -> None:
    write_config(tmp_path, "tts:\n  voice: ko-KR-InJoonNeural\n")

    config = load_config(search_from=tmp_path)

    assert config.source == tmp_path / DEFAULT_CONFIG_FILENAME
    assert config.get("tts.voice") == "ko-KR-InJoonNeural"


def test_empty_file_is_not_an_error(tmp_path: Path) -> None:
    write_config(tmp_path, "# 주석만 있다\n")

    config = load_config(search_from=tmp_path)

    assert config.data == defaults()


# --- --config 경로 ---------------------------------------------------------


def test_explicit_missing_path_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "없는파일.yaml"

    with pytest.raises(ConfigError) as error_info:
        load_config(missing)

    assert str(missing) in str(error_info.value)


def test_explicit_path_overrides_default_filename(tmp_path: Path) -> None:
    write_config(tmp_path, "tts:\n  voice: 기본파일\n")
    explicit = write_config(tmp_path, "tts:\n  voice: 지정파일\n", name="other.yaml")

    config = load_config(explicit, search_from=tmp_path)

    assert config.source == explicit
    assert config.get("tts.voice") == "지정파일"


# --- 병합과 우선순위 -------------------------------------------------------


def test_partial_config_keeps_sibling_defaults(tmp_path: Path) -> None:
    """섹션을 통째로 덮어쓰지 않는다."""
    write_config(tmp_path, "tts:\n  voice: 바꾼목소리\n")

    config = load_config(search_from=tmp_path)

    assert config.get("tts.voice") == "바꾼목소리"
    assert config.get("tts.provider") == "edge_tts"
    assert config.get("llm.generator.model") == "opus"


def test_deeply_nested_partial_config_keeps_siblings(tmp_path: Path) -> None:
    write_config(tmp_path, "llm:\n  verifier:\n    model: sonnet\n")

    config = load_config(search_from=tmp_path)

    assert config.get("llm.verifier.model") == "sonnet"
    assert config.get("llm.verifier.runs") == 2
    assert config.get("llm.generator.model") == "opus"
    assert config.get("llm.timeout_sec") == 600


def test_cli_override_beats_config_file_and_default(tmp_path: Path) -> None:
    write_config(tmp_path, "tts:\n  voice: 파일값\n")

    config = load_config(search_from=tmp_path, overrides={"tts.voice": "CLI값"})

    assert config.get("tts.voice") == "CLI값"


def test_cli_override_applies_without_config_file(tmp_path: Path) -> None:
    config = load_config(search_from=tmp_path, overrides={"quiz.question_count": 5})

    assert config.get("quiz.question_count") == 5
    assert config.get("quiz.countdown_sec") == 3


def test_cli_override_is_validated_like_the_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path, overrides={"tts.voise": "오타"})

    assert "tts.voise" in str(error_info.value)


# --- 검증 ------------------------------------------------------------------


def test_unknown_key_reports_path_and_available_keys(tmp_path: Path) -> None:
    write_config(tmp_path, "llm:\n  generatr:\n    model: opus\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    message = str(error_info.value)
    assert "llm.generatr" in message
    assert "generator" in message  # 쓸 수 있는 키를 함께 알려준다


def test_unknown_top_level_key_reports_path(tmp_path: Path) -> None:
    write_config(tmp_path, "rendering:\n  font_path: x\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "rendering" in str(error_info.value)


def test_wrong_type_reports_path(tmp_path: Path) -> None:
    write_config(tmp_path, 'quiz:\n  question_count: "네개"\n')

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    message = str(error_info.value)
    assert "quiz.question_count" in message
    assert "정수" in message


def test_bool_is_not_accepted_as_int(tmp_path: Path) -> None:
    """파이썬에서 bool은 int의 하위 타입이라 그냥 두면 통과한다."""
    write_config(tmp_path, "quiz:\n  question_count: true\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "quiz.question_count" in str(error_info.value)


def test_int_is_accepted_where_float_is_expected(tmp_path: Path) -> None:
    write_config(tmp_path, "timing:\n  tail_sec: 1\n")

    config = load_config(search_from=tmp_path)

    assert config.get("timing.tail_sec") == 1


def test_nullable_key_accepts_null_and_string(tmp_path: Path) -> None:
    write_config(tmp_path, "render:\n  font_path: null\n")
    assert load_config(search_from=tmp_path).get("render.font_path") is None

    write_config(tmp_path, "render:\n  font_path: assets/fonts/x.ttf\n")
    assert load_config(search_from=tmp_path).get("render.font_path") == "assets/fonts/x.ttf"


def test_a_choice_key_rejects_a_name_off_the_list(tmp_path: Path) -> None:
    """허용 목록이 있는 키는 목록 밖 값을 실행 시작 시점에 잡는다. 목록 자체는 번들
    프리셋(#38)에서 오고, 이름별 검증은 `test_visual_assets.py`가 본다."""
    write_config(tmp_path, "render:\n  caption_style: 없는_스타일\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "render.caption_style" in str(error_info.value)


def test_a_broken_choice_list_is_still_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """번들 프리셋 파일이 깨져 있어도 `load_config`는 `ConfigError`만 던진다.

    `AssetError`를 그대로 올려보내면 `main`이 잡지 못해 스택트레이스가 나고, 사용자는
    설정 오류와 구분할 수 없다 (#38).
    """

    def broken() -> tuple[str, ...]:
        raise AssetError("프리셋 파일이 없다: assets/backgrounds/presets.json")

    monkeypatch.setitem(SPEC["render"], "background", Setting("x", "str", choices=broken))
    write_config(tmp_path, "render:\n  background: deep_navy\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "presets.json" in str(error_info.value)


def test_non_nullable_key_rejects_null(tmp_path: Path) -> None:
    write_config(tmp_path, "tts:\n  voice: null\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "tts.voice" in str(error_info.value)


def test_scalar_where_section_expected_reports_path(tmp_path: Path) -> None:
    write_config(tmp_path, "timing: 3\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "timing" in str(error_info.value)


def test_non_mapping_top_level_is_an_error(tmp_path: Path) -> None:
    write_config(tmp_path, "- 목록이다\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "매핑" in str(error_info.value)


def test_yaml_syntax_error_is_reported(tmp_path: Path) -> None:
    write_config(tmp_path, "llm:\n  generator: [닫히지 않은\n")

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert "YAML" in str(error_info.value)


def test_all_errors_are_reported_at_once(tmp_path: Path) -> None:
    """오류 하나 고치고 다시 돌려서 다음 오류를 보는 왕복을 만들지 않는다."""
    write_config(
        tmp_path,
        'quiz:\n  question_count: "넷"\n  countdown_sec: "넷"\ntts:\n  voise: 오타\n',
    )

    with pytest.raises(ConfigError) as error_info:
        load_config(search_from=tmp_path)

    assert len(error_info.value.messages) == 3


# --- run 설정 기록 (#92) ---------------------------------------------------


def write_record(run_dir: Path, config: Config) -> Path:
    """`main.run`이 하는 것과 같은 자리에 기록을 남긴다."""
    return write_config(run_dir, serialize_config(config), name=RUN_CONFIG_FILENAME)


def test_record_covers_every_key(tmp_path: Path) -> None:
    """부분 집합을 남기면 두 번째 타입에서 빠진 키가 생긴다 — 답은 '전부'다 (PRD 14.1)."""
    original = load_config(search_from=tmp_path)
    write_record(tmp_path, original)

    restored = load_run_config(tmp_path)

    assert flat_keys(restored) == flat_keys(original)
    assert restored.data == original.data


def test_record_round_trips_values_that_are_not_defaults(tmp_path: Path) -> None:
    source = tmp_path / "쓴설정"
    source.mkdir()
    write_config(source, 'tts:\n  voice: 다른목소리\nrender:\n  cta_punch: "구독 · 좋아요"\n')
    original = load_config(search_from=source, overrides={"quiz.question_count": 5})

    write_record(tmp_path, original)
    restored = load_run_config(tmp_path)

    assert restored.get("tts.voice") == "다른목소리"
    assert restored.get("quiz.question_count") == 5
    assert restored.get("render.cta_punch") == "구독 · 좋아요"
    # nullable 값이 문자열 "None"이 되지 않는다.
    assert restored.get("render.font_path") is None


def test_record_is_read_without_looking_at_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**이 이슈의 핵심이다.** `load_config()`를 인자 없이 부르면 cwd의 `config.yaml`을
    찾는데, 그 파일은 생성 이후에 바뀌었을 수 있고 앱 백엔드에서는 cwd를 앱이 정한다."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_record(run_dir, load_config(overrides={"tts.voice": "기록된목소리"}))

    elsewhere = tmp_path / "작업디렉터리"
    elsewhere.mkdir()
    write_config(elsewhere, "tts:\n  voice: cwd목소리\n")
    monkeypatch.chdir(elsewhere)

    assert load_run_config(run_dir).get("tts.voice") == "기록된목소리"


def test_record_is_validated_like_any_config(tmp_path: Path) -> None:
    """손으로 고친 기록도 `SPEC`을 지난다 — 계약이 하나라는 것이 YAML을 고른 이유다."""
    write_config(tmp_path, "tts:\n  voise: 오타\n", name=RUN_CONFIG_FILENAME)

    with pytest.raises(ConfigError) as error_info:
        load_run_config(tmp_path)

    assert "tts.voise" in str(error_info.value)


def test_missing_record_names_the_directory_and_the_file(tmp_path: Path) -> None:
    """이 파일이 생기기 전에 만들어진 run 디렉터리가 실제 실패 경로다."""
    with pytest.raises(ConfigError) as error_info:
        load_run_config(tmp_path)

    message = str(error_info.value)
    assert str(tmp_path) in message
    assert RUN_CONFIG_FILENAME in message


def test_record_keeps_definition_order_and_korean(tmp_path: Path) -> None:
    """사람이 `config.example.yaml`과 나란히 놓고 읽는 파일이다.

    알파벳 순으로 흐트러지거나 한국어가 `\\uXXXX`로 나가면 값은 살아 있어도 기록의 목적이
    사라진다.
    """
    config = load_config(search_from=tmp_path)
    text = serialize_config(config)

    leaves = [key.rsplit(".", 1)[-1] for key, _ in config.flatten()]
    found = [
        match.group(1)
        for match in re.finditer(r"^\s*([\w.]+):", text, re.MULTILINE)
        if match.group(1) in leaves
    ]
    assert found == leaves
    assert "구독 · 좋아요" in text


# --- 기본값과 접근 ---------------------------------------------------------


def test_every_documented_key_has_a_default() -> None:
    config = Config(data=defaults())
    expected = {
        "llm.generator.provider",
        "llm.generator.model",
        "llm.verifier.provider",
        "llm.verifier.model",
        "llm.verifier.runs",
        "llm.verifier.confidence_threshold",
        "llm.timeout_sec",
        "llm.max_retries",
        "llm.providers.claude_cli.binary",
        "tts.provider",
        "tts.voice",
        "tts.timeout_sec",
        "tts.max_retries",
        "tts.cache_dir",
        "source.max_chars",
        "source.title_max_len",
        "timing.lead_in_sec",
        "timing.tail_sec",
        "timing.min_duration_sec",
        "timing.caption_onset_sec",
        "timing.reading_cps",
        "audio.sfx_volume",
        "audio.voice_volume",
        "captions.max_chars_per_line",
        "captions.max_lines",
        "quiz.question_count",
        "quiz.countdown_sec",
        "quiz.hook_max_len",
        "quiz.question_max_len",
        "quiz.answer_max_len",
        "quiz.cta_max_len",
        "quiz.explanation_max_len",
        "metadata.title_max_len",
        "metadata.tag_max_count",
        "render.font_path",
        "render.background",
        "render.caption_style",
        "render.cta_punch",
        "render.cta_tail",
    }

    assert set(flat_keys(config)) == expected


def test_wave1_decisions_are_the_defaults() -> None:
    """PRD 14.1과 7.5.1이 확정한 값이 기본값으로 들어가 있다."""
    config = Config(data=defaults())

    assert config.get("llm.generator.model") == "opus"
    assert config.get("tts.provider") == "edge_tts"
    assert config.get("tts.voice") == "ko-KR-SunHiNeural"
    assert config.get("timing.lead_in_sec") == pytest.approx(0.30)
    assert config.get("timing.tail_sec") == pytest.approx(0.50)
    assert config.get("timing.min_duration_sec") == pytest.approx(1.20)


def test_d1_design_decisions_are_the_defaults() -> None:
    """D1 확정 스펙 3장·5.5가 정한 값 (PRD 14.1의 "D1 영상 디자인" 결정).

    `countdown_sec` 3은 시안의 3 → 2 → 1과 대응한다. 4로 돌아가면 렌더러가 시안이 검증하지
    않은 4자리를 그린다.
    """
    config = Config(data=defaults())

    assert config.get("quiz.countdown_sec") == 3
    assert config.get("render.cta_punch") == "구독 · 좋아요"
    assert config.get("render.cta_tail") == "매일 새 상식 퀴즈"


def test_get_reports_the_missing_segment() -> None:
    with pytest.raises(KeyError) as error_info:
        Config(data=defaults()).get("llm.generator.temperature")

    assert "llm.generator.temperature" in str(error_info.value)


# --- config.example.yaml ---------------------------------------------------


def test_example_file_passes_validation(tmp_path: Path) -> None:
    """예시 파일이 실제 스키마와 어긋나는 것을 막는다."""
    config = load_config(EXAMPLE_PATH, search_from=tmp_path)

    assert config.source == EXAMPLE_PATH


def test_example_file_covers_every_key(tmp_path: Path) -> None:
    example = load_config(EXAMPLE_PATH, search_from=tmp_path)
    text = EXAMPLE_PATH.read_text(encoding="utf-8")

    for key, _ in example.flatten():
        leaf = key.rsplit(".", 1)[-1]
        assert re.search(rf"^\s*{re.escape(leaf)}:", text, re.MULTILINE), f"{key}가 예시에 없다"


def test_example_file_values_match_defaults(tmp_path: Path) -> None:
    """예시는 기본값을 그대로 적어 둔다 — 복사해서 실행해도 동작이 바뀌지 않는다."""
    assert load_config(EXAMPLE_PATH, search_from=tmp_path).data == defaults()


def test_every_leaf_key_in_example_has_a_comment() -> None:
    section_names = _section_names(SPEC)
    uncommented = []

    for line in EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([\w.]+):(.*)$", line)
        if match is None:
            continue
        key, rest = match.group(1), match.group(2)
        if key in section_names and not rest.strip():
            continue  # 섹션 헤더는 위쪽 주석 블록이 설명한다
        if "#" not in rest:
            uncommented.append(key)

    assert uncommented == []


def _section_names(spec: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for key, node in spec.items():
        if isinstance(node, dict):
            names.add(key)
            names |= _section_names(node)
    return names
