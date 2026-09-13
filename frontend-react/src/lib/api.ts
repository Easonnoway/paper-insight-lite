import type {
  ActiveLlmModel,
  AutoBackupRunResult,
  AutoBackupSettings,
  AutoBackupSettingsInput,
  AuthResponse,
  CategorizeApplyPayload,
  CategorizeApplyResponse,
  CategorizeJobKind,
  CategorizeJobListResponse,
  ChatMessage,
  ChatSessionSummary,
  LibraryExportInfo,
  LibraryImportPreview,
  LibraryImportResult,
  MarkedPaperListResponse,
  MyPaperFilter,
  MyPaperSort,
  Paper,
  PaperCategory,
  PaperCategoryListResponse,
  PaperMark,
  PaperMarkCategoryRef,
  ReadingOverviewResponse,
} from '@/types';

const DEV_API_BASE =
  typeof window === 'undefined'
    ? 'http://127.0.0.1:18472'
    : `${window.location.protocol}//${window.location.hostname}:18472`;

const API_BASE = import.meta.env.DEV ? DEV_API_BASE : '';

export function apiUrl(path: string): string {
  return path.startsWith('/') ? `${API_BASE}${path}` : path;
}

export function paperApiPath(paperId: string, suffix = ''): string {
  return `/paper/${encodeURIComponent(paperId)}${suffix}`;
}

function paperMarkApiPath(paperId: string): string {
  return `/papers/${encodeURIComponent(paperId)}/mark`;
}

interface StreamOptions {
  onChunk?: (chunk: string) => void;
  onEvent?: (event: string, data: string) => void;
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }

  let detail = 'Request failed';
  try {
    const parsed = (await response.json()) as { detail?: string };
    detail = parsed.detail ?? detail;
  } catch {
    detail = response.statusText || detail;
  }
  throw new Error(detail);
}

async function apiRequest(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const request = typeof input === 'string' ? apiUrl(input) : input;
  return fetch(request, {
    credentials: 'include',
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  });
}

async function apiFetch<T>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  const response = await apiRequest(input, init);
  return readJson<T>(response);
}

export async function fetchPaperInfo(paperId: string): Promise<Paper> {
  return apiFetch<Paper>(paperApiPath(paperId, '/info'));
}

export async function createArxivPaper(input: string): Promise<Paper> {
  const payload = await apiFetch<{ paper: Paper }>('/arxiv-papers', {
    method: 'POST',
    body: JSON.stringify({ input }),
  });
  return payload.paper;
}

export interface ManualPaperInput {
  title: string;
  authors?: string[];
  abstract?: string | null;
  pdf?: string | null;
  venue?: string | null;
  keywords?: string[];
  published_year?: number | null;
}

export async function createManualPaper(input: ManualPaperInput): Promise<Paper> {
  const payload = await apiFetch<{ paper: Paper }>('/manual-papers', {
    method: 'POST',
    body: JSON.stringify(input),
  });
  return payload.paper;
}

export async function fetchChatSessions(paperId: string): Promise<ChatSessionSummary[]> {
  const response = await apiRequest(paperApiPath(paperId, '/chat/sessions'));
  if (response.status === 401) {
    return [];
  }
  return readJson<ChatSessionSummary[]>(response);
}

