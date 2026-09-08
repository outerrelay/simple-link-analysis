/** Canvas application.
 *
 * Holds the Cytoscape instance and the current chart, and keeps the two in
 * step: positions are persisted after a drag or a layout, and removing a node
 * from the canvas writes only to the chart, never to the knowledge graph.
 */

import { api } from './api.js';
import { showMenu } from './contextmenu.js';
import { hideInspector, showEntity, showRelationship } from './inspector.js';
import { LAYOUTS, runLayout } from './layouts.js';
import { buildStylesheet, toEdge, toNode } from './style.js';

const state = {
  ontology: null,
  cy: null,
  chartId: null,
  saveTimer: null,
};

const $ = (id) => document.getElementById(id);

function setStatus(message, level = '') {
  const status = $('status');
  status.textContent = message;
  status.className = `status ${level}`.trim();
  if (message && level !== 'error') {
    setTimeout(() => {
      if (status.textContent === message) status.textContent = '';
    }, 4000);
  }
}

/* --- start ------------------------------------------------------------ */

async function start() {
  state.ontology = await api.ontology();
  state.cy = cytoscape({
    container: $('cy'),
    style: buildStylesheet(state.ontology),
    minZoom: 0.1,
    maxZoom: 3,
    boxSelectionEnabled: true,
    selectionType: 'additive',
    wheelSensitivity: 0.25,
  });

  // Exposed deliberately: this is how you poke at the graph from the browser
  // console, and how the automated interface checks drive the canvas.
  window.sla = state;

  buildLayoutButtons();
  buildLegend();
  wireCanvasEvents();
  wireChrome();
  await loadCharts();
  setStatus(`Ontology v${state.ontology.version}`);
}

/* --- sidebar ---------------------------------------------------------- */

function buildLayoutButtons() {
  const container = $('layout-buttons');
  for (const layout of LAYOUTS) {
    const button = document.createElement('button');
    button.textContent = layout.label;
    button.addEventListener('click', async () => {
      const scope = document.querySelector('input[name="scope"]:checked').value;
      const result = await runLayout(state.cy, layout.id, scope);
      if (result?.skipped) {
        setStatus(`Layout skipped: ${result.reason}`, 'warn');
        return;
      }
      setStatus(`${layout.label} applied to ${scope === 'selection' ? 'selection' : 'chart'}`);
      persistPositions();
    });
    container.append(button);
  }
}

function buildLegend() {
  const legend = $('legend');
  const present = new Set(state.cy.nodes().map((n) => n.data('type')));
  legend.replaceChildren();

  const types = Object.entries(state.ontology.entity_types)
    .filter(([name]) => present.size === 0 || present.has(name))
    .sort((a, b) => a[1].label.localeCompare(b[1].label));

  for (const [, type] of types) {
    const item = document.createElement('div');
    item.className = 'legend-item';
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.background = type.color || '#7c8798';
    if (type.icon) {
      const icon = document.createElement('img');
      icon.src = `/icons/${type.icon}`;
      icon.alt = '';
      swatch.append(icon);
    }
    const label = document.createElement('span');
    label.textContent = type.label;
    item.append(swatch, label);
    legend.append(item);
  }
}

/* --- canvas events ----------------------------------------------------- */

function wireCanvasEvents() {
  const cy = state.cy;

  cy.on('select unselect', 'node', () => {
    $('selection-count').textContent = `(${cy.nodes(':selected').length})`;
  });

  cy.on('tap', 'node', (event) => showEntity(event.target, state.ontology));
  cy.on('tap', 'edge', (event) => showRelationship(event.target, state.ontology));
  cy.on('tap', (event) => {
    if (event.target === cy) hideInspector();
  });

  cy.on('dragfree', 'node', persistPositions);
  cy.on('cxttap', 'node', (event) => openNodeMenu(event.target, event.renderedPosition));
  cy.on('add remove', () => {
    $('canvas-hint').hidden = cy.nodes().length > 0;
    buildLegend();
  });
}

