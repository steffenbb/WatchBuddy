# 🎬 WatchBuddy

**Your AI-powered movie and TV show recommendation companion**

WatchBuddy is a smart recommendation system that syncs with your Trakt watch history to suggest movies and TV shows tailored to your taste. Create custom SmartLists based on mood, genre, language, and more - all running locally with zero configuration.

Check Wiki for quick fixes: https://github.com/steffenbb/WatchBuddy/wiki

---

## ✨ Features

### 🎯 Smart Recommendations
- **1.4 Million+ Candidate Pool**: Instant recommendations from our massive pre-loaded database (~1.3M movies + 165K shows)
- **Trakt History Integration**: Personalized scoring based on your actual watch history and ratings
- **Multi-Factor Scoring**: Combines popularity, rating, votes, freshness, mood matching, and semantic similarity
- **Discovery Modes**: Find mainstream hits, hidden gems, or a balanced mix based on your preferences
- **Semantic Matching**: TF-IDF-based similarity finds content similar to your favorites
- **Mood-Based Filtering**: Choose from Dark, Cozy, Intense, Quirky, Feel-Good and more

### 📋 SmartLists & List Types

WatchBuddy offers six powerful list types to match different discovery needs:

#### **Custom Lists**
Manually curated lists where you have full control. Perfect for watchlists, collections, or sharing with friends.
- Add/remove titles manually
- Set custom filters and ordering
- Optionally sync to Trakt

#### **Suggested Lists**
Pre-configured recommendation lists with optimized filters for popular use cases.
- "Hidden Gems" - Highly rated but lesser-known titles
- "Recent Blockbusters" - Mainstream hits from the last 2 years
- "Cult Classics" - Beloved niche favorites
- One-click setup with proven filter combinations

#### **Mood Lists**
Emotionally-driven recommendations that match your current feeling.
- Select up to 3 moods (Dark, Cozy, Tense, Quirky, Feel-Good, etc.)
- Mood vector scoring weights genres and themes appropriately
- Perfect for "I want something dark and intense tonight"

#### **Theme Lists**
Curated around specific topics, settings, or concepts.
- Time Travel, Space Exploration, Heist Movies, etc.
- Combines genre filters with keyword matching
- Semantic search finds thematically similar content

#### **Fusion Lists**
Blend multiple genres together for unique combinations.
- "Rom-Com Thrillers" (Romance + Comedy + Thriller)
- "Sci-Fi Horror" (Science Fiction + Horror)
- Automatic genre weighting finds the best crossover titles

#### **Chat Lists** ⭐ *Most Powerful*
Natural language prompts powered by smart parsing - just describe what you want!

**Examples:**
- *"Cozy feel-good movies like The Hangover, prefer stuff after 2000, comedies with a bit of action"*
- *"Dark psychological thrillers in Scandinavian languages"*
- *"Hidden gem sci-fi films from the 80s and 90s"*

**How Chat Lists Work:**
1. **Smart Parsing**: Extracts genres, moods, years, languages, and reference titles from your text
2. **Discovery Detection**: Recognizes "obscure", "popular", "mainstream", "under the radar" keywords
3. **Semantic Anchoring**: Uses "like [movie]" or "similar to [show]" for TF-IDF similarity matching
4. **Automatic Defaults**: Assumes English and mainstream content unless you specify otherwise
5. **Flexible Genre Matching**: Allows broader matches while respecting your intent

**Chat Features:**
- Natural language understanding (no complex syntax needed)
- Automatic mainstream bias for quality results

# WatchBuddy

## Project Overview
WatchBuddy is a technical movie and TV recommendation system built for developers and power users. It features a persistent database of 20,000+ movies and shows, advanced filtering, and multiple list types for different workflows. The backend uses FastAPI, PostgreSQL, Redis, and Celery; the frontend is React + Vite + Tailwind CSS.

---

## List Types

### Manual & Suggested Lists
**Manual Lists:**
- Directly add or remove items to build custom lists for yourself or groups.
- Great for personal curation, watch parties, or collaborative planning.

