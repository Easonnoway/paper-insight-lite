import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

// AutoBackupTab triggers its initial load during render (loadedRef pattern),
// so the API module must be mocked before importing the component.
vi.mock('@/lib/api', () => ({
  fetchAutoBackupSettings: vi.fn().mockResolvedValue({
    enabled: false,
    directory: '',
    interval_kind: 'daily',
    last_backup_at: null,
    last_backup_status: null,
    last_backup_error: null,
    next_backup_at: null,
  }),
  updateAutoBackupSettings: vi.fn(),
  runAutoBackupNow: vi.fn(),
}));

import { AutoBackupTab, BACKUP_INTERVAL_OPTIONS } from './auto-backup-tab';

describe('BACKUP_INTERVAL_OPTIONS', () => {
  it('matches the backend CHECK constraint values', () => {
    expect(BACKUP_INTERVAL_OPTIONS.map((o) => o.value)).toEqual([
      'daily',
      'every_3_days',
      'weekly',
    ]);
    expect(BACKUP_INTERVAL_OPTIONS.map((o) => o.label)).toEqual([
      '每天',
      '每 3 天',
      '每周',
    ]);
  });
});

describe('AutoBackupTab', () => {
  it('renders the settings form with key actions when closed', () => {
    // Closed = no load triggered, so the form renders its default state
    // synchronously (the open state first shows the loading skeleton).
    const html = renderToStaticMarkup(<AutoBackupTab open={false} />);

    expect(html).toContain('自动备份');
    expect(html).toContain('备份目录');
    expect(html).toContain('备份间隔');
    expect(html).toContain('立即备份一次');
    expect(html).toContain('保存设置');
    expect(html).toContain('尚未备份');
    expect(html).toContain('未启用');
  });

  it('shows the loading skeleton while the settings load', () => {
    const html = renderToStaticMarkup(<AutoBackupTab open />);

    expect(html).toContain('加载备份设置');
  });
});
