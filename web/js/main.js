/** Canvas application.
 *
 * Holds the Cytoscape instance and the current chart, and keeps the two in
 * step: positions are persisted after a drag or a layout, and removing a node
 * from the canvas writes only to the chart, never to the knowledge graph.
 */

import { api } from './api.js';
import { showMenu } from './contextmenu.js';
import { initCreate, openNewEntity, openNewRelationship } from './create.js';
import { hideInspector, showEntity, showRelationship } from './inspector.js';
import { LAYOUTS, runLayout } from './layouts.js';
import { initMerge, openMerge } from './merge.js';
import { close as closeReview, initReview, showProposal } from './review.js';
import { buildStylesheet, toEdge, toNode } from './style.js';

const state = {
  ontology: null,
  cy: null,
  chartId: null,
  saveTimer: null,
  actionsByType: new Map(),
  provisionalIds: new Set(),
  selectionOrder: [],
  canvasMode: 'select',
  spaceHeld: false,
};

const $ = (id) => document.getElementById(id);

/** Actions with their own menu entries, so the generic list skips them. */
const HANDLED_SEPARATELY = new Set(['expand.database', 'identity.check']);

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

  wireCanvasMode();
  buildLayoutButtons();
  buildLegend();
  wireCanvasEvents();
  wireChrome();
  initCreate(state.ontology, {
    onEntityCreated: onEntityCreated,
    onRelationshipCreated: onRelationshipCreated,
    onError: (error) => setStatus(error.message, 'error'),
  });
  initMerge({
    onMerged: onMerged,
    onError: (error) => setStatus(`Merge failed: ${error.message}`, 'error'),
  });
  initReview({
    onDecided: onProposalDecided,
    onDismiss: clearProvisional,
    onHighlight: highlightProvisional,
    onError: (error) => setStatus(`Could not save decision: ${error.message}`, 'error'),
  });
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

  // Cytoscape's :selected collection has no notion of the order things were
  // clicked, but "connect these two" needs a direction, and the obvious one is
  // first-selected to second-selected. So the order is tracked here.
  cy.on('select', 'node', (event) => {
    const id = event.target.id();
    if (!state.selectionOrder.includes(id)) state.selectionOrder.push(id);
    $('selection-count').textContent = `(${cy.nodes(':selected').length})`;
  });
  cy.on('unselect', 'node', (event) => {
    state.selectionOrder = state.selectionOrder.filter((id) => id !== event.target.id());
    $('selection-count').textContent = `(${cy.nodes(':selected').length})`;
  });
  cy.on('remove', 'node', (event) => {
    state.selectionOrder = state.selectionOrder.filter((id) => id !== event.target.id());
  });

  cy.on('tap', 'node', (event) => showEntity(event.target, state.ontology));
  cy.on('tap', 'edge', (event) => showRelationship(event.target, state.ontology));
  cy.on('tap', (event) => {
    if (event.target === cy) hideInspector();
  });

  cy.on('dragfree', 'node', persistPositions);
  cy.on('cxttap', 'node', (event) => openNodeMenu(event.target, event.renderedPosition));
  cy.on('cxttap', (event) => {
    if (event.target === cy) openCanvasMenu(event.renderedPosition, event.position);
  });

  // Cytoscape's cxttap does not stop the browser opening its own menu on top
  // of ours. Suppressed on the canvas only: right-clicking a text field should
  // still offer paste.
  $('cy').addEventListener('contextmenu', (event) => event.preventDefault());
  cy.on('add remove', () => {
    $('canvas-hint').hidden = cy.nodes().length > 0;
    buildLegend();
    for (const type of new Set(cy.nodes().map((n) => n.data('type')))) loadActionsFor(type);
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
  const actions = state.actionsByType.get(node.data('type')) ?? [];

  // Expansion reads the database; the rest reach outside it. Keeping them in
  // separate sections makes the difference visible before you click.
  // Two actions have dedicated entries elsewhere in this menu, because they
  // do more than run and report: expansion has its own preview variant, and
  // the duplicate check offers a merge on each match.
  const external = actions.filter((a) => !HANDLED_SEPARATELY.has(a.id));

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
        {
          label: 'Expand — preview first',
          detail:
            'Show what would be added as provisional, and decide before anything ' +
            'is written.',
          onSelect: () =>
            runAction(
              { id: 'expand.database', label: 'Expand', available: true },
              node.id(),
              { policy: 'review' },
            ),
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
      label: external.length ? 'Look up' : null,
      items: external.map((action) => ({
        label: action.available ? action.label : `${action.label} — unavailable`,
        detail: action.available ? action.description : action.unavailable_reason,
        disabled: !action.available,
        onSelect: () => runAction(action, node.id()),
      })),
    },
    {
      items: [
        {
          label: 'Check for duplicates',
          detail: 'Look for other records that may be the same thing.',
          onSelect: () => checkForDuplicates(node.id()),
        },
        ...(selected.length === 2 && selected.contains(node)
          ? [
              {
                label: 'Connect these two…',
                detail: 'Draw a relationship between them, from the first selected.',
                onSelect: () => {
                  const [a, b] = selectedInOrder().map((n) => ({
                    id: n.id(),
                    type: n.data('type'),
                    label: n.data('label'),
                  }));
                  openNewRelationship(a, b);
                },
              },
              {
                label: 'Merge these two…',
                detail:
                  'Combine them into one record. You choose which survives, and ' +
                  'it can be undone.',
                onSelect: () => {
                  const [a, b] = selected.map((n) => n.id());
                  openMerge(a, b);
                },
              },
            ]
          : []),
      ],
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
        {
          label: `Delete from database${targets.length > 1 ? ` (${targets.length})` : ''}`,
          detail:
            'Hides it from every chart and every query. Re-importing will not bring it back.',
          danger: true,
          onSelect: () => deleteFromDatabase(targets.map((n) => n.id())),
        },
      ],
    },
  ].filter((section) => section.items.length);

  showMenu(position, sections, {
    title: node.data('label'),
    subtitle: type ? type.label : node.data('type'),
  });
}

