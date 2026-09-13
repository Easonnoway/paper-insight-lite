import type { PaperCategory } from '@/types';

export interface CategoryTreeNode {
  id: string;
  parentId: string | null;
  name: string;
  position: number;
  /** Papers assigned directly to this category. */
  paperCount: number;
  /** Papers in this category or any descendant (deduplicated by count
   * semantics upstream: the API already counts distinct papers per node, so
   * subtree aggregation is a plain sum of direct counts). */
  subtreeCount: number;
  color: string | null;
  children: CategoryTreeNode[];
}

/**
 * Build a tree from the flat category list. Nodes whose parent_id points to
 * a missing category (e.g. the parent was deleted by another client in a
 * race) are promoted to the top level instead of disappearing.
 */
export function buildCategoryTree(
  categories: Array<Omit<PaperCategory, 'paper_count'> & { paper_count?: number }>,
): CategoryTreeNode[] {
  const nodesById = new Map<string, CategoryTreeNode>();
  for (const cat of categories) {
    nodesById.set(cat.id, {
      id: cat.id,
      parentId: cat.parent_id,
      name: cat.name,
      position: cat.position,
      paperCount: cat.paper_count ?? 0,
      subtreeCount: cat.paper_count ?? 0,
      color: cat.color ?? null,
      children: [],
    });
  }

  const roots: CategoryTreeNode[] = [];
  for (const node of nodesById.values()) {
    if (node.parentId && nodesById.has(node.parentId)) {
      nodesById.get(node.parentId)!.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const sortNodes = (nodes: CategoryTreeNode[]): CategoryTreeNode[] => {
    nodes.sort((a, b) => a.position - b.position || a.name.localeCompare(b.name, 'zh'));
    for (const node of nodes) {
      sortNodes(node.children);
    }
    return nodes;
  };
  sortNodes(roots);

  const sumSubtree = (node: CategoryTreeNode): number => {
    node.subtreeCount = node.paperCount + node.children.reduce(
      (total, child) => total + sumSubtree(child),
      0,
    );
    return node.subtreeCount;
  };
  roots.forEach(sumSubtree);

  return roots;
}

export interface FlatCategoryRow {
  node: CategoryTreeNode;
  depth: number;
  /** Display label with ancestor path, e.g. "LLM 安全 / 越狱防御". */
  pathLabel: string;
}

/** Depth-first flatten for selects and pickers. */
export function flattenCategoryTree(nodes: CategoryTreeNode[]): FlatCategoryRow[] {
  const rows: FlatCategoryRow[] = [];
  const walk = (list: CategoryTreeNode[], depth: number, prefix: string) => {
    for (const node of list) {
      const pathLabel = prefix ? `${prefix} / ${node.name}` : node.name;
      rows.push({ node, depth, pathLabel });
      walk(node.children, depth + 1, pathLabel);
    }
  };
  walk(nodes, 0, '');
  return rows;
}

/** Total paper count shown in the tree header ("全部"). */
export function countAllPapers(nodes: CategoryTreeNode[]): number {
  return nodes.reduce((total, node) => total + node.subtreeCount, 0);
}

/** All category ids inside a node's subtree (including itself). */
export function subtreeCategoryIds(node: CategoryTreeNode): string[] {
  const ids = [node.id];
  for (const child of node.children) {
    ids.push(...subtreeCategoryIds(child));
  }
  return ids;
}

/**
 * Selection change for a category-chip click: any selected paper in the
 * subtree → select nothing; none selected → select all. The old "not all
 * selected → select all" flip re-added papers that other (overlapping)
 * chips had just deselected, since papers belong to multiple categories.
 */
export function planCategoryToggle(
  paperIdsInSubtree: string[],
  selectedIds: ReadonlySet<string>,
): { add: string[]; remove: string[] } {
  const anySelected = paperIdsInSubtree.some((id) => selectedIds.has(id));
  if (!anySelected) {
    return { add: [...paperIdsInSubtree], remove: [] };
  }
  return { add: [], remove: paperIdsInSubtree.filter((id) => selectedIds.has(id)) };
}

/** Chip tri-state: all / partial / none of the subtree's papers selected. */
export function categorySelectionState(
  paperIdsInSubtree: string[],
  selectedIds: ReadonlySet<string>,
): 'all' | 'partial' | 'none' {
  if (paperIdsInSubtree.length === 0) {
    return 'none';
  }
  const selectedCount = paperIdsInSubtree.filter((id) => selectedIds.has(id)).length;
  if (selectedCount === 0) {
    return 'none';
  }
  return selectedCount === paperIdsInSubtree.length ? 'all' : 'partial';
}

/** Whether a category may be re-parented under targetParentId (client-side
 * cycle guard; the backend re-validates). A null target means top level. */
export function canMoveCategory(
  tree: CategoryTreeNode[],
  draggedId: string,
  targetParentId: string | null,
): boolean {
  if (!targetParentId) {
    return true;
  }
  if (targetParentId === draggedId) {
    return false;
  }
  const findById = (nodes: CategoryTreeNode[]): CategoryTreeNode | null => {
    for (const node of nodes) {
      if (node.id === draggedId) {
        return node;
      }
      const found = findById(node.children);
      if (found) {
        return found;
      }
    }
    return null;
  };
  const dragged = findById(tree);
  if (!dragged) {
    return false;
  }
  return !subtreeCategoryIds(dragged).includes(targetParentId);
}
