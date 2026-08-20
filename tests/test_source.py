"""원문 파일·링크 입력과 `source.json` — 이슈 #94·#95의 완료 조건.

**LLM도 TTS도 지나지 않고, 네트워크에도 나가지 않는다.** 여기서 보는 것은 입력을 받아들이는
규칙(인코딩·상한·빈 파일, 그리고 링크의 세 거부 신호)과 그 기록의 계약뿐이고, CLI 배선(어느
순서로 쓰이는가, `--topic`에는 생기지 않는가)은 `tests/test_main.py`가 본다.

링크 갈래는 `stub_http`(가져오기 대역) + **저장된 HTML 바이트**로 돈다. 추출은 대역으로
바꾸지 않는다 — 무엇이 본문으로 뽑히는지가 이 이슈가 고른 것이므로(trafilatura, PRD 14.1)
흉내 내면 검증이 아니라 동어반복이 된다. 대신 추출기가 선택 의존성이라 그 테스트만 skip된다.
"""

from __future__ import annotations

import gzip
import ssl
import sys
import urllib.error
from datetime import datetime
from email.message import Message
from pathlib import Path

import pytest

from conftest import (
    ARTICLE_HEADLINE,
    ARTICLE_PARAGRAPHS,
    ARTICLE_URL,
    PAGE_FURNITURE,
    StubHTTP,
    article_page,
    needs_extractor,
)

from shorts_maker.config import Config, load_config
from shorts_maker.schemas import SOURCE_SCHEMA, SchemaError, validate_source
from shorts_maker.schemas.source import SCHEMA_VERSION
from shorts_maker.source import (
    USER_AGENT,
    SourceError,
    from_text_file,
    from_topic,
    from_url,
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


# --- 링크 갈래 ---------------------------------------------------------------


def http_error(status: int, reason: str = "Unauthorized") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ARTICLE_URL, status, reason, Message(), None)


@needs_extractor
def test_url_record_carries_the_input_facts(tmp_path: Path, stub_http: StubHTTP) -> None:
    """제목·본문·ARTICLE_URL·접근 시각이 남는다 (#95의 완료 조건, PRD 7.1)."""
    stub_http.body = article_page()

    source = from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    record = source.record
    assert record["kind"] == "url"
    assert record["url"] == ARTICLE_URL
    # 위치 필드 둘 중 하나만 값을 가진다 — 파일 경로가 채우는 칸이다.
    assert record["path"] is None
    assert record["title"] == ARTICLE_HEADLINE
    assert record["collected_at"] == "2026-08-20T14:30:05"
    assert record["char_count"] == len(record["text"])
    assert "순간 최대 전력은 104.8기가와트" in record["text"]
    validate_source(record)


@needs_extractor
def test_the_page_furniture_does_not_come_along(tmp_path: Path, stub_http: StubHTTP) -> None:
    """내비게이션·구독 유도가 본문에 섞이면 요약(#32)이 그것을 기사로 읽는다."""
    stub_http.body = article_page()

    source = from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert [word for word in PAGE_FURNITURE if word in source.record["text"]] == []


@needs_extractor
def test_the_generator_gets_the_headline(tmp_path: Path, stub_http: StubHTTP) -> None:
    """링크 경로도 `ContentGenerator` 계약을 바꾸지 않는다 — `topic` 자리에 제목이 간다."""
    stub_http.body = article_page()

    source = from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert source.topic == ARTICLE_HEADLINE


@needs_extractor
def test_an_ms949_response_is_not_mojibake(tmp_path: Path, stub_http: StubHTTP) -> None:
    """응답 인코딩이 utf-8이 아닌 한국어 사이트가 아직 있다 (네이버 카페 `MS949`).

    우리 코드가 먼저 `.decode()`하면 여기서 글자가 깨진다 — 바이트를 그대로 넘기는지를
    보는 테스트다.
    """
    stub_http.body = article_page(charset="MS949", encoding="cp949")

    source = from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert source.record["title"] == ARTICLE_HEADLINE
    assert "공급 예비율은 8.4%까지 떨어졌다" in source.record["text"]


