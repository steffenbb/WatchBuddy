"""
parser.py (AI Engine)
- Normalize prompt and extract genres, languages, year ranges, seed titles, obscurity, numeric/boolean filters, and tone.
- Classify list type and produce tone_vector and suggested_title.
"""
from typing import Dict, Any, List, Tuple, Optional, Set
import re
from .moods_themes_map import MOODS, THEMES, FUSIONS
from .classifiers import detect_tone_keywords, sbert_tone_vector
from .metadata_processing import normalize_prompt
from app.utils.extract_genres_languages import get_genres_and_languages

# Optional NLP (spaCy + WordNet) for better matching
try:
    import spacy  # type: ignore
    _NLP = None
except Exception:
    spacy = None
    _NLP = None
try:
    from nltk.corpus import wordnet as wn  # type: ignore
except Exception:
    wn = None


OBSCURITY_MAP = {
    "very obscure": "obscure_high",
    "obscure": "obscure",
    "popular": "popular",
    "mainstream": "popular",
    "underground": "obscure_high",
}


def _extract_years(text: str) -> Tuple[List[int], List[int]]:
    # Return explicit years and ranges
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text)]
    ranges: List[int] = []
    m = re.search(r"(19\d{2}|20\d{2})\s*[-to]+\s*(19\d{2}|20\d{2})", text)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        ranges = [start, end]
    m2 = re.search(r"(after|since)\s+(19\d{2}|20\d{2})", text)
    if m2:
        ranges = [int(m2.group(2)), 3000]
    m3 = re.search(r"(before)\s+(19\d{2}|20\d{2})", text)
    if m3:
        ranges = [0, int(m3.group(2))]
    return years, ranges


def _extract_seed_titles(text: str) -> List[str]:
    """Capture seed titles after 'like' or 'similar to', stopping at qualifiers like 'but', 'except', 'without'.
    Example: 'like twilight but more light and cozy' -> ['twilight']
    """
    seeds: List[str] = []
    stop_tokens = [r" but ", r" except ", r" without ", r" rather than ", r" though "]
    for kw in ["like", "similar to"]:
        m = re.search(rf"\b{kw}\s+(.+)$", text)
        if m:
            tail = m.group(1)
            # Cut at first stop token if present
            cut_idx = None
            for st in stop_tokens:
                s = re.search(st, tail)
                if s:
                    cut_idx = s.start()
                    break
            if cut_idx is not None:
                tail = tail[:cut_idx]
            # split by comma/and
            parts = [p.strip() for p in re.split(r",| and ", tail) if p.strip()]
            seeds.extend(parts)
    # Normalize whitespace and keep first 5
    return [re.sub(r"\s+", " ", s).strip() for s in seeds[:5]]


def _extract_negative_cues(text: str) -> List[str]:
    """Extract negative cues like "without horror/gore", "no gore", "avoid slasher", "not horror".
    Returns a list of short phrases/keywords to de-emphasize via embeddings.
    """
    t = text.lower()
    phrases: List[str] = []

    # Helper to split a captured tail into individual cues
    def _split_items(s: str) -> List[str]:
        # stop at common qualifiers
        s = re.split(r"(?: but | except | rather than | though )", s)[0]
        # split by comma/and/or
        parts = [p.strip() for p in re.split(r",| and | or ", s) if p.strip()]
        return parts

    # Patterns: without X, no X, avoid X
    m = re.search(r"\bwithout\s+([^\.;\n]+)", t)
    if m:
        phrases.extend(_split_items(m.group(1)))

    for pat in [r"\bno\s+([^\.;\n]+)", r"\bavoid\s+([^\.;\n]+)"]:
        mm = re.search(pat, t)
        if mm:
            phrases.extend(_split_items(mm.group(1)))

    # Simple negative form: "not horror", "not gory", "not violent"
    # Keep only the next 1-3 word chunk
    for mm in re.finditer(r"\bnot\s+([a-z]{2,}(?:\s+[a-z]{2,}){0,2})", t):
        phrases.append(mm.group(1).strip())

    # Clean up overly generic determiners
    clean = []
    for p in phrases:
        p = re.sub(r"\b(too|very|really|any|the|a|an)\b", "", p).strip()
        p = re.sub(r"\s+", " ", p)
        if p and p not in clean:
            clean.append(p)
    # Limit to 8 cues to keep embedding costs low
    return clean[:8]


