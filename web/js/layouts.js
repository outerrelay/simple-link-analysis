/** Graph layouts, runnable over the whole chart or only the selection.
 *
 * Applying a layout to a selection is the awkward case. Cytoscape lays a
 * collection out within a bounding box, so running one over a selection means
 * computing the box the selected nodes already occupy and confining the result
 * to it — otherwise the selection is flung across the whole viewport and the
 * rest of the chart is left behind, which is not what "tidy up these five
 * nodes" should do.
 */

export const LAYOUTS = [
  { id: 'fcose-ish', label: 'Organic', name: 'cose' },
  { id: 'dagre', label: 'Hierarchy', name: 'dagre' },
  { id: 'breadthfirst', label: 'Tree', name: 'breadthfirst' },
  { id: 'circle', label: 'Circle', name: 'circle' },
  { id: 'concentric', label: 'Concentric', name: 'concentric' },
  { id: 'grid', label: 'Grid', name: 'grid' },
];

const BASE_OPTIONS = {
  cose: {
    animate: true,
    animationDuration: 420,
    nodeRepulsion: 26000,
    idealEdgeLength: 170,
    nodeOverlap: 26,
    gravity: 0.4,
    randomize: false,
  },
  dagre: { animate: true, animationDuration: 380, rankDir: 'TB', nodeSep: 55, rankSep: 85 },
  breadthfirst: { animate: true, animationDuration: 380, spacingFactor: 1.3, directed: true },
  circle: { animate: true, animationDuration: 380, spacingFactor: 1.2 },
  concentric: {
    animate: true,
    animationDuration: 380,
    minNodeSpacing: 32,
    concentric: (node) => node.degree(),
    levelWidth: () => 2,
  },
  grid: { animate: true, animationDuration: 320, avoidOverlap: true, spacingFactor: 1.1 },
};

/** Padding around a selection's bounding box, so nodes are not laid on the edge. */
const SELECTION_PADDING = 40;
const MIN_BOX = 220;

/**
 * Run a layout.
 *
 * @param cy         the Cytoscape instance
 * @param layoutId   one of LAYOUTS
 * @param scope      'all' or 'selection'
 * @returns          a promise resolving when the layout has settled
 */
export function runLayout(cy, layoutId, scope = 'all') {
  const spec = LAYOUTS.find((l) => l.id === layoutId) ?? LAYOUTS[0];
  const options = { name: spec.name, ...(BASE_OPTIONS[spec.name] ?? {}) };

  if (scope !== 'selection') {
    const layout = cy.layout({ ...options, fit: true, padding: 45 });
    const done = layout.promiseOn('layoutstop');
    layout.run();
    return done;
  }

  const selected = cy.nodes(':selected');
  if (selected.length < 2) {
    return Promise.resolve({ skipped: true, reason: 'select at least two nodes' });
  }

  // Confine the result to the space the selection already occupies, and lay
  // out only the edges that run between selected nodes.
  const box = selected.boundingBox();
  const width = Math.max(box.w, MIN_BOX);
  const height = Math.max(box.h, MIN_BOX);
  const centreX = box.x1 + box.w / 2;
  const centreY = box.y1 + box.h / 2;

  const collection = selected.union(selected.edgesWith(selected));
  const layout = collection.layout({
    ...options,
    fit: false,
    boundingBox: {
      x1: centreX - width / 2 - SELECTION_PADDING,
      y1: centreY - height / 2 - SELECTION_PADDING,
      w: width + SELECTION_PADDING * 2,
      h: height + SELECTION_PADDING * 2,
    },
  });
  const done = layout.promiseOn('layoutstop');
  layout.run();
  return done;
}
