import { describe, expect, it } from 'vitest';

import type { PaperCategory } from '@/types';
import {
  buildCategoryTree,
  canMoveCategory,
  categorySelectionState,
  countAllPapers,
  flattenCategoryTree,
  planCategoryToggle,
  subtreeCategoryIds,
} from './category-tree-utils';

function cat(partial: Partial<PaperCategory> & { id: string; name: string }): PaperCategory {
  return {
    parent_id: null,
    position: 0,
    paper_count: 0,
    ...partial,
  };
}

describe('buildCategoryTree', () => {
  it('builds a two-level tree and aggregates subtree counts', () => {
    const tree = buildCategoryTree([
      cat({ id: 'llm', name: 'LLM 安全', position: 1, paper_count: 5 }),
      cat({ id: 'kd', name: '知识蒸馏', position: 0, paper_count: 3 }),
      cat({ id: 'jailbreak', name: '越狱防御', parent_id: 'llm', paper_count: 4 }),
    ]);

    expect(tree.map((n) => n.name)).toEqual(['知识蒸馏', 'LLM 安全']);
    const llm = tree[1];
    expect(llm.children).toHaveLength(1);
    expect(llm.children[0].name).toBe('越狱防御');
    expect(llm.subtreeCount).toBe(9);
    expect(tree[0].subtreeCount).toBe(3);
  });

  it('promotes orphan nodes to the top level', () => {
    const tree = buildCategoryTree([
      cat({ id: 'a', name: 'A' }),
      cat({ id: 'orphan', name: '孤儿', parent_id: 'missing-parent' }),
    ]);

    expect(tree.map((n) => n.name)).toContain('孤儿');
    expect(tree).toHaveLength(2);
  });

  it('sorts siblings by position then name', () => {
    const tree = buildCategoryTree([
      cat({ id: 'b', name: 'B分类', position: 1 }),
      cat({ id: 'a', name: 'A分类', position: 1 }),
      cat({ id: 'z', name: 'Z分类', position: 0 }),
    ]);

    expect(tree.map((n) => n.name)).toEqual(['Z分类', 'A分类', 'B分类']);
  });

  it('handles an empty list', () => {
    expect(buildCategoryTree([])).toEqual([]);
  });
});

describe('flattenCategoryTree', () => {
  it('produces depth-first rows with path labels', () => {
    const tree = buildCategoryTree([
      cat({ id: 'root', name: '根', position: 0 }),
      cat({ id: 'child', name: '子', parent_id: 'root' }),
      cat({ id: 'grand', name: '孙', parent_id: 'child' }),
      cat({ id: 'other', name: '其他', position: 1 }),
    ]);

    const rows = flattenCategoryTree(tree);
    expect(rows.map((r) => r.pathLabel)).toEqual([
      '根',
      '根 / 子',
      '根 / 子 / 孙',
      '其他',
    ]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 0]);
  });
});

describe('countAllPapers', () => {
  it('sums every subtree', () => {
    const tree = buildCategoryTree([
      cat({ id: 'a', name: 'A', paper_count: 2 }),
      cat({ id: 'a1', name: 'A1', parent_id: 'a', paper_count: 3 }),
      cat({ id: 'b', name: 'B', paper_count: 7 }),
    ]);

    expect(countAllPapers(tree)).toBe(12);
  });
});

describe('subtreeCategoryIds', () => {
  it('collects the node id and all descendants', () => {
    const tree = buildCategoryTree([
      cat({ id: 'root', name: '根' }),
      cat({ id: 'c1', name: '子1', parent_id: 'root' }),
      cat({ id: 'c2', name: '子2', parent_id: 'root' }),
      cat({ id: 'g1', name: '孙1', parent_id: 'c1' }),
    ]);

    const ids = subtreeCategoryIds(tree[0]);
    expect(ids.sort()).toEqual(['root', 'c1', 'c2', 'g1'].sort());
  });
});

describe('canMoveCategory', () => {
  const tree = buildCategoryTree([
    cat({ id: 'root', name: '根' }),
    cat({ id: 'c1', name: '子1', parent_id: 'root' }),
    cat({ id: 'g1', name: '孙1', parent_id: 'c1' }),
    cat({ id: 'other', name: '无关', position: 1 }),
  ]);

  it('rejects dropping onto itself', () => {
    expect(canMoveCategory(tree, 'root', 'root')).toBe(false);
  });

  it('rejects dropping into its own subtree', () => {
    expect(canMoveCategory(tree, 'root', 'c1')).toBe(false);
    expect(canMoveCategory(tree, 'root', 'g1')).toBe(false);
  });

  it('allows moving under an unrelated node', () => {
    expect(canMoveCategory(tree, 'c1', 'other')).toBe(true);
    expect(canMoveCategory(tree, 'root', 'other')).toBe(true);
  });

  it('allows moving a child back under its own ancestor', () => {
    expect(canMoveCategory(tree, 'g1', 'root')).toBe(true);
  });

  it('allows a null target (top level)', () => {
    expect(canMoveCategory(tree, 'c1', null)).toBe(true);
  });

  it('rejects an unknown dragged id', () => {
    expect(canMoveCategory(tree, 'ghost', 'root')).toBe(false);
  });
});

describe('planCategoryToggle', () => {
  const ids = ['p1', 'p2', 'p3'];

  it('selects all when nothing is selected', () => {
    expect(planCategoryToggle(ids, new Set())).toEqual({
      add: ['p1', 'p2', 'p3'],
      remove: [],
    });
  });

  it('deselects everything when anything is selected', () => {
    expect(planCategoryToggle(ids, new Set(['p1', 'p2', 'p3']))).toEqual({
      add: [],
      remove: ['p1', 'p2', 'p3'],
    });
  });

  it('removes only the selected subset when partially selected (overlap regression)', () => {
    // Overlapping chips: another chip already deselected p1/p2; clicking this
    // chip must not re-add them.
    expect(planCategoryToggle(ids, new Set(['p3']))).toEqual({
      add: [],
      remove: ['p3'],
    });
  });

  it('handles an empty subtree', () => {
    expect(planCategoryToggle([], new Set(['p1']))).toEqual({ add: [], remove: [] });
  });
});

describe('categorySelectionState', () => {
  it('returns none for an empty subtree', () => {
    expect(categorySelectionState([], new Set(['p1']))).toBe('none');
  });

  it('returns none / partial / all', () => {
    const ids = ['p1', 'p2', 'p3'];
    expect(categorySelectionState(ids, new Set())).toBe('none');
    expect(categorySelectionState(ids, new Set(['p1']))).toBe('partial');
    expect(categorySelectionState(ids, new Set(['p1', 'p2', 'p3']))).toBe('all');
  });
});