@needs_extractor
def test_a_gzip_response_is_read_even_though_we_asked_for_identity(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """`Accept-Encoding: identity`를 무시하는 서버에서 사유가 "본문이 없다"로 둔갑하면 안 된다."""
    stub_http.body = gzip.compress(article_page())
    stub_http.content_encoding = "gzip"

    source = from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert source.record["title"] == ARTICLE_HEADLINE


@needs_extractor
def test_the_recorded_url_is_where_the_body_came_from(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """단축 링크·AMP 전환을 지나면 사용자가 친 주소는 본문이 있던 곳이 아니다."""
    landed = "https://news.example.com/article/1?page=all"
    stub_http.body = article_page()
    stub_http.final_url = landed

    source = from_url("https://exam.pl/abcd", config=config_with(tmp_path), now=NOW)

    assert source.record["url"] == landed


# --- 출처(`metadata.json`의 `source`) ----------------------------------------


@needs_extractor
def test_the_attribution_is_the_arrival_url(tmp_path: Path, stub_http: StubHTTP) -> None:
    """`metadata.json`의 `source`로 가는 값이다 — 기록의 `url`과 같은 값이어야 한다 (#100).

    사용자가 친 주소가 아니라 리다이렉트가 도착한 곳이다 (#95).
    """
    landed = "https://news.example.com/article/1?page=all"
    stub_http.body = article_page()
    stub_http.final_url = landed

    source = from_url("https://exam.pl/abcd", config=config_with(tmp_path), now=NOW)

    assert source.attribution == source.record["url"] == landed


def test_a_local_path_is_not_an_attribution(tmp_path: Path) -> None:
    """로컬 파일 경로는 업로드 설명에 붙일 출처가 아니다 (PRD 14.1) — 기록에는 남지만
    `metadata.json`의 `source`로는 가지 않는다 (#100)."""
    source = from_text_file(
        write_article(tmp_path), config=config_with(tmp_path), now=NOW
    )

    assert source.record["path"]
    assert source.attribution is None


def test_the_topic_path_has_no_attribution() -> None:
    """기록이 없으면 출처도 없다 — 여기서 `record`를 첨자로 열면 터진다."""
    assert from_topic("세계 지리 상식").attribution is None


# --- 링크 갈래: 거부 신호 셋 --------------------------------------------------


def test_a_non_200_response_is_rejected_with_its_status(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """유료 매체는 대체로 여기서 401/403으로 드러난다 (스파이크 #31 3.1)."""
    stub_http.error = http_error(401)

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "401" in str(error.value)
    assert "--text-file" in str(error.value)


def test_a_success_status_other_than_200_is_rejected(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """`urlopen`이 오류로 던지지 않는 자리다 — 204·206에는 볼 본문이 없거나 잘려 있다."""
    stub_http.status = 204
    stub_http.body = b""

    with pytest.raises(SourceError, match="204"):
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)


def test_a_non_html_content_type_is_rejected(tmp_path: Path, stub_http: StubHTTP) -> None:
    """PDF를 추출기에 넘기면 빈 결과가 나오고, 그때의 사유는 "본문이 없다"가 아니다."""
    stub_http.content_type = "application/pdf"
    stub_http.body = b"%PDF-1.7"

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "application/pdf" in str(error.value)
    assert "--text-file" in str(error.value)


@needs_extractor
def test_a_page_with_no_body_is_rejected(tmp_path: Path, stub_http: StubHTTP) -> None:
    stub_http.body = b"<!DOCTYPE html><html><body><div id='app'></div></body></html>"

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "추출하지 못했다" in str(error.value)
    assert "--text-file" in str(error.value)


@needs_extractor
def test_a_body_under_the_threshold_is_rejected(tmp_path: Path, stub_http: StubHTTP) -> None:
    """x.com이 200과 함께 주는 "JavaScript를 사용할 수 없습니다" 258자가 이 자리다."""
    stub_http.body = article_page(paragraphs=ARTICLE_PARAGRAPHS[:1])

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "source.url.min_chars" in str(error.value)
    assert "--text-file" in str(error.value)


@needs_extractor
def test_lowering_the_threshold_accepts_the_same_page(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """임계값을 설정 파일로 바꾸면 판정이 따라 바뀐다 (#95의 완료 조건)."""
    stub_http.body = article_page(paragraphs=ARTICLE_PARAGRAPHS[:1])

    source = from_url(
        ARTICLE_URL,
        config=config_with(tmp_path, **{"source.url.min_chars": 50}),
        now=NOW,
    )

    assert source.record["title"] == ARTICLE_HEADLINE


@needs_extractor
def test_an_extracted_body_over_the_char_limit_is_rejected(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """상한의 목적은 사고 방지다 — 요약(#32)이 잘못 지정한 페이지 전체를 모델에 싣지 않는다."""
    stub_http.body = article_page()

    with pytest.raises(SourceError) as error:
        from_url(
            ARTICLE_URL, config=config_with(tmp_path, **{"source.max_chars": 100}), now=NOW
        )

    assert "source.max_chars" in str(error.value)


# --- 링크 갈래: 가져오기 규칙 -------------------------------------------------


def test_the_extractor_is_checked_before_the_network(
    tmp_path: Path, stub_http: StubHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`source` extra가 없으면 **가져오기 전에** 설치 방법을 말하며 멈춘다 (#95의 완료 조건).

    `sys.modules`에 `None`을 넣으면 그 이름의 import가 `ImportError`가 된다 — 설치된
    환경에서도 없는 환경을 만들 수 있다.
    """
    monkeypatch.setitem(sys.modules, "trafilatura", None)

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "trafilatura" in str(error.value)
    assert "youtube-shorts-maker[source]" in str(error.value)
    assert stub_http.calls == []


def test_a_non_http_url_is_rejected_before_the_network(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """막지 않으면 `--url file:///...`이 로컬 파일을 읽는 경로가 된다."""
    with pytest.raises(SourceError, match="http"):
        from_url("file:///C:/Windows/win.ini", config=config_with(tmp_path), now=NOW)

    assert stub_http.calls == []


def test_a_redirect_landing_outside_http_is_rejected(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """도착한 곳도 같은 검사를 지난다."""
    stub_http.body = article_page()
    stub_http.final_url = "ftp://files.example.com/article.txt"

    with pytest.raises(SourceError, match="리다이렉트"):
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        (
            "https://ko.wikipedia.org/wiki/유튜브?q=검색",
            "https://ko.wikipedia.org/wiki/%EC%9C%A0%ED%8A%9C%EB%B8%8C?q=%EA%B2%80%EC%83%89",
        ),
        (
            # 이미 인코딩된 주소를 두 번 인코딩하면 `%25EC…`가 되어 다른 문서를 가리킨다.
            "https://ko.wikipedia.org/wiki/%EC%9C%A0%ED%8A%9C%EB%B8%8C",
            "https://ko.wikipedia.org/wiki/%EC%9C%A0%ED%8A%9C%EB%B8%8C",
        ),
    ],
    ids=("hangul", "already-encoded"),
)
def test_a_hangul_url_is_percent_encoded_before_the_request(
    tmp_path: Path, stub_http: StubHTTP, given: str, sent: str
) -> None:
    """브라우저 주소창에서 복사한 링크가 한글 그대로다.

    요청 줄은 ASCII여야 해서 `http.client`가 `UnicodeEncodeError`로 죽는데, 그것은
    `OSError`가 아니라 가져오기의 어느 갈래에도 걸리지 않는다 — 거부가 아니라 바꿔서 보낸다.
    """
    stub_http.error = http_error(404, "Not Found")

    with pytest.raises(SourceError):
        from_url(given, config=config_with(tmp_path), now=NOW)

    assert stub_http.calls[0]["url"] == sent


def test_the_user_agent_names_the_tool_and_does_not_impersonate_a_browser(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """위장이 필요 없다는 것이 실측이고, 위장 UA는 차단 정책과 싸우는 코드를 부른다."""
    stub_http.body = article_page()
    stub_http.error = http_error(403, "Forbidden")  # 헤더만 보므로 결과는 상관없다

    with pytest.raises(SourceError):
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    headers = {key.lower(): value for key, value in stub_http.calls[0]["headers"].items()}
    assert headers["user-agent"] == USER_AGENT
    assert headers["user-agent"].startswith("shorts-maker/")
    assert "Mozilla" not in headers["user-agent"]


def test_the_timeout_and_redirect_limit_come_from_the_config(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """두 값이 설정 파일로 바뀐다 (#95의 완료 조건).

    리다이렉트는 대역이 실제로 따라가지 않으므로, 상한이 걸렸는지는 opener에 실린 핸들러가
    답한다 — 그 값을 쓰는 것은 `urllib`이다.
    """
    stub_http.body = article_page()
    stub_http.error = http_error(500, "Server Error")

    with pytest.raises(SourceError):
        from_url(
            ARTICLE_URL,
            config=config_with(
                tmp_path,
                **{"source.url.timeout_sec": 7, "source.url.max_redirects": 2},
            ),
            now=NOW,
        )

    assert stub_http.calls[0]["timeout"] == 7
    assert [handler.max_redirections for handler in stub_http.handlers] == [2]


def test_a_redirect_that_never_lands_says_so(tmp_path: Path, stub_http: StubHTTP) -> None:
    """상한을 넘긴 리다이렉트는 `HTTPError`로 돌아온다 — 그때 상태 코드만 내면 원인을 잃는다."""
    stub_http.error = http_error(302, "Found")

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "리다이렉트" in str(error.value)


def test_a_response_over_the_byte_cap_is_rejected(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """표준 라이브러리 `urlopen`에는 크기 상한이 없다 (스파이크 #31 5장)."""
    stub_http.body = article_page()

    with pytest.raises(SourceError) as error:
        from_url(
            ARTICLE_URL, config=config_with(tmp_path, **{"source.url.max_bytes": 200}), now=NOW
        )

    assert "source.url.max_bytes" in str(error.value)


def test_a_timeout_is_reported_as_one(tmp_path: Path, stub_http: StubHTTP) -> None:
    stub_http.error = TimeoutError()

    with pytest.raises(SourceError) as error:
        from_url(
            ARTICLE_URL, config=config_with(tmp_path, **{"source.url.timeout_sec": 3}), now=NOW
        )

    assert "3초" in str(error.value)


def test_a_tls_failure_carries_the_original_error(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """사내 프록시 환경에서 원인을 알 수 있어야 한다 (스파이크 #31 5장).

    Python 3.13의 `VERIFY_X509_STRICT`가 기본이라 그 환경에서는 이 경로가 전부 실패한다 —
    원문 없이 "가져오지 못했다"만 남으면 사용자는 프록시가 아니라 링크를 의심한다.
    """
    raw = "certificate verify failed: Basic Constraints of CA cert not marked critical"
    stub_http.error = urllib.error.URLError(ssl.SSLCertVerificationError(raw))

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert raw in str(error.value)
    assert "SSL_CERT_FILE" in str(error.value)


def test_a_connection_failure_keeps_its_reason(tmp_path: Path, stub_http: StubHTTP) -> None:
    stub_http.error = urllib.error.URLError("[Errno 11001] getaddrinfo failed")

    with pytest.raises(SourceError) as error:
        from_url(ARTICLE_URL, config=config_with(tmp_path), now=NOW)

    assert "getaddrinfo failed" in str(error.value)
    assert "--text-file" in str(error.value)


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