/* --- actions ------------------------------------------------------------ */

/** Fetch and cache which actions apply to a given entity type. */
async function loadActionsFor(entityType) {
  if (state.actionsByType.has(entityType)) return;
  try {
    state.actionsByType.set(entityType, await api.actionsFor(entityType));
  } catch {
    state.actionsByType.set(entityType, []);
  }
}

async function runAction(action, entityId, { policy = null } = {}) {
  try {
    setStatus(`Running ${action.label}…`);
    const job = await api.runAction(action.id, {
      entity_id: entityId,
      chart_id: state.chartId,
      policy,
    });
    const finished = await pollJob(job.id);

    if (finished.status === 'failed') {
      setStatus(`${action.label}: ${finished.message}`, 'error');
      return;
    }
    if (!finished.proposal_set_id) {
      setStatus(finished.message || 'Nothing returned');
      return;
    }

    const proposal = await api.proposal(finished.proposal_set_id);
    const undecided = proposal.items.filter((item) => item.decision === 'pending');

    if (undecided.length === 0) {
      // Either it auto-committed, or everything was already decided before.
      await reloadChartGraph();
      setStatus(finished.message || 'Done');
      return;
    }

    showProvisional(proposal);
    showProposal(proposal);
    setStatus(`${action.label}: ${undecided.length} to review`, 'warn');
  } catch (error) {
    setStatus(`${action.label} failed: ${error.message}`, 'error');
  }
}

