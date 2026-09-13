import { useEffect, useState } from 'react';
import { Eye, Loader2, Pencil, StickyNote, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { RichContent } from '@/components/rich-content';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { updatePaperNote } from '@/lib/api';
import type { PaperMark } from '@/types';

const MAX_NOTE_LENGTH = 10_000;

interface PaperNoteSectionProps {
  paperId: string;
  initialNote?: string | null;
  onNoteChanged: (mark: PaperMark) => void;
}

/**
 * Inline note editor on the paper detail page (read-while-writing flow).
 * Editing is plain text (LaTeX source); "预览" renders the note through
 * RichContent so $...$ / $$...$$ show as real math.
 */
export function PaperNoteSection({
  paperId,
  initialNote,
  onNoteChanged,
}: PaperNoteSectionProps) {
  const savedNote = initialNote ?? '';
  const [draft, setDraft] = useState(savedNote);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // Reset the draft when navigating between papers.
  useEffect(() => {
    setDraft(initialNote ?? '');
  }, [initialNote, paperId]);

  const hasChanges = draft !== savedNote;

  const save = async (note: string | null) => {
    if (saving || (!hasChanges && note === null)) {
      return;
    }
    setSaving(true);
    try {
      const nextMark = await updatePaperNote(paperId, note);
      // A noted paper without a mark row gains viewed=TRUE and shows up in
      // My Papers — surface that so it doesn't surprise the user.
      toast.success(nextMark.viewed ? '笔记已保存（已记为看过）' : '笔记已保存');
      onNoteChanged(nextMark);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '笔记保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      void save(draft.trim() || null);
    }
  };

  return (
    <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5" aria-label="我的笔记">
      <div className="flex items-center gap-2 border-b border-[#eef2f7] pb-4">
        <StickyNote className="h-5 w-5 text-[#f59e0b]" aria-hidden="true" />
        <h2 className="text-xl font-semibold text-[#172033]">我的笔记</h2>
        <span className="ml-auto text-xs text-[#94a3b8]">
          {hasChanges ? <span className="mr-2 text-[#b45309]">未保存</span> : null}
          {draft.length} / {MAX_NOTE_LENGTH}
        </span>
      </div>
      {previewing ? (
        <RichContent
          content={draft || '*（空笔记）*'}
          className="paper-note-math markdown-body mt-4 min-h-24 max-h-96 overflow-y-auto text-base leading-7 text-[#334155]"
        />
      ) : (
        <Textarea
          value={draft}
          maxLength={MAX_NOTE_LENGTH}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={'记录你的想法、疑问、与自己研究的关联…（⌘/Ctrl + Enter 保存）\n支持 LaTeX：$E=mc^2$ 行内公式，$$...$$ 独立公式'}
          className="mt-4 max-h-96 min-h-24 overflow-y-auto border-0 p-0 shadow-none focus-visible:ring-0 md:text-sm"
        />
      )}
      <div className="mt-3 flex justify-between gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="rounded-full text-[#516072] hover:bg-[#f5f7fa]"
          onClick={() => setPreviewing((prev) => !prev)}
          disabled={!draft.trim()}
          title={previewing ? '回到编辑' : '渲染 LaTeX 预览'}
        >
          {previewing
            ? <Pencil className="mr-1 h-3.5 w-3.5" />
            : <Eye className="mr-1 h-3.5 w-3.5" />}
          {previewing ? '编辑' : '预览'}
        </Button>
        <div className="flex items-center gap-2">
          {savedNote ? (
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
    </section>
  );
}
