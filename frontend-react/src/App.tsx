import { Toaster } from '@/components/ui/sonner';
import { MyPapersPage } from '@/pages/my-papers-page';
import { PaperPage } from '@/pages/paper-page';
import { useAppLocation, navigate } from '@/lib/router';
import { useEffect } from 'react';

function useUnknownRouteFallback(pathname: string) {
  const isKnown = pathname === '/' || pathname === '/me' || pathname.startsWith('/papers/');
  useEffect(() => {
    if (!isKnown) {
      navigate('/', { replace: true });
    }
  }, [isKnown]);
}

function App() {
  const location = useAppLocation();
  const pathname = location.pathname;
  useUnknownRouteFallback(pathname);

  let content = <MyPapersPage />;
  if (pathname.startsWith('/papers/')) {
    const paperId = decodeURIComponent(pathname.replace('/papers/', '').split('/')[0]);
    content = <PaperPage paperId={paperId} />;
  }

  return (
    <div className="min-h-screen bg-[#f3f4f6] text-[#172033]">
      <div className="fixed inset-0 -z-10 bg-[radial-gradient(circle_at_top,_rgba(255,214,107,0.35),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(125,211,252,0.22),_transparent_28%),linear-gradient(180deg,_#f7f9fc_0%,_#eef2f8_100%)]" />
      <main className="px-4 pb-16 pt-8 sm:px-6 sm:pt-10 lg:px-8">{content}</main>
      <Toaster richColors position="top-center" />
    </div>
  );
}

export default App;
