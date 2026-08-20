"""원문 파일 입력과 `source.json` — 이슈 #94의 완료 조건.

**LLM도 TTS도 지나지 않는다.** 여기서 보는 것은 입력을 받아들이는 규칙(인코딩·상한·빈 파일)과
그 기록의 계약뿐이고, CLI 배선(어느 순서로 쓰이는가, `--topic`에는 생기지 않는가)은
`tests/test_main.py`가 본다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from shorts_maker.config import Config, load_config
from shorts_maker.schemas import SOURCE_SCHEMA, SchemaError, validate_source
from shorts_maker.schemas.source import SCHEMA_VERSION
from shorts_maker.source import (
    SourceError,
    from_text_file,
    from_topic,
    title_from,
)

NOW = datetime(2026, 8, 20, 14, 30, 5)

ARTICLE = "\n".join(
    [
        "폭염 속 전력 수요 사상 최고",
        "",
        "20일 전력거래소는 최대 전력 수요가 사상 최고치를 기록했다고 밝혔다.",
        "예비율은 8%대로 떨어졌다.",
    ]
)


def config_with(tmp_path: Path, **overrides: object) -> Config:
    """설정 파일이 없는 상태의 기본값 + 필요한 키만 덮어쓴다."""
    return load_config(search_from=tmp_path, overrides=overrides)


def write_article(tmp_path: Path, text: str = ARTICLE, *, name: str = "기사.txt") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- `--topic` 갈래 -----------------------------------------------------------


def test_topic_input_has_no_record() -> None:
    """주제 한 줄은 `source.json`을 만들지 않는다 (PRD 6.2 표)."""
    source = from_topic("세계 지리 상식")

    assert source.topic == "세계 지리 상식"
    assert source.record is None


# --- 원문 파일 갈래 -----------------------------------------------------------


def test_text_file_record_carries_the_input_facts(tmp_path: Path) -> None:
    """입력 종류·파일 경로·글자 수·제목·수집 시각 (#94의 완료 조건)."""
    path = write_article(tmp_path)

    source = from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert source.record == {
        "schema_version": SCHEMA_VERSION,
        "kind": "text_file",
        # 구분자는 `/`다 — Windows 경로를 JSON에 그대로 쓰면 역슬래시가 두 겹이 된다.
        "path": path.as_posix(),
        # 링크 경로가 채우는 칸이다. 키는 있고 값이 없다.
        "url": None,
        "title": "폭염 속 전력 수요 사상 최고",
        "text": ARTICLE,
        "char_count": len(ARTICLE),
        "collected_at": "2026-08-20T14:30:05",
    }


def test_the_generator_gets_the_title_not_the_body(tmp_path: Path) -> None:
    """`ContentGenerator` 계약은 그대로다 — `topic` 자리에 제목이 간다 (PRD 14.1)."""
    source = from_text_file(
        write_article(tmp_path), config=config_with(tmp_path), now=NOW
    )

    assert source.topic == "폭염 속 전력 수요 사상 최고"
    assert source.topic not in ("", ARTICLE)


def test_the_record_passes_its_own_schema(tmp_path: Path) -> None:
    """만든 기록을 파이프라인이 그대로 파일로 쓴다 — 계약을 지나야 한다."""
    source = from_text_file(
        write_article(tmp_path), config=config_with(tmp_path), now=NOW
    )

    validate_source(source.record)


# --- 제목 규칙 ---------------------------------------------------------------


def test_the_title_skips_leading_blank_lines(tmp_path: Path) -> None:
    path = write_article(tmp_path, "\n \n\n첫 문장이 제목이다\n본문\n")

    source = from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert source.record["title"] == "첫 문장이 제목이다"


def test_an_over_long_first_line_is_cut_not_replaced(tmp_path: Path) -> None:
    """문단으로 시작하는 원문도 주제를 잃지 않는다. 원문 전체는 기록에 남는다."""
    body = "가" * 200
    path = write_article(tmp_path, body)

    source = from_text_file(
        path, config=config_with(tmp_path, **{"source.title_max_len": 30}), now=NOW
    )

    assert source.topic == "가" * 30
    assert source.record["text"] == body


def test_the_file_name_is_the_fallback_title(tmp_path: Path) -> None:
    """제목이 비어 있는 결과를 내지 않는다는 것이 이 함수의 계약이다.

    빈 파일은 읽는 단계가 이미 거절하므로 일반 실행에서는 도달하지 않는다.
    """
    assert title_from("   \n\n", tmp_path / "원문기사.txt", max_len=80) == "원문기사"


# --- 인코딩 -----------------------------------------------------------------


def test_a_utf8_bom_does_not_leak_into_the_title(tmp_path: Path) -> None:
    """BOM을 벗기지 않으면 제목 첫 글자에 보이지 않는 문자가 붙는다 — 제목은 프롬프트다."""
    path = tmp_path / "bom.txt"
    path.write_bytes("﻿제목\n본문\n".encode("utf-8"))

    source = from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert source.topic == "제목"
    assert not source.record["text"].startswith("﻿")


def test_crlf_does_not_change_the_char_count(tmp_path: Path) -> None:
    """글자 수가 원문을 저장한 운영체제에 좌우되면 안 된다 — 콘솔과 상한 판정이 그 수를 쓴다."""
    path = tmp_path / "crlf.txt"
    path.write_bytes(ARTICLE.replace("\n", "\r\n").encode("utf-8"))

    source = from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert source.record["text"] == ARTICLE
    assert source.record["char_count"] == len(ARTICLE)


def test_a_cp949_file_is_read_without_mojibake(tmp_path: Path) -> None:
    """메모장으로 저장한 한국어 원문이 그것이다."""
    path = tmp_path / "cp949.txt"
    path.write_bytes(ARTICLE.encode("cp949"))

    source = from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert source.record["text"] == ARTICLE


def test_undecodable_bytes_are_rejected_with_the_remedy(tmp_path: Path) -> None:
    path = tmp_path / "binary.txt"
    path.write_bytes(b"\xff\xfe\x00\x00\x80\x81")

    with pytest.raises(SourceError) as error:
        from_text_file(path, config=config_with(tmp_path), now=NOW)

    assert "인코딩" in str(error.value)
    assert "UTF-8" in str(error.value)


# --- 거절 ------------------------------------------------------------------


def test_an_empty_file_is_rejected(tmp_path: Path) -> None:
    path = write_article(tmp_path, "")

    with pytest.raises(SourceError, match="비어 있다"):
        from_text_file(path, config=config_with(tmp_path), now=NOW)


def test_a_whitespace_only_file_is_rejected(tmp_path: Path) -> None:
    """공백만 있는 파일도 빈 파일이다. 제목도 본문도 만들 수 없다."""
    path = write_article(tmp_path, "   \n\t\n")

    with pytest.raises(SourceError, match="비어 있다"):
        from_text_file(path, config=config_with(tmp_path), now=NOW)


def test_a_file_over_the_char_limit_is_rejected(tmp_path: Path) -> None:
    """상한은 설정 키로 바뀐다. 아래 바이트 검사를 지나도록 ASCII 원문을 쓴다."""
    path = write_article(tmp_path, "a" * 20)

    with pytest.raises(SourceError) as error:
        from_text_file(
            path, config=config_with(tmp_path, **{"source.max_chars": 10}), now=NOW
        )

    assert "20자" in str(error.value)
    assert "source.max_chars" in str(error.value)


def test_a_huge_file_is_rejected_by_its_byte_size(tmp_path: Path) -> None:
    """글자 수보다 앞에 있는 방어선이다 — 그 파일을 메모리에 올리지 않는다.

    바이트 수가 상한의 4배를 넘으면 글자 수도 반드시 상한을 넘는다 (UTF-8 한 글자가 최대
    4바이트). 아래 20자 파일과 달리 메시지가 바이트를 말하는 것으로 어느 검사였는지 갈린다.
    """
    path = write_article(tmp_path, "a" * 41)

    with pytest.raises(SourceError) as error:
        from_text_file(
            path, config=config_with(tmp_path, **{"source.max_chars": 10}), now=NOW
        )

    assert "41바이트" in str(error.value)


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="찾을 수 없다"):
        from_text_file(tmp_path / "없다.txt", config=config_with(tmp_path), now=NOW)