def _detect_media_type(text: str) -> Optional[str]:
    """Detect whether user asks for movies or shows.
    Returns 'movie', 'show', or None.
    """
    t = text.lower()
    # Prefer explicit mentions
    if re.search(r"\b(tv\s*show|tv\s*series|series|show|shows)\b", t):
        return "show"
    if re.search(r"\b(movie|movies|film|films)\b", t):
        return "movie"
    return None

def _extract_entities(text: str) -> Dict[str, List[str]]:
    """Extract named entities (people, organizations) using spaCy NER."""
    global _NLP
    import logging
    logger = logging.getLogger(__name__)
    entities = {"PERSON": [], "ORG": []}
    if spacy is None:
        logger.warning("spaCy is None, cannot extract entities")
        return entities
    try:
        if _NLP is None:
            logger.info("Loading spaCy model en_core_web_sm...")
            _NLP = spacy.load("en_core_web_sm")
        doc = _NLP(text)
        logger.info(f"spaCy extracted {len(doc.ents)} entities from text: {text[:50]}")
        for ent in doc.ents:
            logger.info(f"Entity: {ent.text} ({ent.label_})")
            if ent.label_ == "PERSON" and ent.text not in entities["PERSON"]:
                entities["PERSON"].append(ent.text)
            elif ent.label_ == "ORG" and ent.text not in entities["ORG"]:
                entities["ORG"].append(ent.text)
    except Exception as e:
        logger.warning(f"Entity extraction failed: {e}")
    logger.info(f"Final entities: {entities}")
    return entities

def _lemmatize(text: str) -> List[str]:
    global _NLP
    if spacy is None:
        return re.findall(r"[a-zA-Z']+", text.lower())
    try:
        if _NLP is None:
            _NLP = spacy.load("en_core_web_sm")
        doc = _NLP(text)
        return [t.lemma_.lower() for t in doc if t.is_alpha]
    except Exception:
        return re.findall(r"[a-zA-Z']+", text.lower())

def _synonyms(word: str) -> Set[str]:
    s: Set[str] = {word}
    if wn is None:
        return s
    try:
        for syn in wn.synsets(word):
            for l in syn.lemmas():
                s.add(l.name().replace('_', ' ').lower())
    except Exception:
        pass
    return s

def _extract_numeric(text: str, key_patterns: List[str]) -> Optional[Tuple[str, float]]:
    pat = r"(?:(?:" + "|".join([re.escape(k) for k in key_patterns]) + r"))\s*(>=|<=|>|<|=)\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(m|k|million|thousand))?"
    m = re.search(pat, text)
    if not m:
        return None
    op = m.group(1)
    val = float(m.group(2))
    unit = (m.group(3) or '').lower()
    if unit in ('m', 'million'):
        val *= 1_000_000
    elif unit in ('k', 'thousand'):
        val *= 1_000
    return op, val

def _extract_bool(text: str, key: str) -> Optional[bool]:
    t = text.lower()
    if f"no {key}" in t or f"not {key}" in t or f"exclude {key}" in t:
        return False
    if key in t or f"include {key}" in t or f"only {key}" in t:
        return True
    return None

def _extract_networks(text: str) -> List[str]:
    """Extract TV networks/streaming services from prompt."""
    networks_map = {
        "hbo": "HBO", "netflix": "Netflix", "amazon": "Amazon", "prime video": "Amazon",
        "disney": "Disney+", "disney+": "Disney+", "apple tv": "Apple TV+", "apple": "Apple TV+",
        "hulu": "Hulu", "paramount": "Paramount+", "peacock": "Peacock", "showtime": "Showtime",
        "starz": "STARZ", "amc": "AMC", "fx": "FX", "nbc": "NBC", "cbs": "CBS", "abc": "ABC",
        "cw": "The CW", "fox": "FOX", "bbc": "BBC", "itv": "ITV", "channel 4": "Channel 4",
        "sky": "Sky", "mtv": "MTV", "comedy central": "Comedy Central", "cartoon network": "Cartoon Network",
        "nickelodeon": "Nickelodeon", "discovery": "Discovery", "history": "History Channel",
        "national geographic": "National Geographic", "espn": "ESPN", "syfy": "Syfy",
        "usa network": "USA Network", "tnt": "TNT", "tbs": "TBS", "bravo": "Bravo",
        "adult swim": "Adult Swim", "crunchyroll": "Crunchyroll", "max": "Max"
    }
    found_networks = []
    text_lower = text.lower()
    for keyword, network_name in networks_map.items():
        if keyword in text_lower and network_name not in found_networks:
            found_networks.append(network_name)
    return found_networks

