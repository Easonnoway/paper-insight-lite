import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { LibraryTransferDialog } from './library-transfer-dialog';

describe('LibraryTransferDialog', () => {
  it('renders the header trigger button with an accessible label', () => {
    const html = renderToStaticMarkup(<LibraryTransferDialog />);
    expect(html).toContain('分享 / 导入论文库');
    expect(html).toContain('aria-label="分享 / 导入论文库"');
  });

  it('accepts an optional className for the trigger', () => {
    const html = renderToStaticMarkup(<LibraryTransferDialog className="mx-2" />);
    expect(html).toContain('mx-2');
  });
});
