import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderPlus,
  Inbox,
  Layers,
  Loader2,
  MessageSquareText,
  MoreHorizontal,
  Palette,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  categorizeApplyMyPapers,
  dismissCategorizeJob,
  fetchCategorizeJobs,
  startCategorizeJob,
} from '@/lib/api';
import type { CategorizeJob, CategorizeJobKind } from '@/types';
import {
  buildCategoryTree,
  canMoveCategory,
  flattenCategoryTree,
  type CategoryTreeNode,
} from '@/lib/category-tree-utils';
import {
  CATEGORY_COLOR_KEYS,
  CATEGORY_COLOR_LABELS,
  CATEGORY_COLOR_STYLES,
  getCategoryColorStyle,
} from '@/lib/category-colors';
import type { usePaperCategories } from '@/hooks/use-paper-categories';

const COLLAPSED_STORAGE_KEY = 'category-tree-collapsed';
const PAPER_DRAG_TYPE = 'application/x-paper-id';
const CATEGORY_DRAG_TYPE = 'application/x-category-id';
const JOB_POLL_INTERVAL_MS = 1500;

const JOB_KIND_LABELS: Record<CategorizeJobKind, string> = {
  suggest: '按描述归类',
  auto_uncategorized: '归未分类论文',
  auto_full: '全量重分',
};

type ActiveCategory = 'all' | 'uncategorized' | string;

interface CategoryTreeProps {
  hook: ReturnType<typeof usePaperCategories>;
  activeCategoryId: ActiveCategory;
  onSelectCategory: (categoryId: ActiveCategory) => void;
  /** Notify the parent to reload the paper list (counts may have changed). */
  onAssignmentsChanged: () => void;
}

/** Expanded state is derived: everything except the persisted collapsed set.
 * That way new nodes start expanded without extra bookkeeping. */
function loadCollapsedState(): Set<string> {
  try {
    const stored = window.localStorage.getItem(COLLAPSED_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) {
        return new Set(parsed.filter((id): id is string => typeof id === 'string'));
      }
    }
  } catch {
    // corrupted storage: fall through to default (all expanded)
  }
  return new Set();
}

function persistCollapsedState(collapsed: Set<string>): void {
  try {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify(Array.from(collapsed)));
  } catch {
    // best-effort persistence
  }
}