**Suggested Lists:**
- Automatically generated based on your preferences, watch history, or predefined rules.
- Useful for onboarding, quick recommendations, or exploring new content.

---

### AI Lists (Theme, Mood, Fusion, Chat)
**Theme Lists:**
- AI-generated lists based on genres, themes, or keywords (e.g., "Nordic Noir", "Feel-Good Comedy").

**Mood Lists:**
- Recommendations tailored to your mood (e.g., "Relaxing", "Intense", "Uplifting").

**Fusion Lists:**
- Combine multiple filters: genre, mood, year, language, networks, countries, creators, directors.
- Highly customizable for technical users.

**Chat Lists:**
- Build lists interactively via chat prompts and natural language queries.
- Uses semantic search and TF-IDF for deep matching.



---

### Individual Lists
**Individual Lists:**
- Track single items or create lists for individual users.
- Supports custom scoring, granular control, and export to Trakt.

---

## Technical Architecture

### Persistent Candidate Pool
- Database table pre-populated.
- Enables fast list generation and filtering without external API calls.
- Advanced SQL filtering: genres, languages, media types, years, networks, countries, creators, directors.

### Filtering & Scoring Engine
- Multi-factor scoring: blends persistent scores (obscurity, mainstream, freshness) with mood vectors and semantic matching.
- Uses scikit-learn (TF-IDF, cosine similarity) for semantic search.
- Managed memory contexts for efficient batch scoring and garbage collection.
- All filters are optional and can be combined for precise recommendations.

### AI Functions
- Theme/mood/fusion/chat lists use semantic search, mood vectors, and technical filters.
- SQL WHERE clauses support all major fields, including networks, countries, creators, directors.
- Managed memory context ensures efficient resource usage for large batch operations.

### Trakt Export Integration
- Sync any list to Trakt using TraktClient and TraktIdResolver.
- Resolves missing Trakt IDs from TMDB IDs automatically.
- Supports batch export for AI, manual, and individual lists.
- Handles Trakt API rate limits and token refresh transparently.

---

## Usage Guide

### Creating and Managing Lists
- Use the frontend UI or API endpoints to create, sync, and manage lists.
- Manual lists: Add/remove items directly.
- Suggested lists: Trigger sync for auto-population.
- AI lists: Use chat prompts or select filters for generation.
- Individual lists: Track single items or export to Trakt.

### API Endpoints
- `/api/smartlists/create` – Create a new list
- `/api/smartlists/sync/{id}` – Sync a list (supports `force_full`)
- `/api/notifications/stream` – Real-time notifications via SSE
- `/api/trakt/export` – Export list to Trakt

---

## Screenshots
<img width="1905" height="1639" alt="127 0 0 1_5173_(PC) (7)" src="https://github.com/user-attachments/assets/44c02c2a-1100-4532-bf72-bbd1a3e7c85d" />
<img width="1905" height="1295" alt="127 0 0 1_5173_(PC) (4)" src="https://github.com/user-attachments/assets/d6bf5cde-f17c-4099-a89b-3bd014b9a9e0" />
<img width="1905" height="2186" alt="127 0 0 1_5173_(PC)" src="https://github.com/user-attachments/assets/11f9e134-9cca-4d8f-bf44-2202d743ce27" />
<img width="1905" height="1131" alt="127 0 0 1_5173_(PC) (2)" src="https://github.com/user-attachments/assets/bdb8cda4-99f7-49a9-8beb-ad91635b810c" />
<img width="1905" height="1295" alt="127 0 0 1_5173_(PC) (6)" src="https://github.com/user-attachments/assets/af164a49-169f-4f02-be6c-af1a9fa8231b" />
<img width="1905" height="1528" alt="127 0 0 1_5173_(PC) (9)" src="https://github.com/user-attachments/assets/a04cfa9a-7093-495d-8599-d69722027c14" />
<img width="1905" height="1131" alt="127 0 0 1_5173_(PC) (1)" src="https://github.com/user-attachments/assets/69a1be22-ebc1-4d53-a7ea-413e790bc193" />
<img width="1920" height="1080" alt="127 0 0 1_5173_(PC) (8)" src="https://github.com/user-attachments/assets/d6fc24f2-7bac-4f37-a897-759b3d32e5b1" />
<img width="1905" height="1295" alt="127 0 0 1_5173_(PC) (5)" src="https://github.com/user-attachments/assets/734d2730-b2c1-4fc7-aef1-95cd5fb26549" />


