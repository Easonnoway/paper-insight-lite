import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bookmark,
  ChevronDown,
  ChevronLeft,
  ExternalLink,
  Eye,
  FileText,
  Heart,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ActiveModelBadge } from '@/components/active-model-badge';
import { ChatPanel } from '@/components/chat-panel';
import { CodeAvailabilityBadge } from '@/components/code-availability-badge';
import { PaperNoteSection } from '@/components/paper-note-section';
import { ReasoningStreamPanel } from '@/components/reasoning-stream-panel';
import { RichContent } from '@/components/rich-content';
import { apiUrl, fetchAbstractZh, fetchPaperInfo, fetchPaperMarks, paperApiPath, recordPaperOpened, streamSse, updatePaperMark } from '@/lib/api';
import { getVenueParts, normalizeKeywords } from '@/lib/content';
import { navigate } from '@/lib/router';
import type { Paper, PaperMark } from '@/types';

interface PaperPageProps {
  paperId: string;
}

const BACK_BUTTON_FADE_DISTANCE = 72;
const BACK_BUTTON_MAX_TRANSLATE_Y = 8;
const AUTO_VIEWED_DELAY_MS = 10_000;
const EMPTY_MARKS: Pick<PaperMark, 'viewed' | 'liked' | 'favorited' | 'note'> = {
  viewed: false,
  liked: false,
  favorited: false,
  note: null,
};

function buildChatGptUrl(prompt: string) {
  const params = new URLSearchParams({
    hints: 'search',
    q: prompt,
  });
  return `https://chatgpt.com/?${params.toString()}`;
}

type AiTutorTarget = {
  id: string;
  label: string;
  description: string;
  url: string;
  mode: 'link' | 'copy';
};

