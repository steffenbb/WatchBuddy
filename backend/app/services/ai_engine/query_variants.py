from typing import Dict, Iterable, List, Optional

# Build focused query variants to improve recall per facet
# Variants: entity/people/brands, tone+mood, plot/genre, era/recency, audience/safety

def build_query_variants(
    base_query: str,
    facets: Optional[Dict[str, Iterable[str]]] = None,
    mood: Optional[str] = None,
    season: Optional[str] = None,
    era: Optional[str] = None,
    audience: Optional[str] = None,
    language: Optional[str] = None,
    pacing: Optional[str] = None,
    runtime_band: Optional[str] = None,
    max_variants: int = 4,
) -> List[str]:
    variants: List[str] = []

    def add(parts: List[str]):
        q = " | ".join([p for p in parts if p])
        if q and q not in variants:
            variants.append(q)

    # Base with core context
    add([base_query, _f("mood", mood), _f("season", season)])

    # Entities/brands/people
    ent = _facet_string(facets, ["actors", "directors", "creators", "studios", "networks", "brands"]) if facets else None
    if ent:
        add([base_query, _f("entities", ent)])

    # Tone + genre focused
    tone = _facet_string(facets, ["tones", "genres"]) if facets else None
    add([base_query, _f("tone", tone)])

    # Audience/safety/language
    add([base_query, _f("audience", audience), _f("language", language)])

    # Era/recency and pacing/runtime preferences
    add([base_query, _f("era", era), _f("pacing", pacing), _f("runtime", runtime_band)])

    # Trim to max variants, keep uniqueness
    return variants[:max_variants]


def _facet_string(facets: Dict[str, Iterable[str]], keys: List[str]) -> Optional[str]:
    vals: List[str] = []
    for k in keys:
        v = facets.get(k)
        if not v:
            continue
        vals.extend([str(x) for x in v])
    return ", ".join(vals) if vals else None


def _f(k: str, v: Optional[str]) -> Optional[str]:
    return f"{k}: {v}" if v else None
