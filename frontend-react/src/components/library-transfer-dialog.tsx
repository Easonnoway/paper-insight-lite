import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ArrowDownUp,
  Check,
  Download,
  FileJson,
  Loader2,
  Minus,
  Search,
  Upload,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { AutoBackupTab } from '@/components/auto-backup-tab';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  downloadLibraryExport,
  exportLibrary,
  fetchLibraryExportInfo,
  importLibrary,
  isLibraryExportFile,
  previewLibraryImport,
} from '@/lib/api';
import {
  buildCategoryTree,
  categorySelectionState,
  flattenCategoryTree,
  planCategoryToggle,
  subtreeCategoryIds,
} from '@/lib/category-tree-utils';
import type {
  ExportCandidateCategory,
  ExportCandidatePaper,
  LibraryImportPreview,
} from '@/types';

const MAX_IMPORT_FILE_BYTES = 64 * 1024 * 1024;

interface LibraryTransferDialogProps {
  /** Refresh lists/categories/heatmap after a successful import. */
  onImported?: () => void;
  className?: string;
}

export function LibraryTransferDialog({
  onImported,
  className = '',
}: LibraryTransferDialogProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'export' | 'import' | 'backup'>('export');

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setActiveTab('export');
        }
      }}
    >
      <DialogTrigger asChild>
        <button
          type="button"
          title="分享 / 导入论文库"
          aria-label="分享 / 导入论文库"
          className={`inline-flex h-9 w-9 items-center justify-center rounded-full border border-[#e6ebf2] bg-white/80 text-[#64748b] shadow-sm backdrop-blur transition hover:border-[#bfdbfe] hover:bg-[#eff6ff] hover:text-[#2563eb] ${className}`}
        >
          <ArrowDownUp className="h-4 w-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="grid-cols-[minmax(0,1fr)] max-h-[85vh] overflow-x-hidden overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ArrowDownUp className="h-4 w-4 shrink-0 text-[#2563eb]" />
            分享论文库
          </DialogTitle>
          <DialogDescription>
            导出自己的论文与分类成文件分享给他人，或导入别人分享的文件。
          </DialogDescription>
        </DialogHeader>
        <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as 'export' | 'import' | 'backup')}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="export">导出</TabsTrigger>
            <TabsTrigger value="import">导入</TabsTrigger>
            <TabsTrigger value="backup">自动备份</TabsTrigger>
          </TabsList>
          <TabsContent value="export" className="min-w-0">
            <ExportTab open={open} />
          </TabsContent>
          <TabsContent value="import" className="min-w-0">
            <ImportTab open={open} onImported={onImported} />
          </TabsContent>
          <TabsContent value="backup" className="min-w-0">
            <AutoBackupTab open={open} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function ExportTab({ open }: { open: boolean }) {
  const [papers, setPapers] = useState<ExportCandidatePaper[]>([]);
  const [categories, setCategories] = useState<ExportCandidateCategory[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const loadedRef = useRef(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const info = await fetchLibraryExportInfo();
      setPapers(info.papers);
      setCategories(info.categories);
      // Default to nothing selected — the user picks what to export.
      setSelectedIds(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  if (open && !loadedRef.current && !isLoading && !error) {
    loadedRef.current = true;
    void load();
  }
  if (!open && loadedRef.current) {
    loadedRef.current = false;
  }

  const filteredPapers = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) {
      return papers;
    }
    return papers.filter((paper) =>
      (paper.title ?? '').toLowerCase().includes(term) ||
      paper.id.toLowerCase().includes(term),
    );
  }, [papers, search]);

  // Hooks must precede the early returns below (isLoading / error / empty).
  const uncategorizedIds = useMemo(
    () => papers.filter((paper) => paper.category_ids.length === 0).map((paper) => paper.id),
    [papers],
  );

  const togglePaper = (paperId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(paperId)) {
        next.delete(paperId);
      } else {
        next.add(paperId);
      }
      return next;
    });
  };

  const submitExport = async () => {
    const ids = papers
      .filter((paper) => selectedIds.has(paper.id))
      .map((paper) => paper.id);
    if (ids.length === 0) {
      toast.error('请至少选择一篇论文');
      return;
    }
    setIsExporting(true);
    try {
      const payload = await exportLibrary(ids);
      downloadLibraryExport(payload);
      toast.success(`已导出 ${ids.length} 篇论文`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '导出失败');
    } finally {
      setIsExporting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-sm text-[#728095]">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载论文列表...
      </div>
    );
  }
  if (error) {
    return (
      <div className="py-6 text-center text-sm text-[#b91c1c]">
        {error}
        <div className="mt-2">
          <Button size="sm" variant="outline" onClick={() => { loadedRef.current = false; void load(); }}>
            重试
          </Button>
        </div>
      </div>
    );
  }
  if (papers.length === 0) {
    return (
      <p className="rounded-2xl bg-[#f8fafc] px-4 py-8 text-center text-sm text-[#728095]">
        论文库还是空的，添加论文后就能导出分享。
      </p>
    );
  }

  const categoryRows = flattenCategoryTree(buildCategoryTree(categories));

  const toggleIdList = (ids: string[]) => {
    const plan = planCategoryToggle(ids, selectedIds);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of plan.remove) {
        next.delete(id);
      }
      for (const id of plan.add) {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="space-y-3">
      {categoryRows.length > 0 || uncategorizedIds.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {categoryRows.map((row) => {
            const paperIdsInSubtree = papers
              .filter((paper) =>
                subtreeCategoryIds(row.node).some((cid) => paper.category_ids.includes(cid)),
              )
              .map((paper) => paper.id);
            const selectionState = categorySelectionState(paperIdsInSubtree, selectedIds);
            const chipClass =
              selectionState === 'all'
                ? 'border-[#93c5fd] bg-[#eff6ff] font-medium text-[#1d4ed8]'
                : selectionState === 'partial'
                  ? 'border-[#bfdbfe] bg-white text-[#1d4ed8]'
                  : 'border-[#e6ebf2] bg-white text-[#516072] hover:border-[#93c5fd] hover:text-[#1d4ed8]';
            return (
              <button
                key={row.node.id}
                type="button"
                title={`点击全选「${row.pathLabel}」下 ${paperIdsInSubtree.length} 篇论文，再点全部取消`}
                className={`rounded-full border px-2.5 py-1 text-xs transition ${chipClass}`}
                onClick={() => toggleIdList(paperIdsInSubtree)}
              >
                {selectionState === 'all' ? <Check className="mr-1 inline h-3 w-3" /> : null}
                {selectionState === 'partial' ? <Minus className="mr-1 inline h-3 w-3" /> : null}
                {row.pathLabel} ({paperIdsInSubtree.length})
              </button>
            );
          })}
          {uncategorizedIds.length > 0 ? (() => {
            const selectionState = categorySelectionState(uncategorizedIds, selectedIds);
            const chipClass =
              selectionState === 'all'
                ? 'border-[#93c5fd] bg-[#eff6ff] font-medium text-[#1d4ed8]'
                : selectionState === 'partial'
                  ? 'border-[#bfdbfe] bg-white text-[#1d4ed8]'
                  : 'border-[#e6ebf2] border-dashed bg-white text-[#516072] hover:border-[#93c5fd] hover:text-[#1d4ed8]';
            return (
              <button
                type="button"
                title={`点击全选未分类的 ${uncategorizedIds.length} 篇论文，再点全部取消`}
                className={`rounded-full border px-2.5 py-1 text-xs transition ${chipClass}`}
                onClick={() => toggleIdList(uncategorizedIds)}
              >
                {selectionState === 'all' ? <Check className="mr-1 inline h-3 w-3" /> : null}
                {selectionState === 'partial' ? <Minus className="mr-1 inline h-3 w-3" /> : null}
                未分类 ({uncategorizedIds.length})
              </button>
            );
          })() : null}
        </div>
      ) : null}

      <div className="flex items-center gap-2 rounded-full border border-[#e6ebf2] bg-white px-3 py-1.5">
        <Search className="h-3.5 w-3.5 shrink-0 text-[#94a3b8]" />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索标题或 ID"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[#9aa6b8]"
        />
      </div>

      <div className="max-h-72 space-y-1 overflow-y-auto rounded-2xl border border-[#e6ebf2] bg-white p-2">
        {filteredPapers.map((paper) => (
          <label
            key={paper.id}
            className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-xl px-2 py-1.5 transition hover:bg-[#f8fafc]"
          >
            <Checkbox
              checked={selectedIds.has(paper.id)}
              onCheckedChange={() => togglePaper(paper.id)}
            />
            <span className="min-w-0 flex-1 truncate text-sm text-[#172033]" title={paper.title ?? paper.id}>
              {paper.title || paper.id}
            </span>
            {paper.has_ai ? (
              <span className="shrink-0 rounded-full bg-[#fff7ed] px-1.5 py-0.5 text-[10px] text-[#c2410c]">
                AI
              </span>
            ) : null}
          </label>
        ))}
        {filteredPapers.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-[#94a3b8]">没有匹配的论文</p>
        ) : null}
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-[#586578]">
          已选 <span className="font-semibold text-[#172033]">{selectedIds.size}</span> / {papers.length} 篇
        </span>
        <Button
          size="sm"
          className="rounded-full"
          disabled={isExporting || selectedIds.size === 0}
          onClick={() => { void submitExport(); }}
        >
          {isExporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          导出文件
        </Button>
      </div>
    </div>
  );
}

interface ImportTabProps {
  open: boolean;
  onImported?: () => void;
}

function ImportTab({ open, onImported }: ImportTabProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [parsedPayload, setParsedPayload] = useState<Record<string, unknown> | null>(null);
  const [preview, setPreview] = useState<LibraryImportPreview | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isParsing, setIsParsing] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const resetState = () => {
    setFileName(null);
    setParsedPayload(null);
    setPreview(null);
    setSelectedIds(new Set());
  };

  const handleFile = async (file: File) => {
    if (file.size > MAX_IMPORT_FILE_BYTES) {
      toast.error('文件超过 64MB 上限');
      return;
    }
    setIsParsing(true);
    try {
      const text = await file.text();
      const parsed: unknown = JSON.parse(text);
      if (!isLibraryExportFile(parsed)) {
        toast.error('不是 Paper Insight Lite 导出文件');
        return;
      }
      const result = await previewLibraryImport(parsed);
      if (result.papers.length === 0) {
        toast.error('文件里没有论文');
        return;
      }
      setFileName(file.name);
      setParsedPayload(parsed);
      setPreview(result);
      setSelectedIds(new Set(result.papers.map((paper) => paper.id)));
    } catch (err) {
      if (err instanceof SyntaxError) {
        toast.error('文件不是有效的 JSON');
      } else {
        toast.error(err instanceof Error ? err.message : '读取文件失败');
      }
    } finally {
      setIsParsing(false);
    }
  };

  const submitImport = async () => {
    if (!parsedPayload || selectedIds.size === 0) {
      return;
    }
    setIsImporting(true);
    try {
      const result = await importLibrary(parsedPayload, [...selectedIds]);
      toast.success(
        `导入完成：新增 ${result.created_papers} 篇，合并 ${result.merged_papers} 篇` +
        (result.created_categories > 0 ? `，新建分类 ${result.created_categories} 个` : ''),
      );
      resetState();
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      onImported?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '导入失败');
    } finally {
      setIsImporting(false);
    }
  };

  if (!open) {
    return null;
  }

  return (
    <div className="space-y-3">
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            void handleFile(file);
          }
        }}
      />

      {!preview ? (
        <button
          type="button"
          disabled={isParsing}
          className="flex w-full flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-[#e6ebf2] bg-white px-4 py-10 text-center transition hover:border-[#93c5fd] hover:bg-[#f8fbff] disabled:opacity-60"
          onClick={() => fileInputRef.current?.click()}
        >
          {isParsing ? (
            <Loader2 className="h-6 w-6 animate-spin text-[#2563eb]" />
          ) : (
            <FileJson className="h-6 w-6 text-[#94a3b8]" />
          )}
          <span className="text-sm text-[#516072]">
            {isParsing ? '解析文件...' : '点击选择导出文件（.json）'}
          </span>
          <span className="text-xs text-[#9aa6b8]">导入前会先预览，可勾选部分论文导入</span>
        </button>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2 rounded-2xl bg-[#f8fafc] px-3 py-2">
            <span className="min-w-0 truncate text-xs text-[#586578]" title={fileName ?? undefined}>
              {fileName}
            </span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 shrink-0 rounded-full px-2 text-xs text-[#64748b]"
              onClick={() => {
                resetState();
                if (fileInputRef.current) {
                  fileInputRef.current.value = '';
                }
              }}
            >
              重选文件
            </Button>
          </div>

          <div className="flex flex-wrap gap-2 text-xs">
            <Badge className="border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]">
              新增 {preview.new_count} 篇
            </Badge>
            <Badge className="border-[#fed7aa] bg-[#fff7ed] text-[#c2410c]">
              合并 {preview.merge_count} 篇
            </Badge>
            {preview.new_category_count > 0 ? (
              <Badge variant="outline">新建分类 {preview.new_category_count} 个</Badge>
            ) : null}
            {preview.reused_category_count > 0 ? (
              <Badge variant="outline">复用分类 {preview.reused_category_count} 个</Badge>
            ) : null}
          </div>

          {preview.categories.length > 0 ? (
            <div className="space-y-1 rounded-2xl border border-[#e6ebf2] bg-white p-2.5">
              {preview.categories.map((category) => (
                <div key={category.path} className="flex min-w-0 items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 flex-1 truncate text-[#334155]" title={category.path}>
                    {category.path}
                  </span>
                  <span className={`shrink-0 ${category.will_merge ? 'text-[#c2410c]' : 'text-[#15803d]'}`}>
                    {category.will_merge ? `并入 ${category.existing_name ?? '已有分类'}` : '新建'}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="max-h-60 space-y-1 overflow-y-auto rounded-2xl border border-[#e6ebf2] bg-white p-2">
            {preview.papers.map((paper) => (
              <label
                key={paper.id}
                className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-xl px-2 py-1.5 transition hover:bg-[#f8fafc]"
              >
                <Checkbox
                  checked={selectedIds.has(paper.id)}
                  onCheckedChange={() => {
                    setSelectedIds((prev) => {
                      const next = new Set(prev);
                      if (next.has(paper.id)) {
                        next.delete(paper.id);
                      } else {
                        next.add(paper.id);
                      }
                      return next;
                    });
                  }}
                />
                <span
                  className="min-w-0 flex-1 truncate text-sm text-[#172033]"
                  title={paper.title ?? paper.id}
                >
                  {paper.title || paper.id}
                </span>
                <span
                  className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${
                    paper.is_new
                      ? 'bg-[#f0fdf4] text-[#15803d]'
                      : 'bg-[#fff7ed] text-[#c2410c]'
                  }`}
                >
                  {paper.is_new ? '新增' : '合并'}
                </span>
              </label>
            ))}
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-[#8a96a8]">已有论文的阅读标记不会被改动</span>
            <Button
              size="sm"
              className="rounded-full"
              disabled={isImporting || selectedIds.size === 0}
              onClick={() => { void submitImport(); }}
            >
              {isImporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              导入 {selectedIds.size} 篇
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
