const api = async (url, options = {}) => {
  const response = await fetch(url, {headers: options.body instanceof FormData ? {} : {'Content-Type': 'application/json'}, ...options});
  if (!response.ok) {
    const text = await response.text();
    try { throw new Error(JSON.parse(text).error || text || 'Request failed'); }
    catch { throw new Error(text || 'Request failed'); }
  }
  return response.status === 204 ? null : response.json();
};

const tree = document.querySelector('#tree'), workspace = document.querySelector('#workspace'), empty = document.querySelector('#empty');
const viewer = document.querySelector('#viewer'), pageColumn = document.querySelector('#pageColumn');
const pageTitle = document.querySelector('#pageTitle'), toolButtons = [...document.querySelectorAll('[data-tool]')];
const colorInput = document.querySelector('#color'), brushSize = document.querySelector('#brushSize'), fontSize = document.querySelector('#fontSize');

const PT_TO_PX = 96 / 72;           // a PDF point rendered at 100% zoom
const NOTE_COLORS = ['#fff3a8', '#ffd6e0', '#d9f8c4', '#cdeaff', '#e9d5ff'];
const state = {
  notebooks: [], doc: null, tool: 'select', zoom: 1, currentPage: null, selected: null,
  interaction: null, firstLoad: true, history: [], redo: [], replaying: false, pageNodes: new Map(),
};

function debounce(fn, delay = 350) { let id; return (...args) => { clearTimeout(id); id = setTimeout(() => fn(...args), delay); }; }
function escapeText(text) { return String(text).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c])); }
function updateHistoryButtons() { document.querySelector('#undo').disabled = !state.history.length; document.querySelector('#redo').disabled = !state.redo.length; }
function record(command) { if (state.replaying) return; state.history.push(command); state.redo = []; updateHistoryButtons(); }
async function undo() { const command = state.history.pop(); if (!command) return; state.replaying = true; try { await command.undo(); state.redo.push(command); } finally { state.replaying = false; updateHistoryButtons(); } }
async function redo() { const command = state.redo.pop(); if (!command) return; state.replaying = true; try { await command.redo(); state.history.push(command); } finally { state.replaying = false; updateHistoryButtons(); } }

/* ---------- sidebar: folders and the documents inside them ---------- */

async function refreshTree() {
  state.notebooks = await api('/api/notebooks');
  renderTree();
  if (state.firstLoad) {
    state.firstLoad = false;
    const first = state.notebooks.find(notebook => notebook.pages.length)?.pages[0];
    if (first) openDocument(first.id);
  }
}
function iconButton(label, title, callback) {
  const button = document.createElement('button');
  button.textContent = label; button.title = title; button.className = 'tiny';
  button.onclick = event => { event.stopPropagation(); callback(); };
  return button;
}
function renderTree() {
  tree.innerHTML = '';
  state.notebooks.forEach(notebook => {
    const group = document.createElement('section');
    group.className = 'notebook'; group.dataset.id = notebook.id;
    const head = document.createElement('div');
    head.className = 'notebook-head';
    head.innerHTML = `<span class="title">${escapeText(notebook.title)}</span>`;
    head.append(iconButton('+ Blank', 'New blank document', () => createBlankDocument(notebook.id)));
    head.append(iconButton('↓', 'Export folder as PDF', () => exportNotebook(notebook)));
    head.append(iconButton('✎', 'Rename folder', () => renameNotebook(notebook)));
    head.append(iconButton('×', 'Delete folder', () => removeNotebook(notebook)));
    group.append(head);

    const drop = document.createElement('div');
    drop.className = 'dropzone';
    drop.textContent = 'Upload PDF or image';
    drop.onclick = () => pickDocument(notebook.id);
    drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', event => {
      event.preventDefault(); drop.classList.remove('over');
      const file = event.dataTransfer.files[0];
      if (file) uploadDocument(notebook.id, file);
    });
    group.append(drop);

    notebook.pages.forEach(document_ => {
      const row = document.createElement('div');
      row.className = `page-row ${state.doc?.id === document_.id ? 'selected' : ''}`;
      row.dataset.id = document_.id; row.draggable = true;
      row.innerHTML = `<span class="title">${escapeText(document_.title)}</span><span class="count">${document_.page_count || 0}p</span>`;
      row.append(iconButton('✎', 'Rename document', () => renameDocument(document_)));
      row.append(iconButton('×', 'Delete document', () => removeDocument(document_)));
      row.onclick = () => openDocument(document_.id);
      row.addEventListener('dragstart', event => { event.stopPropagation(); event.dataTransfer.setData('page', document_.id); });
      row.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('page')) { event.preventDefault(); event.stopPropagation(); } });
      row.addEventListener('drop', async event => {
        const source = event.dataTransfer.getData('page');
        if (!source || +source === document_.id) return;
        event.preventDefault(); event.stopPropagation();
        const ids = notebook.pages.map(p => p.id);
        if (!ids.includes(+source)) return;
        ids.splice(ids.indexOf(+source), 1);
        ids.splice(ids.indexOf(document_.id), 0, +source);
        await api('/api/pages/reorder', {method: 'POST', body: JSON.stringify({ids})});
        await refreshTree();
      });
      group.append(row);
    });
    tree.append(group);
  });
}

