import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { AuthContext, type AuthContextValue } from '@/lib/auth';
import { ChatPanel } from './chat-panel';

const AUTH: AuthContextValue = {
  user: null,
  isLoading: false,
  refresh: async () => undefined,
};

describe('ChatPanel', () => {
  it('renders the inline chat panel with an empty-state prompt', () => {
    const html = renderToStaticMarkup(
      <AuthContext.Provider value={AUTH}>
        <ChatPanel paperId="paper-1" />
      </AuthContext.Provider>,
    );

    expect(html).toContain('论文对话');
    expect(html).toContain('输入问题，开始与论文对话');
    expect(html).toContain('aria-label="新对话"');
    expect(html).toContain('aria-label="历史对话"');
    expect(html).toContain('Shift + Enter 发送消息');
  });
});
