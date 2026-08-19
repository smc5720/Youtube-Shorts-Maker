"""스파이크 #31 — 링크 본문 추출기 비교와 미지원 페이지 관측.

세 가지를 잰다.

1. **추출기 비교** — 같은 HTML에서 trafilatura / readability-lxml / justext / 표준
   라이브러리가 각각 무엇을 내는가.
2. **미지원 페이지** — 유료·로그인·JS 렌더링 페이지가 HTTP 상태와 추출 결과에서 어떻게
   보이는가. "페이지 유형을 판별할 수 있는가"의 답이 여기서 나온다.
3. **User-Agent** — 브라우저로 위장하지 않아도 기사 페이지를 받을 수 있는가.

실행:

    py -m venv .spike && .spike\\Scripts\\pip install trafilatura readability-lxml justext
    .spike\\Scripts\\python docs\\spikes\\31-article-extraction\\harness.py

기사 URL은 RSS에서 그때그때 뽑으므로 **다시 돌리면 다른 기사가 잡힌다.** 문서의 숫자는
`results.json`에 적힌 그날의 스냅숏이고, 다시 재야 하는 것은 절대값이 아니라 추출기 사이의
차이다.

주의 — Python 3.13의 `ssl.create_default_context()`는 `VERIFY_X509_STRICT`를 기본으로 켠다.
TLS를 가로채는 사내 프록시의 CA가 basicConstraints를 critical로 표시하지 않으면 그 플래그
하나 때문에 검증이 실패한다. 여기서는 플래그만 끄고 체인 검증은 유지한다.
"""

from __future__ import annotations

import gzip
import html.parser
import io
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path(__file__).with_name("results.json")

HONEST_UA = "shorts-maker/0.1 (+https://github.com/smc5720/Youtube-Shorts-Maker)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

FEEDS = {
    "yna": "https://www.yna.co.kr/rss/news.xml",
    "hani": "https://www.hani.co.kr/rss/",
    "mk": "https://www.mk.co.kr/rss/30000001/",
}

# 200을 주면서 본문이 아닌 것을 주는 페이지들. "유형 판별"이 가능한지 보려고 넣는다.
HARD_PAGES = {
    "js-render/login (x.com)": "https://x.com/home",
    "login (naver cafe)": "https://cafe.naver.com/joonggonara",
    "csr job post (wanted)": "https://www.wanted.co.kr/wd/265643",
    "long article (wikipedia)": "https://ko.wikipedia.org/wiki/%EC%9C%A0%ED%8A%9C%EB%B8%8C",
    "paywall (wsj)": "https://www.wsj.com/tech/ai",
    "paywall (nyt)": "https://www.nytimes.com/section/technology",
}

# 본문에 섞이면 안 되는 껍데기 문구. 사이트 공통 내비게이션·구독 유도에서 골랐다.
JUNK = [
    "회원가입",
    "로그인",
    "많이 본 뉴스",
    "구독하기",
    "댓글쓰기",
    "카카오톡 공유",
    "네이버 메인에서",
    "기사제보",
]


def context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def fetch(url: str, *, ua: str | None = HONEST_UA, timeout: int = 20) -> tuple[int, str, bytes]:
    headers = {"Accept-Encoding": "gzip", "Accept-Language": "ko"}
    if ua:
        headers["User-Agent"] = ua
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=context()) as res:
        raw = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return res.status, res.headers.get("Content-Type", ""), raw


