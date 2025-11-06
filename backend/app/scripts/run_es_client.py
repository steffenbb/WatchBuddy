from app.services.elasticsearch_client import get_elasticsearch_client

es = get_elasticsearch_client()
if not es or not es.is_connected():
    print('ES client not connected')
else:
    res = es.search('harry potter', media_type=None, limit=12, strict_titles_only=False)
    import json
    print(json.dumps(res, indent=2))