function openNodeMenu(node, renderedPosition) {
  const container = $('cy').getBoundingClientRect();
  const position = {
    x: container.left + renderedPosition.x,
    y: container.top + renderedPosition.y,
  };

  const type = state.ontology.entity_types[node.data('type')];
  const outgoing = type ? type.outgoing_relationships : [];
  const selected = state.cy.nodes(':selected');
  const targets = selected.contains(node) && selected.length > 1 ? selected : node;

  const sections = [
    {
      items: [
        {
          label: `Expand${targets.length > 1 ? ` ${targets.length} nodes` : ''}`,
          detail: 'Pull in directly connected entities from the database.',
          onSelect: () => expand(targets.map((n) => n.id()), { depth: 1 }),
        },
        {
          label: 'Expand two hops',
          detail: 'Follow relationships two steps out.',
          onSelect: () => expand(targets.map((n) => n.id()), { depth: 2 }),
        },
        {
          label: 'Show sources',
          detail: 'Reveal the documents and records this came from.',
          onSelect: () =>
            expand(targets.map((n) => n.id()), { depth: 1, includeSources: true }),
        },
      ],
    },
    {
      label: 'Expand by relationship',
      items: outgoing.slice(0, 8).map((name) => {
        const spec =
          state.ontology.relationship_types[name] ||
          state.ontology.system_relationship_types[name];
        return {
          label: spec ? spec.label : name,
          onSelect: () =>
            expand(targets.map((n) => n.id()), { depth: 1, relationshipTypes: [name] }),
        };
      }),
    },
    {
      items: [
        {
          label: 'Select neighbours',
          onSelect: () => targets.closedNeighborhood().nodes().select(),
        },
        {
          label: `Remove from chart${targets.length > 1 ? ` (${targets.length})` : ''}`,
          detail: 'Takes it off the canvas. It stays in the database.',
          danger: true,
          onSelect: () => removeFromChart(targets.map((n) => n.id())),
        },
      ],
    },
  ];

  showMenu(position, sections, {
    title: node.data('label'),
    subtitle: type ? type.label : node.data('type'),
  });
}

/* --- graph operations --------------------------------------------------- */

async function expand(entityIds, options) {
  try {
    setStatus('Expanding…');
    const graph = await api.expand(entityIds, options);
    const added = mergeIntoCanvas(graph);
    if (graph.truncated) {
      setStatus(`Showing the first ${graph.relationships.length} relationships`, 'warn');
    } else {
      setStatus(added === 0 ? 'Nothing new to add' : `Added ${added} entities`);
    }
    if (added > 0) await persistNewNodes();
  } catch (error) {
    setStatus(`Expand failed: ${error.message}`, 'error');
  }
}

/** Add entities and relationships, leaving anything already present alone. */
function mergeIntoCanvas(graph, positions = {}) {
  const cy = state.cy;
  let added = 0;

  cy.batch(() => {
    for (const entity of graph.entities) {
      if (cy.getElementById(entity.id).nonempty()) continue;
      cy.add(toNode(entity, state.ontology, positions[entity.id]));
      added += 1;
    }
    for (const relationship of graph.relationships) {
      if (cy.getElementById(relationship.id).nonempty()) continue;
      if (
        cy.getElementById(relationship.source_id).empty() ||
        cy.getElementById(relationship.target_id).empty()
      ) {
        continue; // an endpoint is not on the canvas, so the edge has nowhere to go
      }
      cy.add(toEdge(relationship, state.ontology));
    }
  });

  if (added > 0 && Object.keys(positions).length === 0) {
    layoutNewcomers();
  }
  return added;
}

/** Place newly added nodes without disturbing what the analyst has arranged. */
function layoutNewcomers() {
  const unplaced = state.cy.nodes().filter((n) => !n.scratch('placed'));
  if (unplaced.length === 0) return;
  const layout = unplaced.union(unplaced.edgesWith(state.cy.nodes())).layout({
    name: 'cose',
    animate: true,
    animationDuration: 400,
    fit: false,
    randomize: false,
    nodeRepulsion: 20000,
    idealEdgeLength: 145,
  });
  layout.run();
  unplaced.forEach((n) => n.scratch('placed', true));
}

async function removeFromChart(entityIds) {
  state.cy.remove(state.cy.collection(entityIds.map((id) => state.cy.getElementById(id))));
  hideInspector();
  if (state.chartId) {
    try {
      await api.removeNodes(state.chartId, entityIds);
    } catch (error) {
      setStatus(`Could not save removal: ${error.message}`, 'error');
      return;
    }
  }
  setStatus(`Removed ${entityIds.length} from the chart — still in the database`);
}