async function createNotebook() {
  const title = prompt('Folder name:', 'New folder');
  if (!title) return;
  await api('/api/notebooks', {method: 'POST', body: JSON.stringify({title})});
  await refreshTree();
}
async function createBlankDocument(notebookId) {
  const title = prompt('Document name:', 'New document');
  if (!title) return;
  const document_ = await api(`/api/notebooks/${notebookId}/pages`, {method: 'POST', body: JSON.stringify({title})});
  await refreshTree();
  openDocument(document_.id);
}
function pickDocument(notebookId) {
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = 'application/pdf,image/png,image/jpeg,image/gif,image/webp';
  picker.onchange = () => { if (picker.files[0]) uploadDocument(notebookId, picker.files[0]); };
  picker.click();
}
async function uploadDocument(notebookId, file) {
  const form = new FormData();
  form.append('document', file);
  setHint(`Rendering ${file.name}…`);
  try {
    const document_ = await api(`/api/notebooks/${notebookId}/documents`, {method: 'POST', body: form});
    await refreshTree();
    openDocument(document_.id);
    setHint('Select a tool, then draw straight on the page');
  } catch (error) {
    setHint('Upload failed');
    alert(error.message);
  }
}
async function renameNotebook(notebook) {
  const title = prompt('New folder name:', notebook.title);
  if (!title) return;
  await api(`/api/notebooks/${notebook.id}`, {method: 'PATCH', body: JSON.stringify({title})});
  refreshTree();
}
async function renameDocument(document_) {
  const title = prompt('New document name:', document_.title);
  if (!title) return;
  await api(`/api/pages/${document_.id}`, {method: 'PATCH', body: JSON.stringify({title})});
  if (state.doc?.id === document_.id) pageTitle.value = title;
  refreshTree();
}
async function removeNotebook(notebook) {
  if (!confirm(`Delete "${notebook.title}" and every document in it? This cannot be undone.`)) return;
  await api(`/api/notebooks/${notebook.id}`, {method: 'DELETE'});
  if (state.doc && notebook.pages.some(p => p.id === state.doc.id)) closeDocument();
  refreshTree();
}
async function removeDocument(document_) {
  if (!confirm(`Delete "${document_.title}" and all of its annotations? This cannot be undone.`)) return;
  await api(`/api/pages/${document_.id}`, {method: 'DELETE'});
  if (state.doc?.id === document_.id) closeDocument();
  refreshTree();
}
function exportNotebook(notebook) {
  if (!notebook.pages.length) { alert('That folder has no documents to export'); return; }
  window.location.href = `/api/notebooks/${notebook.id}/export`;
}

/* ---------- the document viewer: a scrolling column of pages ---------- */

function closeDocument() { state.doc = null; workspace.hidden = true; empty.hidden = false; renderTree(); }

async function openDocument(id) {
  state.doc = await api(`/api/pages/${id}`);
  state.history = []; state.redo = []; state.selected = null;
  updateHistoryButtons();
  workspace.hidden = false; empty.hidden = true;
  pageTitle.value = state.doc.title;
  renderDocument();
  fitWidth();
  renderTree();
}

