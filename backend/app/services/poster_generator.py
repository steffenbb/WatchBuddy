"""
poster_generator.py

Service for generating blended poster images for user lists.
Combines top scoring items' posters with thematic color overlays.
"""
import logging
import os
import json
import requests
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
from io import BytesIO
import colorsys

logger = logging.getLogger(__name__)

# Poster storage directory
POSTER_DIR = Path("/app/data/posters")
POSTER_DIR.mkdir(parents=True, exist_ok=True)

# TMDB image base URL
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Theme color overlays (hue, saturation, lightness adjustments)
THEME_COLORS = {
    "cyberpunk": (180, 0.7, 0.3),  # Teal/purple tones
    "western": (30, 0.6, 0.4),      # Dusted yellow/orange
    "horror": (0, 0.5, 0.2),        # Dark red
    "sci-fi": (200, 0.8, 0.4),      # Blue/cyan
    "romance": (340, 0.6, 0.5),     # Pink/magenta
    "action": (0, 0.8, 0.4),        # Bold red
    "drama": (240, 0.3, 0.3),       # Muted blue
    "comedy": (50, 0.7, 0.6),       # Bright yellow/orange
    "thriller": (280, 0.5, 0.3),    # Dark purple
    "fantasy": (270, 0.7, 0.5),     # Vibrant purple
    "mystery": (220, 0.4, 0.3),     # Dark teal
    "documentary": (120, 0.3, 0.4), # Muted green
    "default": (0, 0.3, 0.4)        # Neutral gray-blue
}


def download_poster_image(poster_path: str) -> Optional[Image.Image]:
    """Download poster image from TMDB."""
    if not poster_path:
        return None
    
    try:
        url = f"{TMDB_IMAGE_BASE}{poster_path}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            img = Image.open(BytesIO(response.content))
            return img.convert("RGB")
    except Exception as e:
        logger.warning(f"Failed to download poster {poster_path}: {e}")
    
    return None


def extract_dominant_colors(image: Image.Image, num_colors: int = 3) -> List[Tuple[int, int, int]]:
    """Extract dominant colors from image using simple color quantization."""
    try:
        # Resize for faster processing
        img_small = image.resize((150, 150))
        
        # Quantize to reduce colors
        img_quantized = img_small.quantize(colors=num_colors)
        palette = img_quantized.getpalette()
        
        # Extract RGB values for each color
        colors = []
        for i in range(num_colors):
            r = palette[i * 3]
            g = palette[i * 3 + 1]
            b = palette[i * 3 + 2]
            colors.append((r, g, b))
        
        return colors
    except Exception as e:
        logger.warning(f"Failed to extract colors: {e}")
        return [(100, 100, 100)]  # Default gray


def detect_theme_from_genres(genres: List[str]) -> str:
    """Detect theme based on genre list."""
    if not genres:
        return "default"
    
    genres_lower = [g.lower() for g in genres]
    
    # Check for specific themes
    for genre in genres_lower:
        if genre in THEME_COLORS:
            return genre
    
    # Check for compound genres
    if "science fiction" in genres_lower or "sci-fi" in genres_lower:
        return "sci-fi"
    
    return "default"


def apply_theme_overlay(image: Image.Image, theme: str, intensity: float = 0.3) -> Image.Image:
    """Apply thematic color overlay to image."""
    try:
        hue_shift, sat_boost, light_adj = THEME_COLORS.get(theme, THEME_COLORS["default"])
        
        # Convert to HSL for color adjustment
        img_hsv = image.convert("HSV")
        h, s, v = img_hsv.split()
        
        # Adjust hue (shift towards theme color)
        h_array = list(h.getdata())
        h_shifted = [(int(h_val + hue_shift * intensity) % 256) for h_val in h_array]
        h = h.point(lambda i: h_shifted[i] if i < len(h_shifted) else i)
        
        # Boost saturation
        s_enhancer = ImageEnhance.Color(image)
        img_saturated = s_enhancer.enhance(1.0 + sat_boost * intensity)
        
        # Adjust brightness
        bright_enhancer = ImageEnhance.Brightness(img_saturated)
        img_final = bright_enhancer.enhance(1.0 + light_adj * intensity)
        
        return img_final
    except Exception as e:
        logger.warning(f"Failed to apply theme overlay: {e}")
        return image


