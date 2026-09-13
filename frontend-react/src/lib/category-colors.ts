// Category color palette shared by the tree, the card badges and the picker.
// Keys MUST stay in the same order as backend/paper_categories.py's
// CATEGORY_COLOR_PALETTE and db/migrations/026_category_color.sql's backfill
// array. All class strings are static literals — Tailwind's JIT scans source
// text, so never build class names by interpolation.

export const CATEGORY_COLOR_KEYS = [
  'amber',
  'blue',
  'violet',
  'emerald',
  'rose',
  'teal',
  'orange',
  'cyan',
  'pink',
  'slate',
] as const;

export type CategoryColorKey = (typeof CATEGORY_COLOR_KEYS)[number];

export interface CategoryColorStyle {
  border: string;
  bg: string;
  text: string;
  dot: string;
}

export const CATEGORY_COLOR_STYLES: Record<CategoryColorKey, CategoryColorStyle> = {
  amber: {
    border: 'border-[#fde68a]',
    bg: 'bg-[#fffbeb]',
    text: 'text-[#b45309]',
    dot: 'bg-amber-400',
  },
  blue: {
    border: 'border-blue-200',
    bg: 'bg-blue-50',
    text: 'text-blue-700',
    dot: 'bg-blue-400',
  },
  violet: {
    border: 'border-violet-200',
    bg: 'bg-violet-50',
    text: 'text-violet-700',
    dot: 'bg-violet-400',
  },
  emerald: {
    border: 'border-emerald-200',
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    dot: 'bg-emerald-400',
  },
  rose: {
    border: 'border-rose-200',
    bg: 'bg-rose-50',
    text: 'text-rose-700',
    dot: 'bg-rose-400',
  },
  teal: {
    border: 'border-teal-200',
    bg: 'bg-teal-50',
    text: 'text-teal-700',
    dot: 'bg-teal-400',
  },
  orange: {
    border: 'border-orange-200',
    bg: 'bg-orange-50',
    text: 'text-orange-700',
    dot: 'bg-orange-400',
  },
  cyan: {
    border: 'border-cyan-200',
    bg: 'bg-cyan-50',
    text: 'text-cyan-700',
    dot: 'bg-cyan-400',
  },
  pink: {
    border: 'border-pink-200',
    bg: 'bg-pink-50',
    text: 'text-pink-700',
    dot: 'bg-pink-400',
  },
  slate: {
    border: 'border-slate-200',
    bg: 'bg-slate-50',
    text: 'text-slate-700',
    dot: 'bg-slate-400',
  },
};

export const DEFAULT_CATEGORY_COLOR_STYLE = CATEGORY_COLOR_STYLES.amber;

export function getCategoryColorStyle(color?: string | null): CategoryColorStyle {
  if (color && color in CATEGORY_COLOR_STYLES) {
    return CATEGORY_COLOR_STYLES[color as CategoryColorKey];
  }
  return DEFAULT_CATEGORY_COLOR_STYLE;
}

export const CATEGORY_COLOR_LABELS: Record<CategoryColorKey, string> = {
  amber: '琥珀',
  blue: '蓝',
  violet: '紫',
  emerald: '绿',
  rose: '玫红',
  teal: '青',
  orange: '橙',
  cyan: '天蓝',
  pink: '粉',
  slate: '灰',
};
