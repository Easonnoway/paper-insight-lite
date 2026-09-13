import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { fetchMe } from '@/lib/api';
import { AuthContext, type AuthContextValue } from '@/lib/auth';
import type { AuthUser } from '@/types';

/**
 * Single-user mode: the backend always resolves /auth/me to the one local
 * account, so "login" is just fetching it. Kept as a provider so existing
 * useAuth() guards keep working unchanged.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = async () => {
    try {
      const payload = await fetchMe();
      setUser(payload.user);
    } catch {
      setUser(null);
    }
  };

  useEffect(() => {
    void refresh().finally(() => setIsLoading(false));
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    isLoading,
    refresh,
  }), [isLoading, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
