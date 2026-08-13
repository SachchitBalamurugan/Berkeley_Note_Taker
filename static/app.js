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
const board = document.querySelector('#board'), viewport = document.querySelector('#viewport'), itemsRoot = document.querySelector('#items'), strokesRoot = document.querySelector('#strokes');
const pageTitle = document.querySelector('#pageTitle'), toolButtons = [...document.querySelectorAll('[data-tool]')];
const state = {notebooks: [], page: null, tool: 'select', view: {x: 0, y: 0, zoom: 0.5}, interaction: null, firstLoad: true, history: [], redo: [], replaying: false, touchPoints: new Map()};
const colors = ['#fff3a8', '#ffd6e0', '#d9f8c4', '#cdeaff', '#e9d5ff'];

function setView() { board.style.transform = `translate(${state.view.x}px,${state.view.y}px) scale(${state.view.zoom})`; }
function boardPoint(event) { const r = viewport.getBoundingClientRect(); return {x: (event.clientX - r.left - state.view.x) / state.view.zoom, y: (event.clientY - r.top - state.view.y) / state.view.zoom}; }
function debounce(fn, delay = 350) { let id; return (...args) => { clearTimeout(id); id = setTimeout(() => fn(...args), delay); }; }
function escapeText(text) { return text.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function updateHistoryButtons() { document.querySelector('#undo').disabled = !state.history.length; document.querySelector('#redo').disabled = !state.redo.length; }
function record(command) { if (state.replaying) return; state.history.push(command); state.redo = []; updateHistoryButtons(); }
async function undo() { const command = state.history.pop(); if (!command) return; state.replaying = true; try { await command.undo(); state.redo.push(command); } finally { state.replaying = false; updateHistoryButtons(); } }
async function redo() { const command = state.redo.pop(); if (!command) return; state.replaying = true; try { await command.redo(); state.history.push(command); } finally { state.replaying = false; updateHistoryButtons(); } }

async function refreshTree() {
  state.notebooks = await api('/api/notebooks');
  renderTree();
  if (state.firstLoad) {
    state.firstLoad = false;
    const firstPage = state.notebooks.find(notebook => notebook.pages.length)?.pages[0];
    if (firstPage) openPage(firstPage.id);
  }
}
function iconButton(label, title, callback) { const b = document.createElement('button'); b.textContent = label; b.title = title; b.className = 'tiny'; b.onclick = e => { e.stopPropagation(); callback(); }; return b; }
function renderTree() {
  tree.innerHTML = '';
  state.notebooks.forEach(notebook => {
    const group = document.createElement('section'); group.className = 'notebook'; group.draggable = true; group.dataset.id = notebook.id;
    group.addEventListener('dragstart', e => { if (e.target === group) e.dataTransfer.setData('notebook', notebook.id); });
    group.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('notebook')) e.preventDefault(); });
    group.addEventListener('drop', async e => { const source = e.dataTransfer.getData('notebook'); if (!source) return; e.preventDefault(); const ids = state.notebooks.map(n => n.id); ids.splice(ids.indexOf(+source),1); ids.splice(ids.indexOf(notebook.id),0,+source); await api('/api/notebooks/reorder',{method:'POST',body:JSON.stringify({ids})}); await refreshTree(); });
    const head = document.createElement('div'); head.className = 'notebook-head'; head.innerHTML = `<span class="title">${escapeText(notebook.title)}</span>`;
    head.append(iconButton('+ Page', 'Add page', () => createPage(notebook.id)));
    head.append(iconButton('⬇', 'Export notebook', () => exportNotebook(notebook)));
    head.append(iconButton('✎', 'Rename notebook', () => renameNotebook(notebook)));
    head.append(iconButton('×', 'Delete notebook', () => removeNotebook(notebook)));
    group.append(head);
    notebook.pages.forEach(page => {
      const row = document.createElement('div'); row.className = `page-row ${state.page?.id === page.id ? 'selected' : ''}`; row.dataset.id = page.id; row.draggable = true;
      row.innerHTML = `<span class="title">${escapeText(page.title)}</span>`;
      row.append(iconButton('✎', 'Rename page', () => renamePage(page)));
      row.append(iconButton('×', 'Delete page', () => removePage(page)));
      row.onclick = () => openPage(page.id);
      row.addEventListener('dragstart', e => { e.stopPropagation(); e.dataTransfer.setData('page', page.id); });
      row.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('page')) { e.preventDefault(); e.stopPropagation(); } });
      row.addEventListener('drop', async e => { const source = e.dataTransfer.getData('page'); if (!source || +source === page.id) return; e.preventDefault(); e.stopPropagation(); const ids = notebook.pages.map(p => p.id); if (!ids.includes(+source)) return; ids.splice(ids.indexOf(+source),1); ids.splice(ids.indexOf(page.id),0,+source); await api('/api/pages/reorder',{method:'POST',body:JSON.stringify({ids})}); await refreshTree(); });
      group.append(row);
    });
    tree.append(group);
  });
}
async function createNotebook() { const title = prompt('Notebook name:', 'New subject'); if (!title) return; const n = await api('/api/notebooks',{method:'POST',body:JSON.stringify({title})}); await createPage(n.id); await refreshTree(); }
async function createPage(notebookId) { const title = prompt('Page name:', 'New page'); if (!title) return; const page = await api(`/api/notebooks/${notebookId}/pages`,{method:'POST',body:JSON.stringify({title})}); await refreshTree(); openPage(page.id); }
async function renameNotebook(n) { const title = prompt('New notebook name:',n.title); if (!title) return; await api(`/api/notebooks/${n.id}`,{method:'PATCH',body:JSON.stringify({title})}); refreshTree(); }
async function renamePage(p) { const title = prompt('New page name:',p.title); if (!title) return; await api(`/api/pages/${p.id}`,{method:'PATCH',body:JSON.stringify({title})}); if(state.page?.id===p.id) pageTitle.value=title; refreshTree(); }
async function removeNotebook(n) { if (!confirm(`Delete “${n.title}” and all of its pages? This cannot be undone.`)) return; await api(`/api/notebooks/${n.id}`,{method:'DELETE'}); if (state.page && n.pages.some(p=>p.id===state.page.id)) closePage(); refreshTree(); }
async function exportNotebook(n) { if (!n.pages.length) { alert('Cannot export empty notebook'); return; } const link = document.createElement('a'); link.href = `/api/notebooks/${n.id}/export`; link.download = `${n.title.replace(/[^a-z0-9]/gi,'_')}.pdf`; link.click(); }
async function removePage(p) { if (!confirm(`Delete “${p.title}” and all its board content? This cannot be undone.`)) return; await api(`/api/pages/${p.id}`,{method:'DELETE'}); if(state.page?.id===p.id) closePage(); refreshTree(); }
function closePage(){ state.page=null; workspace.hidden=true; empty.hidden=false; renderTree(); }
async function openPage(id) { state.page = await api(`/api/pages/${id}`); state.history=[]; state.redo=[]; updateHistoryButtons(); workspace.hidden=false; empty.hidden=true; pageTitle.value=state.page.title; renderBoard(); renderTree(); }

