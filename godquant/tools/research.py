"""Universal research: fetch any link + multi-source briefs. Stdlib-only.

extract(url) handles YouTube (transcript when youtube-transcript-api is
installed, else oEmbed metadata + page text), TikTok / X / Reddit
(oEmbed), generic articles (HTML→text) and file:// paths.
research(query) = DuckDuckGo search + fetch the top hits + brief.
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("godquant.research")

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) godquant-research/1.0"}
_MAX_BYTES = 300000


def _get(url: str, timeout: int = 15) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        return r.read(_MAX_BYTES), ctype


def oembed(url: str) -> dict:
    """Best-effort oEmbed metadata for youtube/tiktok/x/reddit/vimeo."""
    host = urllib.parse.urlparse(url).netloc.lower()
    q = urllib.parse.quote(url, safe="")
    if "youtube.com" in host or "youtu.be" in host:
        ep = f"https://www.youtube.com/oembed?url={q}&format=json"
    elif "tiktok.com" in host:
        ep = f"https://www.tiktok.com/oembed?url={q}"
    elif "twitter.com" in host or "x.com" in host:
        ep = f"https://publish.twitter.com/oembed?url={q}"
    elif "reddit.com" in host:
        ep = f"https://www.reddit.com/oembed?url={q}"
    elif "vimeo.com" in host:
        ep = f"https://vimeo.com/api/oembed.json?url={q}"
    else:
        return {}
    try:
        raw, _ = _get(ep)
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.debug("oembed %s: %s", host, e)
        return {}


def youtube_transcript(url: str) -> str:
    """Video transcript, or '' if unavailable (lib missing / no captions)."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,})", url)
    if not m:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):
            tr = api.fetch(m.group(1))
            parts = [getattr(s, "text", "") for s in tr]
        else:  # older API
            tr = YouTubeTranscriptApi.get_transcript(m.group(1))
            parts = [s.get("text", "") for s in tr]
        return re.sub(r"\s+", " ",
                      " ".join(p for p in parts if p)).strip()
    except Exception as e:
        log.debug("transcript: %s", e)
        return ""


def html_text(html_doc: str) -> tuple[str, str]:
    """(title, text) from raw HTML. No dependencies."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html_doc, re.I | re.S)
    title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:200] \
        if m else ""
    body = re.sub(r"<head.*?</head>|<script.*?</script>|<style.*?</style>|"
                  r"<nav.*?</nav>|<footer.*?</footer>", " ", html_doc,
                  flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return title, re.sub(r"\s+", " ", html.unescape(body)).strip()


def extract(url: str, max_chars: int = 6000) -> dict:
    """Fetch any link → {title, text, source, kind}. Raises ValueError."""
    url = (url or "").strip()
    if url.startswith("file://"):
        p = urllib.parse.urlparse(url).path
        try:
            raw = open(p, encoding="utf-8", errors="replace").read()
        except Exception as e:
            raise ValueError(f"file unreadable: {e}")
        if p.lower().endswith((".html", ".htm")):
            title, text = html_text(raw)
        else:
            title, text = p.rsplit("/", 1)[-1], raw
        return {"title": title[:200], "text": text[:max_chars],
                "source": url, "kind": "file"}
    if not url.startswith(("http://", "https://")):
        raise ValueError("need http(s) or file url")
    host = urllib.parse.urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        tr = youtube_transcript(url)
        meta = oembed(url)
        if tr:
            return {"title": (meta.get("title") or "youtube")[:200],
                    "text": tr[:max_chars], "source": url,
                    "kind": "youtube-transcript"}
    meta = oembed(url) if any(h in host for h in
                              ("youtube", "youtu.be", "tiktok", "twitter",
                               "x.com", "reddit", "vimeo")) else {}
    try:
        raw, ctype = _get(url)
    except Exception as e:
        if meta.get("title"):
            oe = meta.get("title", "")
            if meta.get("author_name"):
                oe += f" (by {meta['author_name']})"
            return {"title": oe[:200], "text": oe, "source": url,
                    "kind": "oembed"}
        raise ValueError(f"fetch failed: {e}")
    if "json" in ctype:
        try:
            text = json.dumps(json.loads(raw.decode("utf-8", "replace")),
                              indent=1)[:max_chars]
        except Exception:
            text = raw.decode("utf-8", "replace")[:max_chars]
        return {"title": meta.get("title", host)[:200], "text": text,
                "source": url, "kind": "json"}
    title, text = html_text(raw.decode("utf-8", "replace"))
    if meta.get("title"):
        title = meta["title"][:200]
    return {"title": (title or host)[:200], "text": text[:max_chars],
            "source": url, "kind": "page"}


def fetch_text(url: str, max_chars: int = 2000) -> str:
    """TITLE:/TEXT: contract (registry web_fetch + server /fetch)."""
    d = extract(url, max_chars=max_chars)
    if d["title"]:
        return f"TITLE: {d['title']}\nTEXT: {d['text']}"
    return f"TEXT: {d['text']}"


def research(query: str, max_sources: int = 3) -> str:
    """Search + read the top hits → brief with numbered sources."""
    from godquant.companion import web_search as WS
    query = (query or "").strip()
    if not query:
        raise ValueError("need a query")
    try:
        hits = WS.ddg_text(query, max_results=max(1, min(5, max_sources)))
    except Exception as e:
        return f"search failed: {e}"
    if not hits:
        return f"no results for: {query}"
    parts = [f"RESEARCH: {query}"]
    for i, h in enumerate(hits, 1):
        href = h.get("href", "")
        try:
            d = extract(href, max_chars=1500)
            parts.append(f"\n[{i}] {d['title']}\n{d['source']}\n"
                         f"{d['text'][:1500]}")
        except Exception as e:
            parts.append(f"\n[{i}] {h.get('title', href)}\n{href}\n"
                         f"(unreadable: {e})")
    return "\n".join(parts)[:8000]