/* --- charts ------------------------------------------------------------- */

async function loadCharts() {
  const charts = await api.listCharts();
  const select = $('chart-select');
  select.replaceChildren();

  if (charts.length === 0) {
    const chart = await api.createChart('Untitled chart');
    charts.push(chart);
  }
  for (const chart of charts) {
    const option = document.createElement('option');
    option.value = chart.id;
    option.textContent = `${chart.name} (${chart.node_count})`;
    select.append(option);
  }
  await openChart(charts[0].id);
}

async function openChart(chartId) {
  const chart = await api.getChart(chartId);
  state.chartId = chart.id;
  $('chart-select').value = chart.id;

  const positions = Object.fromEntries(
    chart.placements.map((p) => [p.entity_id, { x: p.x, y: p.y }]),
  );
  state.cy.elements().remove();
  mergeIntoCanvas(chart.graph, positions);
  state.cy.nodes().forEach((n) => n.scratch('placed', true));
  if (state.cy.nodes().length > 0) state.cy.fit(undefined, 45);
  hideInspector();
}

function placements() {
  return state.cy.nodes().map((node) => ({
    entity_id: node.id(),
    x: node.position('x'),
    y: node.position('y'),
    pinned: false,
  }));
}

async function persistNewNodes() {
  if (!state.chartId) return;
  try {
    await api.addNodes(state.chartId, placements());
  } catch (error) {
    setStatus(`Could not save chart: ${error.message}`, 'error');
  }
}

/** Debounced, because dragging fires this repeatedly. */
function persistPositions() {
  if (!state.chartId) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    try {
      await api.savePositions(state.chartId, placements());
    } catch (error) {
      setStatus(`Could not save positions: ${error.message}`, 'error');
    }
  }, 400);
}

/* --- chrome ------------------------------------------------------------- */

function wireChrome() {
  $('chart-select').addEventListener('change', (event) => openChart(event.target.value));

  $('new-chart').addEventListener('click', async () => {
    const name = prompt('Name for the new chart:', 'Untitled chart');
    if (!name) return;
    const chart = await api.createChart(name);
    const option = document.createElement('option');
    option.value = chart.id;
    option.textContent = `${chart.name} (0)`;
    $('chart-select').append(option);
    await openChart(chart.id);
  });

  $('fit').addEventListener('click', () => state.cy.fit(undefined, 45));

  $('remove-selected').addEventListener('click', () => {
    const selected = state.cy.nodes(':selected');
    if (selected.length === 0) {
      setStatus('Nothing selected', 'warn');
      return;
    }
    removeFromChart(selected.map((n) => n.id()));
  });

  $('inspector-close').addEventListener('click', hideInspector);
  wireSearch();
}

function wireSearch() {
  const input = $('search-input');
  const results = $('search-results');
  let timer = null;

  const hide = () => {
    results.hidden = true;
  };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const term = input.value.trim();
    if (term.length < 2) {
      hide();
      return;
    }
    timer = setTimeout(async () => {
      try {
        const entities = await api.search(term);
        results.replaceChildren();
        if (entities.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'empty';
          empty.textContent = 'No matches';
          results.append(empty);
        }
        for (const entity of entities) {
          const button = document.createElement('button');
          const label = document.createElement('span');
          label.textContent = entity.label;
          const type = document.createElement('span');
          type.className = 'type';
          const spec = state.ontology.entity_types[entity.type];
          type.textContent = spec ? spec.label : entity.type;
          button.append(label, type);
          button.addEventListener('click', async () => {
            hide();
            input.value = '';
            mergeIntoCanvas({ entities: [entity], relationships: [] });
            await persistNewNodes();
            state.cy.getElementById(entity.id).select();
          });
          results.append(button);
        }
        results.hidden = false;
      } catch (error) {
        setStatus(`Search failed: ${error.message}`, 'error');
      }
    }, 220);
  });

  input.addEventListener('blur', () => setTimeout(hide, 160));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hide();
  });
}

start().catch((error) => {
  setStatus(`Could not start: ${error.message}`, 'error');
});