function renderDocument() {
  pageColumn.innerHTML = '';
  state.pageNodes.clear();
  state.doc.pages.forEach(renderDocPage);
  state.currentPage = state.doc.pages[0] || null;
  applyZoom();
  updatePageCounter();
}

function renderDocPage(docPage) {
  const node = document.querySelector('#docPageTemplate').content.firstElementChild.cloneNode(true);
  node.dataset.id = docPage.id;
  const image = node.querySelector('.page-img');
  if (docPage.image_name) image.src = `/uploads/${encodeURIComponent(docPage.image_name)}`;
  else image.remove();
  const strokes = node.querySelector('.page-strokes');
  strokes.setAttribute('viewBox', `0 0 ${docPage.width} ${docPage.height}`);
  node.querySelector('.page-label').textContent = docPage.page_number;
  node.querySelector('.page-remove').onclick = event => { event.stopPropagation(); removeDocPage(docPage); };
  node.querySelector('.page-surface').addEventListener('pointerdown', event => surfaceDown(event, docPage, node));
  node.addEventListener('pointerdown', event => { if (state.tool === 'select' && !event.target.closest('.item')) selectItem(null); });
  pageColumn.append(node);
  state.pageNodes.set(docPage.id, node);
  docPage.strokes.forEach(stroke => renderStroke(stroke, docPage));
  docPage.items.forEach(item => renderItem(item, docPage));
  return node;
}

function pageNode(docPage) { return state.pageNodes.get(docPage.id); }
function docPageById(id) { return state.doc.pages.find(page => page.id === id); }

function applyZoom() {
  state.doc.pages.forEach(docPage => {
    const node = pageNode(docPage);
    const width = docPage.width * PT_TO_PX * state.zoom;
    node.style.width = `${width}px`;
    node.style.height = `${docPage.height * PT_TO_PX * state.zoom}px`;
    node.style.setProperty('--scale', width / docPage.width);
  });
  document.querySelector('#zoomValue').textContent = `${Math.round(state.zoom * 100)}%`;
}
/* The floor is low enough that a wide board migrated from the whiteboard still fits. */
function setZoom(zoom) { state.zoom = Math.max(0.08, Math.min(4, zoom)); applyZoom(); }
function fitWidth() {
  const widest = Math.max(...state.doc.pages.map(page => page.width), 1);
  const available = viewer.clientWidth - 56;
  setZoom(available / (widest * PT_TO_PX));
}

/* Pointer position in the page's own coordinate space (PDF points). */
function pagePoint(event, docPage) {
  const rect = pageNode(docPage).getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / rect.width * docPage.width,
    y: (event.clientY - rect.top) / rect.height * docPage.height,
  };
}

function renderStroke(stroke, docPage) {
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.dataset.id = stroke.id;
  path.classList.add('stroke');
  path.setAttribute('d', pointsToPath(stroke.points));
  path.setAttribute('stroke', stroke.color);
  path.setAttribute('stroke-width', stroke.width);
  if (stroke.tool === 'highlighter') path.setAttribute('opacity', '.32');
  pageNode(docPage).querySelector('.page-strokes').append(path);
}
function pointsToPath(points) {
  if (points.length < 2) return '';
  if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const previous = points[i - 1], current = points[i], next = points[i + 1];
    path += ` Q ${current.x + (next.x - previous.x) * 0.2} ${current.y + (next.y - previous.y) * 0.2} ${next.x} ${next.y}`;
  }
  return path;
}

