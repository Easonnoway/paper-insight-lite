import { useCallback, useRef, useState } from 'react';
import { CalendarClock, DatabaseBackup, Loader2, Play, Save } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import {
  fetchAutoBackupSettings,
  runAutoBackupNow,
  updateAutoBackupSettings,
} from '@/lib/api';
import type {
  AutoBackupSettings,
  BackupIntervalKind,
} from '@/types';

export const BACKUP_INTERVAL_OPTIONS = [
  { value: 'daily', label: '每天' },
  { value: 'every_3_days', label: '每 3 天' },
  { value: 'weekly', label: '每周' },
] as const satisfies readonly { value: BackupIntervalKind; label: string }[];

function formatBackupTime(value: string | null): string {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusBadge({ settings }: { settings: AutoBackupSettings | null }) {
  if (!settings || !settings.last_backup_at) {
    return <Badge variant="outline" className="border-[#e6ebf2] bg-[#f8fafc] text-[#728095]">尚未备份</Badge>;
  }
  if (settings.last_backup_status === 'success') {
    return (
      <Badge variant="outline" className="border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]">
        上次成功
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-[#fecaca] bg-[#fff1f2] text-[#b91c1c]">
      上次失败
    </Badge>
  );
}

const fieldLabelClass = 'text-xs font-medium text-[#516072]';
const fieldInputClass =
  'rounded-xl border-[#e6ebf2] bg-white text-sm text-[#172033] placeholder:text-[#9aa6b8]';

/**
 * "自动备份" tab of the library transfer dialog: settings form + status
 * display + manual run-now, backed by user_auto_backup_settings.
 */
export function AutoBackupTab({ open }: { open: boolean }) {
  const [settings, setSettings] = useState<AutoBackupSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [directory, setDirectory] = useState('');
  const [intervalKind, setIntervalKind] = useState<BackupIntervalKind>('daily');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const loadedRef = useRef(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const next = await fetchAutoBackupSettings();
      setSettings(next);
      setEnabled(next.enabled);
      setDirectory(next.directory);
      setIntervalKind(next.interval_kind);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载备份设置失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  if (open && !loadedRef.current && !isLoading && !error) {
    loadedRef.current = true;
    void load();
  }
  if (!open && loadedRef.current) {
    loadedRef.current = false;
  }

  const saveSettings = async () => {
    if (isSaving) {
      return;
    }
    setIsSaving(true);
    try {
      const next = await updateAutoBackupSettings({
        enabled,
        directory: directory.trim(),
        interval_kind: intervalKind,
      });
      setSettings(next);
      toast.success(enabled ? '备份设置已保存' : '已保存（自动备份未启用）');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存备份设置失败');
    } finally {
      setIsSaving(false);
    }
  };

  const runNow = async () => {
    if (isRunning) {
      return;
    }
    setIsRunning(true);
    try {
      const result = await runAutoBackupNow();
      toast.success(`已备份到 ${result.file_name}`);
      const next = await fetchAutoBackupSettings();
      setSettings(next);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '备份失败');
    } finally {
      setIsRunning(false);
    }
  };

  if (isLoading && !settings) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-[#728095]" role="status">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载备份设置...
      </div>
    );
  }
  if (error && !settings) {
    return (
      <div className="py-8 text-center text-sm text-[#b91c1c]" role="alert">
        {error}
        <Button variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => { loadedRef.current = false; void load(); }}>
          重新加载
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 rounded-2xl bg-[#f8fafc] px-4 py-3">
        <div className="flex items-center gap-2.5">
          <DatabaseBackup className="h-4 w-4 shrink-0 text-[#16a34a]" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-[#172033]">自动备份</p>
            <p className="text-xs text-[#728095]">按间隔把全部论文导出到指定目录（保留最近 10 份）</p>
          </div>
        </div>
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="启用自动备份" />
      </div>

      <div className="space-y-1.5">
        <label className={fieldLabelClass} htmlFor="auto-backup-directory">备份目录（绝对路径）</label>
        <Input
          id="auto-backup-directory"
          value={directory}
          onChange={(event) => setDirectory(event.target.value)}
          placeholder="/Users/you/Backups/paper-library"
          className={fieldInputClass}
        />
      </div>

      <div className="space-y-1.5">
        <label className={fieldLabelClass}>备份间隔</label>
        <Select value={intervalKind} onValueChange={(value) => setIntervalKind(value as BackupIntervalKind)}>
          <SelectTrigger className="w-40 rounded-xl border-[#e6ebf2] bg-white text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {BACKUP_INTERVAL_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2 rounded-2xl bg-[#f8fafc] px-4 py-3">
        <div className="flex items-center gap-2 text-sm text-[#334155]">
          <CalendarClock className="h-4 w-4 shrink-0 text-[#728095]" aria-hidden="true" />
          <span className="text-xs text-[#728095]">上次备份</span>
          <span className="text-xs">{settings?.last_backup_at ? formatBackupTime(settings.last_backup_at) : '—'}</span>
          <StatusBadge settings={settings} />
        </div>
        {settings?.last_backup_status === 'failed' && settings.last_backup_error ? (
          <p className="truncate text-xs text-[#b91c1c]" title={settings.last_backup_error}>
            {settings.last_backup_error}
          </p>
        ) : null}
        <div className="flex items-center gap-2 text-xs text-[#728095]">
          <span>下次备份</span>
          <span>{settings?.enabled && settings.next_backup_at ? formatBackupTime(settings.next_backup_at) : '未启用'}</span>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          className="rounded-full"
          disabled={isRunning || !directory.trim()}
          onClick={() => { void runNow(); }}
          title={directory.trim() ? '立即导出一次全部论文' : '请先填写备份目录'}
        >
          {isRunning ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-1 h-3.5 w-3.5" />}
          立即备份一次
        </Button>
        <Button
          size="sm"
          className="rounded-full"
          disabled={isSaving}
          onClick={() => { void saveSettings(); }}
        >
          {isSaving ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1 h-3.5 w-3.5" />}
          保存设置
        </Button>
      </div>
    </div>
  );
}
