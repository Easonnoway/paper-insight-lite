import { useMemo, useState } from 'react';
import { Bookmark, CalendarDays, Check, Eye, Heart, Loader2, Plus, Sparkles, StickyNote, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { PaperNoteDialog } from '@/components/paper-note-dialog';
import { RichContent } from '@/components/rich-content';
import { autoCategorizePaper, removePaperFromMine, setPaperCategory } from '@/lib/api';
import { getCategoryColorStyle } from '@/lib/category-colors';
import { navigate } from '@/lib/router';
import { getVenueParts, normalizeKeywords } from '@/lib/content';
import type { MarkedPaperItem, PaperCategory, PaperMark } from '@/types';
import { flattenCategoryTree, buildCategoryTree } from '@/lib/category-tree-utils';

const PAPER_DRAG_TYPE = 'application/x-paper-id';

function getConferenceColor(conference: string) {
  switch (conference) {
    case 'ICLR':
      return 'bg-blue-50 text-blue-700 border-blue-200';
    case 'NeurIPS':
      return 'bg-violet-50 text-violet-700 border-violet-200';
    case 'ICML':
      return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'CHI':
      return 'bg-rose-50 text-rose-700 border-rose-200';
    case 'CVPR':
      return 'bg-teal-50 text-teal-700 border-teal-200';
    default:
      return 'bg-slate-100 text-slate-700 border-slate-200';
  }
}

function formatTime(value?: string | null): string {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function formatPublishDate(value?: string | null): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function MarkBadge({ mark }: { mark: PaperMark }) {
  if (mark.favorited) {
    return (
      <Badge variant="outline" className="border-[#fed7aa] bg-[#fff7ed] text-[#ea580c]">
        <Bookmark className="mr-1 h-3 w-3 fill-current" />
        已收藏
      </Badge>
    );
  }
  if (mark.liked) {
    return (
      <Badge variant="outline" className="border-[#fecaca] bg-[#fff1f2] text-[#e11d48]">
        <Heart className="mr-1 h-3 w-3 fill-current" />
        已点赞
      </Badge>
    );
  }
  if (mark.viewed) {
    return (
      <Badge variant="outline" className="border-[#bfdbfe] bg-[#eff6ff] text-[#2563eb]">
        <Eye className="mr-1 h-3 w-3 fill-current" />
        已看过
      </Badge>
    );
  }
  return null;
}

interface PaperHistoryCardProps {
  item: MarkedPaperItem;
  abstractZh?: string;
  onRemoved: () => void;
  /** Full category list for the ＋分类 picker and badge removal. */
  categories: PaperCategory[];
  onCategoriesChanged: () => void;
  onNoteChanged?: (paperId: string, mark: PaperMark) => void;
}

export function PaperHistoryCard({
  item,
  abstractZh,
  onRemoved,
  categories,
  onCategoriesChanged,
  onNoteChanged,
}: PaperHistoryCardProps) {
  const { paper, mark } = item;
  const venue = getVenueParts(paper.venue);
  const keywords = normalizeKeywords(paper.keywords).slice(0, 5);
  const [removing, setRemoving] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [applying, setApplying] = useState(false);
  const [autoCategorizing, setAutoCategorizing] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);

  const assignedIds = useMemo(
    () => new Set((mark.categories ?? []).map((c) => c.id),
    ),
    [mark.categories],
  );
  const flatCategories = useMemo(() => flattenCategoryTree(buildCategoryTree(categories)), [categories]);
  const colorById = useMemo(
    () => new Map(categories.map((c) => [c.id, c.color ?? null])),
    [categories],
  );
  const MAX_VISIBLE_AUTHORS = 3;
  const paperAuthors = useMemo(() => {
    const authors = paper.authors ?? [];
    if (authors.length === 0) {
      return '';
    }
    const shown = authors.slice(0, MAX_VISIBLE_AUTHORS).join(', ');
    return authors.length > MAX_VISIBLE_AUTHORS ? `${shown} 等` : shown;
  }, [paper.authors]);
  const publishDate = formatPublishDate(paper.published_at) ||
    (paper.published_year ? String(paper.published_year) : '');

  const handleRemove = async () => {
    setRemoving(true);
    try {
      await removePaperFromMine(paper.id);
      toast.success('已移出“我的论文”并清除分析缓存');
      onRemoved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '移除失败');
    } finally {
      setRemoving(false);
    }
  };

  const handleAssign = async (categoryId: string) => {
    setApplying(true);
    try {
      if (categoryId === '__clear__') {
        const { clearPaperCategories } = await import('@/lib/api');
        await clearPaperCategories(paper.id);
        toast.success('已移出全部分类');
      } else if (assignedIds.has(categoryId)) {
        await setPaperCategory(paper.id, categoryId, 'unassign');
        toast.success('已移出该分类');
      } else {
        await setPaperCategory(paper.id, categoryId, 'assign');
        toast.success('已归入分类');
      }
      setPickerOpen(false);
      onCategoriesChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '设置分类失败');
    } finally {
      setApplying(false);
    }
  };

  const handleBadgeRemove = async (categoryId: string) => {
    try {
      await setPaperCategory(paper.id, categoryId, 'unassign');
      onCategoriesChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '移出分类失败');
    }
  };

  const handleAutoCategorize = async () => {
    setAutoCategorizing(true);
    try {
      const result = await autoCategorizePaper(paper.id);
      if (result.assigned.length) {
        toast.success(`AI 已归入：${result.assigned.map((c) => c.name).join('、')}`);
      } else {
        toast(result.message || '没有找到合适的分类');
      }
      onCategoriesChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'AI 归类失败');
    } finally {
      setAutoCategorizing(false);
    }
  };

  return (
    <article
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(PAPER_DRAG_TYPE, paper.id);
        e.dataTransfer.effectAllowed = 'move';
      }}
      className="relative cursor-pointer rounded-[28px] bg-white p-5 shadow-sm ring-1 ring-black/5 transition hover:-translate-y-0.5 hover:shadow-lg"
      onClick={() => navigate(`/papers/${paper.id}`)}
    >
      <div className="absolute right-4 top-4 z-10">
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              type="button"
              disabled={removing}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[#e6ebf2] bg-white/80 text-[#94a3b8] shadow-sm backdrop-blur transition hover:border-[#fecaca] hover:bg-[#fff1f2] hover:text-[#ef4444] disabled:opacity-50"
              title="移出我的论文并清除缓存"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent onClick={(e) => e.stopPropagation()}>
            <AlertDialogHeader>
              <AlertDialogTitle>移出“我的论文”？</AlertDialogTitle>
              <AlertDialogDescription>
                会移除你对《{paper.title}》的标记，并清除它的 AI 分析与缓存（论文本身保留在库中，会议计数不变；以后重新查看会重新分析）。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={(e) => e.stopPropagation()}>取消</AlertDialogCancel>
              <AlertDialogAction
                disabled={removing}
                onClick={(e) => { e.stopPropagation(); void handleRemove(); }}
              >
                {removing ? '移除中…' : '确认移出'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 pl-8">
        <Badge variant="outline" className={getConferenceColor(venue.conference)}>
          {venue.label}
        </Badge>
        {paper.primary_area ? (
          <Badge
            variant="outline"
            className="max-w-full min-w-0 truncate border-[#e6ebf2] bg-[#f8fafc] text-[#516072]"
            title={paper.primary_area}
          >
            {paper.primary_area}
          </Badge>
        ) : null}
        <MarkBadge mark={mark} />
        {(mark.categories ?? []).map((cat) => {
          const colorStyle = getCategoryColorStyle(colorById.get(cat.id));
          return (
            <span
              key={cat.id}
              className={`group/badge inline-flex items-center gap-1 rounded-full border py-0.5 pl-2 pr-1 text-xs font-medium ${colorStyle.border} ${colorStyle.bg} ${colorStyle.text} ${
                applying ? 'opacity-60' : ''
              }`}
              title={`分类：${cat.name}（点 ✕ 移出，点名称更换）`}
            >
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setPickerOpen(true); }}
                className="max-w-40 truncate"
              >
                {cat.name}
              </button>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); void handleBadgeRemove(cat.id); }}
                className="rounded-full p-0.5 hover:bg-white/60 hover:text-current"
                title="移出此分类"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          );
        })}
        <button
          type="button"
          disabled={autoCategorizing}
          onClick={(e) => { e.stopPropagation(); void handleAutoCategorize(); }}
          className="inline-flex items-center gap-1 rounded-full border border-dashed border-[#d4dae3] px-2 py-0.5 text-xs font-medium text-[#94a3b8] transition hover:border-[#93c5fd] hover:text-[#2563eb] disabled:opacity-50"
          title="AI 归类：自动归入现有分类"
        >
          {autoCategorizing
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <Sparkles className="h-3 w-3" />}
          AI 归类
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setPickerOpen(true); }}
          className="inline-flex items-center gap-1 rounded-full border border-dashed border-[#d4dae3] px-2 py-0.5 text-xs font-medium text-[#94a3b8] transition hover:border-[#fbbf24] hover:text-[#b45309]"
          title="添加分类（或拖动卡片到左侧分类树）"
        >
          <Plus className="h-3 w-3" /> 分类
        </button>
      </div>

      <h2 className="text-xl font-semibold leading-snug text-[#1f2937] hover:text-[#ff7a00]">
        <RichContent content={paper.title} inline className="paper-title-math" />
      </h2>

      {paperAuthors || publishDate ? (
        <p className="mt-1.5 flex items-center gap-2 truncate text-xs text-[#728095]">
          {paperAuthors ? (
            <span className="min-w-0 truncate" title={paper.authors?.join('、')}>{paperAuthors}</span>
          ) : null}
          {publishDate ? (
            <span className="inline-flex shrink-0 items-center gap-1" title="arXiv 发布时间">
              <CalendarDays className="h-3 w-3" aria-hidden="true" />
              {publishDate}
            </span>
          ) : null}
        </p>
      ) : null}

      {keywords.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {keywords.map((keyword) => (
            <span
              key={`${paper.id}-${keyword}`}
              className="rounded-full border border-[#e6ebf2] bg-[#f8fafc] px-2.5 py-1 text-xs text-[#516072]"
            >
              {keyword}
            </span>
          ))}
        </div>
      ) : null}

      <p className="mt-4 line-clamp-2 text-sm leading-6 text-[#67758a]">
        {abstractZh || paper.abstract || '暂无摘要'}
      </p>

      {mark.note ? (
        <div
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); setNoteOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              e.stopPropagation();
              setNoteOpen(true);
            }
          }}
          className="mt-3 cursor-text rounded-2xl bg-[#fffbeb] px-3 py-2 ring-1 ring-[#fde68a] transition hover:bg-[#fef3c7]"
          title="点击编辑笔记"
        >
          <div className="flex items-center gap-1.5 text-xs font-medium text-[#b45309]">
            <StickyNote className="h-3.5 w-3.5" aria-hidden="true" />
            笔记
          </div>
          {/* Rendered through RichContent so inline LaTeX ($...$ / $$...$$)
              shows as math; line-clamp keeps the preview to two lines. */}
          <RichContent
            content={mark.note}
            inline
            className="paper-note-math mt-1 line-clamp-2 text-sm leading-6 text-[#67758a]"
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setNoteOpen(true); }}
          className="mt-3 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium text-[#94a3b8] transition hover:bg-[#fffbeb] hover:text-[#b45309]"
          title="写笔记"
        >
          <StickyNote className="h-3.5 w-3.5" />
          写笔记
        </button>
      )}

      <div className="mt-4 grid gap-2 text-xs text-[#728095] sm:grid-cols-4">
        <div>最近点击：{formatTime(mark.last_opened_at)}</div>
        <div>看过：{formatTime(mark.viewed_at)}</div>
        <div>点赞：{formatTime(mark.liked_at)}</div>
        <div>收藏：{formatTime(mark.favorited_at)}</div>
      </div>

      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent className="max-w-sm" onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>选择分类</DialogTitle>
          </DialogHeader>
          <p className="-mt-2 text-xs text-[#728095]">
            《{paper.title}》 — 勾选归入、再点已选项移出；一篇论文可属多个分类。
          </p>
          <div className="max-h-72 space-y-0.5 overflow-y-auto">
            {flatCategories.length === 0 ? (
              <p className="px-2 py-4 text-center text-sm text-[#728095]">
                还没有分类，先在左侧分类树新建一个。
              </p>
            ) : null}
            {flatCategories.map((row) => {
              const isAssigned = assignedIds.has(row.node.id);
              return (
                <button
                  key={row.node.id}
                  type="button"
                  disabled={applying}
                  onClick={() => { void handleAssign(row.node.id); }}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition hover:bg-[#f5f7fa] disabled:opacity-50 ${
                    isAssigned ? 'bg-[#fffbeb] font-medium text-[#b45309]' : 'text-[#364152]'
                  }`}
                  style={{ paddingLeft: row.depth * 14 + 8 }}
                  title={row.pathLabel}
                >
                  <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                    isAssigned ? 'border-[#f59e0b] bg-[#f59e0b] text-white' : 'border-[#d4dae3]'
                  }`}>
                    {isAssigned ? <Check className="h-3 w-3" /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{row.node.name}</span>
                  <span className="shrink-0 text-xs text-[#94a3b8]">{row.node.paperCount}</span>
                </button>
              );
            })}
          </div>
          {(mark.categories ?? []).length > 0 ? (
            <Button
              variant="outline"
              className="w-full text-[#b91c1c] hover:bg-[#fef2f2] hover:text-[#b91c1c]"
              disabled={applying}
              onClick={() => { void handleAssign('__clear__'); }}
            >
              移出全部分类
            </Button>
          ) : null}
        </DialogContent>
      </Dialog>

      <PaperNoteDialog
        paperId={paper.id}
        paperTitle={paper.title}
        initialNote={mark.note ?? ''}
        open={noteOpen}
        onOpenChange={setNoteOpen}
        onSaved={(nextMark) => onNoteChanged?.(paper.id, nextMark)}
      />
    </article>
  );
}

export { formatTime as formatMarkTime };