function renderItem(item, docPage) {
  const node = document.querySelector('#itemTemplate').content.firstElementChild.cloneNode(true);
  node.dataset.id = item.id;
  node.classList.add(item.kind === 'image' ? 'stamp' : item.color === 'transparent' ? 'text-box' : 'note');
  placeItem(node, item, docPage);
  node.querySelector('.delete').onclick = async event => {
    event.stopPropagation();
    await deleteItem(item, docPage);
  };
  if (item.kind === 'image') {
    const image = document.createElement('img');
    image.src = `/uploads/${encodeURIComponent(item.image_name)}`;
    image.alt = 'Stamped image';
    image.draggable = false;
    node.prepend(image);
  } else {
    const body = document.createElement('div');
    body.className = 'note-body';
    body.textContent = item.content;
    body.style.fontSize = `calc(var(--scale) * ${item.font_size}px)`;
    body.addEventListener('dblclick', () => beginEditing(body, item));
    body.addEventListener('blur', async () => {
      body.contentEditable = 'false';
      document.body.classList.remove('editing');
      if (body.textContent !== item.content) { item.content = body.textContent; await saveItem(item, ['content']); }
    });
    node.prepend(body);
  }
  node.addEventListener('pointerdown', event => startItemAction(event, item, docPage, node));
  pageNode(docPage).querySelector('.page-items').append(node);
  return node;
}
function beginEditing(body, item) {
  body.contentEditable = 'true';
  document.body.classList.add('editing');
  body.focus();
  const range = document.createRange();
  range.selectNodeContents(body);
  window.getSelection().removeAllRanges();
  window.getSelection().addRange(range);
}
function placeItem(node, item, docPage) {
  node.style.left = `${item.x / docPage.width * 100}%`;
  node.style.top = `${item.y / docPage.height * 100}%`;
  node.style.width = `${item.width / docPage.width * 100}%`;
  node.style.height = `${item.height / docPage.height * 100}%`;
  node.style.zIndex = String(item.position + 1);
}
function itemNode(item) { return pageColumn.querySelector(`.item[data-id="${item.id}"]`); }
function selectItem(item) {
  pageColumn.querySelectorAll('.item.selected').forEach(node => node.classList.remove('selected'));
  state.selected = item;
  if (item) itemNode(item)?.classList.add('selected');
}
async function saveItem(item, keys) {
  const body = {};
  keys.forEach(key => { body[key] = item[key]; });
  await api(`/api/items/${item.id}`, {method: 'PATCH', body: JSON.stringify(body)});
}

/* ---------- drawing, erasing, and placing annotations ---------- */

/* Keep the gesture on one page even when the pointer wanders off it. */
function capturePointer(node, event) {
  try { node.querySelector('.page-surface').setPointerCapture(event.pointerId); }
  catch { /* the pointer already went away; the gesture still works by bubbling */ }
}

function surfaceDown(event, docPage, node) {
  if (!state.doc || state.tool === 'select') return;
  const point = pagePoint(event, docPage);
  if (state.tool === 'eraser') {
    state.interaction = {type: 'erase', pointerId: event.pointerId, docPage};
    eraseAt(point, docPage);
    capturePointer(node, event);
    return;
  }
  if (state.tool === 'text') { createTextBox(docPage, point); return; }
  if (state.tool === 'note') { createStickyNote(docPage, point); return; }
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.classList.add('stroke');
  path.setAttribute('stroke', colorInput.value);
  path.setAttribute('stroke-width', brushSize.value);
  if (state.tool === 'highlighter') path.setAttribute('opacity', '.32');
  node.querySelector('.page-strokes').append(path);
  state.interaction = {type: 'draw', pointerId: event.pointerId, docPage, points: [point], path, tool: state.tool};
  capturePointer(node, event);
  event.preventDefault();
}

function startItemAction(event, item, docPage, node) {
  if (state.tool !== 'select' || event.target.closest('.delete')) return;
  if (node.querySelector('.note-body')?.isContentEditable) return;
  event.stopPropagation();
  selectItem(item);
  const point = pagePoint(event, docPage);
  state.interaction = {
    type: event.target.closest('.resize') ? 'resize' : 'move',
    pointerId: event.pointerId, docPage, node, item, start: point,
    x: item.x, y: item.y, width: item.width, height: item.height,
  };
  try { node.setPointerCapture(event.pointerId); } catch { /* gesture still works by bubbling */ }
  event.preventDefault();
}

function pointerMove(event) {
  const action = state.interaction;
  if (!action || action.pointerId !== event.pointerId) return;
  const point = pagePoint(event, action.docPage);
  if (action.type === 'draw') {
    const last = action.points[action.points.length - 1];
    if (Math.hypot(point.x - last.x, point.y - last.y) > 1.2) {
      action.points.push(point);
      action.path.setAttribute('d', pointsToPath(action.points));
    }
    return;
  }
  if (action.type === 'erase') { eraseAt(point, action.docPage); return; }
  const page = action.docPage;
  if (action.type === 'move') {
    action.item.x = Math.max(0, Math.min(page.width - action.item.width, action.x + point.x - action.start.x));
    action.item.y = Math.max(0, Math.min(page.height - action.item.height, action.y + point.y - action.start.y));
  } else {
    action.item.width = Math.max(30, Math.min(page.width - action.item.x, action.width + point.x - action.start.x));
    action.item.height = Math.max(20, Math.min(page.height - action.item.y, action.height + point.y - action.start.y));
  }
  placeItem(action.node, action.item, page);
}