/** Poll until the job stops running. */
async function pollJob(jobId, { interval = 500, timeout = 120000 } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const job = await api.job(jobId);
    if (job.status !== 'running') return job;
    if (Date.now() > deadline) {
      return { ...job, status: 'failed', message: 'timed out waiting for the action' };
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

/** Draw a proposal on the canvas as provisional, so it can be judged in place. */
function showProvisional(proposal) {
  clearProvisional();
  const cy = state.cy;
  const idFor = new Map();

  cy.batch(() => {
    for (const item of proposal.items) {
      if (item.kind !== 'entity' || item.decision !== 'pending') continue;
      const payload = item.payload;
      const targetId = payload.existing_id || payload.id;
      idFor.set(payload.id, targetId);
      if (cy.getElementById(targetId).nonempty()) continue;

      cy.add(
        toNode(
          {
            id: targetId,
            type: payload.type,
            label: payload.properties?.name ?? payload.type,
            properties: payload.properties ?? {},
          },
          state.ontology,
        ),
      );
      cy.getElementById(targetId).addClass('provisional').scratch('itemId', item.id);
      state.provisionalIds.add(targetId);
    }

    for (const item of proposal.items) {
      if (item.kind !== 'relationship' || item.decision !== 'pending') continue;
      const payload = item.payload;
      const source = idFor.get(payload.source_id) ?? payload.source_id;
      const target = idFor.get(payload.target_id) ?? payload.target_id;
      if (cy.getElementById(source).empty() || cy.getElementById(target).empty()) continue;

      const edgeId = `provisional:${item.id}`;
      if (cy.getElementById(edgeId).nonempty()) continue;
      cy.add(
        toEdge(
          {
            id: edgeId,
            type: payload.type,
            source_id: source,
            target_id: target,
            directed: true,
            properties: payload.properties ?? {},
            valid_from: payload.valid_from,
            valid_to: payload.valid_to,
            assertion_count: 0,
          },
          state.ontology,
        ),
      );
      cy.getElementById(edgeId).addClass('provisional').scratch('itemId', item.id);
      state.provisionalIds.add(edgeId);
    }
  });

  layoutNewcomers();
  // Judging a proposal means seeing it, so bring it into view.
  setTimeout(() => cy.fit(undefined, 60), 450);
}

/** Take provisional elements off the canvas without touching anything real. */
function clearProvisional() {
  const cy = state.cy;
  if (!cy) return;
  cy.batch(() => {
    for (const id of state.provisionalIds) cy.getElementById(id).remove();
  });
  state.provisionalIds.clear();
}

function highlightProvisional(item) {
  const cy = state.cy;
  cy.elements('.provisional-focus').removeClass('provisional-focus');
  if (!item) return;
  cy.elements('.provisional')
    .filter((element) => element.scratch('itemId') === item.id)
    .addClass('provisional-focus');
}

async function onProposalDecided(result, counts) {
  // Accepting writes to the graph but says nothing about which chart the
  // analyst is looking at, so the accepted entities have to be placed here or
  // they would be saved and then vanish from the canvas.
  const accepted = result.items
    .filter((item) => item.kind === 'entity' && item.decision === 'accepted')
    .map((item) => item.payload.existing_id || item.payload.id);

  const positions = new Map(
    state.cy.nodes().map((n) => [n.id(), { x: n.position('x'), y: n.position('y') }]),
  );
  clearProvisional();

  if (state.chartId && accepted.length) {
    try {
      await api.addNodes(
        state.chartId,
        accepted.map((id) => ({
          entity_id: id,
          x: positions.get(id)?.x ?? 0,
          y: positions.get(id)?.y ?? 0,
        })),
      );
    } catch (error) {
      setStatus(`Saved to the database, but not to this chart: ${error.message}`, 'error');
    }
  }

  await reloadChartGraph();
  const parts = [];
  if (counts.accepted) parts.push(`${counts.accepted} accepted`);
  if (counts.rejected) parts.push(`${counts.rejected} rejected — will not be offered again`);
  setStatus(parts.join(', ') || 'Nothing changed');
}

async function deleteFromDatabase(entityIds) {
  const confirmed = confirm(
    `Delete ${entityIds.length} entit${entityIds.length === 1 ? 'y' : 'ies'} from the ` +
      `database?\n\nThey will disappear from every chart and every query, and ` +
      `re-importing the same source will not bring them back.`,
  );
  if (!confirmed) return;

  try {
    for (const id of entityIds) await api.deleteFromGraph(id, { suppress: true });
    state.cy.remove(state.cy.collection(entityIds.map((id) => state.cy.getElementById(id))));
    hideInspector();
    if (state.chartId) await api.removeNodes(state.chartId, entityIds);
    setStatus(`Deleted ${entityIds.length} from the database`);
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`, 'error');
  }
}

/** Re-read the chart from the server, so the canvas reflects what was written. */
async function reloadChartGraph() {
  if (!state.chartId) return;
  const positions = Object.fromEntries(
    state.cy.nodes().map((n) => [n.id(), { x: n.position('x'), y: n.position('y') }]),
  );
  const chart = await api.getChart(state.chartId);
  state.cy.elements().remove();
  state.provisionalIds.clear();
  mergeIntoCanvas(chart.graph, { ...positions, ...Object.fromEntries(
    chart.placements.map((p) => [p.entity_id, { x: p.x, y: p.y }]),
  ) });
  state.cy.nodes().forEach((n) => n.scratch('placed', true));
}

/* --- pan and select ------------------------------------------------------
 *
 * Dragging the background can either move the canvas or draw a selection box,
 * and both are wanted. Rather than make one of them unreachable, the drag has
 * a mode — shown in the toolbar and in the cursor — with escape hatches so
 * neither ever needs the mode switched:
 *
 *   · shift-drag always selects, even in pan mode
 *   · holding space always pans, even in select mode
 *
 * Select is the default, because a box selection is the thing you cannot
 * accomplish any other way, while the canvas can also be moved by holding
 * space or switching mode.
 */

function setCanvasMode(mode) {
  state.canvasMode = mode;
  applyCanvasMode();
  for (const button of document.querySelectorAll('.mode-switch button')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
}

/** Push the current mode onto Cytoscape and the cursor. */
function applyCanvasMode() {
  const panning = state.canvasMode === 'pan' || state.spaceHeld;
  state.cy.userPanningEnabled(panning);
  // Box selection stays enabled while panning so that shift-drag still works.
  state.cy.boxSelectionEnabled(true);

  const canvas = $('cy');
  canvas.classList.toggle('mode-select', state.canvasMode === 'select');
  canvas.classList.toggle('mode-pan', state.canvasMode === 'pan');
  canvas.classList.toggle('panning-temporarily', state.spaceHeld);
}

function wireCanvasMode() {
  for (const button of document.querySelectorAll('.mode-switch button')) {
    button.addEventListener('click', () => setCanvasMode(button.dataset.mode));
  }

  document.addEventListener('keydown', (event) => {
    if (isTyping(event.target)) return;

    if (event.code === 'Space' && !state.spaceHeld) {
      // Hold space to pan without leaving select mode — and stop the page
      // scrolling while it is held.
      event.preventDefault();
      state.spaceHeld = true;
      applyCanvasMode();
      return;
    }
    if (event.key === 'v' || event.key === 'V') setCanvasMode('select');
    if (event.key === 'h' || event.key === 'H') setCanvasMode('pan');
  });

  document.addEventListener('keyup', (event) => {
    if (event.code === 'Space') {
      state.spaceHeld = false;
      applyCanvasMode();
    }
  });

  // Releasing space over another window would otherwise leave it stuck down.
  window.addEventListener('blur', () => {
    state.spaceHeld = false;
    applyCanvasMode();
  });

  setCanvasMode('select');
}

/** True when the keystroke belongs to a field the analyst is typing in. */
function isTyping(target) {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target?.isContentEditable
  );
}

/** Selected nodes, in the order they were clicked. */
function selectedInOrder() {
  const selected = state.cy.nodes(':selected');
  const ordered = state.selectionOrder
    .map((id) => state.cy.getElementById(id))
    .filter((node) => node.nonempty() && node.selected());
  // Anything selected by other means — a box drag, "select neighbours" — has
  // no recorded order, so it goes on the end.
  const seen = new Set(ordered.map((node) => node.id()));
  return [...ordered, ...selected.filter((node) => !seen.has(node.id()))];
}

/** The menu for empty canvas: what you can do without a node under the cursor. */
function openCanvasMenu(renderedPosition, graphPosition) {
  const container = $('cy').getBoundingClientRect();
  showMenu(
    {
      x: container.left + renderedPosition.x,
      y: container.top + renderedPosition.y,
    },
    [
      {
        items: [
          {
            label: 'Add an entity here…',
            detail: 'Create a new record and place it at this point.',
            onSelect: () => openNewEntity(graphPosition),
          },
        ],
      },
    ],
  );
}

/** Place a newly created entity where the analyst asked for it. */
async function onEntityCreated(result, position) {
  const entity = result.entity;
  mergeIntoCanvas({ entities: [entity], relationships: [] }, { [entity.id]: position });
  const node = state.cy.getElementById(entity.id);
  node.scratch('placed', true);
  if (position) node.position(position);
  node.select();
  await persistNewNodes();
  await loadActionsFor(entity.type);

  if (result.matches.length) {
    // Typing in a company is exactly where a duplicate arrives. Say so; do
    // nothing about it.
    setStatus(
      `Added ${entity.label} — ${result.matches.length} possible duplicate` +
        `${result.matches.length === 1 ? '' : 's'}. Right-click to check.`,
      'warn',
    );
  } else {
    setStatus(`Added ${entity.label}`);
  }
}

async function onRelationshipCreated(edge) {
  mergeIntoCanvas({ entities: [], relationships: [edge] });
  setStatus('Relationship added');
  await persistPositions();
}

/** Ask whether this node is already in the database, and offer to merge. */
async function checkForDuplicates(entityId) {
  try {
    setStatus('Checking for duplicates…');
    const matches = await api.matchesFor(entityId);
    if (matches.length === 0) {
      setStatus('No possible duplicates found');
      return;
    }

    // Put the matches on the canvas so they can be seen in context, then offer
    // each one as a merge. Nothing is combined without an explicit choice.
    const graph = await api.expand([entityId, ...matches.map((m) => m.entity_id)], {
      depth: 1,
    });
    mergeIntoCanvas(graph);
    await persistNewNodes();

    const node = state.cy.getElementById(entityId);
    const position = node.nonempty()
      ? (() => {
          const container = $('cy').getBoundingClientRect();
          const rendered = node.renderedPosition();
          return { x: container.left + rendered.x, y: container.top + rendered.y };
        })()
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 };

    showMenu(
      position,
      [
        {
          label: 'Possibly the same as',
          items: matches.map((match) => ({
            label: `${match.name} — ${match.reason}`,
            detail: `${match.strength} match. Opens the merge dialog.`,
            onSelect: () => openMerge(entityId, match.entity_id),
          })),
        },
      ],
      {
        title: 'Possible duplicates',
        subtitle: `${matches.length} found. Nothing is merged until you say so.`,
      },
    );
    setStatus(`${matches.length} possible duplicate${matches.length === 1 ? '' : 's'}`, 'warn');
  } catch (error) {
    setStatus(`Duplicate check failed: ${error.message}`, 'error');
  }
}

async function onMerged(result, { absorbedId }) {
  // The absorbed record is hidden now, so take it off the canvas.
  state.cy.remove(state.cy.getElementById(absorbedId));
  if (state.chartId) {
    try {
      await api.removeNodes(state.chartId, [absorbedId]);
    } catch {
      /* the chart will simply skip it on the next load */
    }
  }
  hideInspector();
  await reloadChartGraph();
  setStatus(`${result.summary}. Undo from the merge history.`, 'warn');
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
    nodeRepulsion: 26000,
    idealEdgeLength: 170,
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
  return state.cy.nodes(':not(.provisional)').map((node) => ({
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

  // Pressing the mouse on a result blurs the input, which used to hide the
  // list before the click completed — so a click held longer than the hide
  // delay did nothing at all. Suppressing the default mousedown behaviour
  // keeps focus on the input, so the list is still there on mouse-up however
  // long the button is held.
  results.addEventListener('mousedown', (event) => event.preventDefault());

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
