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
    """Extract explicit years and year ranges from text with various patterns."""
    # Extract all 4-digit years
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text)]
    ranges: List[int] = []
    
    # Range patterns: "1990-2000", "1990 to 2000", "1990 - 2000"
    m = re.search(r"(19\d{2}|20\d{2})\s*[-–—to]+\s*(19\d{2}|20\d{2})", text, re.IGNORECASE)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        ranges = [start, end]
    
    # "After 2010", "since 2010", "from 2010", "2010 onwards", "2010 and later"
    m2 = re.search(r"(after|since|from)\s+(19\d{2}|20\d{2})", text, re.IGNORECASE)
    if m2:
        ranges = [int(m2.group(2)), 3000]
    m2_alt = re.search(r"(19\d{2}|20\d{2})\s+(onwards|onward|and later|\+)", text, re.IGNORECASE)
    if m2_alt:
        ranges = [int(m2_alt.group(1)), 3000]
    
    # "Before 2010", "until 2010", "up to 2010", "prior to 2010"
    m3 = re.search(r"(before|until|up to|prior to)\s+(19\d{2}|20\d{2})", text, re.IGNORECASE)
    if m3:
        ranges = [0, int(m3.group(2))]
    
    # Decade patterns: "90s", "1990s", "nineties", "2000s", "2010s"
    decade_map = {
        r"\b(90s|1990s|nineties)\b": (1990, 1999),
        r"\b(80s|1980s|eighties)\b": (1980, 1989),
        r"\b(70s|1970s|seventies)\b": (1970, 1979),
        r"\b(60s|1960s|sixties)\b": (1960, 1969),
        r"\b(50s|1950s|fifties)\b": (1950, 1959),
        r"\b(2000s|two thousands)\b": (2000, 2009),
        r"\b(2010s|twenty tens)\b": (2010, 2019),
        r"\b(2020s|twenty twenties)\b": (2020, 2029),
    }
    for pattern, (start, end) in decade_map.items():
        if re.search(pattern, text, re.IGNORECASE):
            ranges = [start, end]
            break
    
    # "Early 2000s", "late 90s", "mid 1980s"
    early_late_pattern = r"(early|late|mid)\s+(19\d0s|20\d0s|\d0s)"
    m4 = re.search(early_late_pattern, text, re.IGNORECASE)
    if m4:
        prefix = m4.group(1).lower()
        decade_str = m4.group(2)
        # Extract decade base year
        decade_match = re.search(r"(19\d0|20\d0|\d0)", decade_str)
        if decade_match:
            base_year = int(decade_match.group(1)) if len(decade_match.group(1)) == 4 else 1900 + int(decade_match.group(1))
            if prefix == "early":
                ranges = [base_year, base_year + 3]
            elif prefix == "mid":
                ranges = [base_year + 3, base_year + 7]
            elif prefix == "late":
                ranges = [base_year + 7, base_year + 9]
    
    return years, ranges


def _extract_seed_titles(text: str) -> List[str]:
    """Capture seed titles with various patterns and case-insensitive matching.
    
    Patterns handled:
    - "like Twilight", "similar to Twilight"
    - "movies like Twilight", "shows like Breaking Bad"
    - "in the style of Tarantino"
    - "reminiscent of The Godfather"
    - Stops at qualifiers: "but", "except", "without", "rather than"
    """
    seeds: List[str] = []
    stop_tokens = [r" but ", r" except ", r" without ", r" rather than ", r" though ", r" however "]
    
    # Extended keyword patterns for seed extraction
    seed_patterns = [
        r"\blike\s+(.+)",
        r"\bsimilar to\s+(.+)",
        r"\bsimilar\s+to\s+(.+)",
        r"\bin the style of\s+(.+)",
        r"\bstyle of\s+(.+)",
        r"\breminiscent of\s+(.+)",
        r"\breminding me of\s+(.+)",
        r"\bcompare[sd]? to\s+(.+)",
        r"\bvibes? of\s+(.+)",
        r"\bmovies? like\s+(.+)",
        r"\bshows? like\s+(.+)",
        r"\bfilms? like\s+(.+)",
        r"\bseries like\s+(.+)",
        r"\bas good as\s+(.+)",
    ]
    
    for pattern in seed_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for m in matches:
            tail = m.group(1)
            # Cut at first stop token if present
            cut_idx = None
            for st in stop_tokens:
                s = re.search(st, tail, re.IGNORECASE)
                if s:
                    cut_idx = s.start()
                    break
            if cut_idx is not None:
                tail = tail[:cut_idx]
            # Split by comma/and/or
            parts = [p.strip() for p in re.split(r",| and | or |\/ ", tail) if p.strip()]
            for part in parts:
                # Clean up common noise words
                part = re.sub(r"\b(the|a|an)\s+", "", part, flags=re.IGNORECASE).strip()
                if part and len(part) > 2:  # Avoid single-letter matches
                    seeds.append(part)
    
    # Normalize whitespace and keep first 5 unique seeds
    seen = set()
    unique_seeds = []
    for s in seeds:
        s_clean = re.sub(r"\s+", " ", s).strip()
        s_lower = s_clean.lower()
        if s_lower not in seen and len(s_clean) > 2:
            unique_seeds.append(s_clean)
            seen.add(s_lower)
            if len(unique_seeds) >= 5:
                break
    
    return unique_seeds


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
    """Detect whether user asks for movies or shows with comprehensive patterns.
    
    Returns 'movie', 'show', or None.
    Prioritizes explicit mentions and contextual clues.
    """
    t = text.lower()
    
    # TV/Show patterns (check first to avoid movie false positives)
    show_patterns = [
        r"\btv\s*show",
        r"\btv\s*series",
        r"\btelevision\s+(show|series)",
        r"\b(series|show|shows)\b",
        r"\bepisode",
        r"\bseason",
        r"\bbinge",
        r"\bstreaming\s+(show|series)",
        r"\bminiseries",
        r"\blimited\s+series",
        r"\bdrama\s+series",
        r"\bcomedy\s+series",
        r"\bsitcom",
        r"\bsoap\s+opera",
    ]
    
    # Movie patterns
    movie_patterns = [
        r"\bmovie",
        r"\bfilm",
        r"\bcinema",
        r"\bfeature\s+film",
        r"\bmotion\s+picture",
        r"\bflick",
        r"\bblockbuster",
        r"\bindie\s+film",
        r"\bdocumentary\s+film",
    ]
    
    # Count matches for each type
    show_count = sum(1 for p in show_patterns if re.search(p, t))
    movie_count = sum(1 for p in movie_patterns if re.search(p, t))
    
    # Return type with more matches, prioritizing shows if equal
    if show_count > movie_count:
        return "show"
    elif movie_count > show_count:
        return "movie"
    
    # If tied or no matches, check for contextual clues
    # "Watch" + plural usually means shows
    if re.search(r"\bwatch\b.*\b(to watch|watching)\b", t):
        return "show"
    
    return None

