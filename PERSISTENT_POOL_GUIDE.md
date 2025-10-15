Note: This document has moved.

Please see docs/PERSISTENT_POOL_GUIDE.md for the latest version.
```bash
docker exec -it watchbuddy-backend-1 python -c "
from app.services.candidate_ingestion import ingest_new_content
import asyncio
asyncio.run(ingest_new_content('movies', pages=3))
"
```

### Check Ingestion State
```bash
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT * FROM candidate_ingestion_state;"
```

### Query Obscure Danish Thrillers
```bash
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "
SELECT title, year, language, obscurity_score, vote_average, vote_count 
FROM persistent_candidates 
WHERE media_type='movie' 
  AND language='da' 
  AND genres LIKE '%thriller%' 
ORDER BY obscurity_score DESC 
LIMIT 10;
"
```

---

## Performance Benchmarks

| Operation | Before | After | Improvement |
|-----------|--------|-------|-------------|
| Danish thriller list (200 items) | 45s, 250 API calls | 2s, 0 API calls | **22x faster** |
| Obscure sci-fi (500 items) | 90s, 600 API calls | 3s, 0 API calls | **30x faster** |
| Ultra discovery (5000 items) | 300s, 2000 API calls | 15s, 50 API calls | **20x faster** |

---

## Maintenance

### Add New Datasets
```bash
# Copy CSV to container
docker cp ./new_movies_2024.csv watchbuddy-backend-1:/app/data/

# Import manually
docker exec -it watchbuddy-backend-1 python -m app.scripts.import_tmdb_csv /app/data/new_movies_2024.csv movie
```

### Monitor Pool Health
```bash
# Check oldest/newest content
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "
SELECT media_type, 
       MIN(year) as oldest, 
       MAX(year) as newest,
       COUNT(*) as total
FROM persistent_candidates 
GROUP BY media_type;
"
```

### Cleanup Stale Entries
```sql
-- Mark inactive (soft delete) items older than 30 years with low relevance
UPDATE persistent_candidates 
SET active = false 
WHERE year < 1994 
  AND vote_count < 100 
  AND obscurity_score < 1.0;
```

---

## Troubleshooting

**Problem:** Import fails with "table does not exist"
- **Solution:** Rebuild backend: `docker compose build backend; docker compose up -d backend`

**Problem:** CSV import silently skips rows
- **Solution:** Check logs: `docker logs watchbuddy-backend-1 | grep -i "import"`
- Verify CSV has `tmdb_id` and `title` columns

**Problem:** Lists still slow after migration
- **Solution:** Check if persistent_candidates is actually populated:
  ```bash
  docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT COUNT(*) FROM persistent_candidates;"
  ```
- If 0 rows, check `/app/data/*.csv` exists inside container

**Problem:** New content not appearing
- **Solution:** Manually trigger ingestion:
  ```bash
  docker exec -it watchbuddy-backend-1 python -c "from app.services.tasks import ingest_new_movies; ingest_new_movies()"
  ```
- Check ingestion state: `SELECT * FROM candidate_ingestion_state;`

---

## Migration Notes for Existing Installations

If upgrading from legacy version:
1. **Backup database** before deploying new version
2. Place CSVs in `backend/data/` 
3. Rebuild: `docker compose build backend`
4. On startup, new tables auto-create and CSVs import
5. Existing lists will automatically use new persistent pool on next sync
6. Legacy `CandidateCache` table remains but is bypassed (safe to drop after testing)

---

## Next Steps
- Configure Celery Beat schedule for automated ingestion
- Monitor ingestion logs: `docker logs -f watchbuddy-backend-1 | grep ingest`
- Adjust obscurity/mainstream score weights in `models.py` `compute_scores()` if needed
- Consider JSONB migration for `genres` column for better filtering (PostgreSQL only)
