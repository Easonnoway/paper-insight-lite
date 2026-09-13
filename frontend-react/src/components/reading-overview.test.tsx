import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ActivityHeatmap, ReadingOverviewPanel } from './reading-overview';
import type { ReadingOverviewResponse } from '@/types';

const OVERVIEW: ReadingOverviewResponse = {
  timezone: 'Asia/Shanghai',
  activity: {
    days: [{ date: '2026-07-13', count: 1 }],
    today_count: 1,
    month_count: 1,
    current_streak: 1,
  },
};

describe('ActivityHeatmap', () => {
  it('renders readable activity summaries for active and zero-count dates', () => {
    const html = renderToStaticMarkup(
      <ActivityHeatmap
        timezone="Asia/Shanghai"
        activity={{
          days: [
            { date: '2026-07-12', count: 0 },
            { date: '2026-07-13', count: 3 },
          ],
          today_count: 3,
          month_count: 12,
          current_streak: 2,
        }}
      />,
    );

    expect(html).toContain('今日阅读');
    expect(html).toContain('近 16 周论文阅读活动，共看过 3 篇论文，今日 3 篇');
    expect(html).toContain('class="whitespace-nowrap"');
    expect(html).toContain('2026年7月13日 · 看过 3 篇论文');
    expect(html).toContain('2026年7月12日 · 看过 0 篇论文');
    expect(html).toContain('tabindex="-1"');
    expect(html).not.toContain('2026年7月14日 · 看过');
  });
});

describe('ReadingOverviewPanel', () => {
  it('renders a closed pill without the heatmap by default', () => {
    const html = renderToStaticMarkup(
      <ReadingOverviewPanel
        overview={OVERVIEW}
        isLoading={false}
        error={null}
        onRetry={() => undefined}
      />,
    );

    expect(html).toContain('阅读概览');
    expect(html).toContain('连续 1 天 · 本月 1 篇 · 今日 1 篇');
    expect(html).toContain('aria-expanded="false"');
    // Popover content stays unrendered while closed.
    expect(html).not.toContain('近 16 周论文阅读活动');
    expect(html).not.toContain('阅读活动');
  });

  it('opens the popover state on the trigger', () => {
    // Note: radix renders PopoverContent through a Portal, which
    // renderToStaticMarkup does not mount — the popover body (heatmap) is
    // verified in the browser instead. Here we pin the trigger state.
    const html = renderToStaticMarkup(
      <ReadingOverviewPanel
        overview={OVERVIEW}
        isLoading={false}
        error={null}
        onRetry={() => undefined}
        defaultOpen
      />,
    );

    expect(html).toContain('阅读概览');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('data-state="open"');
    expect(html).toContain('连续 1 天 · 本月 1 篇 · 今日 1 篇');
  });

  it('shows the load failure hint on the pill when closed', () => {
    const html = renderToStaticMarkup(
      <ReadingOverviewPanel
        overview={null}
        isLoading={false}
        error="服务暂时不可用"
        onRetry={() => undefined}
      />,
    );

    expect(html).toContain('加载失败');
    // Error block with the retry button only exists inside the open popover.
    expect(html).not.toContain('重新加载');
  });
});
