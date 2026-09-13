import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  createPaperCategory,
  deletePaperCategory,
  fetchPaperCategories,
  movePaperCategory,
  renamePaperCategory,
  setPaperCategoryColor,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { PaperCategory } from '@/types';

interface PaperCategoriesState {
  categories: PaperCategory[];
  uncategorizedCount: number;
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createCategory: (name: string, parentId: string | null) => Promise<PaperCategory | null>;
  renameCategory: (categoryId: string, name: string) => Promise<PaperCategory | null>;
  moveCategory: (categoryId: string, parentId: string | null) => Promise<PaperCategory | null>;
  setCategoryColor: (categoryId: string, color: string | null) => Promise<PaperCategory | null>;
  removeCategory: (categoryId: string) => Promise<number | null>;
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function usePaperCategories(): PaperCategoriesState {
  const { user, isLoading: isAuthLoading } = useAuth();
  const [categories, setCategories] = useState<PaperCategory[]>([]);
  const [uncategorizedCount, setUncategorizedCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async (showLoading = true) => {
    const requestId = ++requestIdRef.current;
    if (!user) {
      setCategories([]);
      setUncategorizedCount(0);
      setError(null);
      setIsLoading(isAuthLoading);
      return;
    }

    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    try {
      const payload = await fetchPaperCategories();
      if (requestId === requestIdRef.current) {
        setCategories(payload.categories);
        setUncategorizedCount(payload.uncategorized_count);
      }
    } catch (requestError) {
      if (requestId === requestIdRef.current) {
        setError(getErrorMessage(requestError, '分类加载失败'));
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [isAuthLoading, user]);

  const refresh = useCallback(() => load(false), [load]);

  useEffect(() => {
    if (isAuthLoading) {
      setIsLoading(true);
      return;
    }
    void load(true);
  }, [isAuthLoading, load]);

  const createCategory = useCallback(async (name: string, parentId: string | null) => {
    try {
      const category = await createPaperCategory(name, parentId);
      await refresh();
      return category;
    } catch (requestError) {
      toast.error(getErrorMessage(requestError, '新建分类失败'));
      return null;
    }
  }, [refresh]);

  const renameCategory = useCallback(async (categoryId: string, name: string) => {
    try {
      const category = await renamePaperCategory(categoryId, name);
      await refresh();
      return category;
    } catch (requestError) {
      toast.error(getErrorMessage(requestError, '重命名失败'));
      return null;
    }
  }, [refresh]);

  const moveCategory = useCallback(async (categoryId: string, parentId: string | null) => {
    try {
      const category = await movePaperCategory(categoryId, parentId);
      await refresh();
      return category;
    } catch (requestError) {
      toast.error(getErrorMessage(requestError, '移动分类失败'));
      return null;
    }
  }, [refresh]);

  const setCategoryColor = useCallback(async (categoryId: string, color: string | null) => {
    try {
      const category = await setPaperCategoryColor(categoryId, color);
      await refresh();
      return category;
    } catch (requestError) {
      toast.error(getErrorMessage(requestError, '修改颜色失败'));
      return null;
    }
  }, [refresh]);

  const removeCategory = useCallback(async (categoryId: string) => {
    try {
      const deletedSubcategories = await deletePaperCategory(categoryId);
      await refresh();
      return deletedSubcategories;
    } catch (requestError) {
      toast.error(getErrorMessage(requestError, '删除分类失败'));
      return null;
    }
  }, [refresh]);

  return {
    categories,
    uncategorizedCount,
    isLoading,
    error,
    refresh,
    createCategory,
    renameCategory,
    moveCategory,
    setCategoryColor,
    removeCategory,
  };
}