export function PaperPage({ paperId }: PaperPageProps) {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [abstractZh, setAbstractZh] = useState<string>('');
  const [paperError, setPaperError] = useState<string | null>(null);
  const [paperLoading, setPaperLoading] = useState(true);
  const [analysisText, setAnalysisText] = useState('');
  const [analysisReasoning, setAnalysisReasoning] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('正在获取论文信息...');
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [analysisStreaming, setAnalysisStreaming] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [marks, setMarks] = useState(EMPTY_MARKS);
  const [openedRecorded, setOpenedRecorded] = useState(false);
  const [isLikeAnimating, setIsLikeAnimating] = useState(false);
  const [backButtonProgress, setBackButtonProgress] = useState(0);
  const [openInAiPrompt, setOpenInAiPrompt] = useState('');
  const [openInAiPromptError, setOpenInAiPromptError] = useState<string | null>(null);
  const analysisRequestIdRef = useRef(0);
  const analysisAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let active = true;
    setMarks(EMPTY_MARKS);
    setOpenedRecorded(false);
    void fetchPaperMarks([paperId])
      .then((nextMarks) => {
        if (active) {
          setMarks(nextMarks[paperId] ?? EMPTY_MARKS);
        }
      })
      .catch(() => {
        if (active) {
          setMarks(EMPTY_MARKS);
        }
      });
    return () => {
      active = false;
    };
  }, [paperId]);

  useEffect(() => {
    if (!paper || paperError || openedRecorded) {
      return;
    }

    // Qualifying open = clicked into the detail page and stayed 10 seconds.
    // This only refreshes "recently opened"; the manual "viewed" mark
    // (已看过) is a separate, user-driven flag.
    let active = true;
    const timerId = window.setTimeout(() => {
      void recordPaperOpened(paperId)
        .then((nextMarks) => {
          if (active) {
            setMarks(nextMarks);
            setOpenedRecorded(true);
          }
        })
        .catch(() => {
          // Keep the page quiet; a failed record retries on the next visit.
        });
    }, AUTO_VIEWED_DELAY_MS);

    return () => {
      active = false;
      window.clearTimeout(timerId);
    };
  }, [openedRecorded, paper, paperError, paperId]);

  useEffect(() => {
    const handleScroll = () => {
      const nextProgress = Math.min(Math.max(window.scrollY / BACK_BUTTON_FADE_DISTANCE, 0), 1);
      setBackButtonProgress(nextProgress);
    };

    handleScroll();
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    let active = true;
    setPaperLoading(true);
    setPaperError(null);

    void fetchPaperInfo(paperId)
      .then((payload) => {
        if (active) {
          setPaper(payload);
          void fetchAbstractZh([payload.id])
            .then((map) => { if (active) { setAbstractZh(map[payload.id] || ''); } })
            .catch(() => { /* best-effort */ });
          fetch(apiUrl(paperApiPath(paperId, '/open-in-ai-prompt')))
            .then(async (response) => {
              if (!active) return;
              if (!response.ok) {
                setOpenInAiPromptError('AI 提示词加载失败');
                return;
              }
              const data = (await response.json()) as { prompt?: string };
              if (active && data.prompt) {
                setOpenInAiPrompt(data.prompt);
              }
            })
            .catch(() => { if (active) setOpenInAiPromptError('AI 提示词加载失败'); });
        }
      })
      .catch((error) => {
        if (active) {
          setPaperError(error instanceof Error ? error.message : '加载失败');
          setPaper(null);
        }
      })
      .finally(() => {
        if (active) {
          setPaperLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [paperId]);

  const loadAnalysis = useCallback(async (reanalyze = false) => {
    analysisAbortRef.current?.abort();
    const controller = new AbortController();
    analysisAbortRef.current = controller;
    const requestId = analysisRequestIdRef.current + 1;
    analysisRequestIdRef.current = requestId;

    setAnalysisText('');
    setAnalysisReasoning('');
    setAnalysisError(null);
    setAnalysisLoading(true);
    setAnalysisStreaming(true);
    setAnalysisStatus(reanalyze ? '正在重新分析论文...' : '正在获取论文信息...');

    try {
      await streamSse(
        paperApiPath(paperId, reanalyze ? '?reanalyze=true' : ''),
        { method: 'GET', signal: controller.signal },
        {
          onChunk: (chunk) => {
            if (analysisRequestIdRef.current !== requestId) {
              return;
            }
            setAnalysisLoading(false);
            setAnalysisText((current) => current + chunk);
          },
          onEvent: (event, data) => {
            if (analysisRequestIdRef.current !== requestId) {
              return;
            }
            if (event === 'status') {
              setAnalysisStatus(data);
            }
            if (event === 'reasoning') {
              setAnalysisLoading(false);
              setAnalysisReasoning((current) => current + data);
            }
            if (event === 'final') {
              setAnalysisText(data);
            }
            if (event === 'error') {
              setAnalysisLoading(false);
              setAnalysisStreaming(false);
              setAnalysisReasoning('');
              setAnalysisError(data || '分析失败');
            }
            if (event === 'done') {
              setAnalysisLoading(false);
              setAnalysisStreaming(false);
              setAnalysisReasoning('');
              setAnalysisStatus('');
              void fetchPaperInfo(paperId).then(setPaper).catch(() => {
                // The analysis result is already available; keep the existing metadata if refresh fails.
              });
            }
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setAnalysisLoading(false);
      setAnalysisStreaming(false);
      setAnalysisReasoning('');
      setAnalysisError(error instanceof Error ? error.message : '分析失败');
    } finally {
      if (analysisAbortRef.current === controller) {
        analysisAbortRef.current = null;
      }
    }
  }, [paperId]);

  useEffect(() => {
    void loadAnalysis(false);
    return () => {
      analysisAbortRef.current?.abort();
      analysisAbortRef.current = null;
    };
  }, [loadAnalysis]);

  const venue = getVenueParts(paper?.venue);
  const keywords = normalizeKeywords(paper?.keywords);
  const storedPdfUrl = paper?.pdf;
  const pdfProxyUrl = storedPdfUrl && storedPdfUrl.startsWith('http') ? storedPdfUrl : apiUrl(`/paper/${paperId}/pdf`);
  const isBackButtonHidden = backButtonProgress >= 1;
  const backButtonOpacity = 1 - backButtonProgress;

  const aiTutorTargets: AiTutorTarget[] = openInAiPrompt ? [
    {
      id: 'kimi',
      label: 'Kimi',
      description: '使用相同提示词并自动发送',
      url: `https://www.kimi.com/?prefill_prompt=${encodeURIComponent(openInAiPrompt)}&send_immediately=true`,
      mode: 'link',
    },
    {
      id: 'openai',
      label: 'OpenAI ChatGPT',
      description: '使用 ChatGPT Search 深链',
      url: buildChatGptUrl(openInAiPrompt),
      mode: 'link',
    },
    {
      id: 'gemini',
      label: 'Gemini',
      description: '复制提示词并打开 Gemini，再粘贴',
      url: 'https://gemini.google.com/app',
      mode: 'copy',
    },
    {
      id: 'dola',
      label: 'Dola',
      description: '复制提示词并打开 Dola，再粘贴',
      url: 'https://www.dola.com/chat/',
      mode: 'copy',
    },
  ] : [];

  const copyPromptAndOpen = async (url: string) => {
    try {
      await navigator.clipboard.writeText(openInAiPrompt);
      toast.success('提示词已复制，去目标页 ⌘V / Ctrl+V 粘贴');
    } catch {
      toast.error('复制失败，请手动复制提示词');
    }
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const renderTargetInner = (target: AiTutorTarget) => (
    <>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="font-medium text-[#172033]">{target.label}</span>
        <span className="truncate text-xs text-[#728095]">{target.description}</span>
      </div>
      <ExternalLink className="h-3.5 w-3.5 text-[#8a98ac]" />
    </>
  );

  return (
    <div className="mx-auto max-w-[96rem] animate-fade-in px-4 sm:px-6 lg:px-8">
      <div
        className="mb-4 origin-left transition-[opacity,transform] duration-150"
        style={{
          opacity: backButtonOpacity,
          transform: `translateY(${-BACK_BUTTON_MAX_TRANSLATE_Y * backButtonProgress}px)`,
          pointerEvents: isBackButtonHidden ? 'none' : 'auto',
        }}
      >
        <Button
          variant="ghost"
          className="rounded-full px-0 text-[#728095]"
          tabIndex={isBackButtonHidden ? -1 : 0}
          onClick={() => {
            if (window.history.length > 1) {
              window.history.back();
              return;
            }
            navigate('/');
          }}
        >
          <ChevronLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
      </div>

      <div className="grid gap-6 xl:items-start xl:grid-cols-[minmax(0,1.55fr)_minmax(24rem,0.95fr)] 2xl:grid-cols-[minmax(0,1.7fr)_minmax(26rem,1.02fr)]">
      <div className="space-y-6 min-w-0">
        <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5">
            {paperLoading ? (
              <div className="flex items-center gap-2 text-[#728095]">
                <Loader2 className="h-5 w-5 animate-spin" />
                加载论文信息...
              </div>
            ) : paperError ? (
              <div className="text-[#b91c1c]">{paperError}</div>
            ) : paper ? (
              <div className="space-y-6">
                <div>
                  <h1 className="text-3xl font-semibold leading-tight text-[#172033]">
                    <RichContent content={paper.title} inline className="paper-title-math" />
                  </h1>
                  <div className="mt-4 flex flex-wrap gap-2.5">
                    <Badge variant="outline" className="border-blue-200 bg-blue-50 px-3 py-1 text-sm text-blue-700">
                      {venue.label}
                    </Badge>
                    {paper.primary_area ? (
                      <Badge
                        variant="outline"
                        className="border-[#e6ebf2] bg-[#f8fafc] px-3 py-1 text-sm text-[#516072]"
                      >
                        {paper.primary_area}
                      </Badge>
                    ) : null}
                    <CodeAvailabilityBadge
                      status={paper.code_status}
                      codeUrl={paper.code_url}
                      className="px-3 py-1 text-sm"
                    />
                    {keywords.slice(0, 6).map((keyword, index) => {
                      const className =
                        index % 2 === 0
                          ? 'border-orange-100 bg-orange-50 px-3 py-1 text-sm text-orange-700'
                          : 'border-violet-100 bg-violet-50 px-3 py-1 text-sm text-violet-700';
                      return (
                        <Badge key={`${paper.id}-${keyword}`} variant="outline" className={className}>
                          {keyword}
                        </Badge>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-[#8a98ac]">Abstract</h2>
                  <RichContent content={abstractZh || paper.abstract || '暂无摘要'} className="markdown-body text-base leading-7 text-[#475569]" />
                </div>

                <div className="flex flex-wrap gap-2">
                  <a href={pdfProxyUrl} target="_blank" rel="noreferrer">
                    <Button variant="outline" className="rounded-full border-[#bfdbfe] bg-[#eff6ff] text-[#2563eb]">
                      <FileText className="mr-1.5 h-4 w-4" />
                      PDF
                    </Button>
                  </a>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="outline"
                        className="rounded-full border-[#d8b4fe] bg-[#faf5ff] text-[#9333ea]"
                        disabled={!openInAiPrompt}
                        title={openInAiPromptError ?? (openInAiPrompt ? 'Open in AI' : '正在加载 AI 提示词')}
                      >
                        <Sparkles className="mr-1.5 h-4 w-4" />
                        {openInAiPrompt ? 'Open in AI' : '加载 AI 提示词'}
                        <ChevronDown className="ml-0.5 h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="w-56 rounded-2xl p-1.5">
                      {aiTutorTargets.map((target) => {
                        if (target.mode === 'copy') {
                          return (
                            <DropdownMenuItem
                              key={target.id}
                              onSelect={() => { void copyPromptAndOpen(target.url); }}
                              className="cursor-pointer rounded-xl px-3 py-2.5"
                            >
                              {renderTargetInner(target)}
                            </DropdownMenuItem>
                          );
                        }
                        return (
                          <DropdownMenuItem key={target.id} asChild className="cursor-pointer rounded-xl px-3 py-2.5">
                            <a href={target.url} target="_blank" rel="noreferrer">
                              {renderTargetInner(target)}
                            </a>
                          </DropdownMenuItem>
                        );
                      })}
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.viewed
                        ? 'border-[#bfdbfe] bg-[#eff6ff] text-[#2563eb]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      void updatePaperMark(paperId, { viewed: !marks.viewed }).then(setMarks);
                    }}
                  >
                    <Eye className={`mr-1.5 h-4 w-4 ${marks.viewed ? 'fill-current' : ''}`} />
                    {marks.viewed ? '已看过' : '看过'}
                  </Button>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.liked
                        ? 'border-[#fecaca] bg-[#fff1f2] text-[#e11d48]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      setIsLikeAnimating(true);
                      void updatePaperMark(paperId, { liked: !marks.liked }).then(setMarks);
                      window.setTimeout(() => setIsLikeAnimating(false), 400);
                    }}
                  >
                    <Heart className={`mr-1.5 h-4 w-4 ${isLikeAnimating ? 'animate-heart-beat' : ''} ${marks.liked ? 'fill-current' : ''}`} />
                    {marks.liked ? '已点赞' : '点赞'}
                  </Button>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.favorited
                        ? 'border-[#fed7aa] bg-[#fff7ed] text-[#ea580c]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      void updatePaperMark(paperId, { favorited: !marks.favorited }).then(setMarks);
                    }}
                  >
                    <Bookmark className={`mr-1.5 h-4 w-4 ${marks.favorited ? 'fill-current' : ''}`} />
                    {marks.favorited ? '已收藏' : '收藏'}
                  </Button>
                </div>
              </div>
            ) : null}
        </section>

        <PaperNoteSection
          paperId={paperId}
          initialNote={marks.note}
          onNoteChanged={setMarks}
        />

        <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5">
            <div className="flex flex-col gap-3 border-b border-[#eef2f7] pb-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex shrink-0 items-center gap-2">
                <Sparkles className="h-5 w-5 text-[#ff9900]" />
                <div>
                  <h2 className="whitespace-nowrap text-xl font-semibold text-[#172033]">AI 分析</h2>
                  {analysisStatus ? <p className="text-sm text-[#728095]">{analysisStatus}</p> : null}
                </div>
              </div>
              <div className="flex max-w-full flex-wrap items-center gap-2">
                <ActiveModelBadge className="max-w-[18rem]" />
                <Button
                  variant="outline"
                  className="rounded-full"
                  onClick={() => void loadAnalysis(true)}
                >
                  重新分析
                </Button>
              </div>
            </div>

            {analysisLoading ? (
              <div className="mt-6 flex items-center gap-2 text-[#728095]">
                <Loader2 className="h-5 w-5 animate-spin" />
                {analysisStatus || '正在分析论文...'}
              </div>
            ) : analysisError ? (
              <div className="mt-6 rounded-2xl bg-[#fff1f2] p-4 text-[#b91c1c]">{analysisError}</div>
            ) : (
              <div className="mt-6 space-y-4">
                <ReasoningStreamPanel reasoning={analysisStreaming ? analysisReasoning : ''} />
                {analysisText ? (
                  <RichContent
                    content={analysisText}
                    analysisMode
                    isStreaming={analysisStreaming}
                    className="markdown-body analysis-markdown text-base leading-7 text-[#334155]"
                  />
                ) : null}
              </div>
            )}
        </section>
      </div>

      <div className="xl:sticky xl:top-6 xl:self-start">
        <ChatPanel key={paperId} paperId={paperId} />
      </div>
      </div>
    </div>
  );
}
