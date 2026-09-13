import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { PaperNoteSection } from './paper-note-section';

describe('PaperNoteSection', () => {
  it('renders the editor with the initial note and length cap', () => {
    const html = renderToStaticMarkup(
      <PaperNoteSection
        paperId="synthetic-paper"
        initialNote="草稿内容"
        onNoteChanged={() => undefined}
      />,
    );

    expect(html).toContain('我的笔记');
    expect(html).toContain('草稿内容');
    expect(html).toContain('/ 10000');
    expect(html).toContain('删除笔记');
    expect(html).toContain('保存');
  });

  it('renders without the delete button for an empty note', () => {
    const html = renderToStaticMarkup(
      <PaperNoteSection
        paperId="synthetic-paper"
        initialNote={null}
        onNoteChanged={() => undefined}
      />,
    );

    expect(html).toContain('我的笔记');
    expect(html).not.toContain('删除笔记');
  });
});