---

## Development & Deployment
- Zero-config Docker setup: `docker compose build backend; docker compose up -d backend`
- Persistent PostgreSQL and Redis volumes
- Celery for background tasks and periodic updates
- All secrets stored in Redis (no .env files)

---

## Contributing
- Fork the repo and submit pull requests for new features or bug fixes.
- Please ensure all new code is covered by unit and integration tests.

---

## License
MIT License

---

## Version
November 2025
3. **Add TMDB API Key**:
    - Get free API key from [TMDB](https://www.themoviedb.org/settings/api)
    - Go to Settings → TMDB API Key and save it
4. **Create Your First List**:
    - Click "Create SmartList"
    - Set filters (genre, mood, year, language)
    - Watch your personalized recommendations appear!

---

## 🎯 How Recommendations Work

### The Scoring System

WatchBuddy uses a sophisticated multi-factor scoring algorithm to rank candidates. Every title gets a score from 0.0 to 1.0 based on:

#### **Base Quality Metrics** (40-50% weight)
- **TMDB Rating** (vote_average): Normalized 0-10 scale → 0-1
- **Vote Count**: More votes = more reliable rating (logarithmic scaling)
- **Popularity**: TMDB popularity score indicates current interest

#### **Discovery Mode** (20-30% weight)
Different modes adjust how popularity affects scoring:
- **Mainstream/Popular**: Boosts high mainstream_score (popularity + votes + rating)
- **Obscure/Hidden Gems**: Boosts high obscurity_score (high rating, low popularity)
- **Balanced**: Equal weight to both mainstream and obscure content
- **Ultra Discovery**: Aggressive exploration of lesser-known titles

#### **Freshness Bonus** (5-15% weight)
Recent releases get a boost:
- Content <1 year old: +15% bonus
- Content 1-2 years old: +10% bonus
- Content 2-3 years old: +5% bonus
- Older content: No bonus

#### **Mood Matching** (10-20% weight for mood lists)
Mood vectors map to genres and themes:
- "Dark" → boosts Thriller, Horror, Crime
- "Cozy" → boosts Comedy, Romance, Family
- "Intense" → boosts Action, Thriller, War
- Multiple moods blend together with configurable weights

#### **Semantic Similarity** (20-55% weight when anchor set)
For "like [movie]" queries:
- TF-IDF vectorization of title, overview, and genres
- Cosine similarity between anchor and candidate
- Higher weight (55%) for chat lists to prioritize good matches
- Lower weight (50%) for other lists to balance with other factors

#### **Trakt History Integration** (Dynamic)
- **Watched Penalty**: Already-watched items scored lower (or excluded entirely)
- **Rating Alignment**: Your Trakt ratings influence similar content scoring (future feature)
- **Genre Preference**: Your watch history informs genre weighting (future feature)

#### **Post-Processing Adjustments**
- **Animation/Family Penalty**: -8-10% when not explicitly requested (reduces kid content noise)
- **English Boost**: +5% for English content when language not specified (mainstream bias)
- **Duplicate Filtering**: Removes duplicates and recently shown items

### Score Calculation Example

For a candidate like "21 Jump Street" (2012):
```python
Base Score = 0.7  # Good rating (7.1/10), 5000+ votes, decent popularity

Discovery Adjustment:
  - Mainstream mode: +0.1 (high mainstream_score = 180)
  
Freshness:
  - Released 2012: 0.0 (>3 years old)

Semantic Similarity (if "like The Hangover"):
  - TF-IDF similarity: 0.65
  - Weight: 0.55 (chat list)
  - Contribution: 0.55 * 0.65 = 0.36

English Boost:
  - +0.05 (English content, no language filter)

Final Score = (0.45 * 0.7) + (0.55 * 0.65) + 0.05 = 0.73
```

This balanced approach ensures:
✅ Quality content rises to the top
✅ Discovery preferences are respected
✅ Semantic matches are prioritized for "like X" queries
✅ Fresh content gets visibility
✅ Your watch history keeps recommendations relevant

---

## 📖 Usage Guide

### Creating SmartLists

SmartLists are dynamically generated based on your filters:

1. Click **"Create SmartList"** on the dashboard
2. Configure filters:
    - **Genres**: Select multiple (e.g., `Action`, `Sci-Fi`)
    - **Moods**: Choose up to 3 (`Dark`, `Tense`, `Quirky`)
    - **Languages**: Support for 20+ languages (`en`, `da`, `sv`, `no`, etc.)
    - **Year Range**: Filter by decade or specific years
    - **Obscurity**: Discover hidden gems vs mainstream hits
3. Set **Item Limit** (default: 200)
4. Click **"Sync"** to populate your list

### Syncing with Trakt

WatchBuddy automatically syncs with Trakt:

- **Watched Status**: Items you've watched on Trakt are marked automatically
- **Two-Way Sync**: Changes made in WatchBuddy update Trakt lists
- **Background Updates**: Celery Beat refreshes lists every 24 hours

### How Fetching and Enrichment Works (Plain-English)

- Preloaded pool: WatchBuddy ships with a large, preloaded database of titles so your lists build fast without waiting on external APIs.
- Offline-first syncs: Your SmartLists are created by querying the local database (language, genre, year, mood, etc.).
- Background enrichment: Extra details (artwork, overviews, mappings) are fetched in the background from TMDB/Trakt when available.
- Graceful failures: Some items don’t have perfect cross-service mappings (404s are normal). We keep those items and continue.
- Freshness: Recent titles get vote/popularity refreshed periodically so recommendations stay up to date.

### Managing Lists

- **Custom Lists**: Manually add specific titles
- **Edit Filters**: Modify SmartList criteria anytime and re-sync
- **Delete Lists**: Removes from both WatchBuddy and Trakt
- **Export**: Lists are automatically synced to your Trakt account

---

## 🔧 Configuration

### Environment Variables

WatchBuddy works out-of-the-box, but you can customize via environment variables:

```bash
# docker-compose.override.yml (optional)
services:
  backend:
     environment:
        - LOG_LEVEL=DEBUG  # Default: INFO
  db:
     environment:
        - POSTGRES_PASSWORD=custom_password  # Default: watchbuddy
```

### Data Persistence

All data is stored in Docker volumes:
- `db_data_v2`: PostgreSQL database (lists, items, candidates)
- Redis (settings, cache, Celery queues)

To reset everything:
```bash
docker compose down -v  # WARNING: Deletes all data!
docker compose up -d
```

---

## 🛠️ Troubleshooting

### Lists Not Syncing?
1. Check Trakt connection in Settings
2. Verify TMDB API key is valid
3. Look for errors in `docker logs watchbuddy-backend-1`

### Missing Recommendations?
- First startup takes ~2 minutes to load candidates

---

### Container Issues?
```bash
# Restart all services
docker compose restart

# Rebuild after code changes
docker compose build backend
docker compose up -d backend

# Check service health
docker compose ps
docker logs watchbuddy-backend-1
```

---

## 📊 Technical Stack

- **Backend**: Python 3.11 + FastAPI
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS
- **Database**: PostgreSQL 15
- **Cache**: Redis 7
- **Task Queue**: Celery + Celery Beat
- **Deployment**: Docker + Docker Compose

---

## 📜 Data Attribution

**Contains information from:**
- **TMDB Movies Dataset (2024)** – made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)
- **TMDB TV Shows Dataset (2024)** – made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)

TMDB data is used for metadata enrichment and recommendation scoring. Movie posters and metadata are fetched via the [TMDB API](https://www.themoviedb.org/documentation/api).

---

## 🤝 Contributing

WatchBuddy is built for personal use but open to contributions:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🙏 Acknowledgments

- [Trakt](https://trakt.tv) for watch history API
- [TMDB](https://www.themoviedb.org) for movie/TV metadata
- [OpenDataCommons](https://opendatacommons.org) for TMDB datasets

---


