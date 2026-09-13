import { useEffect, useState } from 'react';
import { Eye, Loader2, Pencil, StickyNote, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { RichContent } from '@/components/rich-content';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { updatePaperNote } from '@/lib/api';
import type { PaperMark } from '@/types';

export const MAX_NOTE_LENGTH = 10_000;

interface PaperNoteDialogProps {
  paperId: string;
  paperTitle: string;
  initialNote: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (mark: PaperMark) => void;
}

/**
 * Note editor for the My Papers card. A Dialog (not an inline expansion):
 * the card itself is click-to-navigate, so inline focus/keyboard handling
 * would need stopPropagation everywhere and risks accidental navigation;
 * the portal also keeps the compact card list from jumping in height.
 * Editing is plain text (LaTeX source); "预览" renders $...$/$$...$$ as math.
 */
export function PaperNoteDialog({
  paperId,
  paperTitle,
  initialNote,
  open,
  onOpenChange,
  onSaved,
}: PaperNoteDialogProps) {
  const [draft, setDraft] = useState(initialNote);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // Re-sync the draft each time the dialog opens for a (possibly different) paper.
  useEffect(() => {
    if (open) {
      setDraft(initialNote);
      setPreviewing(false);
    }
  }, [initialNote, open]);

  const hasChanges = draft !== initialNote;

  const save = async (note: string | null) => {
    if (saving) {
      return;
    }
    setSaving(true);
    try {
      const nextMark = await updatePaperNote(paperId, note);
      toast.success('笔记已保存');
      onSaved(nextMark);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '笔记保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" onClick={(e) => e.stopPropagation()}>
        <DialogHeader className="min-w-0">
          {/* min-w-0 lets truncate work inside the grid DialogContent
              (grid items default to min-width:auto and would otherwise be
              stretched to the title's full intrinsic width). */}
          <DialogTitle className="flex min-w-0 items-center gap-2 pr-6">
            <StickyNote className="h-4 w-4 shrink-0 text-[#f59e0b]" />
            <span className="min-w-0 truncate" title={paperTitle}>《{paperTitle}》</span>
          </DialogTitle>
        </DialogHeader>
        {previewing ? (
          <RichContent
            content={draft || '*（空笔记）*'}
            className="paper-note-math markdown-body max-h-64 min-h-32 overflow-y-auto text-sm leading-6 text-[#334155]"
          />
        ) : (
          <Textarea
            value={draft}
            maxLength={MAX_NOTE_LENGTH}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={'记录你的想法、疑问、与自己研究的关联…\n支持 LaTeX：$E=mc^2$ 行内公式，$$...$$ 独立公式'}
            // Cap the height so long notes scroll inside the textarea
            // instead of overflowing the dialog.
            className="max-h-64 min-h-32 overflow-y-auto"
            autoFocus
          />
        )}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="rounded-full px-2 text-[#516072] hover:bg-[#f5f7fa]"
              onClick={() => setPreviewing((prev) => !prev)}
              disabled={!draft.trim()}
              title={previewing ? '回到编辑' : '渲染 LaTeX 预览'}
            >
              {previewing
                ? <Pencil className="h-3.5 w-3.5" />
                : <Eye className="h-3.5 w-3.5" />}
              {previewing ? '编辑' : '预览'}
            </Button>
            <span className="text-xs text-[#94a3b8]">
              {draft.length} / {MAX_NOTE_LENGTH}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {initialNote ? (
              <Button
                variant="ghost"
                size="sm"
                className="text-[#b91c1c] hover:bg-[#fef2f2] hover:text-[#b91c1c]"
                disabled={saving}
                onClick={() => {
                  setDraft('');
                  void save(null);
                }}
              >
                <Trash2 className="mr-1 h-3.5 w-3.5" />
                删除笔记
              </Button>
            ) : null}
            <Button
              size="sm"
              disabled={!hasChanges || saving}
              onClick={() => { void save(draft.trim() || null); }}
            >
              {saving ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
              保存
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
