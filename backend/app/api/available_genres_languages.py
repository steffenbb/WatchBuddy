from fastapi import APIRouter
from app.utils.extract_genres_languages import get_genres_and_languages

router = APIRouter()

@router.get("/available-genres-languages")
def available_genres_languages():
    genres, languages = get_genres_and_languages()
    return {"genres": genres, "languages": languages}
