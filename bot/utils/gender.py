"""Infer grammatical gender from Russian self-speech endings."""

from __future__ import annotations

import re
from typing import Literal

Gender = Literal["f", "m", "u"]

# Nouns / fixed words ending in -ла that are not feminine past tense.
_FEM_PAST_FALSE = frozenset(
    {
        "школа",
        "сила",
        "стрела",
        "скала",
        "пчела",
        "зола",
        "смола",
        "мгла",
        "игла",
        "метла",
        "кукла",
        "пала",
        "скандала",
        "финала",
        "канала",
        "сигнала",
        "журнала",
        "фестиваля",
    }
)

# Strong first-person / self markers.
_FEM_STRONG = re.compile(
    r"(?i)(?<![а-яё])(?:"
    r"я\s+сама|сама(?:\s|$|[.,!?])|я\s+такая|такая(?:\s|$|[.,!?])|"
    r"я\s+рада|рада(?:\s|$|[.,!?])|я\s+готова|готова(?:\s|$|[.,!?])|"
    r"должна|устала|сказала|думала|хотела|могла|была|пошла|пришла|"
    r"сделала|взяла|дала|поняла|увидела|услышала|написала|прочитала|"
    r"играла|смотрела|сидела|стояла|лежала|жила|шла|нашла|потеряла|"
    r"забыла|вспомнила|решила|начала|закончила|попробовала|попробовала|"
    r"любила|ненавидела|боялась|надеялась|старалась|пыталась"
    r")(?![а-яё])"
)
_MASC_STRONG = re.compile(
    r"(?i)(?<![а-яё])(?:"
    r"я\s+сам|сам(?:\s|$|[.,!?])|я\s+такой|такой(?:\s|$|[.,!?])|"
    r"я\s+рад|рад(?:\s|$|[.,!?])|я\s+готов|готов(?:\s|$|[.,!?])|"
    r"должен|устал|сказал|думал|хотел|мог|был|пош[её]л|приш[её]л|"
    r"сделал|взял|дал|понял|увидел|услышал|написал|прочитал|"
    r"играл|смотрел|сидел|стоял|лежал|жил|ш[её]л|наш[её]л|потерял|"
    r"забыл|вспомнил|решил|начал|закончил|попробовал|"
    r"любил|ненавидел|боялся|надеялся|старался|пытался"
    r")(?![а-яё])"
)

# Generic past tense: ...ла / ...лся(ась) vs ...л / ...лся
_FEM_PAST = re.compile(
    r"(?i)(?<![а-яё])([а-яё]{3,}(?:ла|лась|илась|елась|алась|ялась))(?![а-яё])"
)
_MASC_PAST = re.compile(
    r"(?i)(?<![а-яё])([а-яё]{3,}(?:лся|л))(?![а-яё])"
)


def score_gender_markers(text: str) -> tuple[int, int]:
    """Return (female_hits, male_hits) from one utterance."""
    cleaned = (text or "").casefold().replace("ё", "е")
    if len(cleaned) < 3:
        return 0, 0
    fem = len(_FEM_STRONG.findall(cleaned))
    masc = len(_MASC_STRONG.findall(cleaned))
    for match in _FEM_PAST.finditer(cleaned):
        word = match.group(1)
        if word in _FEM_PAST_FALSE:
            continue
        # Already counted via strong list — still ok to add light weight once.
        fem += 1
    for match in _MASC_PAST.finditer(cleaned):
        word = match.group(1)
        # Skip feminine past that also matches ...л prefix greedily — regex
        # uses (лся|л) so "сказала" won't match masc. "боялся" is masc.
        if word.endswith("ла") or word.endswith("лась"):
            continue
        masc += 1
    return fem, masc


def decide_gender(score_f: int, score_m: int) -> Gender:
    """Pick gender when evidence is clear enough."""
    total = score_f + score_m
    if total < 3:
        return "u"
    if score_f >= score_m + 2 and score_f >= 3:
        return "f"
    if score_m >= score_f + 2 and score_m >= 3:
        return "m"
    if score_f >= 5 and score_f > score_m:
        return "f"
    if score_m >= 5 and score_m > score_f:
        return "m"
    return "u"


def gender_label_ru(gender: Gender) -> str:
    if gender == "f":
        return "женщина"
    if gender == "m":
        return "мужчина"
    return "неизвестно"


def gender_prompt_line(gender: Gender) -> str:
    if gender == "f":
        return (
            "Пол по речи: женщина. "
            "Пиши про неё в женском роде (она, любит, сказала) или без местоимений."
        )
    if gender == "m":
        return (
            "Пол по речи: мужчина. "
            "Пиши про него в мужском роде (он, любит, сказал) или без местоимений."
        )
    return (
        "Пол по речи: неизвестен. "
        "Избегай он/она — пиши нейтрально (этот человек, любит, часто говорит)."
    )