export async function fetchChatMessages(sessionId: string): Promise<ChatMessage[]> {
  return apiFetch<ChatMessage[]>(`/chat/${sessionId}/messages`);
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/chat/${sessionId}`, { method: 'DELETE' });
}

export async function fetchActiveLlmModel(): Promise<ActiveLlmModel> {
  return apiFetch<ActiveLlmModel>('/llm/active');
}

export interface LlmProviderInfo {
  id: string;
  provider_key?: string | null;
  name: string;
  base_url: string;
  has_api_key: boolean;
  api_key_masked?: string | null;
  is_active: boolean;
  is_enabled: boolean;
  active_model?: string | null;
  models: Array<{ model_name: string; is_enabled: boolean }>;
}

export async function fetchLlmProviders(): Promise<LlmProviderInfo[]> {
  const payload = await apiFetch<{ providers: LlmProviderInfo[] }>('/llm/providers');
  return payload.providers ?? [];
}

export async function setActiveLlm(
  providerId: string,
  modelName?: string | null,
): Promise<ActiveLlmModel> {
  return apiFetch<ActiveLlmModel>('/llm/active', {
    method: 'POST',
    body: JSON.stringify({ provider_id: providerId, model_name: modelName ?? null }),
  });
}

export async function updateLlmProviderApiKey(
  providerId: string,
  apiKey: string,
): Promise<{ id: string; name: string; has_api_key: boolean; api_key_masked?: string | null }> {
  return apiFetch(`/llm/providers/${encodeURIComponent(providerId)}/api-key`, {
    method: 'PATCH',
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export interface CreateLlmProviderInput {
  provider_key: string;
  name: string;
  base_url: string;
  api_key?: string | null;
  active_model?: string | null;
}

export async function createLlmProvider(
  input: CreateLlmProviderInput,
): Promise<LlmProviderInfo> {
  return apiFetch<LlmProviderInfo>('/llm/providers', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateLlmProvider(
  providerId: string,
  name: string,
  baseUrl: string,
): Promise<LlmProviderInfo> {
  return apiFetch<LlmProviderInfo>(`/llm/providers/${encodeURIComponent(providerId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name, base_url: baseUrl }),
  });
}

export async function fetchMe(): Promise<AuthResponse> {
  return apiFetch<AuthResponse>('/auth/me');
}

export async function fetchPaperMarks(paperIds: string[]): Promise<Record<string, PaperMark>> {
  if (paperIds.length === 0) {
    return {};
  }
  const params = new URLSearchParams({ paper_ids: paperIds.join(',') });
  const response = await apiRequest(`/me/paper-marks?${params.toString()}`);
  if (response.status === 401) {
    return {};
  }
  const payload = await readJson<{ marks: Record<string, PaperMark> }>(response);
  return payload.marks;
}

export async function fetchMyPapers(
  filter: MyPaperFilter,
  sort: MyPaperSort,
  page: number,
  search = '',
  category = 'all',
): Promise<MarkedPaperListResponse> {
  const params = new URLSearchParams({
    filter,
    sort,
    page: String(page),
    limit: '12',
  });
  if (search.trim()) {
    params.set('search', search.trim());
  }
  if (category && category !== 'all') {
    params.set('category', category);
  }
  return apiFetch<MarkedPaperListResponse>(`/me/papers?${params.toString()}`);
}

export async function fetchReadingOverview(days = 112): Promise<ReadingOverviewResponse> {
  const params = new URLSearchParams({ days: String(days) });
  return apiFetch<ReadingOverviewResponse>(`/me/reading-overview?${params.toString()}`);
}

export async function updatePaperMark(
  paperId: string,
  mark: Partial<PaperMark>,
): Promise<PaperMark> {
  const nextMark = await apiFetch<PaperMark>(paperMarkApiPath(paperId), {
    method: 'PUT',
    body: JSON.stringify(mark),
  });
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('paper:mark-changed', {
      detail: { paperId, mark: nextMark },
    }));
  }
  return nextMark;
}

export async function removePaperFromMine(paperId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(paperMarkApiPath(paperId), { method: 'DELETE' });
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('paper:mark-changed', {
      detail: { paperId, mark: null },
    }));
  }
}

function paperNoteApiPath(paperId: string): string {
  return `/papers/${encodeURIComponent(paperId)}/note`;
}