def test_a_directory_is_rejected_as_not_a_file(tmp_path: Path) -> None:
    """읽기 실패 메시지가 인코딩이나 권한 문제처럼 읽히지 않게 갈라 말한다."""
    with pytest.raises(SourceError, match="파일이 아니다"):
        from_text_file(tmp_path, config=config_with(tmp_path), now=NOW)


# --- `source.json` 계약 ------------------------------------------------------


def valid_record(**overrides: object) -> dict:
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "text_file",
        "path": "input/source.txt",
        "url": None,
        "title": "제목",
        "text": "본문이다",
        "char_count": 4,
        "collected_at": "2026-08-20T14:30:05",
    }
    record.update(overrides)
    return record


def test_the_schema_owns_the_file_name() -> None:
    """파일명도 검증도 스키마 하나에서 나온다 (`SCENES_SCHEMA`와 같은 모양)."""
    assert SOURCE_SCHEMA.name == "source.json"


def test_a_url_record_passes_the_same_schema() -> None:
    """두 입력 경로가 한 계약을 공유한다 — #95가 스키마를 늘리지 않는다."""
    validate_source(
        valid_record(kind="url", path=None, url="https://example.com/article")
    )


def test_the_kind_decides_which_location_field_is_required() -> None:
    with pytest.raises(SchemaError, match="path"):
        validate_source(valid_record(path=None))


def test_a_record_cannot_carry_both_location_fields() -> None:
    """`--text-file` 기록에 `url`이 있으면 그 값이 어디서 왔는지 말할 수 없다."""
    with pytest.raises(SchemaError, match="url"):
        validate_source(valid_record(url="https://example.com/article"))


def test_the_char_count_must_match_the_text() -> None:
    """사람이 콘솔에서 본 글자 수와 요약이 받는 본문의 길이가 갈리면 안 된다."""
    with pytest.raises(SchemaError, match="char_count"):
        validate_source(valid_record(char_count=999))


def test_an_unknown_kind_is_rejected() -> None:
    with pytest.raises(SchemaError, match="kind"):
        validate_source(valid_record(kind="clipboard"))


def test_an_empty_title_is_rejected() -> None:
    """이 값이 생성기의 `topic` 자리로 간다 — 빈 주제가 프롬프트로 갈 길이 없어야 한다."""
    with pytest.raises(SchemaError, match="title"):
        validate_source(valid_record(title="  "))


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(SchemaError, match="author"):
        validate_source(valid_record(author="기자"))