async function pointerUp(event) {
  const action = state.interaction;
  if (!action || action.pointerId !== event.pointerId) return;
  state.interaction = null;
  if (action.type === 'draw') {
    if (action.points.length < 2) { action.path.remove(); return; }
    const definition = {
      tool: action.tool, color: action.path.getAttribute('stroke'),
      width: +action.path.getAttribute('stroke-width'), points: action.points, doc_page_id: action.docPage.id,
    };
    let stroke = await createStroke(definition);
    action.path.dataset.id = stroke.id;
    action.docPage.strokes.push(stroke);
    record({
      undo: () => removeStroke(stroke, action.docPage),
      redo: async () => { stroke = await createStroke(definition); action.docPage.strokes.push(stroke); renderStroke(stroke, action.docPage); },
    });
  } else if (action.type === 'move' || action.type === 'resize') {
    const before = {x: action.x, y: action.y, width: action.width, height: action.height};
    const after = {x: action.item.x, y: action.item.y, width: action.item.width, height: action.item.height};
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      await saveItem(action.item, ['x', 'y', 'width', 'height']);
      record({
        undo: () => applyGeometry(action.item, action.docPage, before),
        redo: () => applyGeometry(action.item, action.docPage, after),
      });
    }
  }
}

async function applyGeometry(item, docPage, geometry) {
  Object.assign(item, geometry);
  await saveItem(item, ['x', 'y', 'width', 'height']);
  const node = itemNode(item);
  if (node) placeItem(node, item, docPage);
}

function distanceToSegment(point, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
  if (!length) return Math.hypot(point.x - a.x, point.y - a.y);
  const t = Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length));
  return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy));
}
function strokeAt(point, docPage) {
  return [...docPage.strokes].reverse().find(stroke =>
    stroke.points.some((p, index) => index && distanceToSegment(point, stroke.points[index - 1], p) <= Math.max(6, stroke.width / 2 + 4)));
}
async function eraseAt(point, docPage) {
  const stroke = strokeAt(point, docPage);
  if (!stroke) return;
  let removed = stroke;
  await removeStroke(removed, docPage);
  record({
    undo: async () => { removed = await createStroke({...removed, doc_page_id: docPage.id}); docPage.strokes.push(removed); renderStroke(removed, docPage); },
    redo: () => removeStroke(removed, docPage),
  });
}
async function createStroke(stroke) {
  return api(`/api/pages/${state.doc.id}/strokes`, {method: 'POST', body: JSON.stringify({
    tool: stroke.tool, color: stroke.color, width: stroke.width, points: stroke.points, doc_page_id: stroke.doc_page_id,
  })});
}
async function removeStroke(stroke, docPage) {
  await api(`/api/strokes/${stroke.id}`, {method: 'DELETE'});
  docPage.strokes = docPage.strokes.filter(s => s.id !== stroke.id);
  pageNode(docPage)?.querySelector(`path[data-id="${stroke.id}"]`)?.remove();
}

