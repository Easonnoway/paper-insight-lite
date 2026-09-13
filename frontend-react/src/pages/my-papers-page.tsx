import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BookMarked, Loader2, Plus, Search } from 'lucide-react';
import { toast } from 'sonner';

import { ActiveModelBadge } from '@/components/active-model-badge';
import { CategoryTree } from '@/components/category-tree';
import { LibraryTransferDialog } from '@/components/library-transfer-dialog';
import { ManualPaperDialog } from '@/components/manual-paper-dialog';
import { ModelSettingsDialog } from '@/components/model-settings-dialog';
import { PaginationBar } from '@/components/pagination-bar';
import { PaperHistoryCard } from '@/components/paper-history-card';
import { ReadingOverviewPanel } from '@/components/reading-overview';
import { useReadingOverview } from '@/hooks/use-reading-overview';
import { usePaperCategories } from '@/hooks/use-paper-categories';
import {
  createArxivPaper,
  fetchAbstractZh,
  fetchMyPapers,
} from '@/lib/api';
import { extractArxivId } from '@/lib/arxiv';
import { buildQueryString, navigate, parsePage, useAppLocation } from '@/lib/router';
import type { MarkedPaperListResponse, MyPaperFilter, MyPaperSort, PaperMark } from '@/types';

const EMPTY_RESULTS: MarkedPaperListResponse = {
  items: [],
  total: 0,
  page: 1,
  pages: 1,
};

const FILTERS: Array<{ value: MyPaperFilter; label: string }> = [
  { value: 'all', label: '全部记录' },
  { value: 'viewed', label: '已看过' },
  { value: 'liked', label: '已点赞' },
  { value: 'favorited', label: '已收藏' },
];

const SORTS: Array<{ value: MyPaperSort; label: string }> = [
  { value: 'opened_at', label: '最近点击' },
  { value: 'viewed_at', label: '最近看过' },
  { value: 'liked_at', label: '最近点赞' },
  { value: 'favorited_first', label: '收藏优先' },
  { value: 'title', label: '标题 A-Z' },
];

function parseFilter(value: string | null): MyPaperFilter {
  return value === 'viewed' || value === 'liked' || value === 'favorited' ? value : 'all';
}

function parseSort(value: string | null): MyPaperSort {
  if (value === 'favorited_first' || value === 'liked_first') {
    return 'favorited_first';
  }
  if (value === 'liked_at' || value === 'opened_at' || value === 'title') {
    return value;
  }
  return 'viewed_at';
}

type ActiveCategory = 'all' | 'uncategorized' | string;

function parseCategory(value: string | null): ActiveCategory {
  const trimmed = (value ?? '').trim();
  return trimmed || 'all';
}

function updateQuery(next: { filter?: MyPaperFilter; sort?: MyPaperSort; page?: number; search?: string; category?: ActiveCategory }) {
  const params = new URLSearchParams(window.location.search);
  if (next.filter) {
    params.set('filter', next.filter);
    params.delete('page');
  }
  if (next.sort) {
    params.set('sort', next.sort);
    params.delete('page');
  }
  if (next.page) {
    params.set('page', String(next.page));
  }
  if (Object.prototype.hasOwnProperty.call(next, 'search')) {
    if (next.search && next.search.trim()) {
      params.set('search', next.search.trim());
    } else {
      params.delete('search');
    }
    params.delete('page');
  }
  if (Object.prototype.hasOwnProperty.call(next, 'category')) {
    if (next.category && next.category !== 'all') {
      params.set('cat', next.category);
    } else {
      params.delete('cat');
    }
    params.delete('page');
  }
  navigate(`/${buildQueryString(params)}`);
}

