import { describe, expect, it } from 'vitest';

import {
  CATEGORY_COLOR_KEYS,
  CATEGORY_COLOR_STYLES,
  DEFAULT_CATEGORY_COLOR_STYLE,
  getCategoryColorStyle,
} from '@/lib/category-colors';

describe('category-colors', () => {
  it('covers every palette key with full class strings', () => {
    expect(CATEGORY_COLOR_KEYS).toHaveLength(10);
    for (const key of CATEGORY_COLOR_KEYS) {
      const style = CATEGORY_COLOR_STYLES[key];
      expect(style.border).toMatch(/^border-/);
      expect(style.bg).toMatch(/^bg-/);
      expect(style.text).toMatch(/^text-/);
      expect(style.dot).toMatch(/^bg-/);
    }
  });

  it('falls back to amber for null, undefined and unknown keys', () => {
    expect(getCategoryColorStyle(null)).toBe(DEFAULT_CATEGORY_COLOR_STYLE);
    expect(getCategoryColorStyle(undefined)).toBe(DEFAULT_CATEGORY_COLOR_STYLE);
    expect(getCategoryColorStyle('neon-green')).toBe(DEFAULT_CATEGORY_COLOR_STYLE);
    expect(DEFAULT_CATEGORY_COLOR_STYLE).toBe(CATEGORY_COLOR_STYLES.amber);
  });

  it('returns the matching style for each known key', () => {
    expect(getCategoryColorStyle('teal')).toBe(CATEGORY_COLOR_STYLES.teal);
    expect(getCategoryColorStyle('violet')).toBe(CATEGORY_COLOR_STYLES.violet);
    expect(getCategoryColorStyle('slate')).toBe(CATEGORY_COLOR_STYLES.slate);
  });

  it('keeps the legacy amber pill classes byte-identical', () => {
    // The default must match the pre-color hardcoded badge classes so
    // existing cards do not visually change.
    expect(CATEGORY_COLOR_STYLES.amber).toEqual({
      border: 'border-[#fde68a]',
      bg: 'bg-[#fffbeb]',
      text: 'text-[#b45309]',
      dot: 'bg-amber-400',
    });
  });
});
