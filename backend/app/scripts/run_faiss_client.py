from app.services.individual_list_search import IndividualListSearchService
s = IndividualListSearchService(user_id=1)
res = s._faiss_search('harry potter')
import json
print(json.dumps(res, indent=2))