async function createNote(docPage, data) {
  const item = await api(`/api/pages/${state.doc.id}/items`, {method: 'POST', body: JSON.stringify({kind: 'note', doc_page_id: docPage.id, ...data})});
  docPage.items.push(item);
  return item;
}
async function removeItem(item) {
  await api(`/api/items/${item.id}`, {method: 'DELETE'});
  const docPage = docPageById(item.doc_page_id);
  if (docPage) docPage.items = docPage.items.filter(i => i.id !== item.id);
  itemNode(item)?.remove();
  if (state.selected?.id === item.id) state.selected = null;
}
async function restoreItem(item, docPage) {
  const replacement = await createNote(docPage, {
    content: item.content, color: item.color, x: item.x, y: item.y,
    width: item.width, height: item.height, font_size: item.font_size,
  });
  Object.assign(item, replacement);
  renderItem(item, docPage);
}
/* Deleting a stamp also unlinks its file on the server, so only notes are restorable. */
async function deleteItem(item, docPage) {
  if (item.kind === 'image' && !confirm('Delete this stamp? It cannot be undone.')) return;
  await removeItem(item);
  if (item.kind !== 'image') record({undo: () => restoreItem(item, docPage), redo: () => removeItem(item)});
}
async function createTextBox(docPage, point) {
  const item = await createNote(docPage, {
    content: '', color: 'transparent', x: Math.max(0, point.x), y: Math.max(0, point.y),
    width: 220, height: 40, font_size: +fontSize.value,
  });
  const node = renderItem(item, docPage);
  selectTool('select');
  selectItem(item);
  beginEditing(node.querySelector('.note-body'), item);
  record({undo: () => removeItem(item), redo: () => restoreItem(item, docPage)});
}
async function createStickyNote(docPage, point) {
  const item = await createNote(docPage, {
    content: '', color: NOTE_COLORS[docPage.items.length % NOTE_COLORS.length],
    x: Math.max(0, point.x), y: Math.max(0, point.y), width: 200, height: 130, font_size: +fontSize.value,
  });
  const node = renderItem(item, docPage);
  selectTool('select');
  selectItem(item);
  beginEditing(node.querySelector('.note-body'), item);
  record({undo: () => removeItem(item), redo: () => restoreItem(item, docPage)});
}

/* ---------- pages, zoom, and navigation ---------- */

async function addBlankPage() {
  if (!state.doc) return;
  const docPage = await api(`/api/pages/${state.doc.id}/blank-page`, {method: 'POST'});
  state.doc.pages.push(docPage);
  renderDocPage(docPage);
  applyZoom();
  updatePageCounter();
  pageNode(docPage).scrollIntoView({block: 'start'});
  refreshTree();
}
async function removeDocPage(docPage) {
  if (state.doc.pages.length < 2) { alert('A document needs at least one page'); return; }
  if (!confirm(`Delete page ${docPage.page_number} and its annotations?`)) return;
  try { await api(`/api/doc-pages/${docPage.id}`, {method: 'DELETE'}); }
  catch (error) { alert(error.message); return; }
  await openDocument(state.doc.id);
  refreshTree();
}
function visiblePage() {
  const middle = viewer.scrollTop + viewer.clientHeight / 2;
  let closest = state.doc?.pages[0] || null, best = Infinity;
  state.doc?.pages.forEach(docPage => {
    const node = pageNode(docPage);
    if (!node) return;
    const centre = node.offsetTop + node.offsetHeight / 2;
    if (Math.abs(centre - middle) < best) { best = Math.abs(centre - middle); closest = docPage; }
  });
  return closest;
}
function updatePageCounter() {
  if (!state.doc) return;
  state.currentPage = visiblePage();
  document.querySelector('#pageCounter').textContent = `Page ${state.currentPage?.page_number || 1} of ${state.doc.pages.length}`;
  state.pageNodes.forEach((node, id) => node.classList.toggle('current', id === state.currentPage?.id));
}
function stepPage(delta) {
  if (!state.doc) return;
  const index = state.doc.pages.indexOf(visiblePage());
  const target = state.doc.pages[Math.max(0, Math.min(state.doc.pages.length - 1, index + delta))];
  if (target) pageNode(target).scrollIntoView({block: 'start'});
}
function setHint(text) { document.querySelector('#hint').textContent = text; }
function selectTool(tool) {
  state.tool = tool;
  toolButtons.forEach(button => button.classList.toggle('active', button.dataset.tool === tool));
  document.body.className = document.body.className.replace(/\btool-\S+/g, '').trim();
  document.body.classList.add(`tool-${tool}`);
  document.querySelector('#fontLabel').hidden = !['text', 'note'].includes(tool);
  document.querySelector('#brushLabel').hidden = !['pen', 'highlighter'].includes(tool);
  if (tool !== 'select') selectItem(null);
}

/* ---------- wiring ---------- */