def create_poster_grid(images: List[Image.Image], theme: str = "default") -> Image.Image:
    """Create a grid layout of posters with theme overlay."""
    num_images = len(images)
    
    if num_images == 0:
        # Create placeholder
        placeholder = Image.new("RGB", (500, 750), color=(20, 20, 20))
        draw = ImageDraw.Draw(placeholder)
        draw.text((250, 375), "No Items", fill=(150, 150, 150), anchor="mm")
        return placeholder
    
    # Determine grid layout
    if num_images <= 2:
        cols, rows = 2, 1
        canvas_width, canvas_height = 500, 375
    elif num_images <= 4:
        cols, rows = 2, 2
        canvas_width, canvas_height = 500, 750
    else:
        cols, rows = 3, 2
        canvas_width, canvas_height = 750, 750
    
    # Create canvas
    canvas = Image.new("RGB", (canvas_width, canvas_height), color=(10, 10, 10))
    
    # Calculate cell size
    cell_width = canvas_width // cols
    cell_height = canvas_height // rows
    
    # Place images in grid
    for idx, img in enumerate(images[:cols * rows]):
        if img is None:
            continue
        
        # Apply theme overlay
        img_themed = apply_theme_overlay(img, theme, intensity=0.2)
        
        # Resize to fit cell
        img_resized = img_themed.resize((cell_width, cell_height), Image.Resampling.LANCZOS)
        
        # Calculate position
        row = idx // cols
        col = idx % cols
        x = col * cell_width
        y = row * cell_height
        
        # Paste image
        canvas.paste(img_resized, (x, y))
    
    # Apply subtle blur and vignette for cinematic effect
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.5))
    
    # Add vignette
    vignette = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    vignette_draw = ImageDraw.Draw(vignette)
    
    for i in range(100):
        alpha = int(i * 2.55 * 0.3)  # 30% max darkness at edges
        vignette_draw.rectangle(
            [i, i, canvas.size[0] - i, canvas.size[1] - i],
            outline=(0, 0, 0, alpha)
        )
    
    canvas = Image.alpha_composite(canvas.convert("RGBA"), vignette).convert("RGB")
    
    return canvas


def generate_list_poster(
    list_id: int,
    items: List[Dict[str, Any]],
    list_type: str = "custom",
    max_items: int = 5
) -> Optional[str]:
    """
    Generate a blended poster for a list.
    
    Args:
        list_id: List ID for filename
        items: List of items with 'poster_path' and 'score' fields
        list_type: Type of list for theme detection
        max_items: Maximum number of posters to blend
    
    Returns:
        Relative path to saved poster (e.g., "list_123.jpg") or None if failed
    """
    try:
        logger.info(f"Generating poster for list {list_id} with {len(items)} items")
        
        # Sort items by score (highest first)
        sorted_items = sorted(
            items,
            key=lambda x: x.get('score', x.get('ai_score', x.get('fit_score', 0))),
            reverse=True
        )
        
        # Get top N items with posters
        poster_images = []
        all_genres = []
        
        for item in sorted_items[:max_items * 2]:  # Fetch more in case some fail
            if len(poster_images) >= max_items:
                break
            
            poster_path = item.get('poster_path')
            if not poster_path:
                continue
            
            img = download_poster_image(poster_path)
            if img:
                poster_images.append(img)
            
            # Collect genres for theme detection
            genres_str = item.get('genres', '[]')
            if isinstance(genres_str, str):
                try:
                    genres = json.loads(genres_str)
                    all_genres.extend(genres)
                except:
                    pass
            elif isinstance(genres_str, list):
                all_genres.extend(genres_str)
        
        if not poster_images:
            logger.warning(f"No poster images available for list {list_id}")
            return None
        
        # Detect theme from genres
        theme = detect_theme_from_genres(all_genres)
        logger.info(f"Detected theme '{theme}' for list {list_id}")
        
        # Create poster grid
        poster = create_poster_grid(poster_images, theme)
        
        # Save poster with type prefix to avoid ID collisions between tables
        # user_lists (custom/chat) use numeric IDs
        # individual_lists use numeric IDs  
        # ai_lists use UUIDs
        if list_type == "individual":
            prefix = "individual_"
        elif list_type in ["mood", "theme", "fusion", "chat"]:
            prefix = f"{list_type}_"
        else:
            prefix = ""  # custom lists have no prefix
        
        filename = f"{prefix}list_{list_id}.jpg"
        filepath = POSTER_DIR / filename
        
        poster.save(filepath, "JPEG", quality=85, optimize=True)
        logger.info(f"Saved poster to {filepath}")
        
        return filename
        
    except Exception as e:
        logger.error(f"Failed to generate poster for list {list_id}: {e}", exc_info=True)
        return None


def delete_list_poster(poster_path: Optional[str]):
    """Delete old poster file if it exists."""
    if not poster_path:
        return
    
    try:
        filepath = POSTER_DIR / poster_path
        if filepath.exists():
            filepath.unlink()
            logger.info(f"Deleted old poster: {poster_path}")
    except Exception as e:
        logger.warning(f"Failed to delete poster {poster_path}: {e}")