export function MyPapersPage() {
  const location = useAppLocation();
  const [results, setResults] = useState<MarkedPaperListResponse>(EMPTY_RESULTS);
  const [abstractZh, setAbstractZh] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [arxivInput, setArxivInput] = useState('');
  const [isAddingArxiv, setIsAddingArxiv] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [modelBadgeKey, setModelBadgeKey] = useState(0);
  const readingOverview = useReadingOverview();
  const categoryTree = usePaperCategories();

  const { filter, sort, page, search, activeCategoryId } = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return {
      filter: parseFilter(params.get('filter')),
      sort: parseSort(params.get('sort')),
      page: parsePage(params.get('page')),
      search: params.get('search') || '',
      activeCategoryId: parseCategory(params.get('cat')),
    };
  }, [location.search]);

  const [searchInput, setSearchInput] = useState(search);
  const searchInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // Sync from URL only when the user isn't actively typing (avoids clobbering
    // their in-progress input when the debounced value lands in the URL).
    if (document.activeElement !== searchInputRef.current) {
      setSearchInput(search);
    }
  }, [search]);

  useEffect(() => {
    const handler = window.setTimeout(() => {
      if (searchInput !== search) {
        updateQuery({ search: searchInput });
      }
    }, 400);
    return () => window.clearTimeout(handler);
  }, [searchInput, search]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = await fetchMyPapers(filter, sort, page, search, activeCategoryId);
      setResults(payload);
      const ids = payload.items.map((i) => i.paper.id);
      if (ids.length) {
        void fetchAbstractZh(ids)
          .then((map) => {
            setAbstractZh((prev) => {
              const next = { ...prev };
              for (const [id, zh] of Object.entries(map)) {
                if (zh) { next[id] = zh; }
              }
              return next;
            });
          })
          .catch(() => { /* best-effort; English abstract stays */ });
      }
    } catch (err) {
      setResults(EMPTY_RESULTS);
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setIsLoading(false);
    }
  }, [filter, page, sort, search, activeCategoryId]);

  useEffect(() => {
    void load();
  }, [load]);

  // In-place note updates (avoids a full list refresh flicker).
  useEffect(() => {
    const handleNoteChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ paperId: string; mark: PaperMark }>).detail;
      if (!detail) {
        return;
      }
      setResults((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.paper.id === detail.paperId ? { ...item, mark: detail.mark } : item,
        ),
      }));
    };
    window.addEventListener('paper:note-changed', handleNoteChanged);
    return () => window.removeEventListener('paper:note-changed', handleNoteChanged);
  }, []);

  const submitArxiv = async () => {
    const trimmed = arxivInput.trim();
    if (!trimmed || isAddingArxiv) {
      return;
    }
    const arxivId = extractArxivId(trimmed);
    if (!arxivId) {
      toast.error('请输入有效的 arXiv 链接或 ID（如 https://arxiv.org/abs/2401.01234）');
      return;
    }
    setIsAddingArxiv(true);
    try {
      const paper = await createArxivPaper(trimmed);
      setArxivInput('');
      navigate(`/papers/${encodeURIComponent(paper.id)}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'arXiv 论文加载失败');
    } finally {
      setIsAddingArxiv(false);
    }
  };

  return (
    <div className="mx-auto max-w-7xl animate-fade-in">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-2">
          <div>
            <div className="flex items-center gap-2">
              <BookMarked className="h-6 w-6 text-[#ff7a00]" />
              <h1 className="text-3xl font-semibold text-[#172033]">我的论文</h1>
            </div>
            <p className="mt-1 text-sm text-[#728095]">
              阅读、点赞和收藏记录，按分类整理。
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="rounded-full bg-white/80 px-4 py-2 text-sm text-[#586578] shadow-sm ring-1 ring-black/5">
            共 {results.total} 条记录
          </div>
          <ReadingOverviewPanel
            overview={readingOverview.overview}
            isLoading={readingOverview.isLoading}
            error={readingOverview.error}
            onRetry={() => void readingOverview.refresh()}
          />
          <LibraryTransferDialog
            onImported={() => {
              void load();
              void categoryTree.refresh();
              void readingOverview.refresh();
            }}
          />
          <ActiveModelBadge key={modelBadgeKey} compact />
          <ModelSettingsDialog onActiveChanged={() => setModelBadgeKey((v) => v + 1)} />
        </div>
      </div>

      <form
        className="mb-6 flex items-center gap-2 rounded-full border border-[#e6ebf2] bg-white px-5 py-3 shadow-sm ring-1 ring-black/5"
        onSubmit={(e) => { e.preventDefault(); void submitArxiv(); }}
      >
        <Plus className="h-4 w-4 shrink-0 text-[#ff7a00]" />
        <input
          value={arxivInput}
          onChange={(event) => setArxivInput(event.target.value)}
          placeholder="粘贴 arXiv 链接或 ID 添加论文（如 https://arxiv.org/abs/2401.01234）"
          className="min-w-0 flex-1 bg-transparent text-sm text-[#172033] outline-none placeholder:text-[#9aa6b8]"
        />
        <button
          type="submit"
          disabled={isAddingArxiv || !arxivInput.trim()}
          className="shrink-0 rounded-full bg-gradient-to-r from-[#ff9900] to-[#ff7a00] px-4 py-1.5 text-sm font-medium text-white shadow-[0_8px_20px_rgba(255,122,0,0.24)] transition hover:from-[#ff8a00] hover:to-[#ff6f00] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isAddingArxiv ? <Loader2 className="h-4 w-4 animate-spin" /> : '添加'}
        </button>
        <button
          type="button"
          onClick={() => setManualOpen(true)}
          className="shrink-0 rounded-full border border-[#e6ebf2] bg-white px-4 py-1.5 text-sm font-medium text-[#586578] transition hover:border-[#bfdbfe] hover:bg-[#eff6ff] hover:text-[#2563eb]"
          title="手动录入 arXiv 收录不了的论文"
        >
          手动添加
        </button>
      </form>

      <div className="grid gap-6 xl:grid-cols-[15rem_minmax(0,1fr)] xl:items-start">
      <aside className="min-w-0 xl:sticky xl:top-6 xl:col-start-1 xl:row-start-1 xl:row-span-2 xl:max-h-[calc(100dvh-3rem)] xl:self-start xl:overflow-y-auto xl:overscroll-contain">
        <CategoryTree
          hook={categoryTree}
          activeCategoryId={activeCategoryId}
          onSelectCategory={(categoryId) => updateQuery({ category: categoryId })}
          onAssignmentsChanged={() => { void load(); }}
        />
      </aside>

      <div className="min-w-0 xl:col-start-2 xl:row-start-1 xl:row-span-2">

      <section className="rounded-[28px] bg-white/80 p-4 shadow-sm ring-1 ring-black/5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {FILTERS.map((item) => (
              <button
                key={item.value}
                type="button"
                className={`rounded-full px-4 py-1.5 text-sm transition ${
                  filter === item.value
                    ? 'bg-[#172033] text-white'
                    : 'border border-[#e6ebf2] bg-white text-[#334155] hover:bg-[#f8fafc]'
                }`}
                onClick={() => updateQuery({ filter: item.value })}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {SORTS.map((item) => (
              <button
                key={item.value}
                type="button"
                className={`rounded-full px-4 py-1.5 text-sm transition ${
                  sort === item.value
                    ? 'bg-[#172033] text-white'
                    : 'border border-[#e6ebf2] bg-white text-[#334155] hover:bg-[#f8fafc]'
                }`}
                onClick={() => updateQuery({ sort: item.value })}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <div className="mt-3 flex items-center gap-2 rounded-full border border-[#e6ebf2] bg-white px-4 py-2 shadow-sm ring-1 ring-black/5">
        <Search className="h-4 w-4 shrink-0 text-[#94a3b8]" />
        <input
          ref={searchInputRef}
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="搜索我的论文（标题 / 摘要 / 关键词）"
          className="min-w-0 flex-1 bg-transparent text-sm text-[#172033] outline-none placeholder:text-[#9aa6b8]"
        />
        {searchInput ? (
          <button
            type="button"
            onClick={() => setSearchInput('')}
            className="shrink-0 rounded-full px-2 text-xs text-[#94a3b8] hover:text-[#475569]"
          >
            清除
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="mt-6 rounded-[28px] bg-white p-6 text-[#b91c1c] shadow-sm ring-1 ring-black/5">
          {error}
        </div>
      ) : null}

      {isLoading ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-[28px] bg-white p-8 text-[#728095] shadow-sm ring-1 ring-black/5">
          <Loader2 className="h-5 w-5 animate-spin" />
          加载我的论文...
        </div>
      ) : results.items.length === 0 ? (
        <div className="mt-6 rounded-[28px] bg-white p-8 text-center text-[#728095] shadow-sm ring-1 ring-black/5">
          暂无记录。在上方粘贴 arXiv 链接添加论文，打开详情后即会记录。
        </div>
      ) : (
        <div className="mt-6 space-y-4">
          {results.items.map((item) => (
            <PaperHistoryCard
              key={item.paper.id}
              item={item}
              abstractZh={abstractZh[item.paper.id]}
              onRemoved={() => { void load(); }}
              categories={categoryTree.categories}
              onCategoriesChanged={() => {
                void load();
                void categoryTree.refresh();
              }}
              onNoteChanged={(paperId, nextMark) => {
                setResults((prev) => ({
                  ...prev,
                  items: prev.items.map((item) =>
                    item.paper.id === paperId ? { ...item, mark: nextMark } : item,
                  ),
                }));
              }}
            />
          ))}
        </div>
      )}
      <PaginationBar page={results.page} pages={results.pages} onPageChange={(nextPage) => updateQuery({ page: nextPage })} />
      </div>
      </div>

      <ManualPaperDialog
        open={manualOpen}
        onOpenChange={setManualOpen}
        onAdded={(paper) => {
          setManualOpen(false);
          navigate(`/papers/${encodeURIComponent(paper.id)}`);
        }}
      />
    </div>
  );
}
