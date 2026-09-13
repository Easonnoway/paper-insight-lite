import { useCallback, useEffect, useState } from 'react';
import { Check, Cpu, KeyRound, Loader2, Pencil, Plus, Settings2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  createLlmProvider,
  fetchLlmProviders,
  setActiveLlm,
  updateLlmProviderApiKey,
  type LlmProviderInfo,
} from '@/lib/api';

interface ModelSettingsDialogProps {
  /** Re-render trigger so the badge next to this icon stays fresh. */
  onActiveChanged?: () => void;
  className?: string;
}

export function ModelSettingsDialog({ onActiveChanged, className = '' }: ModelSettingsDialogProps) {
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<LlmProviderInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [applyingKey, setApplyingKey] = useState<string | null>(null);
  const [isAddingProvider, setIsAddingProvider] = useState(false);
  // providerId -> picked model name (falls back to the provider's active model)
  const [pickedModels, setPickedModels] = useState<Record<string, string>>({});
  // api-key editing state: providerId currently being edited + draft values
  const [editingKeyId, setEditingKeyId] = useState<string | null>(null);
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});
  const [savingKeyId, setSavingKeyId] = useState<string | null>(null);

  const loadProviders = useCallback(async () => {
    setIsLoading(true);
    try {
      const payload = await fetchLlmProviders();
      setProviders(payload);
      setPickedModels((prev) => {
        const next = { ...prev };
        for (const provider of payload) {
          if (!next[provider.id]) {
            next[provider.id] = provider.active_model
              || provider.models.find((m) => m.is_enabled)?.model_name
              || '';
          }
        }
        return next;
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '供应商列表加载失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      void loadProviders();
    }
  }, [open, loadProviders]);

  const applyProvider = async (provider: LlmProviderInfo) => {
    const key = provider.id;
    setApplyingKey(key);
    try {
      await setActiveLlm(provider.id, pickedModels[provider.id] || null);
      await loadProviders();
      onActiveChanged?.();
      toast.success(`已切换到 ${provider.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '切换失败');
    } finally {
      setApplyingKey(null);
    }
  };

  const startEditKey = (provider: LlmProviderInfo) => {
    setEditingKeyId(provider.id);
    setApiKeyDrafts((prev) => ({ ...prev, [provider.id]: '' }));
  };

  const saveKey = async (provider: LlmProviderInfo) => {
    const draft = (apiKeyDrafts[provider.id] ?? '').trim();
    if (!draft) {
      toast.error('请输入 API Key（留空保存会清除已有 Key）');
      return;
    }
    setSavingKeyId(provider.id);
    try {
      await updateLlmProviderApiKey(provider.id, draft);
      await loadProviders();
      onActiveChanged?.();
      setEditingKeyId(null);
      toast.success(`${provider.name} 的 API Key 已保存`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSavingKeyId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          title="模型设置"
          aria-label="模型设置"
          className={`inline-flex h-9 w-9 items-center justify-center rounded-full border border-[#e6ebf2] bg-white/80 text-[#64748b] shadow-sm backdrop-blur transition hover:border-[#fed7aa] hover:bg-[#fff7ed] hover:text-[#f08300] ${className}`}
        >
          <Settings2 className="h-4 w-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-md overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-[#f08300]" />
            模型设置
          </DialogTitle>
          <DialogDescription>
            选择 AI 分析、对话、分类使用的供应商和模型，也可以直接填写各家 API Key。
          </DialogDescription>
        </DialogHeader>

        {isLoading && providers.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-[#728095]">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载供应商...
          </div>
        ) : (
          <div className="space-y-2">
            {providers.map((provider) => {
              const usable = provider.is_enabled && provider.has_api_key;
              const isApplying = applyingKey === provider.id;
              const isEditingKey = editingKeyId === provider.id;
              const isSavingKey = savingKeyId === provider.id;
              return (
                <div
                  key={provider.id}
                  className={`rounded-2xl border p-3 transition ${
                    provider.is_active
                      ? 'border-[#fed7aa] bg-gradient-to-r from-[#fff7ed] to-white'
                      : 'border-[#e6ebf2] bg-white'
                  } ${usable ? '' : 'opacity-55'}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold text-[#172033]">{provider.name}</span>
                        {provider.is_active ? (
                          <span className="shrink-0 rounded-full bg-[#f0fdf4] px-2 py-0.5 text-[10px] font-semibold text-[#15803d]">
                            当前
                          </span>
                        ) : null}
                        {!provider.has_api_key ? (
                          <span className="shrink-0 rounded-full bg-[#f8fafc] px-2 py-0.5 text-[10px] text-[#94a3b8]">
                            无 Key
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-[#8a96a8]" title={provider.base_url}>
                        {provider.base_url}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8 rounded-full text-[#64748b] hover:bg-[#f8fafc] hover:text-[#f08300]"
                        title={provider.has_api_key ? `已保存 ${provider.api_key_masked ?? ''}，点击修改` : '填写 API Key'}
                        aria-label={`编辑 ${provider.name} API Key`}
                        disabled={isSavingKey}
                        onClick={() => (isEditingKey ? setEditingKeyId(null) : startEditKey(provider))}
                      >
                        {isEditingKey ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                      </Button>
                      <Button
                        size="sm"
                        variant={provider.is_active ? 'outline' : 'default'}
                        className="rounded-full"
                        disabled={!usable || isApplying || provider.is_active}
                        onClick={() => { void applyProvider(provider); }}
                      >
                        {isApplying ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : provider.is_active ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          '启用'
                        )}
                      </Button>
                    </div>
                  </div>

                  {isEditingKey ? (
                    <div className="mt-2.5">
                      <div className="flex items-center gap-2 rounded-full border border-[#e6ebf2] bg-white px-3 py-1.5">
                        <KeyRound className="h-3.5 w-3.5 shrink-0 text-[#94a3b8]" />
                        <input
                          type="password"
                          autoFocus
                          value={apiKeyDrafts[provider.id] ?? ''}
                          onChange={(event) =>
                            setApiKeyDrafts((prev) => ({ ...prev, [provider.id]: event.target.value }))
                          }
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              void saveKey(provider);
                            }
                            if (event.key === 'Escape') {
                              setEditingKeyId(null);
                            }
                          }}
                          placeholder={provider.has_api_key
                            ? `当前 ${provider.api_key_masked ?? '已配置'}，输入新 Key 覆盖`
                            : '粘贴 API Key（sk-...）'}
                          className="min-w-0 flex-1 bg-transparent text-sm text-[#172033] outline-none placeholder:text-[#9aa6b8]"
                        />
                        <Button
                          size="sm"
                          className="h-7 shrink-0 rounded-full px-3"
                          disabled={isSavingKey || !(apiKeyDrafts[provider.id] ?? '').trim()}
                          onClick={() => { void saveKey(provider); }}
                        >
                          {isSavingKey ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '保存'}
                        </Button>
                      </div>
                      <p className="mt-1.5 text-[11px] text-[#8a96a8]">
                        Key 只保存在本地数据库，不会上传。Esc 取消编辑。
                      </p>
                    </div>
                  ) : null}

                  {provider.models.length > 0 && !isEditingKey ? (
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {provider.models
                        .filter((model) => model.is_enabled)
                        .map((model) => {
                          const picked = (pickedModels[provider.id] || provider.active_model) === model.model_name;
                          return (
                            <button
                              key={model.model_name}
                              type="button"
                              disabled={!usable}
                              onClick={() => {
                                setPickedModels((prev) => ({ ...prev, [provider.id]: model.model_name }));
                                if (provider.is_active) {
                                  void setActiveLlm(provider.id, model.model_name)
                                    .then(() => {
                                      void loadProviders();
                                      onActiveChanged?.();
                                      toast.success(`已切换到 ${model.model_name}`);
                                    })
                                    .catch((err) => {
                                      toast.error(err instanceof Error ? err.message : '切换失败');
                                    });
                                }
                              }}
                              title={`使用 ${model.model_name}`}
                              className={`rounded-full border px-2.5 py-1 text-xs transition disabled:cursor-not-allowed ${
                                picked
                                  ? 'border-[#ff9900] bg-[#fff7ed] font-medium text-[#c2410c]'
                                  : 'border-[#e6ebf2] bg-white text-[#516072] hover:border-[#fdba74] hover:text-[#c2410c]'
                              }`}
                            >
                              {model.model_name}
                            </button>
                          );
                        })}
                    </div>
                  ) : null}
                </div>
              );
            })}
            {providers.length === 0 && !isLoading && !isAddingProvider ? (
              <p className="rounded-2xl bg-[#f8fafc] px-4 py-6 text-center text-sm text-[#728095]">
                还没有供应商。点击下方「添加供应商」填入 API Key 即可开始使用。
              </p>
            ) : null}
            {isAddingProvider ? (
              <AddProviderForm
                onDone={async () => {
                  setIsAddingProvider(false);
                  await loadProviders();
                }}
                onCancel={() => setIsAddingProvider(false)}
              />
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="w-full rounded-full border-dashed text-[#516072] hover:border-[#fdba74] hover:text-[#c2410c]"
                onClick={() => setIsAddingProvider(true)}
              >
                <Plus className="h-3.5 w-3.5" />
                添加供应商
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function AddProviderForm({
  onDone,
  onCancel,
}: {
  onDone: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const submit = async () => {
    if (!name.trim() || !baseUrl.trim()) {
      toast.error('请填写供应商名称和 Base URL');
      return;
    }
    setIsSaving(true);
    try {
      await createLlmProvider({
        provider_key: `custom-${name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}`,
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || null,
        active_model: model.trim() || null,
      });
      toast.success(`已添加 ${name.trim()}，填好 Key 后点「启用」`);
      await onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsSaving(false);
    }
  };

  const fieldClass =
    'w-full rounded-xl border border-[#e6ebf2] bg-white px-3 py-2 text-sm text-[#172033] outline-none placeholder:text-[#9aa6b8] focus:border-[#fdba74]';

  return (
    <div className="space-y-2 rounded-2xl border border-[#fed7aa] bg-[#fff7ed]/60 p-3">
      <p className="text-sm font-semibold text-[#172033]">添加自定义供应商</p>
      <input
        className={fieldClass}
        placeholder="名称（如 硅基流动 / 本地 vLLM）"
        value={name}
        onChange={(event) => setName(event.target.value)}
        autoFocus
      />
      <input
        className={fieldClass}
        placeholder="Base URL（如 https://api.siliconflow.cn/v1）"
        value={baseUrl}
        onChange={(event) => setBaseUrl(event.target.value)}
      />
      <input
        className={fieldClass}
        placeholder="API Key（可稍后在列表里填）"
        type="password"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
      />
      <input
        className={fieldClass}
        placeholder="默认模型名（如 deepseek-chat，可留空）"
        value={model}
        onChange={(event) => setModel(event.target.value)}
      />
      <div className="flex items-center justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" className="rounded-full" disabled={isSaving} onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" className="rounded-full" disabled={isSaving} onClick={() => { void submit(); }}>
          {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '添加'}
        </Button>
      </div>
    </div>
  );
}
