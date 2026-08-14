"""Film festival business logic."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import Festival, FestivalFilm, GuildConfig
from bot.utils.birthday_emojis import escape_markdown_inline, guild_emoji_pool
from bot.utils.formatting import format_duration
from bot.utils.permissions import fetch_bot_member, is_guild_admin
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime

FEST_ROLE_NAME = "Кино"
MSK_TIMEZONE = "Europe/Moscow"
DEFAULT_RUNTIME_MINUTES = 120
POSTER_USER_AGENT = "ErundaBot/1.0 (https://github.com/K0DDO/ErundaBot)"
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


_TITLE_JUNK_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_RATING_TOKEN_RE = re.compile(
    r"(?i)(?<![^\s,;|/_\-()\[\]{}])(nsfw|нсфв|18\+|16\+|12\+|6\+|0\+)(?![^\s,;|/_\-()\[\]{}])"
)
_ITUNES_AGE = {
    "G": "0+",
    "TVG": "0+",
    "0+": "0+",
    "PG": "6+",
    "TVPG": "6+",
    "6+": "6+",
    "PG13": "12+",
    "TV14": "12+",
    "12+": "12+",
    "R": "16+",
    "16+": "16+",
    "NC17": "18+",
    "TVMA": "18+",
    "18+": "18+",
}


def split_title_and_age(title: str) -> tuple[str, str | None]:
    tokens: list[str] = []

    def collect(match: re.Match[str]) -> str:
        tokens.append(match.group(1).casefold())
        return " "

    cleaned = _RATING_TOKEN_RE.sub(collect, title)
    cleaned = re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", cleaned)
    cleaned = re.sub(r"^[\s,;|/_\-]+|[\s,;|/_\-]+$", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    rating = None
    if any(token in {"nsfw", "нсфв"} for token in tokens):
        rating = "NSFW"
    else:
        for age in ("18+", "16+", "12+", "6+", "0+"):
            if age in tokens:
                rating = age
                break
    return cleaned, rating


def normalize_film_title(title: str) -> str:
    cleaned, _ = split_title_and_age(title)
    cleaned = " ".join(cleaned.split())
    words: list[str] = []
    for word in cleaned.split(" "):
        if not word:
            continue
        bits = word.split("-")
        words.append("-".join(bit[:1].upper() + bit[1:] if bit else bit for bit in bits))
    return " ".join(words)


def film_title_key(title: str) -> str:
    text = normalize_film_title(title).casefold().replace("ё", "е")
    text = _TITLE_JUNK_RE.sub(" ", text)
    return " ".join(text.split())


_KNOWN_AGES = {"NSFW", "18+", "16+", "12+", "6+", "0+"}
_AGE_RANK = {"0+": 0, "6+": 1, "12+": 2, "16+": 3, "18+": 4, "NSFW": 5}
_AGE_CACHE: dict[str, str] = {}
_WIKIDATA_CLAIMS_CACHE: dict[str, dict] = {}
_WIKIDATA_RATING_PROPS = (
    "P1657",
    "P1981",
    "P2629",
    "P2758",
    "P3402",
    "P3834",
    "P2363",
    "P4437",
    "P7573",
    "P2756",
)
_FILM_INSTANCE_IDS = {
    "Q11424",
    "Q202866",
    "Q229390",
    "Q29168811",
    "Q506240",
}
_AGE_STOP_WORDS = {"фильм", "film", "the", "a", "an", "и", "movie", "кино"}
_CERT_TO_AGE = {
    "g": "0+",
    "tv-g": "0+",
    "tvg": "0+",
    "u": "0+",
    "0+": "0+",
    "0": "0+",
    "pg": "6+",
    "tv-pg": "6+",
    "tvpg": "6+",
    "6+": "6+",
    "6": "6+",
    "pg-13": "12+",
    "pg13": "12+",
    "tv-14": "12+",
    "tv14": "12+",
    "12+": "12+",
    "12": "12+",
    "r": "18+",
    "16+": "16+",
    "16": "16+",
    "15": "16+",
    "nc-17": "18+",
    "nc17": "18+",
    "tv-ma": "18+",
    "tvma": "18+",
    "18+": "18+",
    "18": "18+",
}
_TMDB_SEARCH_CACHE: dict[str, str] = {}
_TMDB_META_CACHE: dict[str, tuple[str, str, int]] = {}
_TMDB_MOVIE_HREF_RE = re.compile(
    r'href="/movie/(\d+)(?:-([^"?]*))?',
    re.IGNORECASE,
)
_TMDB_CERT_RE = re.compile(
    r'<span[^>]*class="[^"]*certification[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)
_TMDB_RUNTIME_RE = re.compile(
    r'<span[^>]*class="[^"]*runtime[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)
_TMDB_ISO_DURATION_RE = re.compile(r'"duration"\s*:\s*"(PT[^"]+)"')


def film_age_rating(film: FestivalFilm) -> str | None:
    rating = (film.age_rating or "").strip()
    if rating in _KNOWN_AGES:
        return rating
    _cleaned, parsed = split_title_and_age(film.title)
    return parsed


def format_age_tag(rating: str | None) -> str:
    if not rating:
        return ""
    if rating in {"NSFW", "18+"}:
        return f" · 🔞 {rating}"
    return f" · {rating}"


def _http_json(url: str, timeout: int = 8) -> dict | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": POSTER_USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _http_html(url: str, timeout: int = 8) -> str | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": POSTER_USER_AGENT, "Accept": "text/html"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _og_image_from_html(html: str) -> str | None:
    match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE_ALT.search(html)
    if match is None:
        return None
    image = match.group(1).strip()
    if image.startswith("//"):
        image = "https:" + image
    if image.startswith("https://"):
        return image
    return None


def _kinopoisk_poster(title: str) -> str | None:
    url = "https://www.kinopoisk.ru/index.php?" + urllib.parse.urlencode({"kp_query": title})
    html = _http_html(url)
    if not html:
        return None
    return _og_image_from_html(html)


def _wikipedia_poster(title: str) -> str | None:
    queries = (
        ("ru", f"{title} фильм"),
        ("en", f"{title} film"),
    )
    for lang, query in queries:
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 5,
                    "format": "json",
                }
            )
        )
        data = _http_json(search_url)
        hits = ((data or {}).get("query") or {}).get("search") or []
        for hit in hits:
            page_id = hit.get("pageid")
            if not page_id:
                continue
            image_url = (
                f"https://{lang}.wikipedia.org/w/api.php?"
                + urllib.parse.urlencode(
                    {
                        "action": "query",
                        "pageids": page_id,
                        "prop": "pageimages",
                        "pithumbsize": 500,
                        "pilicense": "any",
                        "format": "json",
                    }
                )
            )
            page_data = _http_json(image_url)
            pages = ((page_data or {}).get("query") or {}).get("pages") or {}
            page = pages.get(str(page_id)) or {}
            source = ((page.get("thumbnail") or {}).get("source")) if isinstance(page, dict) else None
            if isinstance(source, str) and source.startswith("https://"):
                return source
    return None


def _itunes_lookup(title: str, country: str = "ru") -> tuple[str | None, str | None]:
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {
            "term": title,
            "entity": "movie",
            "country": country,
            "limit": 3,
        }
    )
    data = _http_json(url)
    poster = None
    rating = None
    for item in (data or {}).get("results") or []:
        if poster is None:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if isinstance(art, str) and art.startswith("https://"):
                poster = (
                    art.replace("100x100bb", "600x600bb")
                    .replace("60x60bb", "600x600bb")
                )
        if rating is None:
            raw = item.get("contentAdvisoryRating")
            if isinstance(raw, str):
                key = raw.strip().upper().replace(" ", "").replace("-", "")
                rating = _ITUNES_AGE.get(key)
        if poster and rating:
            break
    return poster, rating


def _itunes_poster(title: str) -> str | None:
    poster, _rating = _itunes_lookup(title)
    return poster


def _age_from_rating_label(label: str) -> str | None:
    text = (
        label.casefold()
        .replace(" ", "")
        .replace("_", "")
        .replace("–", "-")
        .replace("—", "-")
    )
    mapped = _CERT_TO_AGE.get(text)
    if mapped:
        return mapped
    if any(token in text for token in ("nc-17", "nc17", "tv-ma", "tvma")):
        return "18+"
    if "pg-13" in text or "pg13" in text or "tv-14" in text or "tv14" in text:
        return "12+"
    if re.search(r"fsk18|(?:^|[^0-9])18(?:\+|p|$)", text):
        return "18+"
    if re.fullmatch(r"r|ratedr|r-rated", text):
        return "18+"
    if re.search(r"fsk16|(?:^|[^0-9])16(?:\+|p|$)|(?:^|[^0-9])15(?:\+|a|$)", text):
        return "16+"
    if re.search(r"fsk12|(?:^|[^0-9])12(?:\+|p|a|$)|undernwelve", text):
        return "12+"
    if "fsk6" in text or text.startswith("pg") or re.search(r"(?:^|[^0-9])6(?:\+|$)", text):
        return "6+"
    if (
        re.fullmatch(r"g|u|0\+|0", text)
        or text in {"ucertificate", "ucert"}
        or "noagerestriction" in text
        or "безвозраст" in text
    ):
        return "0+"
    return None


def _fold_age_text(text: str) -> str:
    text = text.casefold().replace("ё", "е").replace("э", "е")
    text = _TITLE_JUNK_RE.sub(" ", text)
    return " ".join(text.split())


def _age_tokens(text: str) -> list[str]:
    return [token for token in _fold_age_text(text).split() if token and token not in _AGE_STOP_WORDS]


def _sequel_mark(text: str) -> int:
    folded = f" {_fold_age_text(text)} "
    if "росомаха" in folded or "wolverine" in folded:
        return 3
    for number, token in ((4, "4"), (3, "3"), (3, "iii"), (2, "2"), (2, "ii")):
        if f" {token} " in folded:
            return number
    return 1


def _title_score(query: str, candidate: str) -> float:
    if not candidate.strip():
        return 0.0
    query_tokens = _age_tokens(query)
    candidate_tokens = _age_tokens(candidate)
    query_words = {token for token in query_tokens if not token.isdigit()}
    candidate_words = {token for token in candidate_tokens if not token.isdigit()}
    if not query_words or not candidate_words:
        return 0.0
    overlap = query_words & candidate_words
    if not overlap:
        return 0.0
    score = len(overlap) / len(query_words)
    if query_words == candidate_words:
        score += 0.4
    query_seq = _sequel_mark(query)
    candidate_seq = _sequel_mark(candidate)
    if query_seq == candidate_seq:
        score += 0.25
    else:
        score -= 0.35
    return score


def _search_terms(title: str) -> list[str]:
    raw = " ".join(title.split())
    terms = [raw]
    stripped = re.sub(r"[\s,._-]+(1|i)$", "", raw, flags=re.IGNORECASE).strip()
    if stripped and stripped.casefold() != raw.casefold():
        terms.append(stripped)
    return list(dict.fromkeys(terms))


def _wikidata_search_items(term: str, language: str) -> list[tuple[str, str, str]]:
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "search": term,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": 10,
            "format": "json",
        }
    )
    data = _http_json(url)
    found: list[tuple[str, str, str]] = []
    for item in (data or {}).get("search") or []:
        entity_id = item.get("id")
        label = item.get("label") or ""
        description = item.get("description") or ""
        if isinstance(entity_id, str) and entity_id.startswith("Q"):
            found.append((entity_id, str(label), str(description)))
    return found


def _wikipedia_search_items(term: str, lang: str) -> list[tuple[str, str]]:
    search_url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": term,
                "srlimit": 8,
                "format": "json",
            }
        )
    )
    data = _http_json(search_url)
    found: list[tuple[str, str]] = []
    for hit in ((data or {}).get("query") or {}).get("search") or []:
        page_id = hit.get("pageid")
        title = hit.get("title") or ""
        if not page_id:
            continue
        props_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "pageids": page_id,
                    "prop": "pageprops",
                    "format": "json",
                }
            )
        )
        page_data = _http_json(props_url)
        pages = ((page_data or {}).get("query") or {}).get("pages") or {}
        page = pages.get(str(page_id)) or {}
        entity = ((page.get("pageprops") or {}).get("wikibase_item")) if isinstance(page, dict) else None
        if isinstance(entity, str) and entity.startswith("Q"):
            found.append((entity, str(title)))
    return found


def _find_film_entity(title: str) -> str | None:
    scored: dict[str, float] = {}
    texts: dict[str, list[str]] = {}

    def consider(entity_id: str, *labels: str) -> None:
        if entity_id in texts:
            texts[entity_id].extend(label for label in labels if label)
            return
        if not _wikidata_is_film(entity_id):
            return
        texts[entity_id] = [label for label in labels if label]

    def best_match() -> tuple[str | None, float]:
        winner_id = None
        winner_score = 0.0
        for entity_id, labels in texts.items():
            score = max((_title_score(title, label) for label in labels), default=0.0)
            scored[entity_id] = score
            if score > winner_score:
                winner_score = score
                winner_id = entity_id
        return winner_id, winner_score

    for term in _search_terms(title):
        for language in ("ru", "en"):
            for entity_id, label, description in _wikidata_search_items(term, language):
                consider(entity_id, label, description)
    entity_id, score = best_match()
    if entity_id is not None and score >= 0.4:
        return entity_id
    for term in _search_terms(title):
        for lang, query in (("ru", f"{term} фильм"), ("ru", term), ("en", f"{term} film"), ("en", term)):
            for wiki_id, page_title in _wikipedia_search_items(query, lang):
                consider(wiki_id, page_title)
    entity_id, score = best_match()
    if entity_id is None or score < 0.4:
        return None
    return entity_id


def _wikidata_claims(entity_id: str) -> dict:
    cached = _WIKIDATA_CLAIMS_CACHE.get(entity_id)
    if cached is not None:
        return cached
    claims_url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": "claims",
            "format": "json",
        }
    )
    data = _http_json(claims_url)
    entity = ((data or {}).get("entities") or {}).get(entity_id) or {}
    claims = entity.get("claims") or {}
    if not isinstance(claims, dict):
        claims = {}
    _WIKIDATA_CLAIMS_CACHE[entity_id] = claims
    return claims


def _wikidata_is_film(entity_id: str) -> bool:
    for claim in _wikidata_claims(entity_id).get("P31") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        qid = value.get("id") if isinstance(value, dict) else None
        if qid in _FILM_INSTANCE_IDS:
            return True
    return False


def _iso_duration_minutes(value: str) -> int | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip().upper())
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 60 + minutes + (1 if seconds else 0)
    return total or None


def _label_duration_minutes(text: str) -> int | None:
    folded = text.casefold().replace("ё", "е")
    hours = re.search(r"(\d+)\s*(?:h|ч)", folded)
    minutes = re.search(r"(\d+)\s*(?:m|мин)", folded)
    total = 0
    if hours:
        total += int(hours.group(1)) * 60
    if minutes:
        total += int(minutes.group(1))
    return total or None


def _tmdb_meta(tmdb_id: str) -> tuple[str | None, str | None, int | None]:
    cached = _TMDB_META_CACHE.get(tmdb_id)
    if cached is not None:
        rating, poster, runtime = cached
        return rating or None, poster or None, runtime or None
    html = _http_html(f"https://www.themoviedb.org/movie/{tmdb_id}?language=ru")
    if not html:
        html = _http_html(f"https://www.themoviedb.org/movie/{tmdb_id}")
    rating = None
    poster = None
    runtime = None
    if html:
        match = _TMDB_CERT_RE.search(html)
        raw = " ".join(match.group(1).split()) if match else ""
        if raw:
            rating = _age_from_rating_label(raw)
            if rating is None:
                token = re.search(r"\b(18\+|16\+|12\+|6\+|0\+)\b", raw)
                rating = token.group(1) if token else None
        poster = _og_image_from_html(html)
        if poster:
            poster = poster.replace("/t/p/w500/", "/t/p/w780/")
        iso = _TMDB_ISO_DURATION_RE.search(html)
        if iso:
            runtime = _iso_duration_minutes(iso.group(1))
        if runtime is None:
            label = _TMDB_RUNTIME_RE.search(html)
            if label:
                runtime = _label_duration_minutes(" ".join(label.group(1).split()))
    _TMDB_META_CACHE[tmdb_id] = (rating or "", poster or "", runtime or 0)
    return rating, poster, runtime


def _tmdb_age(tmdb_id: str) -> str | None:
    return _tmdb_meta(tmdb_id)[0]


def _tmdb_search_id(title: str) -> str | None:
    key = film_title_key(title)
    cached = _TMDB_SEARCH_CACHE.get(key)
    if cached is not None:
        return cached or None
    best_id: str | None = None
    best_score = 0.0
    fallback: str | None = None
    for term in _search_terms(title):
        url = "https://www.themoviedb.org/search/movie?" + urllib.parse.urlencode(
            {"query": term, "language": "ru"}
        )
        html = _http_html(url)
        if not html:
            continue
        seen: set[str] = set()
        for match in _TMDB_MOVIE_HREF_RE.finditer(html):
            tmdb_id = match.group(1)
            if tmdb_id in seen:
                continue
            seen.add(tmdb_id)
            slug = (match.group(2) or "").replace("-", " ")
            if fallback is None:
                fallback = tmdb_id
            score = _title_score(title, slug)
            if score == 0 and slug:
                score = 0.5
                if _sequel_mark(title) == _sequel_mark(slug):
                    score += 0.25
                else:
                    score -= 0.35
            if score > best_score:
                best_score = score
                best_id = tmdb_id
    found = best_id if best_score >= 0.4 else fallback
    _TMDB_SEARCH_CACHE[key] = found or ""
    return found


def _wikidata_age(entity_id: str) -> str | None:
    claims = _wikidata_claims(entity_id)
    qids: list[str] = []
    for prop in _WIKIDATA_RATING_PROPS:
        for claim in claims.get(prop) or []:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            qid = value.get("id") if isinstance(value, dict) else None
            if isinstance(qid, str) and qid.startswith("Q"):
                qids.append(qid)
    best: str | None = None
    if qids:
        labels_url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {
                "action": "wbgetentities",
                "ids": "|".join(dict.fromkeys(qids)),
                "props": "labels",
                "languages": "en|ru",
                "format": "json",
            }
        )
        labels_data = _http_json(labels_url)
        for item in ((labels_data or {}).get("entities") or {}).values():
            labels = item.get("labels") or {}
            for lang in ("en", "ru"):
                raw = ((labels.get(lang) or {}).get("value"))
                if not isinstance(raw, str):
                    continue
                mapped = _age_from_rating_label(raw)
                if mapped is None:
                    continue
                if best is None or _AGE_RANK[mapped] > _AGE_RANK[best]:
                    best = mapped
    ages: list[str] = []
    if best:
        ages.append(best)
    for claim in claims.get("P4947") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.isdigit():
            tmdb = _tmdb_age(value)
            if tmdb:
                ages.append(tmdb)
            break
    if not ages:
        return None
    return max(ages, key=lambda item: _AGE_RANK[item])


def fetch_film_age(title: str) -> str | None:
    key = film_title_key(title)
    if key in _AGE_CACHE:
        return _AGE_CACHE[key] or None
    rating = None
    tmdb_id = _tmdb_search_id(title)
    if tmdb_id:
        rating = _tmdb_age(tmdb_id)
    if rating is None:
        entity = _find_film_entity(title)
        if entity:
            rating = _wikidata_age(entity)
    _AGE_CACHE[key] = rating or ""
    return rating


def fetch_film_poster(title: str) -> str | None:
    tmdb_id = _tmdb_search_id(title)
    if tmdb_id:
        poster = _tmdb_meta(tmdb_id)[1]
        if poster:
            return poster
    return _wikipedia_poster(title) or _itunes_poster(title) or _kinopoisk_poster(title)


def fetch_film_runtime(title: str) -> int | None:
    tmdb_id = _tmdb_search_id(title)
    if not tmdb_id:
        return None
    return _tmdb_meta(tmdb_id)[2]


def pick_guild_emoji(guild: discord.Guild | None, seed: int) -> str:
    if guild is None:
        return "🎬"
    usable = [emoji for emoji in guild_emoji_pool(guild) if getattr(emoji, "available", True)]
    if not usable:
        return "🎬"
    return str(usable[seed % len(usable)])


class FestivalService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_open(self, guild_id: int) -> Festival | None:
        return await self.db.get_open_festival(guild_id)

    async def require_open(self, guild_id: int) -> Festival:
        festival = await self.get_open(guild_id)
        if festival is None:
            raise ValueError("Нет открытого кинофестиваля. Нужен /fest new")
        return festival

    async def require_by_number(self, guild_id: int, number: int) -> Festival:
        festival = await self.db.get_festival_by_number(guild_id, number)
        if festival is None:
            raise ValueError(f"Кинофестиваль #{number} не найден")
        return festival

    def has_staff(self, member: discord.Member, config: GuildConfig) -> bool:
        if is_guild_admin(member):
            return True
        if config.fest_staff_role_id is None:
            return False
        return any(role.id == config.fest_staff_role_id for role in member.roles)

    async def require_staff(self, member: discord.Member, config: GuildConfig) -> None:
        if not self.has_staff(member, config):
            raise ValueError("Нужна роль «Кино» или права администратора")

    async def ensure_staff_role(self, guild: discord.Guild) -> discord.Role:
        config = await self.db.ensure_guild(guild.id)
        if config.fest_staff_role_id:
            role = guild.get_role(config.fest_staff_role_id)
            if role is not None:
                return role
        bot_member = await fetch_bot_member(guild)
        if not bot_member.guild_permissions.manage_roles:
            raise ValueError("У бота нет права Manage Roles")
        role = await guild.create_role(
            name=FEST_ROLE_NAME,
            mentionable=True,
            reason="Ерунда: роль кинофестиваля",
        )
        await self.db.update_guild(guild.id, fest_staff_role_id=role.id)
        return role

    async def toggle_staff_role(self, guild: discord.Guild, member: discord.Member) -> bool:
        role = await self.ensure_staff_role(guild)
        bot_member = await fetch_bot_member(guild)
        if role >= bot_member.top_role:
            raise ValueError("Роль бота должна быть выше роли «Кино»")
        if role in member.roles:
            await member.remove_roles(role, reason="Ерунда: /fest role")
            return False
        await member.add_roles(role, reason="Ерунда: /fest role")
        return True

    async def create(
        self,
        guild_id: int,
        date_str: str,
        time_str: str,
        tz_name: str,
    ) -> tuple[Festival, Festival | None]:
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        previous = await self.get_open(guild_id)
        if previous is not None:
            previous = await self.db.update_festival(previous.id, status="closed")
        festival = await self.db.create_festival(guild_id, starts_at.isoformat())
        return festival, previous

    async def update_starts(
        self,
        guild_id: int,
        date_str: str,
        time_str: str,
        tz_name: str,
    ) -> Festival:
        festival = await self.require_open(guild_id)
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        return await self.db.update_festival(
            festival.id,
            starts_at=starts_at.isoformat(),
            reminder_sent=0,
        )

    async def delete_and_renumber(self, festival: Festival) -> list[Festival]:
        guild_id = festival.guild_id
        winner = festival.winner_film
        await self.db.delete_festival(festival.id)
        remaining = await self.db.renumber_festivals(guild_id)
        if winner:
            key = film_title_key(winner)
            still_won = any(
                item.winner_film and film_title_key(item.winner_film) == key
                for item in remaining
            )
            if not still_won:
                await self.db.remove_blocked_film(guild_id, key)
        return remaining

    async def set_message(self, festival_id: int, channel_id: int, message_id: int) -> Festival:
        return await self.db.update_festival(
            festival_id,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def add_film(self, guild_id: int, user_id: int, title: str) -> tuple[Festival, FestivalFilm, bool]:
        festival = await self.require_open(guild_id)
        _ignored, rating = split_title_and_age(title)
        cleaned = normalize_film_title(title)
        if not cleaned:
            raise ValueError("Название фильма пустое")
        if await self.db.is_film_blocked(guild_id, film_title_key(cleaned)):
            raise ValueError("Этот фильм уже нельзя предлагать")
        previous = await self.db.get_previous_festival(guild_id, festival.number)
        if previous is not None and previous.winner_user_id == user_id:
            raise ValueError(
                "Ты победил в прошлом кинофестивале. Предложить фильм можно со следующего"
            )
        existing = await self.db.get_festival_film(festival.id, user_id)
        if rating is None:
            _AGE_CACHE.pop(film_title_key(cleaned), None)
            rating = await asyncio.to_thread(fetch_film_age, cleaned)
        runtime = await asyncio.to_thread(fetch_film_runtime, cleaned)
        film = await self.db.upsert_festival_film(
            festival.id,
            user_id,
            cleaned,
            None,
            rating or "",
            runtime,
            overwrite_age=True,
        )
        return festival, film, existing is not None

    async def ensure_posters(
        self,
        festival_id: int,
        *,
        user_id: int | None = None,
    ) -> list[FestivalFilm]:
        films = await self.films(festival_id)
        result: list[FestivalFilm] = []
        for film in films:
            if user_id is not None and film.user_id != user_id:
                result.append(film)
                continue
            if film.image_url:
                result.append(film)
                continue
            image_url = await asyncio.to_thread(fetch_film_poster, film.title)
            runtime = film.runtime_minutes or await asyncio.to_thread(
                fetch_film_runtime, film.title
            )
            if image_url or runtime:
                film = await self.db.upsert_festival_film(
                    festival_id,
                    film.user_id,
                    film.title,
                    image_url,
                    film.age_rating,
                    runtime,
                )
            result.append(film)
        return result

    async def ensure_age_ratings(
        self,
        festival_id: int,
        *,
        user_id: int | None = None,
        fetch: bool = True,
    ) -> list[FestivalFilm]:
        films = await self.films(festival_id)
        result: list[FestivalFilm] = []
        for film in films:
            if user_id is not None and film.user_id != user_id:
                result.append(film)
                continue
            cleaned, parsed = split_title_and_age(film.title)
            cleaned = normalize_film_title(cleaned or film.title)
            if not cleaned:
                result.append(film)
                continue
            title_changed = cleaned != film.title
            known = film.age_rating in _KNOWN_AGES
            if known and not title_changed:
                result.append(film)
                continue
            rating = parsed
            if rating is None and fetch and not known:
                rating = await asyncio.to_thread(fetch_film_age, cleaned)
            if rating is None and known and not title_changed:
                rating = film.age_rating
            if rating is None:
                rating = ""
            if rating == (film.age_rating or "") and cleaned == film.title:
                result.append(film)
                continue
            film = await self.db.upsert_festival_film(
                festival_id,
                film.user_id,
                cleaned,
                film.image_url,
                rating,
                film.runtime_minutes,
                overwrite_age=True,
            )
            result.append(film)
        return result

    async def remove_film(self, guild_id: int, user_id: int) -> Festival:
        festival = await self.require_open(guild_id)
        if not await self.db.remove_festival_film(festival.id, user_id):
            raise ValueError("Ты не предлагал фильм")
        return festival

    async def set_winner(self, guild_id: int, user_id: int) -> tuple[Festival, FestivalFilm]:
        festival = await self.require_open(guild_id)
        film = await self.db.get_festival_film(festival.id, user_id)
        if film is None:
            raise ValueError("У этого человека нет фильма в текущем фестивале")
        festival = await self.db.update_festival(
            festival.id,
            winner_user_id=user_id,
            winner_film=normalize_film_title(film.title),
            status="closed",
        )
        await self.db.add_blocked_film(
            guild_id,
            film_title_key(film.title),
            normalize_film_title(film.title),
        )
        return festival, film

    async def ensure_runtime(self, film: FestivalFilm) -> FestivalFilm:
        if film.runtime_minutes:
            return film
        runtime = await asyncio.to_thread(fetch_film_runtime, film.title)
        if not runtime:
            return film
        return await self.db.upsert_festival_film(
            film.festival_id,
            film.user_id,
            film.title,
            film.image_url,
            film.age_rating,
            runtime,
        )

    async def set_film_score(
        self,
        festival_id: int,
        user_id: int,
        score: int,
    ) -> tuple[Festival, float | None, int]:
        if score < 1 or score > 10:
            raise ValueError("Оценка от 1 до 10")
        festival = await self.db.get_festival(festival_id)
        if festival is None:
            raise ValueError("Кинофестиваль не найден")
        if not festival.winner_user_id:
            raise ValueError("Пока нечего оценивать")
        winner = await self.db.get_festival_film(festival.id, festival.winner_user_id)
        runtime = winner.runtime_minutes if winner is not None else None
        if self.session_phase(festival, runtime) == "upcoming":
            raise ValueError("Сеанс ещё не начался")
        await self.db.upsert_festival_rating(festival_id, user_id, score)
        average, count = await self.db.festival_rating_stats(festival_id)
        return festival, average, count

    async def block_film(self, guild_id: int, title: str) -> tuple[str, Festival | None]:
        cleaned = normalize_film_title(title)
        key = film_title_key(cleaned)
        if not key:
            raise ValueError("Название фильма пустое")
        added = await self.db.add_blocked_film(guild_id, key, cleaned)
        if not added:
            raise ValueError(f"**{cleaned}** уже нельзя предлагать")
        festival = await self.get_open(guild_id)
        if festival is not None:
            for film in await self.films(festival.id):
                if film_title_key(film.title) == key:
                    await self.db.remove_festival_film(festival.id, film.user_id)
        return cleaned, festival

    async def films(self, festival_id: int) -> list[FestivalFilm]:
        return await self.db.list_festival_films(festival_id)

    def format_starts(self, festival: Festival, tz_name: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(festival.starts_at)
        return format_datetime_local(dt, tz_name)

    def _starts_at(self, festival: Festival) -> datetime:
        dt = datetime.fromisoformat(festival.starts_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def session_bounds(
        self,
        festival: Festival,
        runtime_minutes: int | None = None,
    ) -> tuple[datetime, datetime]:
        starts = self._starts_at(festival)
        minutes = runtime_minutes if runtime_minutes and runtime_minutes > 0 else DEFAULT_RUNTIME_MINUTES
        return starts, starts + timedelta(minutes=minutes)

    def session_phase(
        self,
        festival: Festival,
        runtime_minutes: int | None = None,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(timezone.utc)
        starts, ends = self.session_bounds(festival, runtime_minutes)
        if current < starts:
            return "upcoming"
        if current < ends:
            return "playing"
        return "finished"

    def session_text(
        self,
        festival: Festival,
        runtime_minutes: int | None = None,
        *,
        has_winner: bool = False,
    ) -> str:
        starts, ends = self.session_bounds(festival, runtime_minutes)
        phase = self.session_phase(festival, runtime_minutes)
        if not has_winner or phase == "upcoming":
            date_label, time_label = format_datetime_local(starts, MSK_TIMEZONE)
            unix = int(starts.timestamp())
            return (
                f"Сеанс: **{date_label} {time_label} МСК**\n"
                f"-# местное время: <t:{unix}:f>"
            )
        length = format_duration(int((ends - starts).total_seconds()))
        if phase == "playing":
            end_unix = int(ends.timestamp())
            return f"Сеанс: **идёт** · {length}\n-# до конца: <t:{end_unix}:R>"
        return f"Сеанс: **прошёл** · {length}"

    def starts_input(self, festival: Festival, tz_name: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(festival.starts_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%d.%m.%Y"), local.strftime("%H:%M")

    def remaining_label(self, festival: Festival, now: datetime | None = None) -> str:
        starts = datetime.fromisoformat(festival.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return format_countdown((starts - current).total_seconds())

    def _display_name(self, user_id: int, guild: discord.Guild | None) -> str:
        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                return escape_markdown_inline(member.display_name)
        return f"участник #{user_id}"

    def film_list_text(
        self,
        films: list[FestivalFilm],
        guild: discord.Guild | None = None,
    ) -> str:
        shown = films[:40]
        if not shown:
            return "_Пока никто не предложил._"
        lines = [
            (
                f"**{self._display_name(film.user_id, guild)}** — "
                f"{normalize_film_title(film.title)}{format_age_tag(film_age_rating(film))}"
            )
            for film in shown
        ]
        extra = len(films) - len(shown)
        if extra > 0:
            lines.append(f"📌 …и ещё {extra}")
        return "\n".join(lines)

    def card_sections(
        self,
        festival: Festival,
        films: list[FestivalFilm],
        tz_name: str,
        guild: discord.Guild | None = None,
        *,
        winner_emoji: str = "🎬",
        ping_role: discord.Role | None = None,
        rating_average: float | None = None,
        rating_count: int = 0,
    ) -> list[str]:
        has_winner = bool(festival.winner_user_id and festival.winner_film)
        winner_film = next(
            (film for film in films if film.user_id == festival.winner_user_id),
            None,
        ) if has_winner else None
        runtime = winner_film.runtime_minutes if winner_film is not None else None
        sections: list[str] = []
        if has_winner and ping_role is not None:
            sections.append(ping_role.mention)
        sections.append(self.session_text(festival, runtime, has_winner=has_winner))
        sections.append(self.film_list_text(films, guild))
        if has_winner:
            winner_rating = film_age_rating(winner_film) if winner_film is not None else None
            score_line = "Оценка: пока нет"
            if rating_count:
                score_line = f"Оценка: **{rating_average:.1f}** · {rating_count}"
            sections.append(
                f"### {winner_emoji} {normalize_film_title(festival.winner_film or '')}"
                f"{format_age_tag(winner_rating)}\n{score_line}"
            )
        else:
            sections.append("Победитель: ещё не выбран")
        return sections

    def card_body(
        self,
        festival: Festival,
        films: list[FestivalFilm],
        tz_name: str,
        guild: discord.Guild | None = None,
        *,
        winner_emoji: str = "🎬",
        ping_role: discord.Role | None = None,
    ) -> str:
        return "\n\n".join(
            self.card_sections(
                festival,
                films,
                tz_name,
                guild,
                winner_emoji=winner_emoji,
                ping_role=ping_role,
            )
        )

    def poster_urls(self, festival: Festival, films: list[FestivalFilm]) -> list[tuple[str, str]]:
        chosen: list[FestivalFilm]
        if festival.winner_user_id:
            chosen = [
                film
                for film in films
                if film.user_id == festival.winner_user_id and film.image_url
            ][:1]
        else:
            chosen = [film for film in films if film.image_url][:10]
        return [
            (film.image_url, normalize_film_title(film.title))
            for film in chosen
            if film.image_url
        ]

    def export_names(self, films: list[FestivalFilm], guild: discord.Guild) -> str:
        names: list[str] = []
        for film in films:
            member = guild.get_member(film.user_id)
            names.append(member.display_name if member is not None else f"участник {film.user_id}")
        return "\n".join(names) if names else "пока нет заявок"

    def ping_text(self, festival: Festival, tz_name: str, role: discord.Role) -> str:
        starts = datetime.fromisoformat(festival.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= starts:
            return f"{role.mention} мы уже смотрим фильм."
        left = self.remaining_label(festival)
        return f"{role.mention} до фильма **{left}**."

    async def due_reminders(self, config: GuildConfig, now: datetime) -> list[Festival]:
        if config.fest_channel_id is None or config.fest_reminder_minutes <= 0:
            return []
        if config.fest_ping_role_id is None:
            return []
        due: list[Festival] = []
        for festival in await self.db.list_open_festivals():
            if festival.guild_id != config.guild_id or festival.reminder_sent:
                continue
            starts = datetime.fromisoformat(festival.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now >= starts:
                continue
            if starts - now <= timedelta(minutes=config.fest_reminder_minutes):
                due.append(festival)
        return due
