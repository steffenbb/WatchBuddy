import csv
import json
import sys
from pathlib import Path
from typing import Optional

from app.core.database import SessionLocal, engine
from app.models import PersistentCandidate
from sqlalchemy.dialects.postgresql import insert

REQUIRED_COLUMNS = {
    'tmdb_id': ['id'],
    'title': ['title','name'],
    'media_type': ['media_type','type'],
    'original_language': ['original_language'],
    'popularity': ['popularity'],
    'vote_average': ['vote_average'],
    'vote_count': ['vote_count'],
}

OPTIONAL_COLUMNS = {
    'release_date': ['release_date','first_air_date'],
    'overview': ['overview'],
    'genres': ['genres'],
    'keywords': ['keywords'],
    'poster_path': ['poster_path'],
    'backdrop_path': ['backdrop_path'],
    'runtime': ['runtime'],
    'is_adult': ['adult'],
    'status': ['status'],
    'original_title': ['original_title','original_name'],
    'imdb_id': ['imdb_id']
}

def find_col(row_keys, aliases):
    lowered = {k.lower(): k for k in row_keys}
    for a in aliases:
        if a.lower() in lowered:
            return lowered[a.lower()]
    return None

def parse_list_field(raw: str):
    if raw is None:
        return []
    raw = raw.strip()
    if not raw:
        return []
    # Try JSON first
    if raw.startswith('[') and raw.endswith(']'):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x) for x in data]
        except Exception:
            pass
    # Fallback: split by comma
    return [p.strip() for p in raw.split(',') if p.strip()]

def compute_scores(obj: PersistentCandidate):
    obj.compute_scores()


def upsert_batch(db, batch):
    if not batch:
        return
    for item_dict in batch:
        # Use merge for upsert behavior (update if exists, insert if not)
        existing = db.query(PersistentCandidate).filter_by(tmdb_id=item_dict['tmdb_id']).one_or_none()
        if existing:
            # Update existing
            for key, value in item_dict.items():
                if key not in ('id', 'tmdb_id'):
                    setattr(existing, key, value)
        else:
            # Insert new
            pc = PersistentCandidate(**item_dict)
            db.add(pc)


def import_csv(path: Path, default_media_type: Optional[str] = None, manual=True):
    db = SessionLocal()
    inserted = 0
    updated = 0
    try:
        with path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            batch = []
            for row in reader:
                # Resolve required columns
                mapped = {}
                skip = False
                for target, aliases in REQUIRED_COLUMNS.items():
                    col = find_col(headers, aliases)
                    value = row.get(col) if col else None
                    if target == 'tmdb_id':
                        if not value:
                            skip = True
                            break
                        try:
                            mapped['tmdb_id'] = int(value)
                        except ValueError:
                            skip = True
                            break
                    elif target == 'title':
                        if not value:
                            skip = True
                            break
                        mapped['title'] = value.strip()
                    elif target == 'media_type':
                        mt = value.lower() if value else (default_media_type or 'movie')
                        if mt in ('movie','movies'):
                            mapped['media_type'] = 'movie'
                        elif mt in ('show','tv','tvshow','series','shows'):
                            mapped['media_type'] = 'show'
                        else:
                            mapped['media_type'] = 'movie'
                    elif target == 'original_language':
                        mapped['language'] = (value or '').lower()[:5]
                    elif target == 'popularity':
                        try:
                            mapped['popularity'] = float(value) if value else 0.0
                        except ValueError:
                            mapped['popularity'] = 0.0
                    elif target == 'vote_average':
                        try:
                            mapped['vote_average'] = float(value) if value else None
                        except ValueError:
                            mapped['vote_average'] = None
                    elif target == 'vote_count':
                        try:
                            mapped['vote_count'] = int(value) if value else 0
                        except ValueError:
                            mapped['vote_count'] = 0
                if skip:
                    continue
                # Optional columns
                for target, aliases in OPTIONAL_COLUMNS.items():
                    col = find_col(headers, aliases)
                    value = row.get(col) if col else None
                    if value is None or value == '':
                        continue
                    if target in ('genres','keywords'):
                        parsed = parse_list_field(value)
                        mapped[target] = json.dumps(parsed) if parsed else None
                    elif target == 'is_adult':
                        mapped['is_adult'] = str(value).lower() in ('1','true','t','yes','y','True')
                    elif target == 'runtime':
                        try:
                            mapped['runtime'] = int(float(value))
                        except Exception:
                            pass
                    elif target == 'imdb_id':
                        mapped['imdb_id'] = value
                    elif target == 'original_title':
                        mapped['original_title'] = value
                    else:
                        mapped[target] = value
                # Derivations
                if 'release_date' in mapped and mapped.get('release_date') and len(mapped['release_date']) >= 4:
                    try:
                        mapped['year'] = int(mapped['release_date'][:4])
                    except Exception:
                        pass
                mapped['manual'] = manual
                # Initialize derived scores after creation in ORM object
                pc_obj = PersistentCandidate(**mapped)
                pc_obj.compute_scores()
                # Convert object back to dict for bulk upsert
                for key in ('obscurity_score','mainstream_score','freshness_score'):
                    mapped[key] = getattr(pc_obj, key)
                batch.append(mapped)
                if len(batch) >= 500:
                    upsert_batch(db, batch)
                    db.commit()
                    inserted += len(batch)
                    batch = []
            if batch:
                upsert_batch(db, batch)
                db.commit()
                inserted += len(batch)
        print(f"Imported/updated approx {inserted} rows from {path.name}")
    finally:
        db.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.import_tmdb_csv <csv_path> [media_type]")
        sys.exit(1)
    csv_path = Path(sys.argv[1])
    media_type = sys.argv[2] if len(sys.argv) > 2 else None
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)
    import_csv(csv_path, default_media_type=media_type)
