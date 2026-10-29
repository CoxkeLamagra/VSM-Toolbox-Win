import { createPCFFeature } from './features/pcf.js';
import {
  esc,
  duration,
  VALUE_RATINGS,
  buildWorkspaceIndex,
  formValues,
  serviceTimeMetrics,
} from './lib/core.js';
import { bpmnMarkup } from './features/bpmn.js';
import { createStreamDiagram } from './features/stream-diagram.js';

const $ = (s) => document.querySelector(s);
let state,
  view = 'streams',
  selected = null,
  saveForm,
  session = {},
  accounts = [],
  updateStatus = {},
  updateTimer;
let brandSettings = {},
  brandDefaults = {};
let backupState = { backups: [], operation: {} };
let streamMode = 'diagram',
  diagramController = null;
const canEdit = () => ['Admin', 'Editor'].includes(session.environment?.role);
const environmentAdmin = () => session.environment?.role === 'Admin';
const MAINTENANCE_BUSY = new Set([
  'queued',
  'checking',
  'downloading',
  'installing',
  'restarting',
  'backing-up',
  'restoring',
  'cleaning',
  'clearing',
]);
const EDIT_ACTIONS = new Set([
  'new-stream',
  'edit-stream',
  'new-step',
  'edit-step',
  'new-process',
  'import-bpmn',
  'link',
  'delete-stream',
  'delete-step',
  'delete-process',
]);
async function readResponse(response) {
  let body;
  try {
    body = JSON.parse(await response.text());
  } catch {
    throw new Error(
      `Server returned an unexpected response (HTTP ${response.status}). The application may be restarting or a proxy may be unavailable. Try again shortly.`,
    );
  }
  return body;
}
function pollMaintenance() {
  const pollingView = view;
  clearTimeout(updateTimer);
  updateTimer = setTimeout(async () => {
    if (view !== pollingView || !session.user) return;
    try {
      await refresh();
    } catch (e) {
      if (view === pollingView && session.user) {
        error(e);
        pollMaintenance();
      }
    }
  }, 2000);
}
async function refreshMaintenance() {
  // Poll even if this first read overlaps an update or restore restart.
  pollMaintenance();
  try {
    await refresh();
  } catch (e) {
    error(e);
  }
}
async function api(path, method = 'GET', data) {
  const r = await fetch('/api/' + path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(session.csrf_token ? { 'X-CSRF-Token': session.csrf_token } : {}),
      ...(session.environment ? { 'X-Environment-ID': String(session.environment.id) } : {}),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const body = await readResponse(r);
  if (!r.ok) {
    if (r.status === 401 && session.user) {
      session = {};
      pcf.importState = null;
      state = null;
      $('#editor').close();
      render();
    }
    throw new Error(body.error || 'Request failed');
  }
  return body;
}
function error(e) {
  $('#error').textContent = e.message;
  $('#error').hidden = false;
  setTimeout(() => ($('#error').hidden = true), 7000);
}
async function refresh() {
  const brandResponse = await api('branding');
  brandSettings = brandResponse.branding;
  brandDefaults = brandResponse.defaults;
  session = await api('session');
  if (!session.user) {
    state = null;
    applyBranding();
    render();
    return;
  }
  environmentList = await api('environments');
  if (!canAccessView(view)) view = session.environment ? 'streams' : 'environments';
  state = session.environment ? await api('data') : null;
  applyBranding();
  await VIEWS[view].load?.();
  render();
}
let indexedWorkspace, workspaceIndex;
function getWorkspaceIndex() {
  if (!workspaceIndex || indexedWorkspace !== state) {
    indexedWorkspace = state;
    workspaceIndex = buildWorkspaceIndex(state);
  }
  return workspaceIndex;
}
const steps = (id) => [...(getWorkspaceIndex().stepsByStream.get(id) || [])];
// One registry owns page access, optional loading, rendering, and navigation.
const VIEWS = {
  streams: { render: renderContent },
  environments: {
    render: renderEnvironments,
    load: async () => {
      environmentMembers = environmentAdmin()
        ? await api('environments/' + session.environment.id + '/members')
        : [];
    },
  },
  processes: { render: renderContent },
  pcf: { render: renderPCF },
  tags: { environmentAdminOnly: true, render: renderTags },
  branding: { adminOnly: true, render: renderBranding },
  maintenance: {
    adminOnly: true,
    render: renderDataManagement,
    load: async () => {
      maintenanceState = await api('maintenance');
    },
  },
  accounts: {
    adminOnly: true,
    render: renderAccounts,
    load: async () => {
      accounts = await api('users');
    },
  },
  backups: {
    adminOnly: true,
    render: renderBackups,
    load: async () => {
      backupState = await api('backups');
    },
  },
  updates: {
    adminOnly: true,
    render: renderUpdates,
    load: async () => {
      updateStatus = await api('updates');
    },
  },
};
function canAccessView(name) {
  const page = VIEWS[name];
  if (!page || !session.user) return false;
  if (page.adminOnly) return session.user.role === 'Admin';
  if (page.environmentAdminOnly) return environmentAdmin();
  return name === 'environments' || Boolean(session.environment);
}
function navigationButton(name) {
  return $(name === 'processes' ? '#processNav' : '#' + name + 'Nav');
}
function navigate(nextView) {
  view = canAccessView(nextView) ? nextView : 'environments';
  selected = null;
  if (VIEWS[view].adminOnly || VIEWS[view].environmentAdminOnly || view === 'environments')
    refresh().catch(error);
  else render();
}
function syncThemeStylesheet() {
  const signature = JSON.stringify([
    brandSettings.primary,
    brandSettings.sidebar,
    brandSettings.background,
    (state?.tags || []).map((tag) => [tag.id, tag.color]),
  ]);
  // A bounded cache key avoids a growing URL as the tag catalog expands.
  let hash = 2166136261;
  for (let i = 0; i < signature.length; i++)
    hash = Math.imul(hash ^ signature.charCodeAt(i), 16777619);
  const href = '/branding.css?v=' + (hash >>> 0).toString(16);
  if ($('#brandingStyles').getAttribute('href') !== href) $('#brandingStyles').href = href;
}
function stats(items, stream) {
  const times = serviceTimeMetrics(items, stream);
  return `<div class="stats service-time-stats">
    <div class="stat"><div class="label">Cycle time (processing)</div><strong>${duration(times.cycle)}</strong><span>Total active work per request, excluding waiting</span></div>
    <div class="stat"><div class="label">Lead time</div><strong>${duration(times.lead)}</strong><span>Active work + waiting across all steps</span></div>
    <div class="stat"><div class="label">Takt time</div><strong>${times.takt === null ? 'Not set' : duration(times.takt) + '/request'}</strong><span>${times.takt === null ? 'Set net available time and demand in stream details' : `${duration(stream.takt_available_minutes)} available ÷ ${esc(stream.takt_demand)} requests per ${esc(stream.takt_period || 'day')}`}</span></div>
    <div class="stat"><div class="label">Flow efficiency</div><strong>${times.efficiency}%</strong><span>Active processing time / lead time</span></div>
  </div><p class="hint">Service mapping convention: cycle time is active processing per request; lead time includes queues and delays. Totals assume sequential steps. Takt is the required delivery pace for customer demand, not actual completion time.</p>`;
}
let environmentList = [],
  environmentMembers = [];
function resetEnvironmentView() {
  selected = null;
  pcf.focus = null;
  pcf.importState = null;
  pcf.importOpen = false;
  streamMode = 'diagram';
}
async function switchEnvironment(id) {
  await api('environments/' + id + '/switch', 'POST', {});
  resetEnvironmentView();
  await refresh();
}
function editEnvironment(id) {
  const entry = environmentList.find((environment) => environment.id === id) || {};
  modal(
    id ? 'Edit environment' : 'Create environment',
    field('name', 'Environment name', entry.name) +
      field('description', 'Description', entry.description, 'textarea'),
    async (data) => {
      await api('environments' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data);
      if (!id) resetEnvironmentView();
      view = 'environments';
    },
  );
}
function renderEnvironments() {
  const active = environmentList.find((environment) => environment.id === session.environment?.id);
  $('#breadcrumb').textContent = 'Environments';
  const countLabels = {
    streams: 'Value streams',
    steps: 'Delivery steps',
    processes: 'Processes',
    pcf_entries: 'PCF entries',
    tags: 'Tags',
    members: 'Members',
  };
  $('#content').innerHTML =
    `<div class="eyebrow">ENVIRONMENT MANAGEMENT</div><div class="title-row"><div><h1>Environments</h1><p>Separate your value streams, processes, PCF classifications, and tags into independent workspaces.</p></div><button class="primary" data-action="new-environment">+ Create environment</button></div>${
      active
        ? `<section class="environment-dashboard"><div class="section-row"><div><span class="pill">Current environment · ${esc(active.role)}</span><h2>${esc(active.name)}</h2><p>${esc(active.description)}</p></div>${active.role === 'Admin' ? `<div class="detail-actions"><button data-action="edit-environment" data-id="${active.id}">Edit environment</button><button class="danger" data-action="delete-environment" data-id="${active.id}">Delete environment</button></div>` : ''}</div><div class="environment-counts">${Object.entries(
            countLabels,
          )
            .map(
              ([key, label]) =>
                `<div class="stat"><span>${label}</span><strong>${active.counts[key]}</strong></div>`,
            )
            .join(
              '',
            )}</div><div class="detail-actions"><button data-action="environment-content" data-target="streams">Value streams</button><button data-action="environment-content" data-target="processes">Processes</button><button data-action="environment-content" data-target="pcf">PCF framework</button></div></section>`
        : '<div class="empty"><h2>Create your first environment</h2><p>Its creator becomes the environment Admin and can invite existing local accounts.</p></div>'
    }<div class="section-row"><h2>Your environments</h2><span class="label">${environmentList.length} environments</span></div><div class="grid">${environmentList.map((environment) => `<article class="environment-card"><span class="pill">${esc(environment.role)}${environment.id === active?.id ? ' · Active' : ''}</span><h3>${esc(environment.name)}</h3><p>${esc(environment.description || 'An independent local workspace.')}</p><p>${environment.counts.streams} value streams · ${environment.counts.processes} processes · ${environment.counts.pcf_entries} PCF entries</p><button data-action="switch-environment" data-id="${environment.id}" ${environment.id === active?.id ? 'disabled' : ''}>Switch environment</button></article>`).join('')}</div>${active?.role === 'Admin' ? `<section class="environment-members"><h2>Environment members</h2><p>Add existing local accounts and set their role in this environment.</p><form id="addEnvironmentMember"><label>Local username<input name="username" aria-label="Local username" required maxlength="64"></label><label>Environment role<select name="role" aria-label="Environment role"><option>Viewer</option><option>Editor</option><option>Admin</option></select></label><button type="submit">Add member</button></form><div class="table-wrap"><table><thead><tr><th>Username</th><th>Environment role</th><th>Actions</th></tr></thead><tbody>${environmentMembers.map((member) => `<tr><td>${esc(member.username)}</td><td>${esc(member.role)}</td><td><button data-action="edit-environment-member" data-id="${member.user_id}">Edit role</button><button class="danger" data-action="remove-environment-member" data-id="${member.user_id}">Remove member</button></td></tr>`).join('')}</tbody></table></div><p class="hint">The last environment Admin cannot be removed or demoted. Application accounts and other environments are unaffected by membership changes.</p></section>` : ''}`;
  if (active?.role === 'Admin')
    $('#addEnvironmentMember').onsubmit = async (event) => {
      event.preventDefault();
      const button = event.submitter;
      button.disabled = true;
      try {
        await api(
          'environments/' + active.id + '/members',
          'POST',
          Object.fromEntries(new FormData(event.target)),
        );
        await refresh();
      } catch (e) {
        error(e);
        button.disabled = false;
      }
    };
}

function renderContent() {
  $('#breadcrumb').textContent =
    view === 'processes' ? 'Process library' : selected ? 'Value stream map' : 'Value streams';
  if (view === 'processes') return renderProcesses();
  if (selected) return renderStream();
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">SEE THE BIGGER PICTURE</div>
    <div class="title-row">
      <div>
        <h1>Value streams</h1>
        <p>Connect the work you do to the value your customers receive.</p>
      </div>
      <button class="primary" data-action="new-stream">+ Create value stream</button>
    </div>
    <div class="stats">
      <div class="stat">
        <div class="label">Value streams</div>
        <strong>${state.streams.length}</strong>
        <span>Customer journeys mapped</span>
      </div>
      <div class="stat">
        <div class="label">Delivery steps</div>
        <strong>${state.steps.length}</strong>
        <span>Across your value streams</span>
      </div>
      <div class="stat">
        <div class="label">Processes</div>
        <strong>${state.processes.length}</strong>
        <span>In your process library</span>
      </div>
    </div>
    <div class="section-row">
      <h2>Your value streams</h2>
      <span class="label">${state.streams.length} streams</span>
    </div>
    ${state.streams.length
      ? /* HTML */ `
          <div class="grid">
            ${state.streams
              .map(
                (s) => /* HTML */ `
                  <article class="stream-card" data-action="open-stream" data-id="${s.id}">
                    <div class="card-head">
                      <span
                        class="icon ${assignedTags(s)[0]
                          ? 'tagged-icon tag-color-' + assignedTags(s)[0].id
                          : ''}"
                      >
                        ▱
                      </span>
                      <span class="pill">${steps(s.id).length} steps</span>
                    </div>
                    <div>
                      <h3>${esc(s.name)}</h3>
                      <p>
                        ${esc(
                          s.outcome || s.description || 'Define the outcome this stream delivers.',
                        )}
                      </p>
                      ${valueRatingBadges(s)}${tagBadges(s)}
                    </div>
                    <div class="card-meta">
                      <span>${esc(s.customer || 'Customer not defined')}</span>
                      <button class="text-button" data-action="open-stream" data-id="${s.id}">
                        Open map →
                      </button>
                    </div>
                  </article>
                `,
              )
              .join('')}
          </div>
        `
      : /* HTML */ `
          <div class="empty">
            <span class="icon">▱</span>
            <h2>Start with a customer outcome</h2>
            <p>
              Map how your team turns a request into a valuable service.
              <br />
              Try employee onboarding, incident resolution, or a cloud service request.
            </p>
            <button class="primary" data-action="new-stream">+ Create your first stream</button>
          </div>
        `}
  `;
}
function renderStream() {
  const stream = state.streams.find((s) => s.id === selected);
  if (!stream) {
    selected = null;
    return render();
  }
  const items = steps(selected);
  const flowMarkup = /* HTML */ `
    <div class="canvas">
      <div class="flow">
        ${items.length
          ? items
              .map(
                (s, i) =>
                  `${i ? '<div class="arrow">→</div>' : ''}<article class="step" draggable="true" data-id="${s.id}"><div class="step-number">STEP ${String(i + 1).padStart(2, '0')} · DRAG TO REORDER</div><h3>${esc(s.name)}</h3><p>${esc(s.owner || 'No owner assigned')}</p>${valueRatingBadges(s)}${tagBadges(s)}<div class="timing"><div>Cycle time<b>${duration(s.work_minutes)}</b></div><div>Wait time<b>${duration(s.wait_minutes)}</b></div><div>Lead time<b>${duration(s.work_minutes + s.wait_minutes)}</b></div></div>${(
                    getWorkspaceIndex().linksByStep.get(s.id) || []
                  )
                    .map(
                      (l) => /* HTML */ `
                        <div class="linked">
                          ☷ ${esc(getWorkspaceIndex().processes.get(l.process_id)?.name)}
                        </div>
                      `,
                    )
                    .join(
                      '',
                    )}<div class="step-actions"><button class="text-button" data-action="edit-step" data-id="${s.id}">Edit</button><button class="text-button" data-action="link" data-id="${s.id}">Link processes</button><button class="text-button danger" data-action="delete-step" data-id="${s.id}" aria-label="Delete ${esc(s.name)}">×</button></div></article>`,
              )
              .join('')
          : '<p>No steps yet. Add the first activity in your service delivery journey.</p>'}
        <div class="arrow">→</div>
        <button data-action="new-step">+ Add step</button>
      </div>
    </div>
    <p class="hint">
      Drag cards to change the sequence. Link reusable processes to explain how each step is
      delivered. All times are measured in minutes.
    </p>
  `;
  $('#content').innerHTML = /* HTML */ `
    <button class="text-button" data-action="back">← All value streams</button>
    <div class="title-row">
      <div>
        <h1>${esc(stream.name)}</h1>
        <p>
          ${esc(stream.description || 'Follow the journey from customer need to delivered value.')}
        </p>
        ${valueRatingBadges(stream)}${tagBadges(stream)}
      </div>
      <div class="detail-actions">
        <button data-action="edit-stream">Edit details</button>
        <button class="primary" data-action="new-step">+ Add step</button>
        <button class="danger" data-action="delete-stream">Delete</button>
      </div>
    </div>
    <div class="context">
      <div>
        <b>CUSTOMER / BENEFICIARY</b>
        ${esc(stream.customer || 'Not defined')}
      </div>
      <div>
        <b>DESIRED VALUE</b>
        ${esc(stream.outcome || 'Not defined')}
      </div>
    </div>
    ${stats(items, stream)}
    <div class="section-row">
      <h2>${streamMode === 'diagram' ? 'Value stream diagram' : 'Delivery flow'}</h2>
      <div class="view-switch" role="group" aria-label="Stream view">
        <button data-action="diagram-view" aria-pressed="${streamMode === 'diagram'}">
          Diagram
        </button>
        <button data-action="flow-view" aria-pressed="${streamMode === 'flow'}">Edit flow</button>
      </div>
    </div>
    ${streamMode === 'diagram' ? diagramShell(items) : flowMarkup} ${pcfStreamLinks(stream)}
    <section class="voc-panel" aria-label="Voice of the Customer">
      <div class="section-row">
        <h2>Voice of the Customer (VOC)</h2>
        ${canEdit()
          ? '<button class="text-button" data-action="edit-stream">' +
            (stream.voc ? 'Edit customer request' : 'Add customer request') +
            '</button>'
          : ''}
      </div>
      <p>${esc(stream.voc || 'No customer request recorded yet.')}</p>
    </section>
  `;
  if (streamMode === 'diagram' && items.length)
    diagramController = new StreamDiagram($('#streamDiagram'), items);
}
let bpmnProcessId = null;
function importBPMN(id) {
  const process = state.processes.find((p) => p.id === id);
  modal(
    process ? 'Replace BPMN diagram' : 'Import BPMN process',
    (process
      ? `<p>Replace the diagram for ${esc(process.name)}. Process links and tags are retained.</p>`
      : field('name', 'Process name')) +
      '<label>BPMN XML file<input id="bpmnFile" type="file" accept=".bpmn,.xml,text/xml,application/xml" required></label><p>Up to 256 KiB. Imports diagram data only; no workflow execution.</p>',
    async (values) => {
      const file = $('#bpmnFile').files[0];
      if (!file || file.size > 262144)
        throw new Error('Choose a BPMN XML file of at most 256 KiB.');
      const bpmn_xml = await file.text();
      const result = await api(
        'processes' + (process ? '/' + process.id : ''),
        process ? 'PUT' : 'POST',
        process
          ? { bpmn_xml }
          : { name: values.name || file.name.replace(/\.(bpmn|xml)$/i, ''), bpmn_xml },
      );
      bpmnProcessId = process?.id || result.id;
    },
  );
}
function renderProcesses() {
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">MAKE DELIVERY REPEATABLE</div>
    <div class="title-row">
      <div>
        <h1>Process library</h1>
        <p>Document how your services are delivered, then connect processes to your maps.</p>
      </div>
      <div class="detail-actions">
        <button data-action="import-bpmn">Import BPMN XML</button>
        <button class="primary" data-action="new-process">+ Create process</button>
      </div>
    </div>
    ${bpmnMarkup(state.processes.find((p) => p.id === bpmnProcessId))}
    ${state.processes.length
      ? /* HTML */ `
          <div class="grid">
            ${state.processes
              .map(
                (p) => /* HTML */ `
                  <article class="process-card">
                    <div class="card-head">
                      <span class="icon">☷</span>
                      <span class="pill">
                        ${(getWorkspaceIndex().linksByProcess.get(p.id) || []).length} linked steps
                      </span>
                    </div>
                    <div>
                      <h3>${esc(p.name)}</h3>
                      <p>${esc(p.description || 'No description yet')}</p>
                      ${tagBadges(p)}
                    </div>
                    <div class="detail-actions">
                      ${p.bpmn
                        ? `<button data-action="view-bpmn" data-id="${p.id}">View BPMN diagram</button>`
                        : ''}
                      <button data-action="import-bpmn" data-id="${p.id}">
                        ${p.bpmn ? 'Replace' : 'Import'} BPMN XML
                      </button>
                    </div>
                    <div class="card-meta">
                      <span>${esc(p.owner || 'No owner assigned')}</span>
                      <button class="text-button" data-action="edit-process" data-id="${p.id}">
                        View / edit →
                      </button>
                    </div>
                    <button
                      class="text-button danger"
                      data-action="delete-process"
                      data-id="${p.id}"
                    >
                      Delete process
                    </button>
                  </article>
                `,
              )
              .join('')}
          </div>
        `
      : '<div class="empty"><h2>Your shared process library</h2><p>Create processes such as incident triage, change approval, or access provisioning.<br>A process can support multiple steps across value streams.</p></div>'}
  `;
  const canvas = $('#bpmnCanvas');
  if (canvas) {
    $('#bpmnZoom').oninput = (event) => {
      const svg = canvas.querySelector('svg'),
        scale = Number(event.target.value) / 100;
      svg.setAttribute('width', Number(svg.dataset.width) * scale);
      svg.setAttribute('height', Number(svg.dataset.height) * scale);
    };
    let drag;
    canvas.onpointerdown = (event) => {
      if (event.button !== 0) return;
      drag = { x: event.clientX, y: event.clientY, left: canvas.scrollLeft, top: canvas.scrollTop };
      canvas.setPointerCapture(event.pointerId);
    };
    canvas.onpointermove = (event) => {
      if (!drag) return;
      canvas.scrollLeft = drag.left - event.clientX + drag.x;
      canvas.scrollTop = drag.top - event.clientY + drag.y;
    };
    canvas.onpointerup = canvas.onpointercancel = () => {
      drag = null;
    };
  }
}
const field = (name, label, value = '', type = 'text', optional = false) => /* HTML */ `
  <label>
    ${label}${type === 'textarea'
      ? /* HTML */ `
          <textarea name="${name}" aria-label="${esc(label)}">${esc(value)}</textarea>
        `
      : /* HTML */ `
          <input
            name="${name}"
            type="${type}"
            value="${esc(value)}"
            ${name === 'name' ? 'required maxlength="200"' : ''}
            ${type === 'number' ? `min="0" step="any" ${optional ? '' : 'required'}` : ''}
          />
        `}
  </label>
`;
function modal(title, html, save, readOnly = false) {
  $('#dialogTitle').textContent = title;
  $('#fields').innerHTML = html;
  saveForm = readOnly ? null : save;
  $('#form button[type="submit"]').hidden = readOnly;
  if (readOnly)
    $('#fields')
      .querySelectorAll('input,textarea,select')
      .forEach((el) => (el.disabled = true));
  $('#editor').showModal();
}
function assignedTags(entity) {
  const catalog = getWorkspaceIndex().tags;
  return (entity.tag_ids || [])
    .map((id) => catalog.get(id))
    .filter(Boolean)
    .sort(
      (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.id - b.id,
    );
}
function tagBadges(entity) {
  const badges = assignedTags(entity).map((tag) => {
    const category =
      state.tag_categories.find((item) => item.id === tag.category_id)?.name || 'Uncategorized';
    return `<span class="tag-badge tag-color-${tag.id}" title="${esc(category)}">${esc(tag.name)}</span>`;
  });
  return `<div class="tag-badges">${badges.join('')}</div>`;
}
function tagPicker(entity) {
  const groups = [
    ...state.tag_categories.map((c) => ({ id: c.id, name: c.name })),
    { id: null, name: 'Uncategorized' },
  ];
  return /* HTML */ `
    <fieldset class="tag-picker">
      <legend>Tags</legend>
      <input type="hidden" name="_tag_picker" value="1" />
      ${groups
        .map((c) => {
          const tags = state.tags
            .filter((t) => t.category_id === c.id)
            .sort((a, b) => a.name.localeCompare(b.name));
          return tags.length
            ? /* HTML */ `
                <div class="tag-group">
                  <b>${esc(c.name)}</b>
                  ${tags
                    .map(
                      (t) => /* HTML */ `
                        <label class="check">
                          <input
                            type="checkbox"
                            name="_tag_${t.id}"
                            ${(entity.tag_ids || []).includes(t.id) ? 'checked' : ''}
                          />
                          <span class="tag-badge tag-color-${t.id}">${esc(t.name)}</span>
                        </label>
                      `,
                    )
                    .join('')}
                </div>
              `
            : '';
        })
        .join('') || '<p>No tags available. An Admin can create tags under Tags & categories.</p>'}
    </fieldset>
  `;
}
function renderTags() {
  $('#breadcrumb').textContent = 'Administrator / Tags & categories';
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">ADMINISTRATOR</div>
    <div class="title-row">
      <div>
        <h1>Tags & categories</h1>
        <p>
          Manage tags and categories for ${esc(session.environment.name)}. Names are unique within
          this environment. Environment Admins manage this catalog; Admins and Editors can assign
          tags to streams, steps, and processes.
        </p>
      </div>
      <div class="detail-actions">
        <button data-action="new-category">+ Create category</button>
        <button class="primary" data-action="new-tag">+ Create tag</button>
      </div>
    </div>
    ${[...state.tag_categories, { id: null, name: 'Uncategorized' }]
      .map(
        (c) => /* HTML */ `
          <section class="tag-management">
            <div class="section-row">
              <h2>${esc(c.name)}</h2>
              ${c.id
                ? /* HTML */ `
                    <div class="detail-actions">
                      <button data-action="edit-category" data-id="${c.id}">Edit category</button>
                      <button class="danger" data-action="delete-category" data-id="${c.id}">
                        Delete category
                      </button>
                    </div>
                  `
                : ''}
            </div>
            <div class="tag-list">
              ${state.tags
                .filter((t) => t.category_id === c.id)
                .map(
                  (t) => /* HTML */ `
                    <div class="tag-management-row">
                      <span class="tag-badge tag-color-${t.id}">${esc(t.name)}</span>
                      <code>${esc(t.color)}</code>
                      <button data-action="edit-tag" data-id="${t.id}">Edit</button>
                      <button class="danger" data-action="delete-tag" data-id="${t.id}">
                        Remove
                      </button>
                    </div>
                  `,
                )
                .join('') || '<p>No tags in this category.</p>'}
            </div>
          </section>
        `,
      )
      .join('')}
    <p class="hint">
      Deleting a category moves its tags to Uncategorized. Deleting a tag removes its assignments.
      For a stream with several tags, its icon uses the first tag alphabetically.
    </p>
  `;
}
function editTag(id) {
  const tag = state.tags.find((t) => t.id === id) || { color: '#e6404d' };
  modal(
    id ? 'Edit tag' : 'Create tag',
    field('name', 'Tag name', tag.name) +
      field('color', 'Tag color', tag.color, 'color') +
      /* HTML */ `
        <label>
          Category
          <select name="category_id" aria-label="Category">
            <option value="">Uncategorized</option>
            ${state.tag_categories
              .map(
                (c) => /* HTML */ `
                  <option value="${c.id}" ${c.id === tag.category_id ? 'selected' : ''}>
                    ${esc(c.name)}
                  </option>
                `,
              )
              .join('')}
          </select>
        </label>
      `,
    (data) => {
      data.category_id = data.category_id ? Number(data.category_id) : null;
      return api('tags' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data);
    },
  );
}
function editCategory(id) {
  const category = state.tag_categories.find((c) => c.id === id) || {};
  modal(
    id ? 'Edit category' : 'Create category',
    field('name', 'Category name', category.name),
    (data) => api('tag_categories' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data),
  );
}
function pcfChildren(...args) {
  return pcf.pcfChildren(...args);
}
function pcfLabel(...args) {
  return pcf.pcfLabel(...args);
}
function pcfPicker(...args) {
  return pcf.pcfPicker(...args);
}
function pcfStreamLinks(...args) {
  return pcf.pcfStreamLinks(...args);
}
function pcfActions(...args) {
  return pcf.pcfActions(...args);
}
function pcfIndex(...args) {
  return pcf.pcfIndex(...args);
}
function pcfImportPanel(...args) {
  return pcf.pcfImportPanel(...args);
}
function renderPCFImportPreview(...args) {
  return pcf.renderPCFImportPreview(...args);
}
function previewPCFImport(...args) {
  return pcf.previewPCFImport(...args);
}
function applyPCFImport(...args) {
  return pcf.applyPCFImport(...args);
}
function renderPCF(...args) {
  return pcf.renderPCF(...args);
}
function editPCF(...args) {
  return pcf.editPCF(...args);
}
function valueRatingPicker(item) {
  return `<fieldset class="value-ratings"><legend>Value-add classifications</legend>${[
    ['cva', 'Customer Value Add (CVA)'],
    ['bva', 'Business Value Add (BVA)'],
  ]
    .map(
      ([key, label]) =>
        `<label>${label}<select name="${key}" aria-label="${label}"><option value="">Not rated</option>${[3, 2, 1].map((rating) => `<option value="${rating}" ${item[key] === rating ? 'selected' : ''}>${rating} - ${VALUE_RATINGS[rating]}</option>`).join('')}</select></label>`,
    )
    .join('')}</fieldset>`;
}
function valueRatingBadges(item) {
  return `<div class="value-rating-badges">${['cva', 'bva'].map((key) => `<span class="pill" title="${key === 'cva' ? 'Customer' : 'Business'} Value Add">${key.toUpperCase()}: ${item[key] ? `${item[key]} - ${VALUE_RATINGS[item[key]]}` : 'Not rated'}</span>`).join('')}</div>`;
}
function taktTimeFields(stream) {
  return `<fieldset class="value-ratings"><legend>Takt time — customer demand</legend><p class="hint">Use the same period for both inputs. Net available working time excludes breaks and planned downtime; demand is the number of customer requests to deliver in that period.</p>${field('takt_available_minutes', 'Net available working time (minutes per period)', stream.takt_available_minutes, 'number', true)}${field('takt_demand', 'Customer demand (requests per period)', stream.takt_demand, 'number', true)}<label>Demand period<select name="takt_period" aria-label="Demand period">${['shift', 'day', 'week', 'month'].map((period) => `<option value="${period}" ${period === (stream.takt_period || 'day') ? 'selected' : ''}>${period}</option>`).join('')}</select></label><p class="hint">Takt time = net available working time ÷ customer demand. Leave either input blank to keep it unset.</p></fieldset>`;
}
function formSection(title, fields) {
  return `<fieldset class="form-section"><legend>${esc(title)}</legend>${fields}</fieldset>`;
}
function stepFields(step) {
  const summary = serviceTimeMetrics([step]);
  return (
    formSection(
      'Step details',
      field('name', 'Step name', step.name) +
        field('owner', 'Responsible team / owner', step.owner),
    ) +
    formSection(
      'Service timing',
      field('work_minutes', 'Active work time (minutes)', step.work_minutes, 'number') +
        field('wait_minutes', 'Waiting time (minutes)', step.wait_minutes, 'number') +
        `<p id="stepTimePreview" class="hint" aria-live="polite">Cycle time (active work): ${duration(summary.cycle)} · Lead time (work + wait): ${duration(summary.lead)}</p>`,
    ) +
    valueRatingPicker(step) +
    tagPicker(step)
  );
}
function editStream(id) {
  const s = state.streams.find((s) => s.id === id) || {};
  modal(
    id ? 'Edit value stream' : 'Create value stream',
    formSection(
      'Stream details',
      field('name', 'Stream name', s.name) +
        field('customer', 'Customer / beneficiary', s.customer) +
        field('outcome', 'Desired value', s.outcome) +
        field('description', 'Description', s.description, 'textarea'),
    ) +
      valueRatingPicker(s) +
      taktTimeFields(s) +
      formSection(
        'Voice of the Customer',
        field('voc', 'Voice of the Customer (VOC)', s.voc, 'textarea') +
          '<p class="hint">What is the customer actually asking us to deliver? Use their own words.</p>',
      ) +
      tagPicker(s) +
      pcfPicker(s),
    async (data) => {
      const result = await api('streams' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data);
      selected = result.id;
    },
  );
}
function editStep(id) {
  const s = state.steps.find((s) => s.id === id) || { work_minutes: 0, wait_minutes: 0 };
  modal(id ? 'Edit step' : 'Add delivery step', stepFields(s), async (data) => {
    data.work_minutes = Number(data.work_minutes);
    data.wait_minutes = Number(data.wait_minutes);
    if (!id) {
      data.stream_id = selected;
      data.position = Math.max(-1, ...steps(selected).map((s) => s.position)) + 1;
    }
    await api('steps' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data);
  });
  const updateTimes = () => {
    const work = Number($('#fields [name="work_minutes"]').value);
    const wait = Number($('#fields [name="wait_minutes"]').value);
    $('#stepTimePreview').textContent =
      Number.isFinite(work + wait) && work >= 0 && wait >= 0
        ? `Cycle time (active work): ${duration(work)} · Lead time (work + wait): ${duration(work + wait)}`
        : 'Enter valid nonnegative times.';
  };
  for (const name of ['work_minutes', 'wait_minutes'])
    $('#fields [name="' + name + '"]').oninput = updateTimes;
  updateTimes();
}
function editProcess(id) {
  const p = state.processes.find((p) => p.id === id) || {};
  modal(
    id ? 'Process details' : 'Create process',
    field('name', 'Process name', p.name) +
      field('owner', 'Process owner', p.owner) +
      field('description', 'Purpose / description', p.description, 'textarea') +
      field('procedure', 'Procedure / work instructions', p.procedure, 'textarea') +
      tagPicker(p),
    (data) => api('processes' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data),
    !canEdit(),
  );
}
$('#form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!saveForm) return;
  const button = e.submitter;
  button.disabled = true;
  try {
    await saveForm(formValues(e.target));
    $('#editor').close();
    await refresh();
  } catch (e) {
    error(e);
  } finally {
    button.disabled = false;
  }
});
$('#fields').addEventListener('input', (event) => {
  if (event.target.id === 'pcfLinkSearch') {
    const query = event.target.value.toLowerCase();
    $('#fields')
      .querySelectorAll('[data-pcf-search]')
      .forEach((option) => (option.hidden = !option.dataset.pcfSearch.includes(query)));
  }
});
$('#closeDialog').onclick = $('#cancelDialog').onclick = () => $('#editor').close();
$('#editor').addEventListener('close', () => {
  $('#fields').replaceChildren();
  saveForm = null;
});
$('#content').addEventListener('change', (event) => {
  const reference = event.target.dataset.pcfDecision;
  if (reference && pcf.importState) {
    pcf.importState.decisions[reference] = event.target.value;
    const button = $('[data-action="pcf-import-apply"]');
    if (button)
      button.disabled = pcf.importState.preview.duplicates.some(
        (row) => !pcf.importState.decisions[row.reference],
      );
  }
});
$('#content').addEventListener('click', async (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const id = Number(el.dataset.id);
  try {
    if (el.dataset.action === 'new-environment') return editEnvironment();
    if (el.dataset.action === 'switch-environment') return await switchEnvironment(id);
    if (el.dataset.action === 'environment-content') return navigate(el.dataset.target);
    if (
      [
        'edit-environment',
        'delete-environment',
        'edit-environment-member',
        'remove-environment-member',
      ].includes(el.dataset.action)
    ) {
      if (!environmentAdmin()) return;
      if (el.dataset.action === 'edit-environment') return editEnvironment(id);
      if (el.dataset.action === 'delete-environment') {
        const environment = environmentList.find((item) => item.id === id);
        if (!environment) return;
        if (
          !confirm(
            `Delete environment "${environment.name}" and ALL its value streams, delivery steps, processes, PCF entries, tags, categories, and links? This cannot be undone without a backup.`,
          )
        )
          return;
        await api('environments/' + id, 'DELETE', { confirmation: environment.name });
        resetEnvironmentView();
        view = 'environments';
        await refresh();
        return;
      }
      const member = environmentMembers.find((item) => item.user_id === id);
      if (el.dataset.action === 'edit-environment-member')
        return modal(
          'Edit environment role',
          `<label>Environment role<select name="role" aria-label="Environment role">${['Admin', 'Editor', 'Viewer'].map((role) => `<option ${role === member.role ? 'selected' : ''}>${role}</option>`).join('')}</select></label>`,
          (data) => api('environments/' + session.environment.id + '/members/' + id, 'PUT', data),
        );
      if (!confirm('Remove this account from the environment?')) return;
      await api('environments/' + session.environment.id + '/members/' + id, 'DELETE', {});
      await refresh();
      return;
    }
    if (EDIT_ACTIONS.has(el.dataset.action) && !canEdit()) return;
    if (el.dataset.action.startsWith('pcf-import-')) {
      if (!canEdit()) return;
      if (el.dataset.action === 'pcf-import-toggle') {
        pcf.importOpen = !pcf.importOpen;
        $('#pcfImportPanel').hidden = !pcf.importOpen;
        el.setAttribute('aria-expanded', String(pcf.importOpen));
        if (pcf.importOpen) $('#pcfCSVFile').focus();
        return;
      }
      if (el.dataset.action === 'pcf-import-preview') return await previewPCFImport(el);
      if (el.dataset.action === 'pcf-import-apply') return await applyPCFImport(el);
      if (el.dataset.action === 'pcf-import-cancel') {
        pcf.importState = null;
        $('#pcfImportPreview').replaceChildren();
        return;
      }
      if (
        pcf.importState &&
        ['pcf-import-all-yes', 'pcf-import-all-no'].includes(el.dataset.action)
      ) {
        const choice = el.dataset.action === 'pcf-import-all-yes' ? 'yes' : 'no';
        pcf.importState.decisions = Object.fromEntries(
          pcf.importState.preview.duplicates.map((row) => [row.reference, choice]),
        );
        renderPCFImportPreview();
        return;
      }
    }

    if (el.dataset.action === 'pcf-index' || el.dataset.action === 'pcf-tiles') {
      pcf.mode = el.dataset.action === 'pcf-index' ? 'index' : 'tiles';
      return render();
    }
    if (el.dataset.action === 'pcf-root') {
      pcf.focus = null;
      return render();
    }
    if (el.dataset.action === 'open-pcf') {
      view = 'pcf';
      pcf.focus = id;
      return render();
    }
    if (el.dataset.action === 'pcf-stream') {
      view = 'streams';
      selected = id;
      streamMode = 'diagram';
      return render();
    }
    if (['new-pcf', 'edit-pcf', 'delete-pcf'].includes(el.dataset.action)) {
      if (!canEdit()) return;
      if (el.dataset.action === 'new-pcf') return editPCF(null, Number(el.dataset.parent) || null);
      if (el.dataset.action === 'edit-pcf') return editPCF(id);
      if (
        !confirm(
          'Delete this PCF entry? Its value stream links will be removed. Entries with children must have their children moved or removed first.',
        )
      )
        return;
      await api('pcf_entries/' + id, 'DELETE');
      if (pcf.focus === id) pcf.focus = null;
      await refresh();
      return;
    }

    if (
      [
        'new-tag',
        'edit-tag',
        'delete-tag',
        'new-category',
        'edit-category',
        'delete-category',
      ].includes(el.dataset.action)
    ) {
      if (!environmentAdmin()) return;
      if (el.dataset.action === 'new-tag') return editTag();
      if (el.dataset.action === 'edit-tag') return editTag(id);
      if (el.dataset.action === 'new-category') return editCategory();
      if (el.dataset.action === 'edit-category') return editCategory(id);
      const category = el.dataset.action === 'delete-category';
      if (
        !confirm(
          category
            ? 'Delete this category? Its tags will move to Uncategorized.'
            : 'Remove this tag from the application and all assigned items?',
        )
      )
        return;
      await api((category ? 'tag_categories/' : 'tags/') + id, 'DELETE');
      await refresh();
      return;
    }
    if (el.dataset.action === 'new-account') return editAccount();
    if (el.dataset.action === 'edit-account') return editAccount(id);
    if (el.dataset.action === 'delete-account') {
      if (!confirm('Remove this account? Its sessions will be revoked.')) return;
      await api('users/' + id, 'DELETE');
      await refresh();
      return;
    }
    if (el.dataset.action === 'create-backup') {
      await api('backups/create', 'POST', {});
      await refreshMaintenance();
      return;
    }
    if (el.dataset.action === 'restore-backup') {
      if (
        !confirm(
          'Restore application code, accounts and data from this trusted backup? Current data will be replaced. A safety backup is created first, and you must sign in again using an account from the backup.',
        )
      )
        return;
      await api('backups/restore', 'POST', { id: el.dataset.backup });
      await refreshMaintenance();
      return;
    }
    if (el.dataset.action === 'cleanup-backups') {
      const ids = [...document.querySelectorAll('[name="backup-select"]:checked')].map(
        (el) => el.value,
      );
      if (!ids.length) {
        error(new Error('Select backups to remove.'));
        return;
      }
      if (!confirm(`Permanently delete ${ids.length} selected backup(s)?`)) return;
      await api('backups/cleanup', 'POST', { ids });
      await refresh();
      return;
    }
    if (el.dataset.action === 'check-update' || el.dataset.action === 'install-update') {
      if (
        el.dataset.action === 'install-update' &&
        !confirm(
          'Update to the latest GitHub main revision? A database backup will be made and the application will briefly restart.',
        )
      )
        return;
      await api(
        'updates/' + (el.dataset.action === 'check-update' ? 'check' : 'install'),
        'POST',
        {},
      );
      await refreshMaintenance();
      return;
    }
    switch (el.dataset.action) {
      case 'diagram-view':
        streamMode = 'diagram';
        return render();
      case 'flow-view':
        streamMode = 'flow';
        return render();
      case 'new-stream':
        return editStream();
      case 'edit-stream':
        return editStream(selected);
      case 'open-stream':
        selected = id;
        streamMode = 'diagram';
        return render();
      case 'back':
        selected = null;
        return render();
      case 'new-step':
        return editStep();
      case 'edit-step':
        return editStep(id);
      case 'import-bpmn':
        return importBPMN(id);
      case 'view-bpmn':
        bpmnProcessId = id;
        render();
        return $('#bpmnCanvas')?.focus();
      case 'new-process':
        return editProcess();
      case 'edit-process':
        return editProcess(id);
      case 'link':
        return modal(
          'Link processes',
          state.processes.length
            ? state.processes
                .map(
                  (p) => /* HTML */ `
                    <label class="check">
                      <input
                        type="checkbox"
                        name="process_${p.id}"
                        ${state.step_processes.some(
                          (l) => l.step_id === id && l.process_id === p.id,
                        )
                          ? 'checked'
                          : ''}
                      />
                      ${esc(p.name)}
                    </label>
                  `,
                )
                .join('')
            : '<p>Create a process in the Process library first.</p>',
          async (data) =>
            api(`steps/${id}/processes`, 'PUT', {
              process_ids: Object.keys(data).map((k) => Number(k.slice(8))),
            }),
        );
      case 'delete-stream':
        if (!confirm('Delete this stream and all its steps? Processes will remain in the library.'))
          return;
        await api('streams/' + selected, 'DELETE');
        selected = null;
        break;
      case 'delete-step':
        if (!confirm('Delete this step?')) return;
        await api('steps/' + id, 'DELETE');
        break;
      case 'delete-process':
        if (!confirm('Delete this process and remove all its links?')) return;
        await api('processes/' + id, 'DELETE');
        break;
    }
    await refresh();
  } catch (e) {
    error(e);
  }
});
let dragged;
$('#content').addEventListener('dragstart', (e) => {
  const card = e.target.closest('.step');
  if (card && canEdit()) {
    dragged = Number(card.dataset.id);
    e.dataTransfer.setData('text/plain', String(dragged));
  }
});
$('#content').addEventListener('dragover', (e) => {
  if (e.target.closest('.step')) e.preventDefault();
});
$('#content').addEventListener('drop', async (e) => {
  const target = e.target.closest('.step');
  if (!target || !dragged || !canEdit()) return;
  e.preventDefault();
  const items = steps(selected),
    from = items.findIndex((s) => s.id === dragged),
    to = items.findIndex((s) => s.id === Number(target.dataset.id));
  if (from < 0 || to < 0 || from === to) return;
  items.splice(to, 0, items.splice(from, 1)[0]);
  try {
    for (let i = 0; i < items.length; i++)
      await api('steps/' + items[i].id, 'PUT', { position: i });
    await refresh();
  } catch (e) {
    error(e);
    await refresh();
  } finally {
    dragged = null;
  }
});
function render() {
  diagramController?.destroy();
  diagramController = null;
  clearTimeout(updateTimer);
  const user = session.user;
  document.body.classList.toggle('signed-out', !user);
  $('#adminMenu').hidden = user?.role !== 'Admin' && !environmentAdmin();
  for (const [name, page] of Object.entries(VIEWS)) {
    if (page.adminOnly || page.environmentAdminOnly)
      navigationButton(name).hidden = !canAccessView(name);
  }
  $('#identity').innerHTML = user
    ? /* HTML */ `
        <span>
          ${esc(user.username)}
          <span class="pill" title="Application role">App ${esc(user.role)}</span>
        </span>
        <button id="logout" class="text-button">Sign out</button>
      `
    : '';
  if (user) {
    const identity = $('#identity');
    const selector = document.createElement('label');
    selector.className = 'environment-switch';
    selector.textContent = 'Environment';
    selector.innerHTML += `<select id="environmentSwitcher" aria-label="Active environment">${environmentList.length ? environmentList.map((environment) => `<option value="${environment.id}" ${environment.id === session.environment?.id ? 'selected' : ''}>${esc(environment.name)}</option>`).join('') : '<option value="">No environment</option>'}</select>`;
    identity.prepend(selector);
    $('#environmentSwitcher').disabled = !environmentList.length;
    $('#environmentSwitcher').onchange = (event) =>
      switchEnvironment(Number(event.target.value)).catch(error);
  }
  if (user)
    $('#logout').onclick = async () => {
      try {
        await api('auth/logout', 'POST', {});
        pcf.importState = null;
        session = {};
        selected = null;
        view = 'streams';
        await refresh();
      } catch (e) {
        error(e);
      }
    };
  if (!user) return renderAuth();
  for (const name of Object.keys(VIEWS)) {
    const button = navigationButton(name);
    button.classList.toggle('active', view === name);
    button.disabled = !canAccessView(name);
  }
  VIEWS[view].render();
  if (!canEdit()) {
    $('#content')
      .querySelectorAll('[data-action]')
      .forEach((el) => {
        if (EDIT_ACTIONS.has(el.dataset.action)) el.remove();
      });
    $('#content')
      .querySelectorAll('[draggable]')
      .forEach((el) => (el.draggable = false));
    $('#content')
      .querySelectorAll('.step-number')
      .forEach((el) => (el.textContent = el.textContent.replace(' · DRAG TO REORDER', '')));
    $('#content')
      .querySelectorAll('.hint:not(.diagram-help)')
      .forEach((el) => (el.textContent = 'Read-only access. All times are measured in minutes.'));
    $('#content')
      .querySelectorAll('[data-action="edit-process"]')
      .forEach((el) => (el.textContent = 'View details →'));
  }
}
function applyBranding() {
  document.title = brandSettings.name;
  syncThemeStylesheet();
  $('.brand').innerHTML = `${
    brandSettings.logo
      ? /* HTML */ `
          <img class="brand-logo" src="${esc(brandSettings.logo)}" alt="" />
        `
      : '<b aria-hidden="true">◈</b>'
  }<span>${esc(brandSettings.name)}</span>`;
  $('.aside-bottom').innerHTML =
    `<div class="footer-details">${esc(brandSettings.tagline)}<br><span>● Data stays on your server</span></div><a class="app-version" href="https://github.com/CoxkeLamagra/VSM-Toolbox-Win" target="_blank" rel="noopener noreferrer" title="${esc('GitHub repository · Revision: ' + (session.revision || 'local'))}">v${esc(session.version || 'development')}</a>`;
}
let maintenanceState = { operation: {}, supported: false };
function renderDataManagement() {
  const operation = maintenanceState.operation || {},
    busy = MAINTENANCE_BUSY.has(operation.state);
  const disabled = busy || !operation.available || !maintenanceState.supported;
  $('#breadcrumb').textContent = 'Administrator / Data management';
  $('#content').innerHTML =
    `<div class="eyebrow">ADMINISTRATOR</div><div class="title-row"><div><h1>Data management</h1><p>Clear business content after creating a local safety backup.</p></div></div><section class="data-danger-panel"><h2>Clear all business data</h2><p class="data-warning" role="note"><strong>Warning: this removes content in ALL environments: value streams, delivery steps / process flows, processes, PCF entries, and their links.</strong> Accounts, branding, tag definitions, tag categories, and existing backups remain.</p><p>A full local application and database backup is required before deletion. If backup creation fails, nothing is deleted. The application restarts briefly; failed startup restores the backup. You can restore the saved backup from Backups & restore.</p><p role="status">${esc(operation.message || 'Ready')}</p>${operation.backup ? `<p>Latest operation backup: <code>${esc(operation.backup)}</code></p>` : ''}${!maintenanceState.supported ? '<p class="hint">Rerun the Windows service installer to enable safe data clearing.</p>' : ''}<form id="clearDataForm"><label>Type DELETE ALL BUSINESS DATA to confirm<input name="confirmation" aria-label="Confirmation text" autocomplete="off" required pattern="DELETE ALL BUSINESS DATA" ${disabled ? 'disabled' : ''}></label><button class="danger" type="submit" ${disabled ? 'disabled' : ''}>Create backup and clear all data</button></form></section>`;
  $('#clearDataForm').onsubmit = async (event) => {
    event.preventDefault();
    if (
      !confirm(
        'FINAL WARNING: delete every value stream, delivery step / process flow, process, and PCF entry? A full local backup must be created first. Accounts, branding, tag definitions, tag categories, and backup files will remain. Continue?',
      )
    )
      return;
    const button = event.submitter;
    button.disabled = true;
    try {
      await api('maintenance/clear-data', 'POST', Object.fromEntries(new FormData(event.target)));
      await refreshMaintenance();
    } catch (e) {
      error(e);
      button.disabled = false;
    }
  };
  if (busy) pollMaintenance();
}
function renderBranding() {
  $('#breadcrumb').textContent = 'Administrator / Branding & theme';
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">ADMINISTRATOR</div>
    <div class="title-row">
      <div>
        <h1>Branding & theme</h1>
        <p>Make this workspace your own. Settings apply to all users and the sign-in page.</p>
      </div>
    </div>
    <form id="brandingForm" class="branding-panel">
      <h2>Workspace identity</h2>
      <label>
        Application name
        <input name="name" value="${esc(brandSettings.name)}" required maxlength="64" />
      </label>
      <label>
        Workspace tagline
        <input name="tagline" value="${esc(brandSettings.tagline)}" required maxlength="120" />
      </label>
      <label>
        Logo (PNG or JPEG, up to 128 KiB)
        <input id="brandLogoFile" type="file" accept="image/png,image/jpeg" />
      </label>
      ${brandSettings.logo
        ? /* HTML */ `
            <img
              class="logo-preview"
              src="${esc(brandSettings.logo)}"
              alt="Current workspace logo"
            />
          `
        : ''}
      <label class="check">
        <input id="removeBrandLogo" type="checkbox" />
        Remove current logo
      </label>
      <h2>Theme colors</h2>
      <div class="brand-colors">
        ${[
          ['primary', 'Accent / buttons'],
          ['sidebar', 'Navigation background'],
          ['background', 'Page background'],
        ]
          .map(
            ([key, label]) => /* HTML */ `
              <label>
                ${label}
                <input name="${key}" type="color" value="${esc(brandSettings[key])}" />
              </label>
            `,
          )
          .join('')}
      </div>
      <p class="hint">
        Button and navigation text automatically use a contrasting color. All assets stay on this
        server.
      </p>
      <p id="brandingStatus" role="status"></p>
      <div class="detail-actions">
        <button class="primary" type="submit">Save branding</button>
        <button id="resetBranding" type="button">Restore default theme</button>
      </div>
    </form>
  `;
  async function save(theme) {
    await api('branding', 'PUT', theme);
    await refresh();
    $('#brandingStatus').textContent = 'Branding saved.';
  }
  $('#brandingForm').onsubmit = async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      const theme = {
        ...Object.fromEntries(new FormData(event.target)),
        logo: $('#removeBrandLogo').checked ? '' : brandSettings.logo,
      };
      const file = $('#brandLogoFile').files[0];
      if (file) {
        if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 131072)
          throw new Error('Choose a PNG or JPEG up to 128 KiB.');
        theme.logo = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = () => reject(new Error('Could not read logo.'));
          reader.readAsDataURL(file);
        });
      }
      await save(theme);
    } catch (e) {
      error(e);
    } finally {
      button.disabled = false;
    }
  };
  $('#resetBranding').onclick = async () => {
    if (confirm('Restore the default name, logo, and theme for all users?'))
      try {
        await save({ ...brandDefaults });
      } catch (e) {
        error(e);
      }
  };
}
function credentialFields(username = '', passwordRequired = true) {
  return /* HTML */ `
    <label>
      Username
      <input
        name="username"
        aria-label="Username"
        value="${esc(username)}"
        required
        minlength="3"
        maxlength="64"
        autocomplete="username"
      />
      <span class="hint">3–64 letters, numbers, dots, underscores or hyphens.</span>
    </label>
    <label>
      ${passwordRequired ? 'Password' : 'New password (leave blank to keep current)'}
      <input
        name="password"
        aria-label="${passwordRequired ? 'Password' : 'New password'}"
        type="password"
        ${passwordRequired ? 'required' : ''}
        minlength="12"
        maxlength="128"
        autocomplete="new-password"
      />
      <span class="hint">Use 12–128 characters.</span>
    </label>
  `;
}
function renderAuth() {
  $('#breadcrumb').textContent = 'Local accounts';
  const setup = session.setup_required;
  $('#content').innerHTML = /* HTML */ `
    <div class="auth-card">
      <span class="icon">◈</span>
      <h1>${setup ? 'Create your Admin account' : 'Welcome back'}</h1>
      <p>
        ${setup
          ? 'The first account administers this local workspace. Additional accounts are created by an Admin.'
          : `Sign in to your local ${esc(brandSettings.name)} workspace.`}
      </p>
      <form id="authForm">
        ${setup
          ? credentialFields()
          : /* HTML */ `
              <label>
                Username
                <input name="username" autocomplete="username" required maxlength="64" />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autocomplete="current-password"
                  required
                  maxlength="128"
                />
              </label>
            `}
        <button class="primary" type="submit">${setup ? 'Create Admin account' : 'Sign in'}</button>
        <p id="authError" role="alert"></p>
      </form>
    </div>
  `;
  $('#authForm').onsubmit = async (e) => {
    e.preventDefault();
    const button = e.submitter;
    button.disabled = true;
    try {
      session = await api(
        'auth/' + (setup ? 'register' : 'login'),
        'POST',
        Object.fromEntries(new FormData(e.target)),
      );
      await refresh();
    } catch (e) {
      $('#authError').textContent = e.message;
    } finally {
      button.disabled = false;
    }
  };
}
function renderAccounts() {
  $('#breadcrumb').textContent = 'Administrator / Accounts';
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">ADMINISTRATOR</div>
    <div class="title-row">
      <div>
        <h1>Account administration</h1>
        <p>Manage local accounts and their access to this workspace.</p>
      </div>
      <button class="primary" data-action="new-account">+ Create Account</button>
    </div>
    <div class="role-guide">
      <div>
        <b>Admin</b>
        <p>Manage application accounts, backups, branding, and updates.</p>
      </div>
      <div>
        <b>Editor</b>
        <p>Edit content in environments where this role applies.</p>
      </div>
      <div>
        <b>Viewer</b>
        <p>Read content in assigned environments.</p>
      </div>
    </div>
    <div class="section-row">
      <h2>Account Overview</h2>
      <span class="label">${accounts.length} accounts</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Role</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${accounts
            .map(
              (u) => /* HTML */ `
                <tr>
                  <td>
                    ${esc(u.username)}
                    ${u.id === session.user.id ? '<span class="pill">You</span>' : ''}
                  </td>
                  <td><span class="pill">${esc(u.role)}</span></td>
                  <td>
                    <button class="text-button" data-action="edit-account" data-id="${u.id}">
                      Edit
                    </button>
                    <button
                      class="text-button danger"
                      data-action="delete-account"
                      data-id="${u.id}"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              `,
            )
            .join('')}
        </tbody>
      </table>
    </div>
    <p class="hint">
      The last Admin cannot be removed or demoted. Editing an account revokes its existing sessions.
    </p>
  `;
}
function editAccount(id) {
  const u = accounts.find((u) => u.id === id) || { role: 'Viewer' };
  modal(
    id ? 'Edit account' : 'Create Account',
    credentialFields(u.username, !id) +
      /* HTML */ `
        <label>
          Role
          <select name="role" aria-label="Role">
            ${['Admin', 'Editor', 'Viewer']
              .map(
                (role) => /* HTML */ `
                  <option ${u.role === role ? 'selected' : ''}>${role}</option>
                `,
              )
              .join('')}
          </select>
        </label>
      `,
    async (data) => {
      if (id && !data.password) delete data.password;
      await api('users' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', data);
    },
  );
}
function renderUpdates() {
  $('#breadcrumb').textContent = 'Administrator / Updates';
  const busy = MAINTENANCE_BUSY.has(updateStatus.state);
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">ADMINISTRATOR</div>
    <div class="title-row">
      <div>
        <h1>Application updates</h1>
        <p>Install the latest published revision of the GitHub main branch.</p>
      </div>
      <button data-action="check-update" ${busy || !updateStatus.available ? 'disabled' : ''}>
        Check for updates
      </button>
    </div>
    <div class="context update-panel">
      <div>
        <b>INSTALLED REVISION</b>
        <code>${esc(updateStatus.current_revision || 'Local checkout')}</code>
      </div>
      <div>
        <b>LATEST MAIN REVISION</b>
        <code>${esc(updateStatus.latest_revision || 'Not checked')}</code>
      </div>
      <div>
        <b>STATUS</b>
        <p role="status">${esc(updateStatus.message || 'Ready to check for updates.')}</p>
      </div>
      <button
        class="primary"
        data-action="install-update"
        ${busy || !updateStatus.available || updateStatus.backup_protocol !== 1 ? 'disabled' : ''}
      >
        Update from main
      </button>
    </div>
    <p class="hint">
      A complete application and database backup is made before installation. The application
      restarts briefly and checks its health; failed updates restore the previous code and database.
      Existing sessions are revoked after an update, so you may need to sign in again. GitHub
      connectivity is required only when checking or installing updates.
    </p>
    ${updateStatus.available && updateStatus.backup_protocol !== 1
      ? '<p>Install the updated supervisor using the Windows service installer to guarantee full application backups before updating.</p>'
      : ''}${!updateStatus.available
      ? '<p>Web updates require the update supervisor. Install this version using the Windows service installer once to enable them.</p>'
      : ''}
  `;
  if (busy) pollMaintenance();
}
function renderBackups() {
  $('#breadcrumb').textContent = 'Administrator / Backups & restore';
  const operation = backupState.operation || {},
    busy = MAINTENANCE_BUSY.has(operation.state);
  const disabled = busy || !operation.available || !backupState.supported;
  const size = (n) =>
    n < 1024 * 1024 ? (n / 1024).toFixed(1) + ' KiB' : (n / 1024 / 1024).toFixed(1) + ' MiB';
  $('#content').innerHTML = /* HTML */ `
    <div class="eyebrow">ADMINISTRATOR</div>
    <div class="title-row">
      <div>
        <h1>Backups & restore</h1>
        <p>Protect your application, accounts, and value stream data.</p>
      </div>
      <button class="primary" data-action="create-backup" ${disabled ? 'disabled' : ''}>
        Create manual backup
      </button>
    </div>
    <div class="context update-panel">
      <div>
        <b>OPERATION STATUS</b>
        <p role="status">${esc(operation.message || 'Ready')}</p>
      </div>
      <div>
        <b>RESTORE AN UPLOADED BACKUP</b>
        <p>
          Upload a trusted .vsm-backup.zip file (up to 128 MiB). The file is validated before
          restoration. Backups contain application code and account data.
        </p>
        <label>
          Backup file
          <input id="backupUpload" type="file" accept=".zip" ${disabled ? 'disabled' : ''} />
        </label>
        <button id="uploadBackup" ${disabled ? 'disabled' : ''}>Upload and restore</button>
      </div>
      ${!backupState.supported
        ? '<p>Enable the updated supervisor with the Windows service installer before using backup management.</p>'
        : ''}
    </div>
    <div class="section-row">
      <h2>Saved backups</h2>
      <button class="danger" data-action="cleanup-backups" ${disabled ? 'disabled' : ''}>
        Delete selected backups
      </button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Select</th>
            <th>Created / reason</th>
            <th>Revision</th>
            <th>Size</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${backupState.backups
            .map(
              (b) => /* HTML */ `
                <tr>
                  <td>
                    <input
                      aria-label="Select ${esc(b.id)}"
                      type="checkbox"
                      name="backup-select"
                      value="${esc(b.id)}"
                      ${disabled || b.protected ? 'disabled' : ''}
                    />
                  </td>
                  <td>
                    ${esc(new Date(b.created_at * 1000).toLocaleString())}
                    <p>${esc(b.reason || 'Backup')}</p>
                  </td>
                  <td><code>${esc(b.revision || 'Legacy database backup')}</code></td>
                  <td>${size(b.size)}</td>
                  <td>
                    <a href="/api/backups/${encodeURIComponent(b.id)}/download" download>
                      Download
                    </a>
                    <button
                      class="text-button"
                      data-action="restore-backup"
                      data-backup="${esc(b.id)}"
                      ${disabled || !b.restorable ? 'disabled' : ''}
                    >
                      Restore
                    </button>
                  </td>
                </tr>
              `,
            )
            .join('') ||
          '<tr><td colspan="5">No backups yet. Create a manual backup to get started.</td></tr>'}
        </tbody>
      </table>
    </div>
    <p class="hint">
      Every update and restore creates a safety backup before changing the application. Backups
      needed by an active recovery cannot be deleted. Download important backups to another system;
      local backups do not protect against disk loss. Restoring revokes sessions and restores the
      accounts and passwords contained in that backup.
    </p>
  `;
  $('#uploadBackup').onclick = async () => {
    const file = $('#backupUpload').files[0];
    if (!file) {
      error(new Error('Choose a backup file first.'));
      return;
    }
    if (file.size > 128 * 1024 * 1024) {
      error(new Error('Backup exceeds 128 MiB.'));
      return;
    }
    if (
      !confirm(
        'Only restore a trusted application backup. This installs its application code and replaces current accounts and data. A safety backup will be created first. Continue?',
      )
    )
      return;
    const button = $('#uploadBackup');
    button.disabled = true;
    try {
      const response = await fetch('/api/backups/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': session.csrf_token },
        body: file,
      });
      const result = await readResponse(response);
      if (!response.ok) throw new Error(result.error || 'Upload failed');
      await api('backups/restore', 'POST', { id: result.id });
      await refreshMaintenance();
    } catch (e) {
      error(e);
      button.disabled = false;
    }
  };
  if (busy) pollMaintenance();
}
// Diagram positions are a personal view; they never change delivery order or process links.
function diagramShell(items) {
  if (!items.length)
    return '<div class="empty"><h2>No delivery steps yet</h2><p>Add a step to start mapping your value stream and its processes.</p><button data-action="new-step">+ Add step</button></div>';
  const ids = new Set(items.map((s) => s.id));
  const linked = new Set(
    state.step_processes.filter((l) => ids.has(l.step_id)).map((l) => l.process_id),
  );
  return /* HTML */ `
    <div class="diagram-frame">
      <div class="diagram-toolbar">
        <div class="diagram-legend">
          <span>
            <i class="legend-step"></i>
            Delivery steps
            <b>${items.length}</b>
          </span>
          <span>
            <i class="legend-process"></i>
            Linked processes
            <b>${linked.size}</b>
          </span>
        </div>
        <div class="diagram-controls" role="group" aria-label="Diagram controls">
          <button data-diagram="zoom-out" aria-label="Zoom out" title="Zoom out (−)">−</button>
          <output id="diagramZoom" aria-label="Zoom level">100%</output>
          <button data-diagram="zoom-in" aria-label="Zoom in" title="Zoom in (+)">+</button>
          <button data-diagram="fit">Fit to screen</button>
          <button data-diagram="reset">Reset layout</button>
        </div>
      </div>
      <svg
        id="streamDiagram"
        class="stream-diagram"
        role="region"
        aria-label="Interactive value stream diagram"
        aria-describedby="diagramHelp"
        tabindex="0"
      >
        <defs>
          <pattern id="diagramGrid" width="24" height="24" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="1" class="diagram-dot" />
          </pattern>
          <marker
            id="flowArrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        <rect width="100%" height="100%" fill="url(#diagramGrid)" />
        <g class="diagram-scene">
          <g class="diagram-edges"></g>
          <g class="diagram-nodes"></g>
        </g>
      </svg>
    </div>
    <p id="diagramHelp" class="hint diagram-help">
      Drag the background to pan; drag a card to arrange it. Scroll or pinch to zoom. Double-click a
      step or process for details. With the diagram focused, use + / − to zoom and 0 to fit; use
      arrow keys to pan, or to move a focused card. Layout is saved in this browser. Delivery order
      is managed in Edit flow.
    </p>
  `;
}

const pcf = createPCFFeature({
  getState: () => state,
  getView: () => view,
  canEdit,
  api,
  refresh,
  modal,
  field,
  $,
});

const StreamDiagram = createStreamDiagram({
  getState: () => state,
  getSession: () => session,
  getSelected: () => selected,
  assignedTags,
  editProcess,
  editStep,
  canEdit,
  modal,
  stepFields,
  $,
});

$('#adminToggle').onclick = () => {
  const expanded = $('#adminToggle').getAttribute('aria-expanded') !== 'true';
  $('#adminToggle').setAttribute('aria-expanded', String(expanded));
  $('#adminEntries').hidden = !expanded;
};
$('#sidebarToggle').onclick = () => {
  const collapsed = document.body.classList.toggle('sidebar-collapsed');
  const label = collapsed ? 'Expand menu' : 'Collapse menu';
  $('#sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
  $('#sidebarToggle').setAttribute('aria-label', label);
  $('#sidebarToggle').title = label;
  $('#sidebarToggle span').textContent = label;
};

for (const name of Object.keys(VIEWS)) {
  navigationButton(name).onclick = () => navigate(name);
}

refresh().catch(error);