export function CategoryTree({ hook, activeCategoryId, onSelectCategory, onAssignmentsChanged }: CategoryTreeProps) {
  const { categories, uncategorizedCount, isLoading, createCategory, renameCategory, moveCategory, setCategoryColor, removeCategory } = hook;
  const [collapsed, setCollapsed] = useState<Set<string>>(() => loadCollapsedState());
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  /** What kind of payload hovers over dropTargetId — drives the highlight color. */
  const [dropTargetKind, setDropTargetKind] = useState<'paper' | 'category' | null>(null);
  const [aiMenuOpen, setAiMenuOpen] = useState(false);

  // Dialog state: { mode, categoryId?, name? }
  const [nameDialog, setNameDialog] = useState<{ mode: 'create' | 'rename'; categoryId?: string; initialName: string } | null>(null);
  const [nameInput, setNameInput] = useState('');
  const [moveDialog, setMoveDialog] = useState<{ categoryId: string; name: string } | null>(null);
  const [moveTarget, setMoveTarget] = useState<string>('');
  const [deleteDialog, setDeleteDialog] = useState<{ categoryId: string; name: string; subcategories: number } | null>(null);

  const tree = useMemo(() => buildCategoryTree(categories), [categories]);

  const toggleExpanded = useCallback((nodeId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      persistCollapsedState(next);
      return next;
    });
  }, []);

  const openCreateDialog = useCallback((parentId: string | null) => {
    setNameDialog({ mode: 'create', categoryId: parentId ?? undefined, initialName: '' });
    setNameInput('');
  }, []);

  const openRenameDialog = useCallback((node: CategoryTreeNode) => {
    setNameDialog({ mode: 'rename', categoryId: node.id, initialName: node.name });
    setNameInput(node.name);
  }, []);

  const submitNameDialog = useCallback(async () => {
    if (!nameDialog) {
      return;
    }
    const name = nameInput.trim();
    if (!name) {
      toast.error('分类名不能为空');
      return;
    }
    const isCreate = nameDialog.mode === 'create';
    const result = isCreate
      ? await createCategory(name, nameDialog.categoryId ?? null)
      : await renameCategory(nameDialog.categoryId!, name);
    if (result) {
      toast.success(isCreate ? `已创建「${result.name}」` : `已重命名为「${result.name}」`);
      if (isCreate && result.parent_id) {
        setCollapsed((prev) => {
          const next = new Set(prev);
          next.delete(result.parent_id!);
          persistCollapsedState(next);
          return next;
        });
      }
      setNameDialog(null);
    }
  }, [createCategory, nameDialog, nameInput, renameCategory]);

  const submitMoveDialog = useCallback(async () => {
    if (!moveDialog) {
      return;
    }
    const result = await moveCategory(moveDialog.categoryId, moveTarget || null);
    if (result) {
      toast.success(`「${result.name}」已移动`);
      setMoveDialog(null);
    }
  }, [moveCategory, moveDialog, moveTarget]);

  const submitDeleteDialog = useCallback(async () => {
    if (!deleteDialog) {
      return;
    }
    const deleted = await removeCategory(deleteDialog.categoryId);
    if (deleted !== null) {
      toast.success(`已删除「${deleteDialog.name}」${deleted > 0 ? `（含 ${deleted} 个子分类）` : ''}`);
      if (activeCategoryId === deleteDialog.categoryId) {
        onSelectCategory('all');
      }
      onAssignmentsChanged();
      setDeleteDialog(null);
    }
  }, [activeCategoryId, deleteDialog, onAssignmentsChanged, onSelectCategory, removeCategory]);

  // ---- Background AI categorization jobs ----
  // Dialog shows aiFlow (input view) or the activeJob (thinking/preview/result).
  // Closing the dialog only drops activeJobId — jobs keep running server-side.
  const [jobs, setJobs] = useState<CategorizeJob[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  /** Input view: target mode pins the suggestion to one category node. */
  const [aiFlow, setAiFlow] = useState<{ mode: 'free' | 'target'; targetCategoryId?: string; targetCategoryName?: string } | null>(null);
  const [descriptionInput, setDescriptionInput] = useState('');
  const [isStartingJob, setIsStartingJob] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [previewName, setPreviewName] = useState('');
  const [previewParentId, setPreviewParentId] = useState<string>('');
  const [selectedPaperIds, setSelectedPaperIds] = useState<Set<string>>(new Set());
  const handledJobIds = useRef<Set<string>>(new Set());
  const reasoningBoxRef = useRef<HTMLDivElement | null>(null);

  const activeJob = useMemo(
    () => jobs.find((job) => job.id === activeJobId) ?? null,
    [jobs, activeJobId],
  );
  const hasRunningJob = jobs.some((job) => job.status === 'running');
  const hasPendingSuggestion = jobs.some((job) => job.status === 'done' && job.kind === 'suggest');

  const openAiDescribeDialog = useCallback((targetNode?: CategoryTreeNode) => {
    setAiMenuOpen(false);
    setDescriptionInput('');
    setActiveJobId(null);
    setAiFlow(targetNode
      ? { mode: 'target', targetCategoryId: targetNode.id, targetCategoryName: targetNode.name }
      : { mode: 'free' });
  }, []);

  const closeAiDialog = useCallback(() => {
    setAiFlow(null);
    setActiveJobId(null);
    setDescriptionInput('');
    setSelectedPaperIds(new Set());
    setIsStartingJob(false);
    setIsApplying(false);
  }, []);

  /** Enter the preview view for a done suggest job (from polling or the menu). */
  const showSuggestion = useCallback((job: CategorizeJob) => {
    if (!job.suggestion) {
      return;
    }
    setAiFlow(null);
    setPreviewName(job.suggestion.category_name ?? '');
    setPreviewParentId(job.suggestion.parent_id ?? '');
    setSelectedPaperIds(new Set(job.suggestion.matched.map((item) => item.paper_id)));
    setActiveJobId(job.id);
  }, []);

  const startJob = useCallback(async (kind: CategorizeJobKind, description?: string, targetCategoryId?: string) => {
    setIsStartingJob(true);
    try {
      const { job_id: jobId } = await startCategorizeJob({
        kind,
        description,
        target_category_id: targetCategoryId ?? null,
      });
      setAiFlow(null);
      setDescriptionInput('');
      setActiveJobId(jobId);
      // Optimistic seed so the dialog renders the thinking view immediately.
      setJobs((prev) => [
        {
          id: jobId,
          kind,
          status: 'running',
          description: description ?? null,
          target_category_name: null,
          reasoning_tail: '',
          suggestion: null,
          auto_result: null,
          error: null,
          created_at: new Date().toISOString(),
        },
        ...prev.filter((job) => job.id !== jobId),
      ]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '任务创建失败');
    } finally {
      setIsStartingJob(false);
    }
  }, []);

  const submitAiDescription = useCallback(async () => {
    if (!aiFlow) {
      return;
    }
    const description = descriptionInput.trim();
    if (!description) {
      toast.error('请先描述想找的论文主题');
      return;
    }
    await startJob(
      'suggest',
      description,
      aiFlow.mode === 'target' ? aiFlow.targetCategoryId : undefined,
    );
  }, [aiFlow, descriptionInput, startJob]);

  const togglePreviewPaper = useCallback((paperId: string) => {
    setSelectedPaperIds((prev) => {
      const next = new Set(prev);
      if (next.has(paperId)) {
        next.delete(paperId);
      } else {
        next.add(paperId);
      }
      return next;
    });
  }, []);

  const submitAiApply = useCallback(async () => {
    if (!activeJob?.suggestion) {
      return;
    }
    const paperIds = Array.from(selectedPaperIds);
    if (paperIds.length === 0) {
      toast.error('请至少勾选一篇论文');
      return;
    }
    const suggestion = activeJob.suggestion;
    const isReuse = Boolean(suggestion.reuse_category_id);
    if (!isReuse && !previewName.trim()) {
      toast.error('分类名不能为空');
      return;
    }
    setIsApplying(true);
    try {
      const result = await categorizeApplyMyPapers({
        category_name: isReuse ? (suggestion.category_name ?? '') : previewName.trim(),
        parent_id: isReuse ? null : previewParentId || null,
        reuse_category_id: suggestion.reuse_category_id,
        paper_ids: paperIds,
      });
      toast.success(`已归入「${result.category.name}」${result.assigned} 篇`);
      void dismissCategorizeJob(activeJob.id).catch(() => undefined);
      setJobs((prev) => prev.filter((job) => job.id !== activeJob.id));
      closeAiDialog();
      await hook.refresh();
      onAssignmentsChanged();
      onSelectCategory(result.category.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '归类失败');
    } finally {
      setIsApplying(false);
    }
  }, [activeJob, closeAiDialog, hook, onAssignmentsChanged, onSelectCategory, previewName, previewParentId, selectedPaperIds]);

  const discardSuggestion = useCallback(async () => {
    if (!activeJob) {
      return;
    }
    const jobId = activeJob.id;
    closeAiDialog();
    setJobs((prev) => prev.filter((job) => job.id !== jobId));
    try {
      await dismissCategorizeJob(jobId);
    } catch {
      // listing already updated optimistically; ignore server errors
    }
  }, [activeJob, closeAiDialog]);

  // Poll jobs while any is running; on mount fetch once to resume background work.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const payload = await fetchCategorizeJobs();
        if (cancelled) {
          return;
        }
        const nextJobs = payload.jobs ?? [];
        setJobs((prevJobs) => {
          for (const job of nextJobs) {
            const before = prevJobs.find((p) => p.id === job.id);
            if (before?.status === 'running' && job.status !== 'running' && !handledJobIds.current.has(job.id)) {
              handledJobIds.current.add(job.id);
              if (job.status === 'error') {
                toast.error(`AI 分类任务失败：${job.error ?? '未知错误'}`);
              } else if (job.kind === 'suggest') {
                toast('AI 归类建议就绪，点 ✨ 按钮查看');
              } else {
                const result = job.auto_result;
                const createdSuffix = result && result.created_categories.length
                  ? `，新建分类：${result.created_categories.join('、')}` : '';
                toast.success(`AI 已归类 ${result?.updated ?? 0} 处（共 ${result?.total ?? 0} 篇）${createdSuffix}`);
                void hook.refresh();
                onAssignmentsChanged();
              }
            }
          }
          return nextJobs;
        });
      } catch {
        // transient poll failure: keep polling
      }
      if (!cancelled) {
        timer = setTimeout(poll, JOB_POLL_INTERVAL_MS);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [hook, onAssignmentsChanged]);

  // Keep the reasoning view scrolled to the bottom as it grows.
  useEffect(() => {
    const node = reasoningBoxRef.current;
    if (node && activeJob?.status === 'running') {
      node.scrollTop = node.scrollHeight;
    }
  }, [activeJob?.status, activeJob?.reasoning_tail]);

  const countSubcategories = useCallback((node: CategoryTreeNode): number => {
    return node.children.reduce((total, child) => total + 1 + countSubcategories(child), 0);
  }, []);

  const handlePaperDrop = useCallback(
    async (event: React.DragEvent, categoryId: ActiveCategory) => {
      event.preventDefault();
      event.stopPropagation();
      setDropTargetId(null);
      setDropTargetKind(null);
      const paperId = event.dataTransfer.getData(PAPER_DRAG_TYPE);
      if (!paperId) {
        return;
      }
      try {
        if (categoryId === 'uncategorized') {
          const { clearPaperCategories } = await import('@/lib/api');
          await clearPaperCategories(paperId);
          toast.success('已移出全部分类');
        } else {
          const { setPaperCategory } = await import('@/lib/api');
          await setPaperCategory(paperId, categoryId, 'assign');
          toast.success('已归入分类');
        }
        await hook.refresh();
        onAssignmentsChanged();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '归类失败');
      }
    },
    [hook, onAssignmentsChanged],
  );

  const isPaperDrag = (event: React.DragEvent) =>
    event.dataTransfer.types.includes(PAPER_DRAG_TYPE);

  const isCategoryDrag = (event: React.DragEvent) =>
    event.dataTransfer.types.includes(CATEGORY_DRAG_TYPE);

  /** Expand the target node after a nested drop so the moved child is visible. */
  const revealNode = useCallback((nodeId: string) => {
    setCollapsed((prev) => {
      if (!prev.has(nodeId)) {
        return prev;
      }
      const next = new Set(prev);
      next.delete(nodeId);
      persistCollapsedState(next);
      return next;
    });
  }, []);

  const handleCategoryDrop = useCallback(
    async (event: React.DragEvent, targetParentId: string | null, targetName?: string) => {
      event.preventDefault();
      event.stopPropagation();
      setDropTargetId(null);
      setDropTargetKind(null);
      const draggedId = event.dataTransfer.getData(CATEGORY_DRAG_TYPE);
      if (!draggedId || draggedId === targetParentId) {
        return;
      }
      if (!canMoveCategory(tree, draggedId, targetParentId)) {
        toast.error('不能移动到自己的子分类下');
        return;
      }
      const moved = await moveCategory(draggedId, targetParentId);
      if (moved) {
        toast.success(targetName ? `「${moved.name}」已移动到「${targetName}」` : `「${moved.name}」已移到顶层`);
        if (targetParentId) {
          revealNode(targetParentId);
        }
      }
    },
    [moveCategory, revealNode, tree],
  );

  const renderNode = (node: CategoryTreeNode, depth: number) => {
    const isExpanded = !collapsed.has(node.id);
    const isActive = activeCategoryId === node.id;
    const isDropTarget = dropTargetId === node.id;
    const hasChildren = node.children.length > 0;

    return (
      <div key={node.id}>
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              role="button"
              tabIndex={0}
              onClick={() => onSelectCategory(node.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { onSelectCategory(node.id); }
              }}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(CATEGORY_DRAG_TYPE, node.id);
                e.dataTransfer.effectAllowed = 'move';
              }}
              onDragOver={(e) => {
                if (isPaperDrag(e) || isCategoryDrag(e)) {
                  e.preventDefault();
                  setDropTargetId(node.id);
                  setDropTargetKind(isCategoryDrag(e) ? 'category' : 'paper');
                }
              }}
              onDragLeave={(e) => {
                if ((isPaperDrag(e) || isCategoryDrag(e)) && dropTargetId === node.id) {
                  setDropTargetId(null);
                  setDropTargetKind(null);
                }
              }}
              onDrop={(e) => {
                if (isCategoryDrag(e)) {
                  void handleCategoryDrop(e, node.id, node.name);
                } else {
                  void handlePaperDrop(e, node.id);
                }
              }}
              className={`group flex w-full cursor-pointer items-center gap-1 rounded-xl py-1.5 pr-2 text-sm transition ${
                isActive
                  ? 'bg-[#fff3e8] font-medium text-[#c2410c] ring-1 ring-[#ffd6b0]'
                  : 'text-[#364152] hover:bg-[#f5f7fa]'
              } ${isDropTarget
                ? (dropTargetKind === 'category'
                  ? 'ring-2 ring-blue-400 bg-blue-50'
                  : 'ring-2 ring-[#ff7a00] bg-[#fff3e8]')
                : ''}`}
              style={{ paddingLeft: depth * 14 + 4 }}
            >
              {hasChildren ? (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); toggleExpanded(node.id); }}
                  className="shrink-0 rounded p-0.5 text-[#94a3b8] hover:text-[#172033]"
                  title={isExpanded ? '收起' : '展开'}
                >
                  <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
                </button>
              ) : (
                <span className="w-[22px] shrink-0" />
              )}
              {isExpanded && hasChildren ? (
                <FolderOpen className="h-4 w-4 shrink-0 text-[#f59e0b]" />
              ) : (
                <Folder className="h-4 w-4 shrink-0 text-[#94a3b8] group-hover:text-[#f59e0b]" />
              )}
              <span className={`h-2 w-2 shrink-0 rounded-full ${getCategoryColorStyle(node.color).dot}`} />
              <span className="min-w-0 flex-1 truncate" title={node.name}>{node.name}</span>
              <span className="shrink-0 text-xs tabular-nums text-[#94a3b8]">{node.subtreeCount}</span>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onSelect={() => openCreateDialog(node.id)}>
              <FolderPlus className="mr-2 h-4 w-4" /> 新建子分类
            </ContextMenuItem>
            <ContextMenuItem onSelect={() => openRenameDialog(node)}>
              <Pencil className="mr-2 h-4 w-4" /> 重命名
            </ContextMenuItem>
            <ContextMenuSub>
              <ContextMenuSubTrigger>
                <Palette className="mr-2 h-4 w-4" /> 颜色
              </ContextMenuSubTrigger>
              <ContextMenuSubContent className="max-h-64 overflow-y-auto">
                {CATEGORY_COLOR_KEYS.map((key) => (
                  <ContextMenuItem key={key} onSelect={() => { void setCategoryColor(node.id, key); }}>
                    <span className={`mr-2 h-3 w-3 shrink-0 rounded-full ${CATEGORY_COLOR_STYLES[key].dot}`} />
                    {CATEGORY_COLOR_LABELS[key]}
                    {(node.color ?? 'amber') === key ? <Check className="ml-auto h-3.5 w-3.5" /> : null}
                  </ContextMenuItem>
                ))}
                <ContextMenuSeparator />
                <ContextMenuItem onSelect={() => { void setCategoryColor(node.id, null); }}>
                  <span className="mr-2 h-3 w-3 shrink-0 rounded-full border border-[#d4dae3] bg-white" />
                  跟随默认
                </ContextMenuItem>
              </ContextMenuSubContent>
            </ContextMenuSub>
            {depth > 0 ? (
              <ContextMenuItem onSelect={() => { setMoveTarget(''); setMoveDialog({ categoryId: node.id, name: node.name }); }}>
                <Layers className="mr-2 h-4 w-4" /> 移动到…
              </ContextMenuItem>
            ) : null}
            <ContextMenuItem onSelect={() => openAiDescribeDialog(node)}>
              <MessageSquareText className="mr-2 h-4 w-4" /> AI 按描述归入此分类…
            </ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem className="text-red-600" onSelect={() => setDeleteDialog({ categoryId: node.id, name: node.name, subcategories: countSubcategories(node) })}>
              <Trash2 className="mr-2 h-4 w-4" /> 删除
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
        {hasChildren && isExpanded
          ? node.children.map((child) => renderNode(child, depth + 1))
          : null}
      </div>
    );
  };

  const moveOptions = useMemo(() => {
    if (!moveDialog) {
      return [];
    }
    return flattenCategoryTree(tree).filter((row) => row.node.id !== moveDialog.categoryId);
  }, [moveDialog, tree]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-[24px] bg-white/85 p-4 text-sm text-[#728095] shadow-sm ring-1 ring-black/5">
        <Loader2 className="h-4 w-4 animate-spin" /> 分类加载中…
      </div>
    );
  }

  return (
    <div className="rounded-[24px] bg-white/85 p-3 shadow-sm ring-1 ring-black/5">
      <div className="mb-2 flex items-center justify-between px-1">
        <h2 className="text-sm font-semibold text-[#172033]">分类</h2>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="relative h-7 w-7 rounded-full"
            title="AI 归类"
            disabled={isStartingJob}
            onClick={(e) => {
              e.stopPropagation();
              setAiMenuOpen((prev) => !prev);
            }}
          >
            {hasRunningJob ? <Loader2 className="h-4 w-4 animate-spin text-[#2563eb]" /> : <Sparkles className="h-4 w-4 text-[#2563eb]" />}
            {hasPendingSuggestion ? (
              <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#ff7a00]" />
            ) : null}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 rounded-full"
            title="新建顶层分类"
            onClick={() => openCreateDialog(null)}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {aiMenuOpen ? (
        <div className="mb-2 space-y-1 rounded-xl bg-[#eff6ff] p-1.5 text-xs ring-1 ring-[#bfdbfe]">
          <button
            type="button"
            className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[#2563eb] hover:bg-white"
            disabled={isStartingJob}
            onClick={() => { void startJob('auto_uncategorized'); }}
          >
            <Sparkles className="h-3.5 w-3.5" /> 归未分类论文
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[#b45309] hover:bg-white"
            disabled={isStartingJob}
            onClick={() => { void startJob('auto_full'); }}
          >
            <MoreHorizontal className="h-3.5 w-3.5" /> 全量重分（覆盖现有）
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[#7c3aed] hover:bg-white"
            disabled={isStartingJob}
            onClick={() => openAiDescribeDialog()}
          >
            <MessageSquareText className="h-3.5 w-3.5" /> 按描述归类…
          </button>

          {jobs.length > 0 ? (
            <div className="space-y-0.5 border-t border-[#bfdbfe] pt-1">
              {jobs.slice(0, 5).map((job) => (
                <div key={job.id} className="flex items-center gap-1.5 rounded-lg px-2 py-1 hover:bg-white">
                  {job.status === 'running' ? (
                    <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[#2563eb]" />
                  ) : job.status === 'done' ? (
                    <Check className="h-3 w-3 shrink-0 text-green-600" />
                  ) : (
                    <X className="h-3 w-3 shrink-0 text-red-500" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-[#364152]" title={job.description ?? JOB_KIND_LABELS[job.kind]}>
                    {JOB_KIND_LABELS[job.kind]}
                    {job.description ? `：${job.description}` : ''}
                  </span>
                  {job.status === 'running' ? (
                    <button
                      type="button"
                      className="shrink-0 rounded px-1 py-0.5 text-[#2563eb] hover:bg-[#dbeafe]"
                      onClick={() => { setAiMenuOpen(false); setActiveJobId(job.id); }}
                    >
                      查看
                    </button>
                  ) : null}
                  {job.status === 'done' && job.suggestion ? (
                    <button
                      type="button"
                      className="shrink-0 rounded px-1 py-0.5 text-[#2563eb] hover:bg-[#dbeafe]"
                      onClick={() => { setAiMenuOpen(false); showSuggestion(job); }}
                    >
                      查看
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-[#94a3b8] hover:bg-[#dbeafe] hover:text-[#172033]"
                    title={job.status === 'running' ? '从列表移除（任务继续）' : '移除'}
                    onClick={() => {
                      setJobs((prev) => prev.filter((j) => j.id !== job.id));
                      if (activeJobId === job.id) {
                        closeAiDialog();
                      }
                      if (job.status !== 'running') {
                        void dismissCategorizeJob(job.id).catch(() => undefined);
                      }
                    }}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div
        className="space-y-0.5"
        onDragOver={(e) => {
          // Top-level zone: only category drags. Paper drops stay on nodes.
          if (isCategoryDrag(e)) {
            e.preventDefault();
            setDropTargetId(null);
            setDropTargetKind(null);
          }
        }}
        onDrop={(e) => {
          if (isCategoryDrag(e)) {
            void handleCategoryDrop(e, null);
          }
        }}
      >
        <div
          role="button"
          tabIndex={0}
          onClick={() => onSelectCategory('all')}
          onKeyDown={(e) => { if (e.key === 'Enter') { onSelectCategory('all'); } }}
          className={`flex cursor-pointer items-center gap-2 rounded-xl px-2 py-1.5 text-sm transition ${
            activeCategoryId === 'all'
              ? 'bg-[#fff3e8] font-medium text-[#c2410c] ring-1 ring-[#ffd6b0]'
              : 'text-[#364152] hover:bg-[#f5f7fa]'
          }`}
        >
          <Layers className="h-4 w-4 shrink-0 text-[#94a3b8]" />
          <span className="flex-1">全部论文</span>
        </div>
        <div
          role="button"
          tabIndex={0}
          onClick={() => onSelectCategory('uncategorized')}
          onKeyDown={(e) => { if (e.key === 'Enter') { onSelectCategory('uncategorized'); } }}
          onDragOver={(e) => { if (isPaperDrag(e)) { e.preventDefault(); setDropTargetId('uncategorized'); } }}
          onDragLeave={(e) => { if (isPaperDrag(e) && dropTargetId === 'uncategorized') { setDropTargetId(null); } }}
          onDrop={(e) => { void handlePaperDrop(e, 'uncategorized'); }}
          className={`flex cursor-pointer items-center gap-2 rounded-xl px-2 py-1.5 text-sm transition ${
            activeCategoryId === 'uncategorized'
              ? 'bg-[#fff3e8] font-medium text-[#c2410c] ring-1 ring-[#ffd6b0]'
              : 'text-[#364152] hover:bg-[#f5f7fa]'
          } ${dropTargetId === 'uncategorized' ? 'ring-2 ring-[#ff7a00] bg-[#fff3e8]' : ''}`}
        >
          <Inbox className="h-4 w-4 shrink-0 text-[#94a3b8]" />
          <span className="flex-1">未分类</span>
          <span className="shrink-0 text-xs tabular-nums text-[#94a3b8]">{uncategorizedCount}</span>
        </div>
        {tree.map((node) => renderNode(node, 0))}
      </div>

      <Dialog open={nameDialog !== null} onOpenChange={(open) => { if (!open) { setNameDialog(null); } }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{nameDialog?.mode === 'rename' ? '重命名分类' : '新建分类'}</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={nameInput}
            placeholder="分类名称"
            onChange={(e) => setNameInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { void submitNameDialog(); }
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNameDialog(null)}>取消</Button>
            <Button onClick={() => { void submitNameDialog(); }}>
              {nameDialog?.mode === 'rename' ? '保存' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={aiFlow !== null || activeJob !== null}
        onOpenChange={(open) => { if (!open) { closeAiDialog(); } }}
      >
        <DialogContent className="max-w-lg">
          {aiFlow !== null ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  {aiFlow.mode === 'target'
                    ? `AI 按描述归入「${aiFlow.targetCategoryName}」`
                    : 'AI 按描述归类'}
                </DialogTitle>
              </DialogHeader>
              <p className="text-sm text-[#728095]">
                {aiFlow.mode === 'target'
                  ? '描述想找的论文主题，AI 会在你的论文里挑出符合的归入该分类。'
                  : '用一句话描述想找的论文主题，AI 会挑出符合的论文并建议分类。'}
              </p>
              <Textarea
                autoFocus
                value={descriptionInput}
                placeholder="例如：关于思维链（COT）安全 / 代码安全 / 提示词注入的论文"
                rows={3}
                maxLength={200}
                onChange={(e) => setDescriptionInput(e.target.value)}
              />
              <DialogFooter>
                <Button variant="outline" onClick={closeAiDialog}>取消</Button>
                <Button disabled={isStartingJob} onClick={() => { void submitAiDescription(); }}>
                  {isStartingJob ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1.5 h-4 w-4" />}
                  让 AI 挑选
                </Button>
              </DialogFooter>
            </>
          ) : null}

          {activeJob !== null && activeJob.status === 'running' ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  {activeJob.kind === 'suggest'
                    ? (activeJob.target_category_name
                        ? `AI 正在为「${activeJob.target_category_name}」挑选论文…`
                        : 'AI 正在思考…')
                    : `${JOB_KIND_LABELS[activeJob.kind]}进行中…`}
                </DialogTitle>
              </DialogHeader>
              <div
                ref={reasoningBoxRef}
                className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-xl bg-[#f8fafc] p-3 font-mono text-xs leading-5 text-[#64748b] ring-1 ring-black/5"
              >
                {activeJob.reasoning_tail || '正在连接模型…'}
              </div>
              <p className="text-xs text-[#94a3b8]">
                关闭窗口不会取消任务，稍后可点分类卡片左上角的 ✨ 按钮查看进度。
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={closeAiDialog}>后台运行</Button>
              </DialogFooter>
            </>
          ) : null}

          {activeJob !== null && activeJob.status === 'error' ? (
            <>
              <DialogHeader>
                <DialogTitle>AI 分类任务失败</DialogTitle>
              </DialogHeader>
              <div className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600 ring-1 ring-red-100">
                {activeJob.error ?? '未知错误'}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => { void discardSuggestion(); }}>知道了</Button>
                {activeJob.kind === 'suggest' ? (
                  <Button onClick={() => { setDescriptionInput(activeJob.description ?? ''); setActiveJobId(null); setAiFlow({ mode: activeJob.target_category_name ? 'target' : 'free' }); }}>
                    重新描述
                  </Button>
                ) : null}
              </DialogFooter>
            </>
          ) : null}

          {activeJob !== null && activeJob.status === 'done' && activeJob.auto_result !== null ? (
            <>
              <DialogHeader>
                <DialogTitle>{JOB_KIND_LABELS[activeJob.kind]}完成</DialogTitle>
              </DialogHeader>
              <div className="rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-700 ring-1 ring-emerald-100">
                已归类 {activeJob.auto_result.updated} 处（共 {activeJob.auto_result.total} 篇）
                {activeJob.auto_result.created_categories.length > 0
                  ? `，新建分类：${activeJob.auto_result.created_categories.join('、')}`
                  : ''}
              </div>
              <DialogFooter>
                <Button onClick={() => { void discardSuggestion(); }}>完成</Button>
              </DialogFooter>
            </>
          ) : null}

          {activeJob !== null && activeJob.status === 'done' && activeJob.suggestion !== null ? (
            (() => {
              const suggestion = activeJob.suggestion;
              const isReuse = Boolean(suggestion.reuse_category_id);
              const reuseName = isReuse
                ? (activeJob.target_category_name
                    ?? categories.find((c) => c.id === suggestion.reuse_category_id)?.name
                    ?? '')
                : '';
              const allChecked = suggestion.matched.length > 0 && selectedPaperIds.size === suggestion.matched.length;
              return (
                <>
                  <DialogHeader>
                    <DialogTitle>确认归类建议</DialogTitle>
                  </DialogHeader>

                  {isReuse ? (
                    <div className="rounded-xl bg-[#fff7ed] px-3 py-2 text-sm text-[#9a3412] ring-1 ring-[#fed7aa]">
                      将直接归入现有分类「{reuseName}」（不新建分类）
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-[#728095]">新分类名（可修改）</label>
                        <Input value={previewName} maxLength={12} onChange={(e) => setPreviewName(e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-[#728095]">挂载位置</label>
                        <div className="max-h-40 space-y-0.5 overflow-y-auto rounded-xl ring-1 ring-black/5">
                          <button
                            type="button"
                            className={`w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[#f5f7fa] ${previewParentId === '' ? 'bg-[#fff3e8] font-medium text-[#c2410c]' : 'text-[#364152]'}`}
                            onClick={() => setPreviewParentId('')}
                          >
                            （顶层）
                          </button>
                          {flattenCategoryTree(tree).map((row) => (
                            <button
                              key={row.node.id}
                              type="button"
                              className={`w-full truncate rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[#f5f7fa] ${previewParentId === row.node.id ? 'bg-[#fff3e8] font-medium text-[#c2410c]' : 'text-[#364152]'}`}
                              style={{ paddingLeft: row.depth * 14 + 8 }}
                              onClick={() => setPreviewParentId(row.node.id)}
                              title={row.pathLabel}
                            >
                              {row.node.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="flex items-center justify-between text-xs text-[#728095]">
                    <span>已选 {selectedPaperIds.size} / 命中 {suggestion.matched.length} 篇（共 {suggestion.total} 篇）</span>
                    {suggestion.matched.length > 0 ? (
                      <button
                        type="button"
                        className="rounded-lg px-2 py-0.5 text-[#2563eb] hover:bg-[#eff6ff]"
                        onClick={() => setSelectedPaperIds(allChecked ? new Set() : new Set(suggestion.matched.map((m) => m.paper_id)))}
                      >
                        {allChecked ? '取消全选' : '全选'}
                      </button>
                    ) : null}
                  </div>

                  {suggestion.matched.length === 0 ? (
                    <div className="rounded-xl bg-[#f5f7fa] px-3 py-6 text-center text-sm text-[#728095]">
                      没有找到符合描述的论文，换个说法试试
                    </div>
                  ) : (
                    <div className="max-h-56 space-y-1 overflow-y-auto">
                      {suggestion.matched.map((item) => (
                        <label
                          key={item.paper_id}
                          className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-[#f5f7fa]"
                        >
                          <Checkbox
                            className="mt-0.5"
                            checked={selectedPaperIds.has(item.paper_id)}
                            onCheckedChange={() => togglePreviewPaper(item.paper_id)}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-[#172033]" title={item.title}>{item.title}</span>
                            {item.existing_categories.length > 0 ? (
                              <span className="block truncate text-xs text-[#94a3b8]">
                                已在：{item.existing_categories.map((c) => c.name).join('、')}
                              </span>
                            ) : null}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}

                  <DialogFooter>
                    <Button variant="outline" disabled={isApplying} onClick={() => { void discardSuggestion(); }}>
                      丢弃建议
                    </Button>
                    <Button
                      disabled={isApplying || suggestion.matched.length === 0}
                      onClick={() => { void submitAiApply(); }}
                    >
                      {isApplying ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
                      确认归入{selectedPaperIds.size > 0 ? ` ${selectedPaperIds.size} 篇` : ''}
                    </Button>
                  </DialogFooter>
                </>
              );
            })()
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={moveDialog !== null} onOpenChange={(open) => { if (!open) { setMoveDialog(null); } }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>移动「{moveDialog?.name}」到…</DialogTitle>
          </DialogHeader>
          <div className="max-h-64 space-y-0.5 overflow-y-auto">
            <button
              type="button"
              className={`w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[#f5f7fa] ${moveTarget === '' ? 'bg-[#fff3e8] font-medium text-[#c2410c]' : 'text-[#364152]'}`}
              onClick={() => setMoveTarget('')}
            >
              （顶层）
            </button>
            {moveOptions.map((row) => (
              <button
                key={row.node.id}
                type="button"
                className={`w-full truncate rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[#f5f7fa] ${moveTarget === row.node.id ? 'bg-[#fff3e8] font-medium text-[#c2410c]' : 'text-[#364152]'}`}
                style={{ paddingLeft: row.depth * 14 + 8 }}
                onClick={() => setMoveTarget(row.node.id)}
                title={row.pathLabel}
              >
                {row.node.name}
              </button>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMoveDialog(null)}>取消</Button>
            <Button
              onClick={() => { void submitMoveDialog(); }}
              title={moveTarget ? '移动到所选分类下' : '移动到顶层'}
            >
              移动
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteDialog !== null} onOpenChange={(open) => { if (!open) { setDeleteDialog(null); } }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除「{deleteDialog?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteDialog && deleteDialog.subcategories > 0
                ? `将同时删除 ${deleteDialog.subcategories} 个子分类。`
                : ''}
              分类下的论文不会被删除，只是回到「未分类」。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              onClick={(e) => { e.preventDefault(); void submitDeleteDialog(); }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
