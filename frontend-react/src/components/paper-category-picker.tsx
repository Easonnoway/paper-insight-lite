import { useMemo } from 'react';
import { Check } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  clearPaperCategories,
  setPaperCategory,
} from '@/lib/api';
import { buildCategoryTree, flattenCategoryTree } from '@/lib/category-tree-utils';
import type { PaperCategory, PaperMark } from '@/types';

interface PaperCategoryPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  paperId: string;
  paperTitle: string;
  /** Full category list (for the tree). */
  categories: PaperCategory[];
  /** The paper's current mark (for assigned category ids). */
  mark: Pick<PaperMark, 'categories'>;
  /** Called after any assignment change so the owner can refresh. */
  onChanged: () => void;
}

/**
 * Shared multi-select category picker (home cards + paper detail page).
 * Click to assign, click again to unassign; one paper may live in many
 * categories.
 */
export function PaperCategoryPicker({
  open,
  onOpenChange,
  paperId,
  paperTitle,
  categories,
  mark,
  onChanged,
}: PaperCategoryPickerProps) {
  const assignedIds = useMemo(
    () => new Set((mark.categories ?? []).map((c) => c.id)),
    [mark.categories],
  );
  const flatCategories = useMemo(
    () => flattenCategoryTree(buildCategoryTree(categories)),
    [categories],
  );

  const handleAssign = async (categoryId: string) => {
    try {
      if (categoryId === '__clear__') {
        await clearPaperCategories(paperId);
        toast.success('已移出全部分类');
      } else if (assignedIds.has(categoryId)) {
        await setPaperCategory(paperId, categoryId, 'unassign');
        toast.success('已移出该分类');
      } else {
        await setPaperCategory(paperId, categoryId, 'assign');
        toast.success('已归入分类');
      }
      onOpenChange(false);
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '设置分类失败');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>选择分类</DialogTitle>
        </DialogHeader>
        <p className="-mt-2 text-xs text-[#728095]">
          《{paperTitle}》 — 勾选归入、再点已选项移出；一篇论文可属多个分类。
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
                onClick={() => { void handleAssign(row.node.id); }}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition hover:bg-[#f5f7fa] ${
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
            onClick={() => { void handleAssign('__clear__'); }}
          >
            移出全部分类
          </Button>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