def _extract_countries(text: str) -> List[str]:
    """Extract production countries from prompt."""
    countries_map = {
        "american": "US", "usa": "US", "us": "US", "united states": "US",
        "british": "GB", "uk": "GB", "united kingdom": "GB", "england": "GB",
        "french": "FR", "france": "FR",
        "german": "DE", "germany": "DE",
        "italian": "IT", "italy": "IT",
        "spanish": "ES", "spain": "ES",
        "japanese": "JP", "japan": "JP",
        "korean": "KR", "korea": "KR", "south korea": "KR",
        "chinese": "CN", "china": "CN",
        "indian": "IN", "india": "IN", "bollywood": "IN",
        "canadian": "CA", "canada": "CA",
        "australian": "AU", "australia": "AU",
        "mexican": "MX", "mexico": "MX",
        "brazilian": "BR", "brazil": "BR",
        "russian": "RU", "russia": "RU",
        "nordic": ["SE", "NO", "DK", "FI"], "scandinavian": ["SE", "NO", "DK"],
        "swedish": "SE", "sweden": "SE",
        "norwegian": "NO", "norway": "NO",
        "danish": "DK", "denmark": "DK",
        "finnish": "FI", "finland": "FI",
    }
    found_countries = []
    text_lower = text.lower()
    for keyword, country_code in countries_map.items():
        if keyword in text_lower:
            if isinstance(country_code, list):
                for code in country_code:
                    if code not in found_countries:
                        found_countries.append(code)
            elif country_code not in found_countries:
                found_countries.append(country_code)
    return found_countries

def _extract_creators_directors(text: str, entities: Dict[str, List[str]]) -> Tuple[List[str], List[str]]:
    """Separate creators/directors from general people entities based on context."""
    creators = []
    directors = []
    text_lower = text.lower()
    
    # Look for creator/director keywords
    creator_patterns = [r"created by ([^,\.]+)", r"from ([^,\.]+)", r"by creator ([^,\.]+)"]
    director_patterns = [r"directed by ([^,\.]+)", r"director ([^,\.]+)", r"from director ([^,\.]+)"]
    
    for pattern in creator_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            # Match against extracted person entities
            for person in entities.get("PERSON", []):
                if person.lower() in match.lower():
                    if person not in creators:
                        creators.append(person)
    
    for pattern in director_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            for person in entities.get("PERSON", []):
                if person.lower() in match.lower():
                    if person not in directors:
                        directors.append(person)
    
    return creators, directors


