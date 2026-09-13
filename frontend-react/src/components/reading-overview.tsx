import { useState } from 'react';
import {
  Activity,
  BookOpenCheck,
  CalendarDays,
  Clock3,
  Flame,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  buildActivityCalendar,
  formatActivityTooltip,
  getActivityLevel,
} from '@/lib/reading-overview';
import type {
  ReadingActivitySummary,
  ReadingOverviewResponse,
} from '@/types';

const ACTIVITY_COLORS = [
  'bg-[#edf0f3] ring-[#e5e7eb]',
  'bg-[#dcfce7] ring-[#bbf7d0]',
  'bg-[#86efac] ring-[#4ade80]',
  'bg-[#4ade80] ring-[#22c55e]',
  'bg-[#16a34a] ring-[#15803d]',
] as const;

export function ActivityHeatmap({
  activity,
  timezone,
}: {
  activity: ReadingActivitySummary;
  timezone: string;
}) {
  const weeks = buildActivityCalendar(activity.days);
  const totalCount = weeks
    .flatMap((week) => week.cells)
    .reduce((total, day) => total + Math.max(0, day.count), 0);

  return (
    <section aria-labelledby="reading-activity-heading">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#f0fdf4] text-[#16a34a]">
            <Activity className="h-4 w-4" aria-hidden="true" />
          </span>
          <div>
            <h3 id="reading-activity-heading" className="text-sm font-semibold text-[#172033]">阅读活动</h3>
            <p className="mt-0.5 text-xs text-[#7a8798]">近 16 周首次看过的论文</p>
          </div>
        </div>
        <span className="rounded-full bg-[#f8fafc] px-2 py-1 text-[10px] text-[#8a96a8]" title={timezone}>
          {timezone === 'Asia/Shanghai' ? '北京时间' : timezone}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-2xl bg-[#f8fafc] px-2 py-2.5">
          <dt className="flex items-center justify-center gap-1 text-[10px] text-[#8793a5]">
            <Flame className="h-3 w-3 text-[#f97316]" aria-hidden="true" />
            连续阅读
          </dt>
          <dd className="mt-1 text-sm font-semibold text-[#172033]">{activity.current_streak} 天</dd>
        </div>
        <div className="rounded-2xl bg-[#f8fafc] px-2 py-2.5">
          <dt className="flex items-center justify-center gap-1 text-[10px] text-[#8793a5]">
            <CalendarDays className="h-3 w-3 text-[#2563eb]" aria-hidden="true" />
            本月阅读
          </dt>
          <dd className="mt-1 text-sm font-semibold text-[#172033]">{activity.month_count} 篇</dd>
        </div>
        <div className="rounded-2xl bg-[#f8fafc] px-2 py-2.5">
          <dt className="flex items-center justify-center gap-1 text-[10px] text-[#8793a5]">
            <Clock3 className="h-3 w-3 text-[#16a34a]" aria-hidden="true" />
            今日阅读
          </dt>
          <dd className="mt-1 text-sm font-semibold text-[#172033]">{activity.today_count} 篇</dd>
        </div>
      </dl>

      {weeks.length ? (
        <div className="mt-4">
          <div className="grid grid-cols-[1.4rem_minmax(0,1fr)] gap-2">
            <div aria-hidden="true" />
            <div
              className="grid h-4 gap-[3px] text-[9px] leading-none text-[#9aa5b4]"
              style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}
              aria-hidden="true"
            >
              {weeks.map((week) => <span key={week.key} className="whitespace-nowrap">{week.monthLabel}</span>)}
            </div>
            <div className="grid grid-rows-7 gap-[3px] text-[9px] text-[#9aa5b4]" aria-hidden="true">
              {['一', '', '三', '', '五', '', '日'].map((label, index) => (
                <span key={`${label}-${index}`} className="flex items-center">{label}</span>
              ))}
            </div>
            <div
              className="grid gap-[3px]"
              style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}
              role="grid"
              aria-label={`近 16 周论文阅读活动，共看过 ${totalCount} 篇论文，今日 ${activity.today_count} 篇`}
            >
              {weeks.map((week) => (
                <div key={week.key} className="grid grid-rows-7 gap-[3px]" role="row">
                  {week.cells.map((cell, dayIndex) => {
                    if (!cell.date) {
                      return <span key={`${week.key}-${dayIndex}`} className="aspect-square" aria-hidden="true" />;
                    }
                    const label = formatActivityTooltip(cell.date, cell.count);
                    return (
                      <Tooltip key={cell.date}>
                        <TooltipTrigger asChild>
                          <span
                            role="gridcell"
                            tabIndex={cell.count > 0 ? 0 : -1}
                            aria-label={label}
                            className={`aspect-square rounded-[3px] ring-1 ring-inset outline-none transition focus-visible:ring-2 focus-visible:ring-[#2563eb] ${ACTIVITY_COLORS[getActivityLevel(cell.count)]}`}
                          />
                        </TooltipTrigger>
                        <TooltipContent side="top" sideOffset={6}>{label}</TooltipContent>
                      </Tooltip>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          <div className="mt-3 flex items-center justify-end gap-1 text-[10px] text-[#9aa5b4]" aria-label="颜色越深表示当天阅读论文越多">
            <span>少</span>
            {ACTIVITY_COLORS.map((color, index) => (
              <span key={color} className={`h-2.5 w-2.5 rounded-[3px] ring-1 ring-inset ${color}`} aria-label={`活跃度 ${index}`} />
            ))}
            <span>多</span>
          </div>
        </div>
      ) : (
        <p className="mt-4 rounded-2xl bg-[#f8fafc] px-3 py-5 text-center text-xs text-[#8793a5]">
          暂无阅读活动，标记第一篇“看过”后这里就会亮起来。
        </p>
      )}
    </section>
  );
}

function ReadingOverviewSkeleton() {
  return (
    <div className="space-y-5" role="status" aria-label="阅读概览加载中">
      <div className="flex items-center gap-3">
        <div className="h-8 w-8 animate-pulse rounded-xl bg-[#edf0f3]" />
        <div className="space-y-2">
          <div className="h-3 w-20 animate-pulse rounded bg-[#e2e8f0]" />
          <div className="h-2.5 w-32 animate-pulse rounded bg-[#edf0f3]" />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {[0, 1, 2].map((item) => <div key={item} className="h-14 animate-pulse rounded-2xl bg-[#f1f5f9]" />)}
      </div>
      <div className="h-32 animate-pulse rounded-2xl bg-[#f8fafc]" />
      <span className="sr-only">加载中</span>
    </div>
  );
}

export interface ReadingOverviewPanelProps {
  overview: ReadingOverviewResponse | null;
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Test hook: force the initial popover state (defaults to closed;
   * localStorage is unused since the popover re-opens on demand). */
  defaultOpen?: boolean;
}

/**
 * Compact topbar pill ("阅读概览 · 连续 X 天 …") that opens the full
 * heatmap inside a popover bubble — keeps the list column uncluttered.
 */
export function ReadingOverviewPanel({
  overview,
  isLoading,
  error,
  onRetry,
  defaultOpen,
}: ReadingOverviewPanelProps) {
  const [open, setOpen] = useState(() => defaultOpen ?? false);

  const summary = overview
    ? `连续 ${overview.activity.current_streak} 天 · 本月 ${overview.activity.month_count} 篇 · 今日 ${overview.activity.today_count} 篇`
    : null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="阅读概览"
          aria-expanded={open}
          className="flex items-center gap-2 rounded-full bg-white/80 px-4 py-2 text-sm text-[#586578] shadow-sm ring-1 ring-black/5 transition hover:bg-white"
        >
          <BookOpenCheck className="h-4 w-4 shrink-0 text-[#16a34a]" aria-hidden="true" />
          <span className="shrink-0 font-medium text-[#172033]">阅读概览</span>
          {summary ? (
            <span className="hidden min-w-0 truncate md:inline">{summary}</span>
          ) : isLoading ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[#94a3b8]" aria-hidden="true" />
          ) : error ? (
            <span className="min-w-0 truncate text-xs text-[#b45309]">加载失败</span>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="w-[26rem] rounded-[24px] p-5" aria-label="阅读概览详情">
        <div className="mb-3 flex items-center gap-2">
          <BookOpenCheck className="h-4 w-4 text-[#16a34a]" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-[#172033]">阅读概览</h2>
          {summary ? (
            <span className="ml-auto text-xs text-[#728095]">{summary}</span>
          ) : null}
        </div>
        {isLoading && !overview ? (
          <ReadingOverviewSkeleton />
        ) : error && !overview ? (
          <div className="rounded-2xl bg-[#fff7ed] p-4 text-center">
            <p className="text-sm text-[#9a3412]">{error}</p>
            <Button variant="outline" size="sm" className="mt-3 rounded-full" onClick={onRetry}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              重新加载
            </Button>
          </div>
        ) : overview ? (
          <ActivityHeatmap activity={overview.activity} timezone={overview.timezone} />
        ) : null}
        {error && overview ? (
          <div className="mt-4 flex items-center justify-between gap-2 pt-3 text-xs text-[#b45309]">
            <span>刷新失败，当前显示上次结果。</span>
            <button type="button" className="font-medium hover:underline" onClick={onRetry}>重试</button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