def _extract_entities(text: str) -> Dict[str, List[str]]:
    """Extract named entities (people, organizations) using spaCy NER with fallback manual patterns."""
    global _NLP
    import logging
    logger = logging.getLogger(__name__)
    entities = {"PERSON": [], "ORG": []}
    
    # Manual fallback patterns for common actors/directors/studios
    manual_persons = {
        'spielberg': 'Steven Spielberg', 'scorsese': 'Martin Scorsese', 'tarantino': 'Quentin Tarantino',
        'nolan': 'Christopher Nolan', 'kubrick': 'Stanley Kubrick', 'hitchcock': 'Alfred Hitchcock',
        'coppola': 'Francis Ford Coppola', 'fincher': 'David Fincher', 'wes anderson': 'Wes Anderson',
        'ridley scott': 'Ridley Scott', 'james cameron': 'James Cameron', 'peter jackson': 'Peter Jackson',
        'denis villeneuve': 'Denis Villeneuve', 'bong joon-ho': 'Bong Joon-ho', 'park chan-wook': 'Park Chan-wook',
        'guillermo del toro': 'Guillermo del Toro', 'edgar wright': 'Edgar Wright', 'jordan peele': 'Jordan Peele',
        'greta gerwig': 'Greta Gerwig', 'sofia coppola': 'Sofia Coppola', 'ava duvernay': 'Ava DuVernay',
        'spike lee': 'Spike Lee', 'paul thomas anderson': 'Paul Thomas Anderson', 'coen brothers': 'Coen Brothers',
        'tom hanks': 'Tom Hanks', 'meryl streep': 'Meryl Streep', 'denzel washington': 'Denzel Washington',
        'leonardo dicaprio': 'Leonardo DiCaprio', 'brad pitt': 'Brad Pitt', 'johnny depp': 'Johnny Depp',
        'robert de niro': 'Robert De Niro', 'al pacino': 'Al Pacino', 'morgan freeman': 'Morgan Freeman',
        'samuel l jackson': 'Samuel L. Jackson', 'christian bale': 'Christian Bale', 'matt damon': 'Matt Damon',
        'scarlett johansson': 'Scarlett Johansson', 'natalie portman': 'Natalie Portman', 'kate winslet': 'Kate Winslet',
        'cate blanchett': 'Cate Blanchett', 'jennifer lawrence': 'Jennifer Lawrence', 'emma stone': 'Emma Stone',
        'ryan gosling': 'Ryan Gosling', 'timothee chalamet': 'Timothée Chalamet', 'florence pugh': 'Florence Pugh',
    }
    
    manual_orgs = {
        'marvel': 'Marvel Studios', 'disney': 'Disney', 'pixar': 'Pixar', 'warner bros': 'Warner Bros',
        'universal': 'Universal Pictures', 'paramount': 'Paramount Pictures', 'sony': 'Sony Pictures',
        'a24': 'A24', 'miramax': 'Miramax', 'dreamworks': 'DreamWorks', 'lucasfilm': 'Lucasfilm',
        'hbo': 'HBO', 'netflix': 'Netflix', 'amazon': 'Amazon Studios', 'apple': 'Apple TV+',
        'studio ghibli': 'Studio Ghibli', 'blumhouse': 'Blumhouse Productions', 'legendary': 'Legendary Pictures',
    }
    
    text_lower = text.lower()
    
    # Check manual patterns first
    for pattern, full_name in manual_persons.items():
        if pattern in text_lower and full_name not in entities["PERSON"]:
            entities["PERSON"].append(full_name)
            logger.info(f"Manual PERSON match: {full_name}")
    
    for pattern, full_name in manual_orgs.items():
        if pattern in text_lower and full_name not in entities["ORG"]:
            entities["ORG"].append(full_name)
            logger.info(f"Manual ORG match: {full_name}")
    
    # Use spaCy if available for additional entities
    if spacy is None:
        logger.debug("spaCy not available, using manual patterns only")
        return entities
    
    try:
        if _NLP is None:
            logger.info("Loading spaCy model en_core_web_sm...")
            _NLP = spacy.load("en_core_web_sm")
        
        # SMART CASING: Convert to title case for better spaCy NER performance
        # "tom hanks" → "Tom Hanks", "christopher nolan" → "Christopher Nolan"
        # But preserve already-capitalized text to avoid breaking "HBO" → "Hbo"
        def smart_title_case(text: str) -> str:
            """Apply title case only to lowercase sequences, preserve existing caps."""
            words = []
            for word in text.split():
                # If word is all lowercase, apply title case
                if word.islower() or word[0].islower():
                    words.append(word.title())
                else:
                    # Preserve existing capitalization (HBO, A24, etc.)
                    words.append(word)
            return " ".join(words)
        
        text_for_ner = smart_title_case(text)
        logger.debug(f"[NER] Smart cased text: {text_for_ner[:100]}")
        
        doc = _NLP(text_for_ner)
        logger.info(f"spaCy extracted {len(doc.ents)} entities from text: {text_for_ner[:50]}")
        for ent in doc.ents:
            logger.info(f"Entity: {ent.text} ({ent.label_})")
            if ent.label_ == "PERSON" and ent.text not in entities["PERSON"]:
                entities["PERSON"].append(ent.text)
            elif ent.label_ == "ORG" and ent.text not in entities["ORG"]:
                entities["ORG"].append(ent.text)
    except Exception as e:
        logger.warning(f"spaCy entity extraction failed: {e}")
    
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
    """Expanded synonym mapping with genre-specific and common phrase synonyms."""
    # Base synonyms
    s: Set[str] = {word}
    
    # Manual genre synonym mappings (more reliable than WordNet for film genres)
    genre_synonyms = {
        'sci-fi': {'science fiction', 'scifi', 'sf'},
        'science fiction': {'sci-fi', 'scifi', 'sf'},
        'rom-com': {'romantic comedy', 'romcom'},
        'romantic comedy': {'rom-com', 'romcom'},
        'thriller': {'suspense', 'suspenseful'},
        'suspense': {'thriller', 'suspenseful'},
        'horror': {'scary', 'terror', 'frightening', 'spooky'},
        'scary': {'horror', 'terror', 'frightening', 'spooky'},
        'comedy': {'funny', 'humorous', 'comedic'},
        'funny': {'comedy', 'humorous', 'comedic'},
        'action': {'explosive', 'adrenaline'},
        'drama': {'dramatic', 'serious'},
        'documentary': {'doc', 'docu', 'non-fiction'},
        'animated': {'animation', 'cartoon', 'anime'},
        'animation': {'animated', 'cartoon', 'anime'},
        'fantasy': {'magical', 'mystical'},
        'mystery': {'detective', 'whodunit', 'crime'},
        'detective': {'mystery', 'whodunit', 'crime'},
        'western': {'cowboy', 'spaghetti western'},
        'war': {'military', 'combat', 'battlefield'},
        'musical': {'music', 'song and dance'},
        'biography': {'biopic', 'biographical', 'bio'},
        'biopic': {'biography', 'biographical', 'bio'},
        'historical': {'period', 'period piece', 'history'},
        'period': {'historical', 'period piece', 'history'},
        'noir': {'film noir', 'neo-noir', 'dark'},
        'family': {'kids', 'children', 'family-friendly'},
        'kids': {'family', 'children', 'family-friendly'},
        'adventure': {'adventurous', 'quest'},
        'romantic': {'romance', 'love story', 'love'},
        'romance': {'romantic', 'love story', 'love'},
        'psychological': {'mind-bending', 'mental', 'cerebral'},
        'dark': {'grim', 'noir', 'bleak', 'gritty'},
        'gritty': {'dark', 'grim', 'raw', 'realistic'},
        'indie': {'independent', 'art house', 'arthouse'},
        'independent': {'indie', 'art house', 'arthouse'},
        'cult': {'cult classic', 'underground'},
        'superhero': {'comic book', 'marvel', 'dc'},
        'heist': {'caper', 'robbery', 'con'},
        'zombie': {'undead', 'walking dead'},
        'vampire': {'dracula', 'bloodsucker'},
        'alien': {'extraterrestrial', 'et', 'ufo'},
        'disaster': {'catastrophe', 'apocalypse', 'end of the world'},
        'survival': {'post-apocalyptic', 'dystopian'},
        'dystopian': {'post-apocalyptic', 'survival', 'bleak future'},
        'cyberpunk': {'cyber', 'futuristic', 'tech noir'},
        'time travel': {'time-travel', 'temporal'},
        'epic': {'grand', 'sweeping', 'large-scale'},
    }
    
    word_lower = word.lower()
    if word_lower in genre_synonyms:
        s.update(genre_synonyms[word_lower])
    
    # Also check WordNet for additional synonyms
    if wn is not None:
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
    """Extract TV networks/streaming services from prompt with case-insensitive matching."""
    networks_map = {
        "hbo": "HBO", "hbo max": "Max", "netflix": "Netflix", "amazon": "Amazon", "prime video": "Amazon",
        "prime": "Amazon", "disney": "Disney+", "disney+": "Disney+", "disney plus": "Disney+",
        "apple tv": "Apple TV+", "apple tv+": "Apple TV+", "apple": "Apple TV+",
        "hulu": "Hulu", "paramount": "Paramount+", "paramount+": "Paramount+", "paramount plus": "Paramount+",
        "peacock": "Peacock", "showtime": "Showtime", "starz": "STARZ",
        "amc": "AMC", "fx": "FX", "fxx": "FXX", "nbc": "NBC", "cbs": "CBS", "abc": "ABC",
        "cw": "The CW", "the cw": "The CW", "fox": "FOX", "bbc": "BBC", "itv": "ITV", 
        "channel 4": "Channel 4", "sky": "Sky", "mtv": "MTV", 
        "comedy central": "Comedy Central", "cartoon network": "Cartoon Network",
        "nickelodeon": "Nickelodeon", "discovery": "Discovery", "history": "History Channel",
        "history channel": "History Channel", "national geographic": "National Geographic",
        "nat geo": "National Geographic", "espn": "ESPN", "syfy": "Syfy", "sci-fi channel": "Syfy",
        "usa network": "USA Network", "usa": "USA Network", "tnt": "TNT", "tbs": "TBS", 
        "bravo": "Bravo", "adult swim": "Adult Swim", "crunchyroll": "Crunchyroll", 
        "max": "Max", "hulu": "Hulu", "showtime": "Showtime",
    }
    found_networks = []
    text_lower = text.lower()
    # Sort by length descending to match longer phrases first (e.g., "hbo max" before "hbo")
    sorted_keywords = sorted(networks_map.keys(), key=len, reverse=True)
    for keyword in sorted_keywords:
        network_name = networks_map[keyword]
        # Use word boundary matching for better precision
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower) and network_name not in found_networks:
            found_networks.append(network_name)
    return found_networks