export async function updatePaperNote(
  paperId: string,
  note: string | null,
): Promise<PaperMark> {
  const nextMark = await apiFetch<PaperMark>(paperNoteApiPath(paperId), {
    method: 'PUT',
    body: JSON.stringify({ note }),
  });
  if (typeof window !== 'undefined') {
    // Deliberately not 'paper:mark-changed': that event triggers a reading
    // overview refresh, which has nothing to do with notes.
    window.dispatchEvent(new CustomEvent('paper:note-changed', {
      detail: { paperId, mark: nextMark },
    }));
  }
  return nextMark;
}

/** Record a qualifying detail-page open (clicked in + dwell). Refreshes
 * last_opened_at only; the manual viewed flag is untouched. */
export async function recordPaperOpened(paperId: string): Promise<PaperMark> {
  const nextMark = await apiFetch<PaperMark>(
    `/papers/${encodeURIComponent(paperId)}/opened`,
    { method: 'POST' },
  );
  if (typeof window !== 'undefined') {
    // Not 'paper:mark-changed' on purpose: the reading overview heatmap keys
    // off the manual viewed mark, and a passive open must not refresh it.
    window.dispatchEvent(new CustomEvent('paper:opened-changed', {
      detail: { paperId, mark: nextMark },
    }));
  }
  return nextMark;
}

export async function setPaperCategory(
  paperId: string,
  categoryId: string,
  action: 'assign' | 'unassign',
): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/papers/${encodeURIComponent(paperId)}/category`, {
    method: 'PUT',
    body: JSON.stringify({ category_id: categoryId, action }),
  });
}

export async function clearPaperCategories(paperId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/papers/${encodeURIComponent(paperId)}/categories`, {
    method: 'DELETE',
  });
}

export async function fetchPaperCategories(): Promise<PaperCategoryListResponse> {
  return apiFetch<PaperCategoryListResponse>('/me/paper-categories');
}

export async function createPaperCategory(
  name: string,
  parentId: string | null,
): Promise<PaperCategory> {
  const payload = await apiFetch<{ category: PaperCategory }>('/me/paper-categories', {
    method: 'POST',
    body: JSON.stringify({ name, parent_id: parentId }),
  });
  return payload.category;
}

