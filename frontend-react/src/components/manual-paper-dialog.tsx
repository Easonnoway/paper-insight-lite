import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { createManualPaper } from '@/lib/api';
import type { Paper } from '@/types';

/** Split a free-typed list (comma / 顿号 / semicolon separators), trim,
 * drop empties, dedupe. Exported for direct unit testing. */
export function parseDelimitedItems(raw: string): string[] {
  return Array.from(new Set(
    raw
      .split(/[,，、;；]/)
      .map((item) => item.trim())
      .filter(Boolean),
  ));
}

export interface ManualPaperDraft {
  title: string;
  authorsRaw: string;
  abstract: string;
  pdf: string;
  venue: string;
  keywordsRaw: string;
  yearRaw: string;
}

export const EMPTY_MANUAL_PAPER_DRAFT: ManualPaperDraft = {
  title: '',
  authorsRaw: '',
  abstract: '',
  pdf: '',
  venue: '',
  keywordsRaw: '',
  yearRaw: '',
};

/** Returns an error message, or null when the draft is submittable. */
export function validateManualPaper(draft: ManualPaperDraft): string | null {
  if (!draft.title.trim()) {
    return '请填写论文标题';
  }
  const yearText = draft.yearRaw.trim();
  if (yearText) {
    const year = Number(yearText);
    if (!Number.isInteger(year) || year < 1900 || year > 2100) {
      return '发表年份需在 1900–2100 之间';
    }
  }
  if (parseDelimitedItems(draft.authorsRaw).length > 200) {
    return '作者数量过多（最多 200 位）';
  }
  if (parseDelimitedItems(draft.keywordsRaw).length > 100) {
    return '关键词数量过多（最多 100 个）';
  }
  return null;
}

interface ManualPaperFormProps {
  submitting: boolean;
  onSubmit: (draft: ManualPaperDraft) => void;
}

/** Inner form, split from the Dialog shell: Radix renders dialog content
 * through a portal, which renderToStaticMarkup-based tests can't mount. */
export function ManualPaperForm({ submitting, onSubmit }: ManualPaperFormProps) {
  const [draft, setDraft] = useState<ManualPaperDraft>(EMPTY_MANUAL_PAPER_DRAFT);

  const fieldClass =
    'rounded-xl border-[#e6ebf2] bg-white text-sm text-[#172033] placeholder:text-[#9aa6b8]';

  const update = (patch: Partial<ManualPaperDraft>) =>
    setDraft((prev) => ({ ...prev, ...patch }));

  const handleSubmit = () => {
    onSubmit(draft);
  };

  return (
    <div className="space-y-3.5">
      <div className="space-y-1.5">
        <Label htmlFor="manual-paper-title" className="text-xs font-medium text-[#516072]">
          <span className="text-[#b91c1c]">*</span> 标题
        </Label>
        <Input
          id="manual-paper-title"
          value={draft.title}
          onChange={(e) => update({ title: e.target.value })}
          placeholder="论文标题（必填）"
          className={fieldClass}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-paper-authors" className="text-xs font-medium text-[#516072]">
          作者
        </Label>
        <Input
          id="manual-paper-authors"
          value={draft.authorsRaw}
          onChange={(e) => update({ authorsRaw: e.target.value })}
          placeholder="逗号或顿号分隔，如：张三, 李四、Wang, X."
          className={fieldClass}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-paper-abstract" className="text-xs font-medium text-[#516072]">
          摘要
        </Label>
        <Textarea
          id="manual-paper-abstract"
          value={draft.abstract}
          onChange={(e) => update({ abstract: e.target.value })}
          placeholder="论文摘要（可选；无 PDF 时 AI 分析基于标题+摘要进行）"
          rows={4}
          className={`${fieldClass} min-h-0 resize-y`}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-paper-pdf" className="text-xs font-medium text-[#516072]">
          PDF 链接
        </Label>
        <Input
          id="manual-paper-pdf"
          value={draft.pdf}
          onChange={(e) => update({ pdf: e.target.value })}
          placeholder="https://example.com/paper.pdf（可选，提供后 AI 分析读全文）"
          className={fieldClass}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="manual-paper-venue" className="text-xs font-medium text-[#516072]">
            发表处
          </Label>
          <Input
            id="manual-paper-venue"
            value={draft.venue}
            onChange={(e) => update({ venue: e.target.value })}
            placeholder="如 ICCV 2019"
            className={fieldClass}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="manual-paper-year" className="text-xs font-medium text-[#516072]">
            发表年份
          </Label>
          <Input
            id="manual-paper-year"
            value={draft.yearRaw}
            onChange={(e) => update({ yearRaw: e.target.value })}
            placeholder="如 2019"
            inputMode="numeric"
            className={fieldClass}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-paper-keywords" className="text-xs font-medium text-[#516072]">
          关键词
        </Label>
        <Input
          id="manual-paper-keywords"
          value={draft.keywordsRaw}
          onChange={(e) => update({ keywordsRaw: e.target.value })}
          placeholder="逗号或顿号分隔，如：知识蒸馏、模型压缩"
          className={fieldClass}
        />
      </div>
      <div className="flex justify-end pt-1">
        <Button size="sm" className="rounded-full" disabled={submitting} onClick={handleSubmit}>
          {submitting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
          添加
        </Button>
      </div>
    </div>
  );
}

interface ManualPaperDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: (paper: Paper) => void;
}

export function ManualPaperDialog({ open, onOpenChange, onAdded }: ManualPaperDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (draft: ManualPaperDraft) => {
    const error = validateManualPaper(draft);
    if (error) {
      toast.error(error);
      return;
    }
    if (submitting) {
      return;
    }
    setSubmitting(true);
    try {
      const yearText = draft.yearRaw.trim();
      const paper = await createManualPaper({
        title: draft.title.trim(),
        authors: parseDelimitedItems(draft.authorsRaw),
        abstract: draft.abstract.trim() || null,
        pdf: draft.pdf.trim() || null,
        venue: draft.venue.trim() || null,
        keywords: parseDelimitedItems(draft.keywordsRaw),
        published_year: yearText ? Number(yearText) : null,
      });
      toast.success('已添加，AI 分析进行中');
      onAdded(paper);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '添加论文失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid-cols-[minmax(0,1fr)] max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>手动添加论文</DialogTitle>
          <DialogDescription>
            arXiv 收录不了的论文（老论文、书章节、其他会议）可以手动录入。
          </DialogDescription>
        </DialogHeader>
        <ManualPaperForm key={String(open)} submitting={submitting} onSubmit={(d) => { void handleSubmit(d); }} />
      </DialogContent>
    </Dialog>
  );
}