def _extract_countries(text: str) -> List[str]:
    """Extract production countries from prompt with case-insensitive word boundary matching."""
    countries_map = {
        "american": "US", "usa": "US", "us": "US", "united states": "US", "u.s.": "US", "u.s.a.": "US",
        "british": "GB", "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "england": "GB", "english": "GB",
        "french": "FR", "france": "FR",
        "german": "DE", "germany": "DE",
        "italian": "IT", "italy": "IT",
        "spanish": "ES", "spain": "ES",
        "japanese": "JP", "japan": "JP",
        "korean": "KR", "korea": "KR", "south korea": "KR", "south korean": "KR",
        "chinese": "CN", "china": "CN",
        "indian": "IN", "india": "IN", "bollywood": "IN",
        "canadian": "CA", "canada": "CA",
        "australian": "AU", "australia": "AU", "aussie": "AU",
        "mexican": "MX", "mexico": "MX",
        "brazilian": "BR", "brazil": "BR",
        "russian": "RU", "russia": "RU",
        "nordic": ["SE", "NO", "DK", "FI"], "scandinavian": ["SE", "NO", "DK"],
        "swedish": "SE", "sweden": "SE",
        "norwegian": "NO", "norway": "NO",
        "danish": "DK", "denmark": "DK",
        "finnish": "FI", "finland": "FI",
        "icelandic": "IS", "iceland": "IS",
        "irish": "IE", "ireland": "IE",
        "scottish": "GB", "scotland": "GB",
        "welsh": "GB", "wales": "GB",
        "dutch": "NL", "netherlands": "NL", "holland": "NL",
        "belgian": "BE", "belgium": "BE",
        "swiss": "CH", "switzerland": "CH",
        "austrian": "AT", "austria": "AT",
        "polish": "PL", "poland": "PL",
        "czech": "CZ", "czechia": "CZ", "czech republic": "CZ",
        "hungarian": "HU", "hungary": "HU",
        "greek": "GR", "greece": "GR",
        "turkish": "TR", "turkey": "TR",
        "israeli": "IL", "israel": "IL",
        "south african": "ZA", "south africa": "ZA",
        "new zealand": "NZ", "new zealander": "NZ", "kiwi": "NZ",
        "hong kong": "HK", "hong kong cinema": "HK",
        "taiwanese": "TW", "taiwan": "TW",
        "thai": "TH", "thailand": "TH",
        "vietnamese": "VN", "vietnam": "VN",
        "indonesian": "ID", "indonesia": "ID",
        "filipino": "PH", "philippines": "PH",
        "singaporean": "SG", "singapore": "SG",
        "malaysian": "MY", "malaysia": "MY",
    }
    found_countries = []
    text_lower = text.lower()
    # Sort by length descending to match longer phrases first
    sorted_keywords = sorted(countries_map.keys(), key=len, reverse=True)
    for keyword in sorted_keywords:
        country_code = countries_map[keyword]
        # Use word boundary matching for precision
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
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
    
    # Extended patterns for better detection
    creator_patterns = [
        r"created by ([^,\.]+)", r"from ([^,\.]+)", r"by creator ([^,\.]+)",
        r"by ([^,\.]+) \(creator\)", r"creator ([^,\.]+)"
    ]
    director_patterns = [
        r"directed by ([^,\.]+)", r"director ([^,\.]+)", r"from director ([^,\.]+)",
        r"by ([^,\.]+) \(director\)", r"by director ([^,\.]+)"
    ]
    
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