function renderBoard() { itemsRoot.innerHTML = ''; strokesRoot.innerHTML = ''; state.page.strokes.forEach(renderStroke); state.page.items.forEach(renderItem); setView(); }
function renderItem(item) {
  const node = document.querySelector('#itemTemplate').content.firstElementChild.cloneNode(true); node.dataset.id=item.id; node.classList.add(item.kind); node.style.left=`${item.x}px`; node.style.top=`${item.y}px`; node.style.width=`${item.width}px`; node.style.height=`${item.height}px`; node.style.zIndex=String(item.position+1);
  node.querySelector('.delete').onclick = async e => { e.stopPropagation(); if(confirm('Delete this item?')) { await api(`/api/items/${item.id}`,{method:'DELETE'}); state.page.items=state.page.items.filter(i=>i.id!==item.id); node.remove(); } };
  if(item.kind==='note') { node.style.background=item.color; if(item.color==='transparent') node.classList.add('text-box'); const content=document.createElement('div'); content.className='note'; content.contentEditable='true'; content.spellcheck=true; content.textContent=item.content; content.addEventListener('pointerdown',e=>e.stopPropagation()); content.addEventListener('input',debounce(async()=>{item.content=content.textContent; await saveItem(item,['content']);})); node.insertBefore(content,node.firstChild); }
  else { const img=document.createElement('img'); img.src=`/uploads/${encodeURIComponent(item.image_name)}`; img.alt='Uploaded note image'; node.insertBefore(img,node.firstChild); }
  node.addEventListener('pointerdown', startItemAction); itemsRoot.append(node);
}
function renderStroke(stroke) { const p=document.createElementNS('http://www.w3.org/2000/svg','path'); p.dataset.id=stroke.id; p.classList.add('stroke'); p.setAttribute('d', pointsToPath(stroke.points)); p.setAttribute('stroke',stroke.color); p.setAttribute('stroke-width',stroke.width); if(stroke.tool==='highlighter') p.setAttribute('opacity','.32'); if(state.tool==='eraser') p.style.pointerEvents='stroke'; strokesRoot.append(p); }
function pointsToPath(points) {
  if (points.length < 2) return '';
  if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;

  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p0 = points[i - 1], p1 = points[i], p2 = points[i + 1];
    const cp1x = p1.x + (p2.x - p0.x) * 0.2;
    const cp1y = p1.y + (p2.y - p0.y) * 0.2;
    path += ` Q ${cp1x} ${cp1y} ${p2.x} ${p2.y}`;
  }
  return path;
}
async function saveItem(item, keys) { const body={}; keys.forEach(k=>body[k]=item[k]); await api(`/api/items/${item.id}`,{method:'PATCH',body:JSON.stringify(body)}); }