class Strip(html.parser.HTMLParser):
    """표준 라이브러리 바닥선. script/style을 빼고 텍스트 노드만 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def _title_tag(markup: str) -> str | None:
    found = re.search(r"<title[^>]*>(.*?)</title>", markup, re.S | re.I)
    return found.group(1).strip() if found else None


def _decode(raw: bytes) -> str:
    """표준 라이브러리 경로가 직접 해야 하는 일. meta charset을 보고 디코드한다."""
    found = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    encoding = found.group(1).decode("ascii", "replace") if found else "utf-8"
    try:
        return raw.decode(encoding, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def by_stdlib(raw: bytes, url: str) -> tuple[str, str | None]:
    markup = _decode(raw)
    parser = Strip()
    parser.feed(markup)
    return parser.text(), _title_tag(markup)


def by_trafilatura(raw: bytes, url: str) -> tuple[str, str | None]:
    import trafilatura

    doc = trafilatura.extract(raw, url=url, output_format="json", with_metadata=True)
    if not doc:
        return "", None
    data = json.loads(doc)
    return data.get("text") or "", data.get("title")


def by_readability(raw: bytes, url: str) -> tuple[str, str | None]:
    from readability import Document

    doc = Document(_decode(raw))
    parser = Strip()
    parser.feed(doc.summary(html_partial=True))
    return parser.text(), doc.short_title()


def by_justext(raw: bytes, url: str) -> tuple[str, str | None]:
    import justext

    paragraphs = justext.justext(raw, justext.get_stoplist("Korean"))
    kept = [p.text for p in paragraphs if not p.is_boilerplate]
    return "\n".join(kept), _title_tag(_decode(raw))


EXTRACTORS = {
    "stdlib": by_stdlib,
    "trafilatura": by_trafilatura,
    "readability": by_readability,
    "justext": by_justext,
}


def article_urls() -> dict[str, str]:
    """RSS에서 기사 URL을 하나씩 뽑는다. 목록 페이지(링크에 숫자가 없는 것)는 버린다."""
    picked: dict[str, str] = {}
    for name, feed in FEEDS.items():
        try:
            _, _, raw = fetch(feed)
        except (urllib.error.URLError, OSError) as error:
            print(f"  RSS 실패 {name}: {error}")
            continue
        body = raw.decode("utf-8", "replace")
        links = re.findall(r"<link>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\]\s]+)", body)
        for link in links:
            if "rss" in link or not re.search(r"\d{5,}", link):
                continue
            picked[f"article ({name})"] = link
            break
    return picked


def measure_extractors(pages: dict[str, tuple[str, bytes]]) -> list[dict]:
    rows = []
    for label, (url, raw) in pages.items():
        for name, extract in EXTRACTORS.items():
            start = time.perf_counter()
            try:
                text, title = extract(raw, url)
                error = ""
            except Exception as exc:  # noqa: BLE001 - 스파이크는 실패도 결과다
                text, title, error = "", None, f"{type(exc).__name__}: {exc}"
            rows.append(
                {
                    "page": label,
                    "url": url,
                    "extractor": name,
                    "chars": len(text),
                    "ms": round((time.perf_counter() - start) * 1000),
                    "title": title,
                    "junk_hits": [j for j in JUNK if j in text],
                    "head": text[:120].replace("\n", " / "),
                    "error": error,
                }
            )
    return rows


def measure_user_agent(urls: list[str]) -> list[dict]:
    rows = []
    for url in urls:
        for name, ua in (("honest", HONEST_UA), ("browser", BROWSER_UA), ("none", None)):
            try:
                status, _, raw = fetch(url, ua=ua)
                rows.append({"url": url, "ua": name, "status": status, "bytes": len(raw)})
            except urllib.error.HTTPError as error:
                rows.append({"url": url, "ua": name, "status": error.code, "bytes": 0})
            except (urllib.error.URLError, OSError) as error:
                rows.append({"url": url, "ua": name, "status": type(error).__name__, "bytes": 0})
    return rows


def main() -> None:
    print("기사 URL 수집 중")
    targets = article_urls() | HARD_PAGES

    pages: dict[str, tuple[str, bytes]] = {}
    fetched: list[dict] = []
    for label, url in targets.items():
        try:
            status, ctype, raw = fetch(url)
            pages[label] = (url, raw)
            charset = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
            fetched.append(
                {
                    "page": label,
                    "url": url,
                    "status": status,
                    "content_type": ctype,
                    "bytes": len(raw),
                    "meta_charset": charset.group(1).decode("ascii", "replace") if charset else None,
                }
            )
        except urllib.error.HTTPError as error:
            fetched.append({"page": label, "url": url, "status": error.code, "bytes": 0})
            print(f"  {label}: HTTP {error.code}")
        except (urllib.error.URLError, OSError) as error:
            fetched.append({"page": label, "url": url, "status": type(error).__name__, "bytes": 0})
            print(f"  {label}: {error}")

    print(f"추출기 비교 — 페이지 {len(pages)}개")
    extraction = measure_extractors(pages)

    print("User-Agent 확인")
    agent = measure_user_agent([url for label, (url, _) in pages.items() if "article" in label])

    versions = {}
    for package in ("trafilatura", "readability-lxml", "justext", "lxml"):
        try:
            import importlib.metadata as meta

            versions[package] = meta.version(package)
        except Exception:  # noqa: BLE001
            versions[package] = None

    OUT.write_text(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "versions": versions,
                "fetch": fetched,
                "extraction": extraction,
                "user_agent": agent,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    last = None
    for row in extraction:
        if row["page"] != last:
            print(f"\n== {row['page']}  {row['url']}")
            last = row["page"]
        print(
            f"  {row['extractor']:<12} {row['chars']:>7}자 {row['ms']:>5}ms "
            f"junk={len(row['junk_hits'])}"
        )
        print(f"      {row['error'] or row['head']}")
    print(f"\n{OUT} 기록 완료")


if __name__ == "__main__":
    main()