def _extract_actors(text: str, entities: Dict[str, List[str]]) -> List[str]:
    """Extract actors from prompt using patterns like 'starring X', 'with X', 'featuring X'."""
    actors = []
    text_lower = text.lower()
    
    # Actor-specific patterns
    actor_patterns = [
        r"starring ([^,\.]+)",
        r"stars ([^,\.]+)",
        r"with ([^,\.]+) in it",
        r"with ([^,\.]+) as",
        r"featuring ([^,\.]+)",
        r"features ([^,\.]+)",
        r"acted by ([^,\.]+)",
        r"performance by ([^,\.]+)",
        r"performances by ([^,\.]+)",
    ]
    
    for pattern in actor_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            # Match against extracted person entities
            for person in entities.get("PERSON", []):
                if person.lower() in match.lower():
                    if person not in actors:
                        actors.append(person)
    
    return actors


def _extract_decades(text: str) -> List[str]:
    """Extract decade references like '80s movies', '1990s', '2010s shows'."""
    decades = []
    text_lower = text.lower()
    
    # Pattern for decades: 50s, 60s, 70s, 80s, 90s, 2000s, 2010s, etc.
    decade_patterns = [
        (r"\b(50s|50's|fifties|1950s)\b", "1950s"),
        (r"\b(60s|60's|sixties|1960s)\b", "1960s"),
        (r"\b(70s|70's|seventies|1970s)\b", "1970s"),
        (r"\b(80s|80's|eighties|1980s)\b", "1980s"),
        (r"\b(90s|90's|nineties|1990s)\b", "1990s"),
        (r"\b(2000s|2000's|aughts|two thousands)\b", "2000s"),
        (r"\b(2010s|2010's|twenty tens)\b", "2010s"),
        (r"\b(2020s|2020's|twenty twenties)\b", "2020s"),
    ]
    
    for pattern, decade_name in decade_patterns:
        if re.search(pattern, text_lower):
            if decade_name not in decades:
                decades.append(decade_name)
    
    return decades


