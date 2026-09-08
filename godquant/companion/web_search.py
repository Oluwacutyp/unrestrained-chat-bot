"""Dependency-free web research — stdlib port of research_features.py.

Uses DuckDuckGo's lite HTML endpoint over urllib (no duckduckgo-search package).
Serves both the CompanionAgent (chat search) and the ResearcherAgent (real
grounded research instead of pure generation). bs4/requests used only if present.
"""
from __future__ import annotations

import html as _html
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("godquant.search")

_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; Termux) AppleWebKit/537.36"}

try:
    from bs4 import BeautifulSoup as _BS  # type: ignore
except Exception:
    _BS = None


def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


def ddg_text(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo lite-HTML text search. Returns [{title, body, href}]."""
    url = ("https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query))
    try:
        page = _get(url)
    except Exception as e:
        log.debug("ddg search failed: %s", e)
        return []
    def _normalize(href: str) -> str:
        href = _html.unescape(href.strip())
        if href.startswith("//"):
            href = "https:" + href
        # lite endpoint wraps results: //duckduckgo.com/l/?uddg=<real url>
        if "duckduckgo.com/l/" in href:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            real = (q.get("uddg") or [""])[0]
            return real if real.startswith("http") else ""
        return href if href.startswith("http") else ""

    results = []
    if _BS is not None:
        soup = _BS(page, "html.parser")
        for a in soup.find_all("a", href=True):
            href = _normalize(a["href"])
            if not href:
                continue
            title = a.get_text(strip=True)
            if len(title) < 8:
                continue
            parent = a.find_parent("tr")
            body = parent.get_text(" ", strip=True) if parent else ""
            body = body.replace(title, "", 1).strip()[:300]
            results.append({"title": title[:200], "body": body, "href": href})
            if len(results) >= max_results:
                break
    else:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
            href = _normalize(m.group(1))
            if not href:
                continue
            title = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if len(title) < 8:
                continue
            results.append({"title": title[:200], "body": "", "href": href})
            if len(results) >= max_results * 2:
                break
    # de-dup preserving order
    seen, out = set(), []
    for r in results:
        if r["href"] not in seen:
            seen.add(r["href"])
            out.append(r)
    return out[:max_results]


def advanced_search(query: str, search_type: str = "text") -> str:
    """Port of the original advanced_search(): text | news | images | wiki | fact_check."""
    if search_type == "news":
        hits = ddg_text(f"{query} news", 5)
        lines = ["📰 Latest News:\n"]
        for i, h in enumerate(hits, 1):
            lines.append(f"{i}. {h['title']}\n   {h['body'][:200]}...\n   🔗 {h['href']}\n")
        return "\n".join(lines) if hits else "No news found."
    if search_type == "images":
        hits = ddg_text(f"{query} images", 5)
        lines = ["🖼 Image Results (pages hosting images):\n"]
        for i, h in enumerate(hits, 1):
            lines.append(f"{i}. {h['title']}\n   🔗 {h['href']}\n")
        return "\n".join(lines) if hits else "No image pages found."
    if search_type == "fact_check":
        return fact_check_query(query)
    if search_type == "wiki":
        return wiki_search(query)
    hits = ddg_text(query, 5)
    lines = ["🔍 Search Results:\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}\n   {h['body'][:250]}...\n   🔗 {h['href']}\n")
    return "\n".join(lines) if hits else "No results found."


def fetch_webpage_content(url: str, max_chars: int = 2000) -> str:
    """Port of the original scraper. bs4 if present, else regex stripping."""
    try:
        page = _get(url)
    except Exception as e:
        return f"Could not fetch webpage: {e}"
    if _BS is not None:
        soup = _BS(page, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    else:
        text = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", page)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = _html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def fact_check_query(claim: str) -> str:
    hits = ddg_text(f"{claim} site:snopes.com OR site:factcheck.org OR site:politifact.com", 3)
    if not hits:
        return "No fact-check information found. Claim could not be verified."
    lines = ["🔍 Fact Check Results:\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}\n   {h['body'][:200]}...\n   🔗 {h['href']}\n")
    return "\n".join(lines)


def wiki_search(query: str) -> str:
    # try the Wikipedia API first (fast, clean), fall back to DDG
    try:
        api = ("https://en.wikipedia.org/api/rest_v1/page/summary/" +
               urllib.parse.quote(query.replace(" ", "_")))
        import json
        data = json.loads(_get(api))
        if data.get("extract"):
            return f"📚 Wikipedia: {data['extract'][:800]}"
    except Exception:
        pass
    hits = ddg_text(f"{query} site:wikipedia.org", 1)
    if hits:
        return f"📚 Wikipedia: {hits[0]['title']} — {hits[0]['body'][:400]}"
    return "No Wikipedia article found."


def smart_search(user_message: str) -> str:
    """Port of the enhanced router: detect news/fact/wiki/text automatically."""
    lower = user_message.lower()
    if any(w in lower for w in ["news", "latest", "recent", "today", "price", "market"]):
        return advanced_search(user_message, "news")
    if any(w in lower for w in ["fact", "true or", "false or", "verify", "is it true"]):
        return fact_check_query(user_message)
    if "wikipedia" in lower or "wiki" in lower or lower.startswith(("what is", "who is", "who was")):
        return wiki_search(user_message)
    return advanced_search(user_message, "text")


def should_search(user_message: str) -> bool:
    lower = user_message.lower()
    return any(k in lower for k in ["search", "find", "what is", "who is", "latest",
                                    "news", "current", "price", "today", "verify",
                                    "wikipedia", "wiki", "fact check", "look up"])
