"""
moods_themes_map.py
- Curated mood, theme, and fusion presets for AI recommendation lists.
"""

MOODS = [
    "uplifting", "dark", "romantic", "funny", "suspenseful", "inspirational",
    "bawdy", "lighthearted", "hilarious", "melancholic", "adventurous", "chilling",
    "heartwarming", "gritty", "wholesome", "noirish", "feel-good", "somber",
    "happy", "sad", "excited", "chill", "nostalgic", "intense", "hopeful", "quirky", "epic", "cozy",
    "whimsical", "tragic", "optimistic", "cynical", "dreamy", "serene", "anxious", "euphoric", "reflective",
    "mysterious", "playful", "energetic", "sentimental", "spooky", "raw", "bleak", "fiery",
    "subdued", "tense", "joyful",
    # Expanded moods
    "contemplative", "chaotic", "peaceful", "frantic", "mellow", "vibrant", "brooding", "buoyant",
    "haunting", "exhilarating", "wistful", "empowering", "disorienting", "grounding", "ethereal",
    "visceral", "tranquil", "turbulent", "bittersweet", "cathartic", "surreal", "grim", "radiant",
    "meditative", "rebellious", "tender", "savage", "luminous", "oppressive", "liberating", "claustrophobic",
    "expansive", "intimate", "alienating", "welcoming", "absurd", "profound", "frenetic", "languid",
    "mesmerizing", "jarring", "soothing", "provocative", "comforting", "unsettling", "triumphant", "defeated"
]

THEMES = [
    "coming of age", "revenge", "redemption", "family", "friendship", "betrayal",
    "survival", "good vs evil", "self-discovery", "forbidden love", "hero's journey",
    "heist", "courtroom drama", "political intrigue", "sports underdog", "war and peace",
    "forgiveness", "sacrifice", "power", "loss", "ambition", "courage", "destiny", "faith", "technology", "nature",
    "peace", "love", "isolation", "community", "prejudice", "rebellion", "tradition", "change", "loyalty",
    "greed", "hope", "fear", "trust", "honor", "deception", "truth", "family secrets", "class struggle",
    # Expanded themes
    "corruption", "justice", "identity crisis", "cultural clash", "time travel", "parallel universes",
    "artificial intelligence", "environmental collapse", "colonization", "resistance", "memory and nostalgia",
    "generational conflict", "addiction", "mental health", "body horror", "transformation", "duality",
    "the American dream", "immigration", "diaspora", "gentrification", "wealth inequality", "surveillance",
    "free will vs determinism", "nature vs nurture", "mortality", "legacy", "second chances", "chosen one",
    "fish out of water", "odd couple", "mentor and student", "road to recovery", "breaking bad",
    "found family", "toxic relationships", "unconditional love", "artistic struggle", "fame and fortune",
    "fall from grace", "rise to power", "revolution", "utopia vs dystopia", "man vs machine", "man vs nature",
    "existential dread", "search for meaning", "divine intervention", "supernatural forces", "haunted past",
    "unfinished business", "small town secrets", "corporate greed", "whistleblowing", "conspiracy", "cover-up"
]

FUSIONS = [
    "romantic comedy", "action thriller", "sci-fi adventure", "dark comedy", "mystery drama",
    "fantasy epic", "horror comedy", "sci-fi noir", "historical thriller", "crime comedy",
    "supernatural romance", "post-apocalyptic western", "noir fantasy", "musical horror",
    "sports drama", "political satire", "spy adventure", "coming-of-age sci-fi", "documentary thriller",
    "animated family mystery", "war romance", "psychological horror", "biographical comedy", "space opera",
    # Expanded fusions
    "cyberpunk noir", "steampunk adventure", "zombie comedy", "vampire romance", "gothic horror",
    "techno thriller", "period mystery", "martial arts fantasy", "superhero drama", "disaster epic",
    "heist comedy", "western sci-fi", "samurai western", "kitchen sink drama", "mumblecore comedy",
    "folk horror", "body horror thriller", "slasher comedy", "courtroom thriller", "sports comedy",
    "mockumentary horror", "found footage thriller", "anthology drama", "road trip comedy", "buddy cop action",
    "fish out of water comedy", "revenge thriller", "con artist comedy", "survival horror", "creature feature",
    "kaiju disaster", "mecha action", "isekai adventure", "time loop thriller", "multiverse adventure",
    "noir western", "sci-fi romance", "fantasy romance", "supernatural thriller", "paranormal mystery",
    "teen horror", "coming-of-age drama", "slice of life drama", "workplace comedy", "ensemble drama",
    "psychological thriller", "erotic thriller", "legal drama", "medical drama", "procedural crime",
    "whodunit mystery", "locked room mystery", "giallo horror", "exploitation thriller", "grindhouse action"
]

MOOD_THEME_LABELS = MOODS + THEMES + FUSIONS

# Optional: suggest presets using Trakt history and embeddings
def suggest_presets_from_history(history_titles: list[str]) -> list[dict]:
    """Return a few suggested presets based on user's recent history titles using label similarity.
    Each preset is a dict {type, label, generated_title}.
    """
    try:
        if not history_titles:
            return []
        # Lazy import to avoid heavy deps unless used
        from .classifiers import sbert_tone_vector
        labels = MOOD_THEME_LABELS
        # Represent user history by concatenating titles
        text = "; ".join(history_titles[:20])
        vec = sbert_tone_vector(text)
        # Pick top labels by similarity weight
        top_idx = sorted(range(len(labels)), key=lambda i: vec[i], reverse=True)[:5]
        presets = []
        for i in top_idx:
            lbl = labels[i]
            preset_type = "mood" if lbl in MOODS else ("theme" if lbl in THEMES else "fusion")
            presets.append({
                "type": preset_type,
                "label": lbl,
                "generated_title": f"{lbl.title()} Picks",
            })
        return presets
    except Exception:
        return []