function startItemAction(e) {
  if(state.tool!=='select' || e.target.closest('.delete') || e.target.closest('.note')) return;
  e.stopPropagation(); const node=e.currentTarget, item=state.page.items.find(i=>i.id===+node.dataset.id), point=boardPoint(e); const resize=e.target.closest('.resize');
  state.interaction={type:resize?'resize':'move', pointerId:e.pointerId, node,item, start:point, x:item.x,y:item.y,width:item.width,height:item.height}; node.setPointerCapture(e.pointerId);
}
function distanceToSegment(point, a, b) {
  const dx=b.x-a.x, dy=b.y-a.y, length=dx*dx+dy*dy;
  if (!length) return Math.hypot(point.x-a.x, point.y-a.y);
  const t=Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/length));
  return Math.hypot(point.x-(a.x+t*dx),point.y-(a.y+t*dy));
}
function strokeAt(point) {
  return [...state.page.strokes].reverse().find(stroke => stroke.points.some((p,index) => index && distanceToSegment(point, stroke.points[index-1], p) <= Math.max(16, stroke.width / 2 + 10)));
}
function beginPinch() {
  const [first, second] = [...state.touchPoints.values()];
  if (state.interaction?.type === 'draw') { state.interaction.path.remove(); board.removeAttribute('data-drawing'); }
  state.interaction={type:'pinch',distance:Math.hypot(second.x-first.x,second.y-first.y),zoom:state.view.zoom,x:state.view.x,y:state.view.y};
}
function canvasDown(e) {
  if(!state.page) return;
  if(e.pointerType==='touch') { state.touchPoints.set(e.pointerId,{x:e.clientX,y:e.clientY}); if(state.touchPoints.size===2) { beginPinch(); viewport.setPointerCapture(e.pointerId); return; } }
  if(state.tool==='select' && (e.target===viewport || e.target===board)) { state.interaction={type:'pan',pointerId:e.pointerId,startClient:{x:e.clientX,y:e.clientY},x:state.view.x,y:state.view.y}; viewport.setPointerCapture(e.pointerId); return; }
  if(state.tool==='eraser') { const stroke=strokeAt(boardPoint(e)); if(stroke) eraseStroke(stroke.id); return; }
  if(['pen','highlighter'].includes(state.tool) && (e.target===board || e.target===strokesRoot || e.target===viewport)) { const point=boardPoint(e), path=document.createElementNS('http://www.w3.org/2000/svg','path'); path.classList.add('stroke'); path.setAttribute('stroke',document.querySelector('#color').value); path.setAttribute('stroke-width',document.querySelector('#brushSize').value); if(state.tool==='highlighter')path.setAttribute('opacity','.32'); strokesRoot.append(path); state.interaction={type:'draw',pointerId:e.pointerId,points:[point],path, tool:state.tool}; viewport.setPointerCapture(e.pointerId); board.setAttribute('data-drawing',''); }
}
function pointerMove(e) {
  if(e.pointerType==='touch' && state.touchPoints.has(e.pointerId)) state.touchPoints.set(e.pointerId,{x:e.clientX,y:e.clientY});
  const a=state.interaction; if(!a) return;
  if(a.type==='pinch') { if(state.touchPoints.size<2)return; const [first,second]=[...state.touchPoints.values()]; const distance=Math.hypot(second.x-first.x,second.y-first.y); const zoom=Math.max(.25,Math.min(2.5,a.zoom*distance/a.distance)); const rect=viewport.getBoundingClientRect(), cx=(first.x+second.x)/2-rect.left, cy=(first.y+second.y)/2-rect.top; state.view.x=cx-(cx-a.x)*zoom/a.zoom; state.view.y=cy-(cy-a.y)*zoom/a.zoom; state.view.zoom=zoom; setView(); return; }
  if(a.pointerId!==e.pointerId) return;
  if(a.type==='pan') { state.view.x=a.x+e.clientX-a.startClient.x; state.view.y=a.y+e.clientY-a.startClient.y; setView(); return; } const p=boardPoint(e); if(a.type==='draw') { const lastPoint=a.points[a.points.length-1]; if(!lastPoint || Math.hypot(p.x-lastPoint.x, p.y-lastPoint.y) > 2) { a.points.push(p); a.path.setAttribute('d',pointsToPath(a.points)); } return; } if(a.type==='move') { a.item.x=Math.max(0,a.x+p.x-a.start.x); a.item.y=Math.max(0,a.y+p.y-a.start.y); } if(a.type==='resize') { a.item.width=Math.max(130,a.width+p.x-a.start.x); a.item.height=Math.max(80,a.height+p.y-a.start.y); } a.node.style.left=`${a.item.x}px`;a.node.style.top=`${a.item.y}px`;a.node.style.width=`${a.item.width}px`;a.node.style.height=`${a.item.height}px`;
}
async function createStroke(stroke) { return api(`/api/pages/${state.page.id}/strokes`, {method:'POST', body:JSON.stringify({tool:stroke.tool,color:stroke.color,width:stroke.width,points:stroke.points})}); }
async function removeStroke(stroke) { await api(`/api/strokes/${stroke.id}`,{method:'DELETE'}); state.page.strokes=state.page.strokes.filter(s=>s.id!==stroke.id); strokesRoot.querySelector(`path[data-id="${stroke.id}"]`)?.remove(); }
async function pointerUp(e) {
  if(e.pointerType==='touch') state.touchPoints.delete(e.pointerId);
  const a=state.interaction; if(!a) return;
  if(a.type==='pinch') { if(state.touchPoints.size<2) state.interaction=null; return; }
  if(a.pointerId!==e.pointerId) return; state.interaction=null; board.removeAttribute('data-drawing');
  if(a.type==='draw') {
    if(a.points.length<2){a.path.remove();return;}
    let stroke=await createStroke({tool:a.tool,color:a.path.getAttribute('stroke'),width:+a.path.getAttribute('stroke-width'),points:a.points});
    a.path.dataset.id=stroke.id; state.page.strokes.push(stroke);
    record({undo:()=>removeStroke(stroke), redo:async()=>{stroke=await createStroke(stroke);state.page.strokes.push(stroke);renderStroke(stroke);}});
  } else if(a.type==='move'||a.type==='resize') {
    const before={x:a.x,y:a.y,width:a.width,height:a.height}, after={x:a.item.x,y:a.item.y,width:a.item.width,height:a.item.height};
    if(JSON.stringify(before)!==JSON.stringify(after)) {
      await saveItem(a.item,['x','y','width','height']);
      record({undo:()=>applyGeometry(a.item,before),redo:()=>applyGeometry(a.item,after)});
    }
  }
}
async function applyGeometry(item, geometry) { Object.assign(item,geometry); await saveItem(item,['x','y','width','height']); const node=itemsRoot.querySelector(`.item[data-id="${item.id}"]`); if(node){node.style.left=`${item.x}px`;node.style.top=`${item.y}px`;node.style.width=`${item.width}px`;node.style.height=`${item.height}px`;} }
async function eraseStroke(id) { let stroke=state.page.strokes.find(s=>s.id===id); if(!stroke)return; await removeStroke(stroke); record({undo:async()=>{stroke=await createStroke(stroke);state.page.strokes.push(stroke);renderStroke(stroke);},redo:()=>removeStroke(stroke)}); }