def parse_prompt(prompt: str) -> Dict[str, Any]:
    # Extract entities from ORIGINAL prompt before lowercasing (spaCy needs capitalization)
    entities = _extract_entities(prompt)
    
    normalized = normalize_prompt(prompt)
    genres_avail, langs_avail = get_genres_and_languages(min_count=0)
    lemmas = _lemmatize(normalized)
    # Genres with synonym expansion
    genres = []
    seen = set()
    for g in genres_avail:
        gl = g.lower()
        
        # Special case: Don't match "TV Movie" genre when user just says "movie", "movies", "show", or "tv show"
        # Only match if they explicitly say "tv movie" or "television movie"
        if gl == "tv movie":
            if not re.search(r"\btv\s+movie", normalized) and not re.search(r"\btelevision\s+movie", normalized):
                continue
        
        toks = gl.replace('/', ' ').split()
        syns = set()
        for tok in toks:
            syns |= _synonyms(tok)
        syns.add(gl)
        if any(s in normalized for s in syns) or any(s in lemmas for s in syns):
            if g not in seen:
                genres.append(g)
                seen.add(g)
    languages = [l for l in langs_avail if re.search(rf"\b{re.escape(l.lower())}\b", normalized)]
    years, year_range = _extract_years(normalized)
    seeds = _extract_seed_titles(normalized)
    negative_cues = _extract_negative_cues(normalized)
    media_type = _detect_media_type(normalized)
    # Obscurity
    obscurity = None
    for key, val in OBSCURITY_MAP.items():
        if key in normalized:
            obscurity = val
            break
    adult = _extract_bool(normalized, 'adult')
    # Original language pattern: "original language: xx"
    orig_lang = None
    m_lang = re.search(r"original language\s*:\s*([a-z]{2,5})", normalized)
    if m_lang:
        orig_lang = m_lang.group(1)
    rating_cmp = _extract_numeric(normalized, ["vote average", "rating", "score"])
    votes_cmp = _extract_numeric(normalized, ["vote count", "votes"])
    revenue_cmp = _extract_numeric(normalized, ["revenue", "box office"])
    budget_cmp = _extract_numeric(normalized, ["budget"])
    popularity_cmp = _extract_numeric(normalized, ["popularity"])
    
    # TV-specific numeric filters
    seasons_cmp = _extract_numeric(normalized, ["seasons", "number of seasons"])
    episodes_cmp = _extract_numeric(normalized, ["episodes", "number of episodes"])
    runtime_cmp = _extract_numeric(normalized, ["runtime", "length", "minutes"])
    
    # Extract networks, countries, creators
    networks = _extract_networks(normalized)
    countries = _extract_countries(normalized)
    creators, directors = _extract_creators_directors(normalized, entities)
    
    # Production status for TV shows
    in_production = None
    if "ongoing" in normalized or "still airing" in normalized or "currently airing" in normalized:
        in_production = True
    elif "ended" in normalized or "finished" in normalized or "completed" in normalized:
        in_production = False
    
    # Quoted phrases as must-have snippets
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', prompt)
    phrases = [q[0] or q[1] for q in quoted if (q[0] or q[1])]
    tokens = list(dict.fromkeys(lemmas))[:50]
    # Tone
    tone_keywords = detect_tone_keywords(normalized)
    tone_vec = sbert_tone_vector(normalized)
    # Classify list type
    ltype = "chat"
    if any(t in normalized for t in ["mood", *MOODS]):
        ltype = "mood"
    elif any(t in normalized for t in ["theme", *THEMES]):
        ltype = "theme"
    elif any(t in normalized for t in ["fusion", *FUSIONS]):
        ltype = "fusion"

    # Suggested title - build from most relevant prompt elements
    suggested_title = None
    title_parts = []
    
    # Priority 1: Seed titles (most specific)
    if seeds:
        title_parts.append(f"Like {seeds[0].title()}")
    
    # Priority 2: Actors/Directors/Creators
    elif entities["PERSON"]:
        title_parts.append(f"{entities['PERSON'][0]} Films")
    
    # Priority 3: Tone keywords
    elif tone_keywords:
        title_parts.append(tone_keywords[0].title())
    
    # Priority 4: Genres
    elif genres:
        if len(genres) == 1:
            title_parts.append(genres[0].title())
        else:
            title_parts.append(f"{genres[0].title()} & {genres[1].title()}")
    
    # Priority 5: Media type
    if media_type and not seeds:
        if media_type == "movie":
            title_parts.append("Movies")
        else:
            title_parts.append("Shows")
    
    # Priority 6: Add tone/mood if we have other elements
    if title_parts and tone_keywords and not any(t in str(title_parts) for t in tone_keywords):
        title_parts.append(tone_keywords[0].title())
    
    # Priority 7: Add year range if specified
    if year_range and len(year_range) == 2:
        if year_range[0] > 1900 and year_range[1] < 3000:
            title_parts.append(f"({year_range[0]}-{year_range[1]})")
        elif year_range[0] > 1900:
            title_parts.append(f"(Since {year_range[0]})")
    elif years:
        title_parts.append(f"({years[0]}s)")
    
    # Construct final title
    if title_parts:
        suggested_title = " ".join(title_parts)
        # Limit length
        if len(suggested_title) > 60:
            suggested_title = suggested_title[:57] + "..."
    else:
        suggested_title = "AI Picks"

    filters = {
        "genres": genres or None,
        "languages": languages or None,
        "years": years or None,
        "year_range": year_range or None,
        "obscurity": obscurity or None,
        "adult": adult,
        "original_language": orig_lang,
        "media_type": media_type or None,
        "rating_cmp": rating_cmp,
        "votes_cmp": votes_cmp,
        "revenue_cmp": revenue_cmp,
        "budget_cmp": budget_cmp,
        "popularity_cmp": popularity_cmp,
        "seasons_cmp": seasons_cmp,
        "episodes_cmp": episodes_cmp,
        "runtime_cmp": runtime_cmp,
        "networks": networks or None,
        "countries": countries or None,
        "in_production": in_production,
        "phrases": phrases or None,
        "tokens": tokens or None,
        "negative_cues": negative_cues or None,
        "actors": entities["PERSON"] or None,  # People mentioned in prompt
        "creators": creators or None,  # TV show creators
        "directors": directors or None,  # Movie/TV directors
        "studios": entities["ORG"] or None,  # Production companies/studios
        "tone": (tone_keywords or None),
    }

    return {
        "normalized_prompt": normalized,
        "filters": {k: v for k, v in filters.items() if v},
        "seed_titles": seeds,
        "tone_vector": tone_vec.tolist(),
        "suggested_title": suggested_title,
        "type": ltype,
    }
