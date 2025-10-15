# Persistent Candidate CSV Data Directory

## Purpose
This directory is for placing TMDB CSV datasets that will be automatically imported into the `persistent_candidates` table on first startup.

## Required CSV Format
Place your TMDB datasets here before building the Docker image:
```
backend/data/TMDB_movies_dataset.csv
backend/data/TMDB_shows_dataset.csv
```

### Supported Column Names (Flexible Matching)
The import script auto-detects columns using aliases:

**Required:**
- `tmdb_id` or `id` (integer)
- `title` or `name` (string)

**Recommended:**
- `media_type` or `type` (movie/show/tv)
- `original_language` or `language` (ISO 639-1 code, e.g., 'en', 'da')
- `popularity` (float)
- `vote_average` or `rating` (float, 0-10)
- `vote_count` or `votes` (integer)
- `release_date` or `first_air_date` (YYYY-MM-DD)

**Optional:**
- `genres` or `genre_names` (JSON array or comma-separated)
- `keywords` (JSON array or comma-separated)
- `overview` or `description` or `summary` (text)
- `poster_path` or `poster` (TMDB path)
- `backdrop_path` or `backdrop` (TMDB path)
- `runtime` (integer, minutes)
- `status` (Released/Post Production/etc.)
- `original_title` or `original_name`

## Bootstrap Behavior
- On first container startup, `init_db()` checks if `persistent_candidates` table is empty.
- If empty, scans `/app/data/*.csv` and imports all matching CSVs.
- Computes derived scores (obscurity, mainstream, freshness) during import.
- Subsequent startups skip import if table already populated.

## Manual Import
If you add CSVs after initial setup or want to refresh:
```bash
docker exec -it watchbuddy-backend-1 python -m app.scripts.import_tmdb_csv /app/data/your_file.csv movie
```

## Volume Mounting (Alternative)
Instead of bundling CSVs in the image, mount this directory as a volume:
```yaml
# docker-compose.yml
backend:
  volumes:
    - ./backend/data:/app/data
```
Then place CSVs in `backend/data/` on the host.

## Security Note
Do not commit large CSV files to Git. Add them to `.gitignore`:
```
backend/data/*.csv
!backend/data/README.md
```
