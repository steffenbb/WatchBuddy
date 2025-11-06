import { apiGet, apiPost, apiPatch, apiDelete, apiPut } from "./client";

export type IndividualList = {
  id: number;
  name: string;
  description?: string;
  created_at: string;
  updated_at: string;
  trakt_list_id?: string;
  trakt_synced_at?: string;
  is_public: boolean;
  item_count?: number;
  poster_path?: string | null;
};

export type IndividualListItem = {
  id: number;
  tmdb_id: number;
  trakt_id?: number;
  media_type: "movie" | "show";
  title: string;
  original_title?: string;
  year?: number;
  overview?: string;
  poster_path?: string;
  backdrop_path?: string;
  genres?: string;
  order_index: number;
  fit_score?: number;
  added_at: string;
  metadata_json?: any;
};

export type SearchResult = {
  tmdb_id: number;
  media_type: "movie" | "show";
  title: string;
  original_title?: string;
  year?: number;
  overview?: string;
  poster_path?: string;
  backdrop_path?: string;
  genres?: string;
  vote_average?: number;
  vote_count?: number;
  popularity?: number;
  fit_score?: number;
  relevance_score?: number;
};

export type Suggestion = SearchResult & {
  similarity_score?: number;
  is_high_fit: boolean;
};

export async function getIndividualLists(userId = 1): Promise<IndividualList[]> {
  return apiGet(`/individual-lists/?user_id=${userId}`);
}

export async function createIndividualList(name: string, description?: string, is_public = false, userId = 1): Promise<IndividualList> {
  return apiPost(`/individual-lists/`, { name, description, is_public, user_id: userId });
}

export async function deleteIndividualList(id: number, userId = 1) {
  return apiDelete(`/individual-lists/${id}?user_id=${userId}`);
}

export async function getIndividualList(id: number, userId = 1) {
  return apiGet(`/individual-lists/${id}?user_id=${userId}`);
}

export async function updateIndividualList(id: number, data: Partial<Pick<IndividualList, "name"|"description"|"is_public">>, userId = 1) {
  return apiPatch(`/individual-lists/${id}?user_id=${userId}`, data);
}

export async function addItemsToIndividualList(listId: number, items: Partial<IndividualListItem>[], userId = 1) {
  return apiPost(`/individual-lists/${listId}/items`, { items, user_id: userId });
}

export async function removeItemFromIndividualList(listId: number, itemId: number, userId = 1) {
  return apiDelete(`/individual-lists/${listId}/items/${itemId}?user_id=${userId}`);
}

export async function reorderIndividualList(listId: number, itemIds: number[], userId = 1) {
  return apiPut(`/individual-lists/${listId}/items/reorder`, { item_ids: itemIds, user_id: userId });
}

export async function searchIndividualList(listId: number, query: string, userId = 1, limit = 50, skipFitScoring = false) {
  return apiGet(`/individual-lists/${listId}/search?q=${encodeURIComponent(query)}&user_id=${userId}&limit=${limit}&skip_fit_scoring=${skipFitScoring}`);
}

export async function suggestionsForIndividualList(listId: number, userId = 1) {
  return apiGet(`/individual-lists/${listId}/suggestions?user_id=${userId}`);
}

export async function syncIndividualListToTrakt(listId: number, userId = 1) {
  return apiPost(`/individual-lists/${listId}/sync-trakt`, { user_id: userId });
}