def _extract_rating_qualifiers(text: str) -> List[str]:
    """Extract rating/quality qualifiers like 'highly rated', 'critically acclaimed', 'cult classic'."""
    qualifiers = []
    text_lower = text.lower()
    
    # Quality patterns
    quality_patterns = {
        'highly_rated': [r'\bhighly rated\b', r'\bhigh rating\b', r'\btop rated\b', r'\bwell rated\b'],
        'critically_acclaimed': [r'\bcritically acclaimed\b', r'\bacclaimed\b', r'\bcritically praised\b', 
                                r'\bcritical favorite\b', r'\bcritical darling\b'],
        'cult_classic': [r'\bcult classic\b', r'\bcult favorite\b', r'\bcult film\b', r'\bcult following\b'],
        'award_winning': [r'\baward winning\b', r'\baward-winning\b', r'\boscar winning\b', 
                         r'\bemmy winning\b', r'\bprize winning\b'],
        'underrated': [r'\bunderrated\b', r'\bunder-rated\b', r'\bunder the radar\b', r'\bhidden gem\b'],
        'popular': [r'\bpopular\b', r'\bfamous\b', r'\bwell known\b', r'\bmainstream\b'],
        'obscure': [r'\bobscure\b', r'\bunknown\b', r'\brare\b', r'\bhard to find\b'],
    }
    
    for qualifier, patterns in quality_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                if qualifier not in qualifiers:
                    qualifiers.append(qualifier)
                break
    
    return qualifiers


def _extract_studios(text: str, entities: Dict[str, List[str]]) -> List[str]:
    """Extract production studios/companies from prompt."""
    studios = []
    text_lower = text.lower()
    
    # Studio-specific patterns
    studio_patterns = [
        r"by ([^,\.]+) studios?",
        r"from ([^,\.]+) studios?",
        r"([^,\.]+) production",
        r"([^,\.]+) pictures",
        r"produced by ([^,\.]+)",
    ]
    
    for pattern in studio_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            # Match against extracted org entities
            for org in entities.get("ORG", []):
                if org.lower() in match.lower():
                    if org not in studios:
                        studios.append(org)
    
    # Also include direct org entity matches that are known studios
    known_studios = ['Marvel Studios', 'Disney', 'Pixar', 'Warner Bros', 'Universal Pictures', 
                    'Paramount Pictures', 'Sony Pictures', 'A24', 'Miramax', 'DreamWorks', 
                    'Lucasfilm', 'Studio Ghibli', 'Blumhouse Productions', 'Legendary Pictures',
                    'Netflix', 'Amazon Studios', 'Apple TV+', 'HBO']
    
    for studio in known_studios:
        if studio in entities.get("ORG", []) and studio not in studios:
            studios.append(studio)
    
    return studios


