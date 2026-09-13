import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  downloadLibraryExport,
  exportLibrary,
  fetchLibraryExportInfo,
  importLibrary,
  isLibraryExportFile,
  previewLibraryImport,
} from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('library transfer api', () => {
  it('fetchLibraryExportInfo hits the export-info endpoint', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ papers: [], categories: [] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const info = await fetchLibraryExportInfo();

    expect(info.papers).toEqual([]);
    const [url] = fetchMock.mock.calls[0] as unknown as [unknown];
    expect(String(url)).toContain('/me/library/export-info');
  });

  it('exportLibrary sends paper_ids and returns the payload', async () => {
    const payload = { format: 'paper-insight-lite.export', version: 1, papers: [] };
    const fetchMock = vi.fn(async () => jsonResponse(payload));
    vi.stubGlobal('fetch', fetchMock);

    const result = await exportLibrary(['arxiv:1706.03762']);

    expect(result).toEqual(payload);
    const [, init] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      paper_ids: ['arxiv:1706.03762'],
    });
  });

  it('previewLibraryImport and importLibrary post the payload shape', async () => {
    const payload = { format: 'paper-insight-lite.export', version: 1, papers: [] };
    const fetchMock = vi.fn()
      .mockImplementationOnce(async () =>
        jsonResponse({
          exported_at: null,
          papers: [],
          new_count: 0,
          merge_count: 0,
          categories: [],
          new_category_count: 0,
          reused_category_count: 0,
        }),
      )
      .mockImplementationOnce(async () =>
        jsonResponse({
          created_papers: 1,
          merged_papers: 2,
          created_categories: 0,
          reused_categories: 0,
          assignments_added: 3,
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const preview = await previewLibraryImport(payload);
    expect(preview.new_count).toBe(0);
    const [previewUrl, previewInit] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
    expect(String(previewUrl)).toContain('/me/library/import/preview');
    expect(previewInit.method).toBe('POST');

    const result = await importLibrary(payload, ['a', 'b']);
    expect(result.created_papers).toBe(1);
    expect(result.merged_papers).toBe(2);
    const [importUrl, importInit] = fetchMock.mock.calls[1] as unknown as [unknown, RequestInit];
    expect(String(importUrl)).toContain('/me/library/import');
    expect(JSON.parse(String(importInit.body))).toEqual({
      payload,
      paper_ids: ['a', 'b'],
    });
  });

  it('isLibraryExportFile only accepts matching format', () => {
    expect(isLibraryExportFile({ format: 'paper-insight-lite.export' })).toBe(true);
    expect(isLibraryExportFile({ format: 'other' })).toBe(false);
    expect(isLibraryExportFile(null)).toBe(false);
    expect(isLibraryExportFile('json')).toBe(false);
  });
});

describe('downloadLibraryExport', () => {
  it('triggers a download named paper-library-YYYYMMDD.json', () => {
    const anchor = {
      href: '',
      download: '',
      click: vi.fn(),
      remove: vi.fn(),
    };
    // node 环境（vite.config environment: 'node'）没有 DOM，stub 一个最小 document。
    const fakeDocument = {
      createElement: vi.fn(() => anchor),
      body: { appendChild: vi.fn() },
    };
    vi.stubGlobal('document', fakeDocument);
    const revoke = vi.fn();
    const createObjectURL = vi.fn(() => 'blob:mock');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: revoke }));

    downloadLibraryExport({ format: 'paper-insight-lite.export' }, new Date(2026, 8, 14));

    expect(anchor.download).toBe('paper-library-20260914.json');
    expect(anchor.click).toHaveBeenCalledTimes(1);
    expect(anchor.remove).toHaveBeenCalledTimes(1);
    expect(fakeDocument.body.appendChild).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:mock');
  });
});
