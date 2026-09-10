/** Build the Cytoscape stylesheet from the ontology.
 *
 * Nothing here hard-codes an entity type. Adding a type to ontology.yaml gives
 * it a styled node on the next reload, with no change to this file.
 */

const FALLBACK_COLOR = '#7c8798';

export function buildStylesheet(ontology) {
  const style = [
    {
      selector: 'node',
      style: {
        'background-color': FALLBACK_COLOR,
        'background-image': 'none',
        'background-fit': 'none',
        'background-width': '55%',
        'background-height': '55%',
        'background-image-opacity': 1,
        width: 42,
        height: 42,
        shape: 'ellipse',
        label: 'data(label)',
        color: '#e6e9ee',
        'font-size': 10.5,
        'font-family': 'system-ui, sans-serif',
        'text-valign': 'bottom',
        'text-margin-y': 5,
        // Narrower than it might be: a wide label collides with its
        // neighbours side to side, which is harder to read past than the same
        // text wrapped onto another line. This governs the wrap width only —
        // how much of the name is shown is MAX_LABEL below.
        'text-max-width': 128,
        // Wrap on spaces rather than cutting the name off. Breaks are
        // restricted to whitespace: "anywhere" splits words mid-syllable even
        // when a space was available, turning Oslo into "Osl / o". A token
        // with no space in it — an LEI, say — is left to run wide, which
        // reads better than breaking it in an arbitrary place.
        'text-wrap': 'wrap',
        'text-overflow-wrap': 'whitespace',
        'text-outline-color': '#0f1216',
        'text-outline-width': 2.5,
        'border-width': 0,
        'transition-property': 'border-width, border-color',
        'transition-duration': '120ms',
      },
    },
    {
      selector: 'node:selected',
      style: { 'border-width': 3, 'border-color': '#ffffff', 'border-opacity': 0.9 },
    },
    {
      // A source is evidence, not part of the network being analysed, so it
      // is drawn as a square to read differently at a glance.
      selector: 'node[?isSource]',
      style: { shape: 'round-rectangle', width: 36, height: 36 },
    },
    {
      selector: 'edge',
      style: {
        width: 1.4,
        'line-color': '#4a5462',
        'target-arrow-color': '#4a5462',
        'curve-style': 'bezier',
        label: 'data(label)',
        'font-size': 9,
        color: '#8b94a3',
        'text-rotation': 'autorotate',
        'text-background-color': '#0f1216',
        'text-background-opacity': 0.85,
        'text-background-padding': 2,
        'arrow-scale': 0.85,
      },
    },
    {
      selector: 'edge[?directed]',
      style: { 'target-arrow-shape': 'triangle' },
    },
    {
      selector: 'edge:selected',
      style: { 'line-color': '#ffffff', 'target-arrow-color': '#ffffff', width: 2.2 },
    },
    {
      // A relationship that no longer holds: dashed, and dimmed.
      selector: 'edge[?expired]',
      style: { 'line-style': 'dashed', 'line-opacity': 0.55 },
    },
    {
      // System relationships are not claims about the network. MENTIONS is
      // provenance and SAME_AS is a proposal awaiting review, so neither
      // should read like an ordinary fact.
      selector: 'edge[?system]',
      style: {
        'line-style': 'dotted',
        'line-color': '#5c6675',
        'target-arrow-color': '#5c6675',
        'line-opacity': 0.75,
        color: '#6b7688',
      },
    },
    {
      selector: 'edge[type = "SAME_AS"]',
      style: { 'line-color': '#8a6d3b', 'line-style': 'dashed', width: 1.8 },
    },
    {
      selector: '.dimmed',
      style: { opacity: 0.22 },
    },
    {
      // Proposed, not yet accepted. Nothing here is in the database, and the
      // styling has to make that unmistakable at a glance.
      selector: '.provisional',
      style: {
        'border-width': 2,
        'border-color': '#e8b84b',
        'border-style': 'dashed',
        'border-opacity': 1,
        'background-opacity': 0.45,
        color: '#e8b84b',
      },
    },
    {
      selector: 'edge.provisional',
      style: {
        'line-style': 'dashed',
        'line-color': '#e8b84b',
        'target-arrow-color': '#e8b84b',
        'line-opacity': 0.9,
        color: '#e8b84b',
      },
    },
    {
      selector: '.provisional-focus',
      style: { 'border-width': 4, 'border-color': '#ffffff', 'border-style': 'solid' },
    },
  ];

  for (const [name, type] of Object.entries(ontology.entity_types)) {
    style.push({
      selector: `node[type = "${name}"]`,
      style: {
        'background-color': type.color || FALLBACK_COLOR,
        'background-image': type.icon ? `url(/icons/${type.icon})` : 'none',
        'background-fit': 'none',
        'background-clip': 'none',
        'background-image-containment': 'over',
        'background-width': '56%',
        'background-height': '56%',
        'background-position-x': '50%',
        'background-position-y': '50%',
      },
    });
  }

  return style;
}

/** Longest label drawn in full before it is shortened.

   Generous enough for the names this ontology actually holds — the longest in
   the sample data is 51 characters — while stopping a pathological name from
   becoming a wall of text under the node. */
const MAX_LABEL = 64;

/** Shorten a label at a word boundary, so wrapping has whole words to work with. */
export function displayLabel(text) {
  const label = String(text ?? '');
  if (label.length <= MAX_LABEL) return label;

  const cut = label.slice(0, MAX_LABEL);
  const lastSpace = cut.lastIndexOf(' ');
  // Only break at a space if one falls reasonably late; otherwise the name is
  // one long token and cutting mid-word is the honest option.
  return `${lastSpace > MAX_LABEL * 0.6 ? cut.slice(0, lastSpace) : cut.trimEnd()}…`;
}

/** Cytoscape element for one entity. */
export function toNode(entity, ontology, position) {
  const isSource = (ontology.source_types || []).includes(entity.type);
  return {
    group: 'nodes',
    data: {
      id: entity.id,
      label: displayLabel(entity.label),
      type: entity.type,
      isSource: isSource ? 1 : 0,
      properties: entity.properties,
    },
    position: position ? { x: position.x, y: position.y } : undefined,
  };
}

/** Cytoscape element for one relationship. */
export function toEdge(relationship, ontology) {
  const spec =
    ontology.relationship_types[relationship.type] ||
    ontology.system_relationship_types[relationship.type];
  const expired = Boolean(relationship.valid_to) && new Date(relationship.valid_to) < new Date();
  const system = Object.hasOwn(ontology.system_relationship_types, relationship.type);
  return {
    group: 'edges',
    data: {
      id: relationship.id,
      source: relationship.source_id,
      target: relationship.target_id,
      label: spec ? spec.label : relationship.type,
      type: relationship.type,
      directed: relationship.directed ? 1 : 0,
      expired: expired ? 1 : 0,
      system: system ? 1 : 0,
      properties: relationship.properties,
      valid_from: relationship.valid_from,
      valid_to: relationship.valid_to,
      assertion_count: relationship.assertion_count,
    },
  };
}