def _extract_seasonal_keywords(text: str) -> List[str]:
    """Extract seasonal/holiday keywords from text for thematic matching.
    
    Returns list of detected seasons/holidays to boost semantic search.
    """
    seasonal_patterns = {
        'christmas': [r'\bchristmas\b', r'\bxmas\b', r'\bholiday\b', r'\bholidays\b', 
                     r'\bsanta\b', r'\byule\b', r'\bfestive\b'],
        'halloween': [r'\bhalloween\b', r'\bsamhain\b', r'\ball hallows\b'],
        'easter': [r'\beaster\b', r'\bspring holiday\b'],
        'valentine': [r'\bvalentine\b', r'\bvalentines\b', r'\bvalentine\'s\b'],
        'thanksgiving': [r'\bthanksgiving\b', r'\bturkey day\b'],
        'new year': [r'\bnew year\b', r'\bnew years\b', r'\bnew year\'s\b'],
        'summer': [r'\bsummer\b', r'\bsummertime\b'],
        'winter': [r'\bwinter\b', r'\bwintry\b', r'\bwintertime\b'],
        'spring': [r'\bspring\b', r'\bspringtime\b'],
        'fall': [r'\bfall\b', r'\bautumn\b'],
        'hanukkah': [r'\bhanukkah\b', r'\bchanukah\b'],
        'ramadan': [r'\bramadan\b'],
        'diwali': [r'\bdiwali\b'],
    }
    
    text_lower = text.lower()
    detected = []
    
    for season, patterns in seasonal_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                if season not in detected:
                    detected.append(season)
                break
    
    return detected


