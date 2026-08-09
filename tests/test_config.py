"""설정 로더 검증 — 이슈 #6의 완료 조건에 대응한다."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shorts_maker.config import (
    DEFAULT_CONFIG_FILENAME,
    SPEC,
    Config,
    ConfigError,
    defaults,
    load_config,
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
    assert config.get("quiz.countdown_sec") == 4


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
        "timing.lead_in_sec",
        "timing.tail_sec",
        "timing.min_duration_sec",
        "quiz.question_count",
        "quiz.countdown_sec",
        "quiz.answer_max_len",
        "quiz.explanation_max_len",
        "metadata.title_max_len",
        "metadata.tag_max_count",
        "render.font_path",
        "render.background",
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
