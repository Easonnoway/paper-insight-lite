export interface Paper {
  id: string;
  title: string;
  conference?: string;
  conferenceType?: 'Oral' | 'Poster' | 'Spotlight' | '';
  keywords: string[];
  abstract: string;
  venue?: string | null;
  primary_area?: string | null;
  authors?: string[];
  pdf?: string | null;
  llm_response?: string | null;
  created_at?: string;
  /** arXiv publish date (arxiv_papers.published_at); null for OpenReview papers. */
  published_at?: string | null;
  /** User-entered publication year (papers.published_year); arXiv papers use published_at. */
  published_year?: number | null;
  sort_order?: number | null;
  code_status?: PaperCodeStatus | null;
  code_url?: string | null;
  code_evidence?: string | null;
  code_checked_at?: string | null;
  hf_daily?: HfDailyPaperMeta | null;
  arxiv?: ArxivPaperMeta | null;
  openReviewUrl?: string;
  pdfUrl?: string;
  hasSeen?: boolean;
  isLiked?: boolean;
  isFavorited?: boolean;
}

export type PaperCodeStatus = 'open_source' | 'unavailable' | 'not_found' | 'unknown';

export interface HfDailyPaperMeta {
  daily_date?: string | null;
  rank?: number | null;
  upvotes?: number | null;
  thumbnail?: string | null;
  discussion_id?: string | null;
  project_page?: string | null;
  github_repo?: string | null;
  github_stars?: number | null;
  num_comments?: number | null;
}

export interface ArxivPaperMeta {
  arxiv_id?: string | null;
  arxiv_url?: string | null;
  pdf_url?: string | null;
  published_at?: string | null;
  updated_at?: string | null;
  added_at?: string | null;
  added_by_user_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ChatMessage {
  id?: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp?: Date;
  created_at?: string;
}

export interface SearchFilters {
  title: boolean;
  abstract: boolean;
  keywords: boolean;
}

export type PaperReadFilter = 'all' | 'unread' | 'read';

export type PaperCodeFilter = 'all' | 'open_source' | 'not_open_source';

export interface PaperReadCounts {
  all: number;
  unread: number;
  read: number;
}

export interface PaperListResponse {
  papers: Paper[];
  total: number;
  page: number;
  pages: number;
  read_counts?: PaperReadCounts | null;
}

export interface ChatSessionSummary {
  id: string;
  user_id?: string;
  paper_id?: string;
  title: string | null;
  created_at: string;
}

export interface ActiveLlmModel {
  configured: boolean;
  provider_key?: string | null;
  provider_name?: string | null;
  model_name?: string | null;
}

export interface PaperMarkCategoryRef {
  id: string;
  name: string;
}

export interface PaperMark {
  viewed: boolean;
  liked: boolean;
  favorited: boolean;
  categories?: PaperMarkCategoryRef[];
  note?: string | null;
  first_viewed_at?: string | null;
  viewed_at?: string | null;
  liked_at?: string | null;
  favorited_at?: string | null;
  /** Last qualifying detail-page open (clicked in + 10s dwell); independent
   * of the manual "viewed" mark. */
  last_opened_at?: string | null;
  updated_at?: string | null;
}

export type MyPaperFilter = 'all' | 'viewed' | 'liked' | 'favorited';
export type MyPaperSort = 'viewed_at' | 'liked_at' | 'favorited_first' | 'opened_at' | 'title';

export interface PaperCategory {
  id: string;
  parent_id: string | null;
  name: string;
  position: number;
  paper_count: number;
  color?: string | null;
  created_at?: string;
}

export interface PaperCategoryListResponse {
  categories: PaperCategory[];
  uncategorized_count: number;
}

export type CategorizeJobKind = 'suggest' | 'auto_uncategorized' | 'auto_full';

export interface CategorizeSuggestedPaper {
  paper_id: string;
  title: string;
  existing_categories: PaperMarkCategoryRef[];
}

export interface CategorizeSuggestion {
  category_name: string | null;
  parent_id: string | null;
  reuse_category_id: string | null;
  matched: CategorizeSuggestedPaper[];
  total: number;
}

export interface CategorizeApplyPayload {
  category_name: string;
  parent_id: string | null;
  reuse_category_id: string | null;
  paper_ids: string[];
}

export interface CategorizeApplyResponse {
  ok: boolean;
  category: PaperCategory;
  assigned: number;
}

export interface CategorizeAutoResult {
  updated: number;
  total: number;
  created_categories: string[];
}

export interface CategorizeJob {
  id: string;
  kind: CategorizeJobKind;
  status: 'running' | 'done' | 'error';
  description: string | null;
  target_category_name: string | null;
  reasoning_tail: string;
  suggestion: CategorizeSuggestion | null;
  auto_result: CategorizeAutoResult | null;
  error: string | null;
  created_at: string;
}

export interface CategorizeJobListResponse {
  jobs: CategorizeJob[];
}

export interface MarkedPaperItem {
  paper: Paper;
  mark: PaperMark;
}

export interface MarkedPaperListResponse {
  items: MarkedPaperItem[];
  total: number;
  page: number;
  pages: number;
}

export interface ReadingActivityDay {
  date: string;
  count: number;
}

export interface ReadingActivitySummary {
  days: ReadingActivityDay[];
  today_count: number;
  month_count: number;
  current_streak: number;
}

export interface HfDailyReadingItem {
  paper_id: string;
  title: string;
  rank: number;
  viewed: boolean;
}

export interface ReadingOverviewResponse {
  timezone: string;
  activity: ReadingActivitySummary;
}

export interface AuthUser {
  id: string;
  email: string;
  role: 'user' | 'admin';
  is_active: boolean;
  email_verified: boolean;
  created_at?: string;
  last_login_at?: string | null;
}

export interface AuthResponse {
  user: AuthUser;
}

export interface ExportCandidatePaper {
  id: string;
  title: string | null;
  venue?: string | null;
  has_ai: boolean;
  category_ids: string[];
}

export interface ExportCandidateCategory {
  id: string;
  parent_id: string | null;
  name: string;
  position: number;
}

export interface LibraryExportInfo {
  papers: ExportCandidatePaper[];
  categories: ExportCandidateCategory[];
}

export interface ImportPreviewPaper {
  id: string;
  title: string | null;
  is_new: boolean;
  will_fill_ai: boolean;
  will_fill_abstract_zh: boolean;
}

export interface ImportPreviewCategory {
  path: string;
  will_merge: boolean;
  existing_name?: string | null;
}

export interface LibraryImportPreview {
  exported_at: string | null;
  papers: ImportPreviewPaper[];
  new_count: number;
  merge_count: number;
  categories: ImportPreviewCategory[];
  new_category_count: number;
  reused_category_count: number;
}

export interface LibraryImportResult {
  created_papers: number;
  merged_papers: number;
  created_categories: number;
  reused_categories: number;
  assignments_added: number;
}

export type BackupIntervalKind = 'daily' | 'every_3_days' | 'weekly';

export interface AutoBackupSettings {
  enabled: boolean;
  directory: string;
  interval_kind: BackupIntervalKind;
  last_backup_at: string | null;
  last_backup_status: 'success' | 'failed' | null;
  last_backup_error: string | null;
  /** Server-computed; the client never derives it. */
  next_backup_at: string | null;
}

export interface AutoBackupSettingsInput {
  enabled: boolean;
  directory: string;
  interval_kind: BackupIntervalKind;
}

export interface AutoBackupRunResult {
  file_name: string;
  file_path: string;
  pruned: string[];
  last_backup_at: string;
}