def parse_prompt(prompt: str, default_obscurity: str = "balanced") -> Dict[str, Any]:
    """Parse user prompt to extract all filters, entities, and metadata.
    
    CRITICAL: Extract entities from ORIGINAL prompt BEFORE lowercasing,
    since spaCy NER and manual patterns need proper capitalization.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Step 1: Extract entities from ORIGINAL prompt (preserves capitalization)
    logger.info(f"[PARSE] Processing prompt: {prompt[:100]}")
    entities = _extract_entities(prompt)
    logger.info(f"[PARSE] Extracted entities: {entities}")
    
    # Step 2: Normalize prompt (converts to lowercase for matching)
    normalized = normalize_prompt(prompt)
    logger.info(f"[PARSE] Normalized prompt: {normalized[:100]}")
    
    genres_avail, langs_avail = get_genres_and_languages(min_count=0)
    lemmas = _lemmatize(normalized)
    # Genres with aggressive synonym expansion and case-insensitive matching
    genres = []
    seen = set()
    for g in genres_avail:
        gl = g.lower()
        
        # Special case: Don't match "TV Movie" genre when user just says "movie", "movies", "show", or "tv show"
        # Only match if they explicitly say "tv movie" or "television movie"
        if gl == "tv movie":
            if not re.search(r"\btv\s+movie", normalized) and not re.search(r"\btelevision\s+movie", normalized):
                continue
        
        # Build comprehensive synonym set for this genre
        toks = gl.replace('/', ' ').replace('-', ' ').split()
        syns = set()
        for tok in toks:
            syns |= _synonyms(tok)
        syns.add(gl)
        syns.add(g)  # Add original case too
        
        # Check normalized prompt and lemmas with word boundary matching
        matched = False
        for s in syns:
            s_lower = s.lower()
            # Word boundary match to avoid false positives (e.g., "action" in "faction")
            if re.search(rf"\b{re.escape(s_lower)}\b", normalized):
                matched = True
                break
            # Also check lemmas for inflected forms
            if s_lower in lemmas:
                matched = True
                break
        
        if matched and g not in seen:
            genres.append(g)
            seen.add(g)
            logger.debug(f"[PARSE] Matched genre: {g} via synonyms: {syns}")
    # Language extraction with expanded synonyms and common phrases
    # Map full names -> ISO codes actually stored in persistent_candidates.original_language.
    language_name_to_code = {
        'english': 'en', 'french': 'fr', 'german': 'de', 'spanish': 'es', 'italian': 'it', 'portuguese': 'pt',
        'russian': 'ru', 'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh', 'mandarin': 'zh', 'cantonese': 'zh',
        'hindi': 'hi', 'arabic': 'ar', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da', 'finnish': 'fi',
        'dutch': 'nl', 'polish': 'pl', 'turkish': 'tr', 'thai': 'th', 'indonesian': 'id', 'vietnamese': 'vi',
        'greek': 'el', 'hebrew': 'he', 'czech': 'cs', 'hungarian': 'hu', 'romanian': 'ro', 'ukrainian': 'uk',
        'bulgarian': 'bg', 'serbian': 'sr', 'croatian': 'hr', 'slovak': 'sk', 'catalan': 'ca', 'basque': 'eu',
        'icelandic': 'is', 'persian': 'fa', 'urdu': 'ur', 'bengali': 'bn', 'tamil': 'ta', 'telugu': 'te',
        'malayalam': 'ml', 'kannada': 'kn', 'marathi': 'mr', 'punjabi': 'pa', 'gujarati': 'gu',
        # Common synonyms and phrases
        'brit': 'en', 'british': 'en', 'american': 'en', 'aussie': 'en', 'australian': 'en',
        'latino': 'es', 'latin american': 'es', 'mexican': 'es', 'argentinian': 'es',
        'bollywood': 'hi', 'korean drama': 'ko', 'k-drama': 'ko', 'anime': 'ja', 'j-drama': 'ja',
        'scandinavian': ['sv', 'no', 'da'], 'nordic': ['sv', 'no', 'da', 'fi', 'is'],
        'slavic': ['ru', 'pl', 'cs', 'uk', 'bg', 'sr', 'hr', 'sk'],
    }
    languages = []
    for lname, code in language_name_to_code.items():
        # Case-insensitive word boundary matching with flexible spacing
        lname_pattern = re.escape(lname).replace(r'\ ', r'[ -]?')  # Allow hyphens or spaces
        if re.search(rf"\b{lname_pattern}\b", normalized, re.IGNORECASE):
            if isinstance(code, list):
                for c in code:
                    if c not in languages:
                        languages.append(c)
                        logger.debug(f"[PARSE] Matched language: {lname} -> {c}")
            elif code not in languages:
                languages.append(code)
                logger.debug(f"[PARSE] Matched language: {lname} -> {code}")
    # Avoid accidental capture of negation 'no' or filler 'in' etc. (only full names processed above)
    # If user explicitly provided an 'original language: xx' pattern, orig_lang is handled separately below.
    try:
        import logging
        logging.getLogger(__name__).debug(f"[PARSE] Extracted languages from prompt='{prompt[:80]}': {languages}")
    except Exception:
        pass
    years, year_range = _extract_years(normalized)
    logger.info(f"[PARSE] Extracted years: {years}, year_range: {year_range}")
    
    # NEW: If decades detected and no explicit year_range, convert decade to year_range
    decades = _extract_decades(normalized)
    logger.info(f"[PARSE] Extracted decades: {decades}")
    
    if decades and not year_range:
        # Use first decade to create year_range
        decade_map = {
            "1950s": (1950, 1959), "1960s": (1960, 1969), "1970s": (1970, 1979),
            "1980s": (1980, 1989), "1990s": (1990, 1999), "2000s": (2000, 2009),
            "2010s": (2010, 2019), "2020s": (2020, 2029),
        }
        first_decade = decades[0]
        if first_decade in decade_map:
            year_range = list(decade_map[first_decade])
            logger.info(f"[PARSE] Converted decade '{first_decade}' to year_range: {year_range}")
    
    seeds = _extract_seed_titles(normalized)
    logger.info(f"[PARSE] Extracted seed titles: {seeds}")
    
    negative_cues = _extract_negative_cues(normalized)
    logger.info(f"[PARSE] Extracted negative cues: {negative_cues}")
    
    media_type = _detect_media_type(normalized)
    logger.info(f"[PARSE] Detected media_type: {media_type}")
    # Obscurity - use from prompt if specified, otherwise use default
    obscurity = None
    for key, val in OBSCURITY_MAP.items():
        if key in normalized:
            obscurity = val
            break
    # If not explicitly mentioned in prompt, use default setting
    if obscurity is None and default_obscurity:
        obscurity = default_obscurity
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
    
    # Extract networks, countries, creators with logging
    networks = _extract_networks(normalized)
    logger.info(f"[PARSE] Extracted networks: {networks}")
    
    countries = _extract_countries(normalized)
    logger.info(f"[PARSE] Extracted countries: {countries}")
    
    creators, directors = _extract_creators_directors(normalized, entities)
    logger.info(f"[PARSE] Extracted creators: {creators}, directors: {directors}")
    
    # NEW: Extract actors, studios, decades, rating qualifiers
    actors = _extract_actors(normalized, entities)
    logger.info(f"[PARSE] Extracted actors: {actors}")
    
    studios = _extract_studios(normalized, entities)
    logger.info(f"[PARSE] Extracted studios: {studios}")
    
    # Note: decades already extracted above before year_range conversion
    
    rating_qualifiers = _extract_rating_qualifiers(normalized)
    logger.info(f"[PARSE] Extracted rating qualifiers: {rating_qualifiers}")
    
    # Production status for TV shows
    in_production = None
    if "ongoing" in normalized or "still airing" in normalized or "currently airing" in normalized:
        in_production = True
    elif "ended" in normalized or "finished" in normalized or "completed" in normalized:
        in_production = False
    
    # Quoted phrases as must-have snippets
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', prompt)
    phrases = [q[0] or q[1] for q in quoted if (q[0] or q[1])]
    
    # CRITICAL: Extract ALL themes, fusions, and multi-word moods from moods_themes_map.py
    # This ensures ANY label from our curated lists is recognized and enforced via hard-inclusion
    # Examples: "political satire", "buddy cop action", "time loop thriller", "coming of age", etc.
    all_labels = MOODS + THEMES + FUSIONS
    
    # Check for multi-word labels (themes/fusions/moods with spaces)
    # Single-word moods will be captured by tokens, but multi-word terms need phrases
    multi_word_labels = [label for label in all_labels if " " in label or "-" in label]
    
    for label in multi_word_labels:
        # Use word boundaries and case-insensitive matching
        # Escape special regex characters in label
        escaped_label = re.escape(label)
        pattern = r'\b' + escaped_label.replace(r'\ ', r'\s+') + r'\b'
        if re.search(pattern, normalized, re.IGNORECASE):
            clean = label.lower().strip()
            if clean not in [p.lower() for p in phrases]:
                phrases.append(clean)
                logger.info(f"[PARSE] Extracted multi-word label phrase: {clean}")
    
    tokens = list(dict.fromkeys(lemmas))[:50]
    # Tone/Mood extraction with BOTH keyword matching AND SBERT similarity
    tone_keywords = detect_tone_keywords(normalized)
    tone_vec = sbert_tone_vector(normalized)
    
    # Seasonal/Holiday extraction for thematic emphasis
    seasonal_keywords = _extract_seasonal_keywords(normalized)
    if seasonal_keywords:
        logger.info(f"[PARSE] Extracted seasonal keywords: {seasonal_keywords}")
    
    # Also extract mood descriptors from the prompt itself
    # Look for common mood adjectives and phrases
    mood_patterns = [
        r'\b(feel[- ]good|feelgood|uplifting|inspiring|heartwarming)\b',
        r'\b(dark|grim|bleak|gritty|noir|moody|brooding)\b',
        r'\b(funny|hilarious|comedic|humorous|witty|laugh)\b',
        r'\b(scary|frightening|terrifying|spooky|creepy|eerie|horror)\b',
        r'\b(sad|melancholy|depressing|emotional|tearjerker|tear-jerker|tragic)\b',
        r'\b(romantic|love story|passionate|heartfelt|tender|sweet)\b',
        r'\b(suspenseful|tense|thrilling|gripping|intense|edge of your seat)\b',
        r'\b(action[- ]packed|explosive|adrenaline|high[- ]octane|fast[- ]paced)\b',
        r'\b(thought[- ]provoking|philosophical|cerebral|intellectual|deep|mind[- ]bending)\b',
        r'\b(whimsical|quirky|offbeat|eccentric|charming|magical)\b',
        r'\b(epic|grand|sweeping|spectacular|monumental)\b',
        r'\b(intimate|personal|character[- ]driven|quiet|subtle)\b',
        r'\b(nostalgic|retro|vintage|throwback|classic|old[- ]school)\b',
        r'\b(mysterious|enigmatic|cryptic|puzzling|intriguing)\b',
        r'\b(peaceful|calming|serene|tranquil|relaxing|soothing)\b',
        r'\b(chaotic|frantic|hectic|wild|crazy|unpredictable)\b',
        r'\b(empowering|powerful|confident|bold|fierce|badass)\b',
        r'\b(cozy|comforting|warm|homey|snug|hygge)\b',
    ]
    
    for pattern in mood_patterns:
        matches = re.findall(pattern, normalized, re.IGNORECASE)
        for match in matches:
            # Normalize the match (remove hyphens/spaces)
            clean_match = re.sub(r'[ -]', '', match.lower())
            if clean_match not in [tk.lower().replace(' ', '').replace('-', '') for tk in tone_keywords]:
                tone_keywords.append(match)
    
    logger.info(f"[PARSE] Extracted tone keywords: {tone_keywords}")
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
        "actors": actors or None,  # Actors extracted from starring/featuring patterns
        "creators": creators or None,  # TV show creators
        "directors": directors or None,  # Movie/TV directors
        "studios": studios or None,  # Production companies/studios extracted from patterns
        "people": entities["PERSON"] or None,  # All people entities (broader catch)
        "orgs": entities["ORG"] or None,  # All org entities (broader catch)
        "decades": decades or None,  # Decade references (80s, 90s, etc.)
        "rating_qualifiers": rating_qualifiers or None,  # Quality cues (highly rated, cult classic, etc.)
        "tone": (tone_keywords or None),
        "seasonal": (seasonal_keywords or None),  # Holiday/seasonal themes
        "seed_titles": (seeds or None),  # Seed titles for similarity matching
    }
    
    # Final comprehensive logging of all extracted filters
    logger.info(
        f"[PARSE] FINAL EXTRACTION SUMMARY:\n"
        f"  Genres: {genres}\n"
        f"  Languages: {languages}\n"
        f"  Media Type: {media_type}\n"
        f"  Years: {years}, Range: {year_range}\n"
        f"  Obscurity: {obscurity}\n"
        f"  Networks: {networks}\n"
        f"  Countries: {countries}\n"
        f"  Actors: {entities['PERSON']}\n"
        f"  Directors: {directors}\n"
        f"  Creators: {creators}\n"
        f"  Studios: {entities['ORG']}\n"
        f"  Seeds: {seeds}\n"
        f"  Tone: {tone_keywords}\n"
        f"  Seasonal: {seasonal_keywords}\n"
        f"  Negative: {negative_cues}\n"
        f"  Suggested Title: {suggested_title}\n"
        f"  List Type: {ltype}"
    )

    result = {
        "normalized_prompt": normalized,
        "filters": {k: v for k, v in filters.items() if v},
        "seed_titles": seeds,
        "tone_vector": tone_vec.tolist(),
        "suggested_title": suggested_title,
        "type": ltype,
    }
    
    logger.info(f"[PARSE] Returning {len(result['filters'])} non-empty filters")
    
    return result
