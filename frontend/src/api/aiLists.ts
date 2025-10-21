// src/api/aiLists.ts
// API client for AI-powered lists
import { apiPost, apiGet } from './client';

export async function createAiList(prompt: string, user_id = 1) {
  return apiPost('/ai/create', { prompt, user_id });
}

export async function listAiLists(user_id = 1) {
  return apiPost('/ai/list', { user_id });
}

export async function refreshAiList(ai_list_id: string, user_id = 1) {
  return apiPost(`/ai/refresh/${ai_list_id}`, { user_id });
}

export async function deleteAiList(ai_list_id: string, user_id = 1) {
  return apiPost(`/ai/delete/${ai_list_id}`, { user_id });
}

export async function getPromptCacheByHash(hash: string) {
  return apiGet(`/ai/prompt-cache/${hash}`);
}

export async function listAiListItems(ai_list_id: string) {
  return apiGet(`/ai/${ai_list_id}/items`);
}

export async function generateSeven(user_id = 1) {
  return apiPost('/ai/generate-7', { user_id });
}

export async function getCooldown(ai_list_id: string) {
  return apiGet(`/ai/cooldown/${ai_list_id}`);
}
