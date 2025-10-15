def safe_extract(data: dict, key: str, default=None):
    """Safely extracts a value from a dict."""
    return data.get(key, default)

def normalize_payload(payload: dict):
    """Ensures consistent casing and key names."""
    return {k.lower(): v for k, v in payload.items()}