async function createNote(data) { return api(`/api/pages/${state.page.id}/items`,{method:'POST',body:JSON.stringify({kind:'note',...data})}); }
async function removeItem(item) { await api(`/api/items/${item.id}`,{method:'DELETE'}); state.page.items=state.page.items.filter(i=>i.id!==item.id); itemsRoot.querySelector(`.item[data-id="${item.id}"]`)?.remove(); }
document.querySelector('#addNotebook').onclick=createNotebook;
document.querySelector('#addNote').onclick=async()=>{
  if(!state.page)return;
  const data={content:'',color:colors[state.page.items.filter(i=>i.kind==='note').length%colors.length],x:220,y:180,width:260,height:180};
  let item=await createNote(data);state.page.items.push(item);renderItem(item);
  record({undo:()=>removeItem(item),redo:async()=>{item=await createNote(data);state.page.items.push(item);renderItem(item);}});
};
document.querySelector('#addText').onclick=async()=>{
  if(!state.page)return;
  const data={content:'Type here',color:'transparent',x:250,y:220,width:300,height:100};
  let item=await createNote(data);state.page.items.push(item);renderItem(item);
  record({undo:()=>removeItem(item),redo:async()=>{item=await createNote(data);state.page.items.push(item);renderItem(item);}});
  const text=itemsRoot.querySelector(`.item[data-id="${item.id}"] .note`); text?.focus();
};
document.querySelector('#upload').onchange=async e=>{const file=e.target.files[0];if(!file||!state.page)return;const form=new FormData();form.append('image',file);try{const item=await api(`/api/pages/${state.page.id}/upload`,{method:'POST',body:form});state.page.items.push(item);renderItem(item);}catch(err){alert(err.message)}e.target.value='';};
document.querySelector('#syncBtn').onclick=async()=>{const btn=document.querySelector('#syncBtn');btn.disabled=true;btn.textContent='⏳ Syncing...';try{const res=await api('/api/sync',{method:'POST'});btn.textContent='✓ '+res.message;setTimeout(()=>{btn.textContent='☁ Sync'},3000);}catch(err){btn.textContent='✗ Sync failed';console.error(err);setTimeout(()=>{btn.textContent='☁ Sync'},3000);}finally{btn.disabled=false;}};
toolButtons.forEach(b=>b.onclick=()=>{state.tool=b.dataset.tool;toolButtons.forEach(x=>x.classList.toggle('active',x===b));document.body.classList.toggle('drawing',['pen','highlighter'].includes(state.tool));document.body.classList.toggle('eraser',state.tool==='eraser');strokesRoot.querySelectorAll('path').forEach(p=>p.style.pointerEvents=state.tool==='eraser'?'stroke':'none');});
function fitView() {
  if (!state.page || (!state.page.items.length && !state.page.strokes.length)) {
    state.view = {x: 0, y: 0, zoom: 0.5};
    setView();
    return;
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  state.page.items.forEach(item => {
    minX = Math.min(minX, item.x);
    minY = Math.min(minY, item.y);
    maxX = Math.max(maxX, item.x + item.width);
    maxY = Math.max(maxY, item.y + item.height);
  });
  state.page.strokes.forEach(stroke => {
    stroke.points.forEach(p => {
      minX = Math.min(minX, p.x - stroke.width / 2);
      minY = Math.min(minY, p.y - stroke.width / 2);
      maxX = Math.max(maxX, p.x + stroke.width / 2);
      maxY = Math.max(maxY, p.y + stroke.width / 2);
    });
  });
  if (!isFinite(minX)) { state.view = {x: 55, y: 45, zoom: 0.62}; setView(); return; }
  const padding = 80, width = maxX - minX + padding * 2, height = maxY - minY + padding * 2;
  const viewWidth = viewport.clientWidth, viewHeight = viewport.clientHeight;
  const zoom = Math.min(viewWidth / width, viewHeight / height, 1.5);
  state.view.zoom = zoom;
  state.view.x = viewWidth / 2 - (minX + (maxX - minX) / 2) * zoom;
  state.view.y = viewHeight / 2 - (minY + (maxY - minY) / 2) * zoom;
  setView();
}
document.querySelector('#undo').onclick=undo;
document.querySelector('#redo').onclick=redo;
document.querySelector('#brushSize').oninput=e=>{document.querySelector('#brushValue').value=e.target.value;document.querySelector('#brushValue').textContent=e.target.value;};
document.addEventListener('keydown',e=>{ if(!(e.ctrlKey||e.metaKey)||e.key.toLowerCase()!=='z'||e.target.isContentEditable||['INPUT','TEXTAREA'].includes(e.target.tagName)) return; e.preventDefault(); if(e.shiftKey) redo(); else undo(); });
pageTitle.addEventListener('change',async()=>{if(state.page&&pageTitle.value.trim()){state.page.title=pageTitle.value.trim();await api(`/api/pages/${state.page.id}`,{method:'PATCH',body:JSON.stringify({title:state.page.title})});refreshTree();}});
viewport.addEventListener('pointerdown',canvasDown); viewport.addEventListener('pointermove',pointerMove); viewport.addEventListener('pointerup',pointerUp); viewport.addEventListener('pointercancel',pointerUp);
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    const dir = btn.dataset.dir;
    const zoom = btn.dataset.zoom;
    const pan = 60;
    if(dir === 'up') state.view.y += pan;
    else if(dir === 'down') state.view.y -= pan;
    else if(dir === 'left') state.view.x += pan;
    else if(dir === 'right') state.view.x -= pan;
    else if(zoom === 'in') state.view.zoom = Math.min(2.5, state.view.zoom * 1.2);
    else if(zoom === 'out') state.view.zoom = Math.max(0.25, state.view.zoom / 1.2);
    else if(btn.id === 'fit') fitView();
    setView();
  });
});
viewport.addEventListener('wheel',e=>{if(!e.ctrlKey&&!e.metaKey)return;e.preventDefault();const r=viewport.getBoundingClientRect(), old=state.view.zoom, next=Math.max(.25,Math.min(1.5,old*(e.deltaY>0?.9:1.1)));const dx=e.clientX-r.left,dy=e.clientY-r.top;state.view.x=dx-(dx-state.view.x)*next/old;state.view.y=dy-(dy-state.view.y)*next/old;state.view.zoom=next;setView();},{passive:false});
document.querySelector('#search').addEventListener('input',debounce(async e=>{const host=document.querySelector('#results'),q=e.target.value.trim();host.innerHTML='';if(!q)return;const results=await api(`/api/search?q=${encodeURIComponent(q)}`);results.forEach(result=>{const b=document.createElement('button');b.className='result';b.innerHTML=`${escapeText(result.page_title)}<small>${escapeText(result.notebook_title)}</small>`;b.onclick=()=>{host.innerHTML='';document.querySelector('#search').value='';openPage(result.page_id)};host.append(b)});},220));
setView();refreshTree();
