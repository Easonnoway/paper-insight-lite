import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  createManualPaper: vi.fn().mockResolvedValue({ id: 'manual:abc', title: 'T' }),
}));

import {
  EMPTY_MANUAL_PAPER_DRAFT,
  ManualPaperForm,
  parseDelimitedItems,
  validateManualPaper,
} from './manual-paper-dialog';

describe('parseDelimitedItems', () => {
  it('splits on commas, 顿号, and semicolons; trims, drops empties, dedupes', () => {
    expect(parseDelimitedItems('A, B，C、D;E； A')).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(parseDelimitedItems('')).toEqual([]);
    expect(parseDelimitedItems('  ')).toEqual([]);
    expect(parseDelimitedItems('张三, 张三, 李四')).toEqual(['张三', '李四']);
  });
});

describe('validateManualPaper', () => {
  it('requires a title', () => {
    expect(validateManualPaper(EMPTY_MANUAL_PAPER_DRAFT)).toBe('请填写论文标题');
  });

  it('bounds the publication year', () => {
    expect(validateManualPaper({ ...EMPTY_MANUAL_PAPER_DRAFT, title: 'T', yearRaw: '1899' }))
      .toBe('发表年份需在 1900–2100 之间');
    expect(validateManualPaper({ ...EMPTY_MANUAL_PAPER_DRAFT, title: 'T', yearRaw: '2101' }))
      .toBe('发表年份需在 1900–2100 之间');
    expect(validateManualPaper({ ...EMPTY_MANUAL_PAPER_DRAFT, title: 'T', yearRaw: 'abc' }))
      .toBe('发表年份需在 1900–2100 之间');
  });

  it('accepts a valid draft', () => {
    expect(validateManualPaper({
      ...EMPTY_MANUAL_PAPER_DRAFT,
      title: 'T',
      yearRaw: '2019',
      authorsRaw: 'A、B',
    })).toBeNull();
  });
});

describe('ManualPaperForm', () => {
  it('renders every field with hints and the submit button', () => {
    const html = renderToStaticMarkup(
      <ManualPaperForm submitting={false} onSubmit={() => undefined} />,
    );

    expect(html).toContain('标题');
    expect(html).toContain('作者');
    expect(html).toContain('摘要');
    expect(html).toContain('PDF 链接');
    expect(html).toContain('发表处');
    expect(html).toContain('发表年份');
    expect(html).toContain('关键词');
    expect(html).toContain('添加');
  });
});

// ---------- edit mode ----------

import { paperToDraft } from './manual-paper-dialog';
import type { Paper } from '@/types';

describe('paperToDraft', () => {
  it('converts a Paper into form values', () => {
    const paper: Paper = {
      id: 'manual:abc',
      title: 'A Paper',
      authors: ['张三', '李四'],
      abstract: 'The abstract',
      pdf: 'https://example.com/x.pdf',
      venue: 'ICCV 2019',
      keywords: ['蒸馏', '压缩'],
      published_year: 2019,
    };
    expect(paperToDraft(paper)).toEqual({
      title: 'A Paper',
      authorsRaw: '张三、李四',
      abstract: 'The abstract',
      pdf: 'https://example.com/x.pdf',
      venue: 'ICCV 2019',
      keywordsRaw: '蒸馏、压缩',
      yearRaw: '2019',
    });
  });

  it('tolerates missing fields', () => {
    const paper = { id: 'manual:abc', title: 'Only Title' } as Paper;
    expect(paperToDraft(paper)).toEqual({
      title: 'Only Title',
      authorsRaw: '',
      abstract: '',
      pdf: '',
      venue: '',
      keywordsRaw: '',
      yearRaw: '',
    });
  });
});

describe('ManualPaperForm edit mode', () => {
  it('prefills the form from initialDraft', () => {
    const html = renderToStaticMarkup(
      <ManualPaperForm
        submitting={false}
        initialDraft={{
          title: 'Prefilled Title',
          authorsRaw: 'A、B',
          abstract: 'Prefilled abstract',
          pdf: '',
          venue: 'UI测试 2026',
          keywordsRaw: 'k1',
          yearRaw: '2026',
        }}
        onSubmit={() => undefined}
      />,
    );

    expect(html).toContain('Prefilled Title');
    expect(html).toContain('Prefilled abstract');
    expect(html).toContain('UI测试 2026');
  });

  it('starts empty without initialDraft (create mode unchanged)', () => {
    const html = renderToStaticMarkup(
      <ManualPaperForm submitting={false} onSubmit={() => undefined} />,
    );

    expect(html).toContain('论文标题（必填）');
  });
});