document.querySelector('#addNotebook').onclick = createNotebook;
document.querySelector('#addPage').onclick = addBlankPage;
document.querySelector('#undo').onclick = undo;
document.querySelector('#redo').onclick = redo;
document.querySelector('#zoomIn').onclick = () => setZoom(state.zoom * 1.2);
document.querySelector('#zoomOut').onclick = () => setZoom(state.zoom / 1.2);
document.querySelector('#fitWidth').onclick = () => { if (state.doc) fitWidth(); };
document.querySelector('#prevPage').onclick = () => stepPage(-1);
document.querySelector('#nextPage').onclick = () => stepPage(1);
document.querySelector('#exportDoc').onclick = () => { if (state.doc) window.location.href = `/api/pages/${state.doc.id}/export`; };
toolButtons.forEach(button => { button.onclick = () => selectTool(button.dataset.tool); });

brushSize.oninput = () => { document.querySelector('#brushValue').textContent = brushSize.value; };
fontSize.oninput = async () => {
  document.querySelector('#fontValue').textContent = fontSize.value;
  const item = state.selected;
  if (!item || item.kind === 'image') return;
  item.font_size = +fontSize.value;
  itemNode(item).querySelector('.note-body').style.fontSize = `calc(var(--scale) * ${item.font_size}px)`;
  await saveItem(item, ['font_size']);
};

document.querySelector('#upload').onchange = async event => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file || !state.doc) return;
  const docPage = state.currentPage || state.doc.pages[0];
  const bitmap = await createImageBitmap(file).catch(() => null);
  const width = docPage.width * 0.3;
  const height = bitmap ? width * (bitmap.height / bitmap.width) : width * 0.66;
  const form = new FormData();
  form.append('image', file);
  form.append('doc_page_id', docPage.id);
  form.append('x', docPage.width * 0.1);
  form.append('y', docPage.height * 0.1);
  form.append('width', width);
  form.append('height', height);
  try {
    const item = await api(`/api/pages/${state.doc.id}/upload`, {method: 'POST', body: form});
    docPage.items.push(item);
    renderItem(item, docPage);
    selectTool('select');
    selectItem(item);
  } catch (error) { alert(error.message); }
};

document.querySelector('#syncBtn').onclick = async () => {
  const button = document.querySelector('#syncBtn');
  button.disabled = true;
  const original = button.textContent;
  button.textContent = '⏳';
  try { await api('/api/sync', {method: 'POST'}); setHint('Pushed to GitHub'); }
  catch (error) { setHint(`Sync failed: ${error.message}`); }
  finally { button.textContent = original; button.disabled = false; }
};

pageTitle.addEventListener('change', async () => {
  if (!state.doc || !pageTitle.value.trim()) return;
  state.doc.title = pageTitle.value.trim();
  await api(`/api/pages/${state.doc.id}`, {method: 'PATCH', body: JSON.stringify({title: state.doc.title})});
  refreshTree();
});

pageColumn.addEventListener('pointermove', pointerMove);
pageColumn.addEventListener('pointerup', pointerUp);
pageColumn.addEventListener('pointercancel', pointerUp);
viewer.addEventListener('scroll', debounce(updatePageCounter, 80));
viewer.addEventListener('wheel', event => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  setZoom(state.zoom * (event.deltaY > 0 ? 0.9 : 1.1));
}, {passive: false});
window.addEventListener('resize', debounce(() => { if (state.doc) applyZoom(); }, 200));

document.addEventListener('keydown', event => {
  if (event.target.isContentEditable || ['INPUT', 'TEXTAREA'].includes(event.target.tagName)) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
    return;
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && state.selected) {
    event.preventDefault();
    const item = state.selected;
    deleteItem(item, docPageById(item.doc_page_id));
  }
});

document.querySelector('#search').addEventListener('input', debounce(async event => {
  const host = document.querySelector('#results'), term = event.target.value.trim();
  host.innerHTML = '';
  if (!term) return;
  const results = await api(`/api/search?q=${encodeURIComponent(term)}`);
  results.forEach(result => {
    const button = document.createElement('button');
    button.className = 'result';
    button.innerHTML = `${escapeText(result.page_title)}<small>${escapeText(result.notebook_title)}</small>`;
    button.onclick = () => { host.innerHTML = ''; document.querySelector('#search').value = ''; openDocument(result.page_id); };
    host.append(button);
  });
}, 220));

selectTool('select');
refreshTree();
