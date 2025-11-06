import { apiGet, apiPost } from './client';

export interface Phase {
  id: number;
  label: string;
  icon?: string;
  start_at: string;
  end_at?: string;
  item_count: number;
  movie_count: number;
  show_count: number;
  phase_type: 'active' | 'minor' | 'historical' | 'future';
  phase_score: number;
  explanation?: string;
  dominant_genres: string[];
  dominant_keywords: string[];
  representative_posters: string[];
  franchise_name?: string;
  avg_runtime?: number;
  top_language?: string;
  cohesion: number;
  watch_density: number;
}

export interface PhaseDetail extends Phase {
  tmdb_ids: number[];
  trakt_ids: number[];
  media_types: string[];
}

export async function fetchCurrentPhase(userId = 1): Promise<Phase | null> {
  const res = await apiGet(`/users/${userId}/phases/current`);
  return res.phase || null;
}

export async function fetchPhaseHistory(userId = 1, limit = 10): Promise<Phase[]> {
  const res = await apiGet(`/users/${userId}/phases?limit=${limit}`);
  return res.phases || [];
}

export async function fetchPhaseDetail(userId = 1, phaseId: number): Promise<PhaseDetail> {
  const res = await apiGet(`/users/${userId}/phases/${phaseId}`);
  return res.phase as PhaseDetail;
}

export async function refreshPhases(userId = 1): Promise<void> {
  await apiPost(`/users/${userId}/phases/refresh`, { user_id: userId });
}

export async function convertPhaseToList(userId = 1, phaseId: number, listName?: string): Promise<{ list_id: number; task_id: string }>{
  const res = await apiPost(`/users/${userId}/phases/${phaseId}/convert`, { user_id: userId, list_name: listName });
  return { list_id: res.list_id, task_id: res.task_id };
}

export async function fetchPhaseTimeline(userId = 1): Promise<any> {
  return await apiGet(`/users/${userId}/phases/timeline`);
}

export interface PhasePrediction {
  label: string;
  icon: string;
  predicted_start: string;
  predicted_end: string;
  item_count: number;
  movie_count: number;
  show_count: number;
  confidence: number;
  explanation: string;
  dominant_genres: string[];
  dominant_keywords: string[];
  representative_posters: string[];
  cohesion: number;
}

export async function fetchPredictedPhase(userId = 1, lookbackDays = 42): Promise<PhasePrediction | null> {
  const res = await apiGet(`/users/${userId}/phases/predicted?lookback_days=${lookbackDays}`);
  return res.prediction || null;
}