export async function renamePaperCategory(categoryId: string, name: string): Promise<PaperCategory> {
  const payload = await apiFetch<{ category: PaperCategory }>(
    `/me/paper-categories/${encodeURIComponent(categoryId)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) },
  );
  return payload.category;
}

export async function movePaperCategory(
  categoryId: string,
  parentId: string | null,
): Promise<PaperCategory> {
  const payload = await apiFetch<{ category: PaperCategory }>(
    `/me/paper-categories/${encodeURIComponent(categoryId)}`,
    { method: 'PATCH', body: JSON.stringify({ parent_id: parentId }) },
  );
  return payload.category;
}

export async function setPaperCategoryColor(
  categoryId: string,
  color: string | null,
): Promise<PaperCategory> {
  const payload = await apiFetch<{ category: PaperCategory }>(
    `/me/paper-categories/${encodeURIComponent(categoryId)}`,
    { method: 'PATCH', body: JSON.stringify({ color }) },
  );
  return payload.category;
}

export interface AutoCategorizeResult {
  ok: boolean;
  assigned: PaperMarkCategoryRef[];
  skipped: number;
  message?: string;
}

export async function autoCategorizePaper(paperId: string): Promise<AutoCategorizeResult> {
  return apiFetch<AutoCategorizeResult>(
    `/papers/${encodeURIComponent(paperId)}/auto-categorize`,
    { method: 'POST' },
  );
}

export async function deletePaperCategory(categoryId: string): Promise<number> {
  const payload = await apiFetch<{ ok: boolean; deleted_subcategories: number }>(
    `/me/paper-categories/${encodeURIComponent(categoryId)}`,
    { method: 'DELETE' },
  );
  return payload.deleted_subcategories;
}

export async function startCategorizeJob(payload: {
  kind: CategorizeJobKind;
  description?: string;
  target_category_id?: string | null;
}): Promise<{ job_id: string }> {
  return apiFetch<{ job_id: string }>('/me/papers/categorize-jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchCategorizeJobs(): Promise<CategorizeJobListResponse> {
  return apiFetch<CategorizeJobListResponse>('/me/papers/categorize-jobs');
}

export async function dismissCategorizeJob(jobId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/me/papers/categorize-jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  });
}

export async function categorizeApplyMyPapers(
  payload: CategorizeApplyPayload,
): Promise<CategorizeApplyResponse> {
  return apiFetch<CategorizeApplyResponse>('/me/papers/categorize-apply', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAbstractZh(ids: string[]): Promise<Record<string, string | null>> {
  if (ids.length === 0) {
    return {};
  }
  const payload = await apiFetch<{ results: Record<string, string | null> }>(
    '/papers/abstract-zh',
    { method: 'POST', body: JSON.stringify({ ids }) },
  );
  return payload.results ?? {};
}

export async function fetchLibraryExportInfo(): Promise<LibraryExportInfo> {
  return apiFetch<LibraryExportInfo>('/me/library/export-info');
}

export async function exportLibrary(paperIds: string[] | null): Promise<unknown> {
  return apiFetch<unknown>('/me/library/export', {
    method: 'POST',
    body: JSON.stringify({ paper_ids: paperIds }),
  });
}

export async function previewLibraryImport(payload: unknown): Promise<LibraryImportPreview> {
  return apiFetch<LibraryImportPreview>('/me/library/import/preview', {
    method: 'POST',
    body: JSON.stringify({ payload }),
  });
}

export async function importLibrary(
  payload: unknown,
  paperIds: string[],
): Promise<LibraryImportResult> {
  return apiFetch<LibraryImportResult>('/me/library/import', {
    method: 'POST',
    body: JSON.stringify({ payload, paper_ids: paperIds }),
  });
}

const LIBRARY_EXPORT_FORMAT = 'paper-insight-lite.export';

export function isLibraryExportFile(payload: unknown): payload is Record<string, unknown> {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    (payload as Record<string, unknown>).format === LIBRARY_EXPORT_FORMAT
  );
}

export function downloadLibraryExport(payload: unknown, date = new Date()): void {
  const stamp = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('');
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `paper-library-${stamp}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function fetchAutoBackupSettings(): Promise<AutoBackupSettings> {
  return apiFetch<AutoBackupSettings>('/me/backup-settings');
}

export async function updateAutoBackupSettings(
  input: AutoBackupSettingsInput,
): Promise<AutoBackupSettings> {
  return apiFetch<AutoBackupSettings>('/me/backup-settings', {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export async function runAutoBackupNow(): Promise<AutoBackupRunResult> {
  return apiFetch<AutoBackupRunResult>('/me/backup/run', { method: 'POST' });
}

function dispatchEvent(block: string, handlers: StreamOptions): void {
  if (!block.trim()) {
    return;
  }

  let eventName = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message';
      continue;
    }

    if (line.startsWith('data:')) {
      const value = line.slice(5);
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value);
    }
  }

  const payload = dataLines.join('\n');
  if (eventName === 'message' && payload) {
    handlers.onChunk?.(payload);
  }
  handlers.onEvent?.(eventName, payload);
}

export async function streamSse(
  input: RequestInfo | URL,
  init: RequestInit,
  handlers: StreamOptions,
): Promise<void> {
  const request = typeof input === 'string' ? apiUrl(input) : input;
  const response = await fetch(request, { credentials: 'include', ...init });
  if (!response.ok) {
    throw new Error(response.statusText || 'Stream request failed');
  }

  if (!response.body) {
    throw new Error('Streaming is not supported in this browser');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n');
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      dispatchEvent(part, handlers);
    }
  }

  if (buffer.trim()) {
    dispatchEvent(buffer, handlers);
  }
}
