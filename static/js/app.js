const API_BASE = '/api';

const state = {
    deployments: [],
    projects: [],
    blueprints: [],
    volumes: [],
    currentView: 'dashboard',
    pollingInterval: null,
    editingId: null
};

// ===== PROJECT DELETE =====
async function deleteProject(id) {
    if (!confirm('Delete this project? All its environments will also be deleted!')) return;
    try {
        await api(`/projects/${id}`, { method: 'DELETE' });
        showToast('Project deleted', 'ok');
        renderDashboard(); // Re-fetch everything
    } catch (e) {
        showToast('Delete failed: ' + e.message, 'error');
    }
}

// ===== TOAST =====
function showToast(msg, type = 'ok') {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = `show toast-${type}`;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.className = ''; }, 3500);
}

// ===== API HELPER =====
async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    const token = localStorage.getItem('auth_token');

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: headers
    });

    if (response.status === 401 || response.status === 403) {
        if (window.location.hash !== '#login') {
            localStorage.removeItem('auth_token');
            window.location.hash = '#login';
        }
        throw new Error('Unauthorized. Please log in.');
    }

    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Error ${response.status}`);
    return data;
}

// ===== ROUTER =====
function navigate() {
    try {
        const hash = window.location.hash || '#dashboard';
        const parts = hash.split('/');
        const view = parts[0].substring(1);
        const id = parts[1];

        // Auth guard
        const token = localStorage.getItem('auth_token');
        if (!token && view !== 'login') {
            window.location.hash = '#login';
            return;
        }

        document.querySelectorAll('.view-container').forEach(el => el.classList.remove('view-active'));
        if (state.pollingInterval) { clearInterval(state.pollingInterval); state.pollingInterval = null; }

        // Nav highlight
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

        const topbarRight = document.querySelector('.topbar-right');
        const sidebar = document.getElementById('app-sidebar');
        const pageTitle = document.getElementById('page-title');

        if (view === 'login') {
            if (sidebar) sidebar.classList.add('d-none');
            if (pageTitle) pageTitle.textContent = 'Authentication Required';
            if (topbarRight) topbarRight.style.display = 'none';
            const viewLogin = document.getElementById('view-login');
            if (viewLogin) viewLogin.classList.add('view-active');
        } else {
            if (sidebar) sidebar.classList.remove('d-none');

            if (view === 'create') {
                if (pageTitle) pageTitle.textContent = 'Create Environment';
                if (topbarRight) topbarRight.style.display = 'none';
                renderCreate();
            } else if (view === 'blueprints') {
                const navBlueprints = document.getElementById('nav-blueprints');
                if (navBlueprints) navBlueprints.classList.add('active');
                if (pageTitle) pageTitle.textContent = 'Image Templates';
                if (topbarRight) {
                    topbarRight.innerHTML = `<button class="btn-create" onclick="showCreateBlueprint()">+ New Template</button>`;
                    topbarRight.style.display = 'flex';
                }
                renderBlueprints();
            } else if (view === 'volumes') {
                const navVolumes = document.getElementById('nav-volumes');
                if (navVolumes) navVolumes.classList.add('active');
                if (pageTitle) pageTitle.textContent = 'Storage Volumes';
                if (topbarRight) {
                    topbarRight.innerHTML = `<button class="btn-create" onclick="createVolume()">+ Create Volume</button>`;
                    topbarRight.style.display = 'flex';
                }
                const viewVolumes = document.getElementById('view-volumes');
                if (viewVolumes) viewVolumes.classList.add('view-active');
                renderVolumes();
            } else if (view === 'details' && id) {
                if (pageTitle) pageTitle.textContent = 'Environment Details';
                if (topbarRight) topbarRight.style.display = 'none';
                renderDetails(id);
            } else {
                const navEnvs = document.getElementById('nav-environments');
                if (navEnvs) navEnvs.classList.add('active');
                if (pageTitle) pageTitle.textContent = 'Environments';
                if (topbarRight) {
                    topbarRight.innerHTML = `
                    <button class="btn-create" onclick="window.location.hash='#create'">+ Create New Environment</button>
                    `;
                    topbarRight.style.display = 'flex';
                }
                renderDashboard();
            }
        }
    } catch (e) {
        console.error("Routing error:", e);
    }
}

// ===== DASHBOARD =====
async function renderDashboard() {
    document.getElementById('view-dashboard').classList.add('view-active');

    try {
        const [deployments, projects, blueprints, volumes] = await Promise.all([
            api('/deployments'),
            api('/projects').catch(() => []),
            api('/blueprints').catch(() => []),
            api('/volumes').catch(() => [])
        ]);
        state.deployments = deployments;
        state.projects = projects;
        state.blueprints = blueprints;
        state.volumes = volumes || [];

        // Build project lookup map
        const projectMap = {};
        projects.forEach(p => { projectMap[p.id] = p.name; });

        // Update sidebar projects
        const colors = ['dot-g', 'dot-b', 'dot-o', 'dot-p', 'dot-r'];
        const projectHtml = projects.length === 0
            ? '<div class="project-item" style="color:#4a5568">No projects yet</div>'
            : projects.map((p, i) => `
                <div class="project-item" style="justify-content:space-between">
                    <div style="display:flex;align-items:center;gap:8px;overflow:hidden">
                        <span class="dot ${colors[i % colors.length]}"></span>
                        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${p.name}</span>
                    </div>
                    <button class="ab del" style="width:22px;height:22px;font-size:11px;flex-shrink:0" onclick="deleteProject('${p.id}')" title="Delete Project">&#10006;</button>
                </div>`).join('');
        document.getElementById('project-list').innerHTML = projectHtml;

        // Stats
        const running = deployments.filter(d => d.status === 'RUNNING' || d.status === 'running').length;
        const pending = deployments.filter(d => d.status === 'PENDING' || d.status === 'pending').length;
        const failed = deployments.filter(d => d.status === 'FAILED' || d.status === 'failed').length;
        document.getElementById('stat-running').textContent = running;
        document.getElementById('stat-pending').textContent = pending;
        document.getElementById('stat-failed').textContent = failed;

        const tbody = document.getElementById('deployment-list');
        if (deployments.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No environments found. Click "+ Create New Environment" to get started.</td></tr>';
        } else {
            tbody.innerHTML = deployments.map(d => {
                // New model: network_name, containers[], user_id — no image_tag / project_id
                const envName = d.network_name || d.id.substring(0, 8);
                const imagesSummary = (d.containers || []).map(c => {
                    const img = c.image || '';
                    // Strip 'git:' prefix for display
                    return img.startsWith('git:') ? '🔧 ' + c.role : img;
                }).join(', ') || '—';
                const containerCount = (d.containers || []).length;
                const statusKey = (d.status || '').toUpperCase();
                return `
                <tr>
                    <td>
                        <div class="env-name">${envName}</div>
                        <div class="env-id">ID: ${d.id.substring(0, 8)} &middot; ${containerCount} container${containerCount !== 1 ? 's' : ''}</div>
                    </td>
                    <td style="color:#94a3b8;font-size:13px;">${imagesSummary}</td>
                    <td><span class="badge badge-${d.status}">${d.status}</span></td>
                    <td style="font-size:13px;color:#94a3b8;">&mdash;</td>
                    <td>
                        <div class="actions">
                            <a href="#details/${d.id}" class="ab" title="Manage">&#128203;</a>
                            <button class="ab btn-edit" onclick="editDeployment('${d.id}')" title="Edit">&#9998;</button>
                            ${statusKey === 'STOPPED' ? `<button class="ab" onclick="handleAction('${d.id}','start')" title="Start">&#9654;</button>` : ''}
                            ${statusKey === 'RUNNING' ? `<button class="ab" onclick="handleAction('${d.id}','stop')" title="Stop">&#9209;</button>` : ''}
                            <button class="ab del" onclick="handleDelete('${d.id}')" title="Delete"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
                        </div>
                    </td>
                </tr>`;
            }).join('');
        }
        document.getElementById('table-count').textContent = `Showing ${deployments.length} entr${deployments.length === 1 ? 'y' : 'ies'}`;

    } catch (e) {
        showToast('Failed to load data: ' + e.message, 'error');
    }
}

function filterTable() {
    const q = document.getElementById('search-input').value.toLowerCase();
    document.querySelectorAll('#deployment-list tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}

// ===== PROJECTS SIDEBAR =====
async function loadProjects() {
    try {
        const projects = await api('/projects');
        state.projects = projects;
        const colors = ['dot-green', 'dot-blue', 'dot-orange', 'dot-purple', 'dot-red'];
        const html = projects.length === 0
            ? '<div class="project-item" style="color:#4a5568">No projects yet</div>'
            : projects.map((p, i) => `<div class="project-item"><span class="dot ${colors[i % colors.length]}"></span>${p.name}</div>`).join('');
        document.getElementById('project-list').innerHTML = html;
    } catch (e) {
        // silently fail for sidebar
    }
}

// ===== BLUEPRINTS =====
async function renderBlueprints() {
    document.getElementById('view-blueprints').classList.add('view-active');
    document.getElementById('create-bp-form').style.display = 'none';
    try {
        const bps = await api('/blueprints');
        const tbody = document.getElementById('blueprint-list');
        tbody.__data = bps; // Save for edit function
        if (bps.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No templates yet. Click "+ New Template" to create one.</td></tr>';
        } else {
            tbody.innerHTML = bps.map(bp => `
                <tr>
                    <td><div class="env-name">${bp.name}</div></td>
                    <td style="color:#94a3b8;font-size:13px">${bp.image_tag}</td>
                    <td>${bp.default_port}</td>
                    <td><div class="actions">
                        <button class="ab" onclick="editBlueprint('${bp.id}')" title="Edit">&#9998;</button>
                        <button class="ab del" onclick="deleteBlueprint('${bp.id}')" title="Delete"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
                    </div></td>
                </tr>
            `).join('');
        }
    } catch (e) {
        showToast('Failed to load blueprints: ' + e.message, 'error');
    }
}

function showCreateBlueprint() {
    document.getElementById('create-bp-form').style.display = 'block';
    document.getElementById('bp-form-title').textContent = 'New Image Template';
    document.getElementById('bp-submit-btn').textContent = 'Save Template';
    document.getElementById('blueprint-form').reset();
    document.getElementById('bp_id').value = '';
}

function editBlueprint(id) {
    const bps = document.getElementById('blueprint-list').__data || [];
    const bp = bps.find(b => b.id === id);
    if (!bp) return;

    document.getElementById('create-bp-form').style.display = 'block';
    document.getElementById('bp-form-title').textContent = 'Edit Image Template';
    document.getElementById('bp-submit-btn').textContent = 'Update Template';

    const form = document.getElementById('blueprint-form');
    form.elements['bp_id'].value = bp.id;
    form.elements['bp_name'].value = bp.name;
    form.elements['bp_image'].value = bp.image_tag;
    form.elements['bp_port'].value = bp.default_port;
    form.elements['bp_cpu_limit'].value = bp.cpu_limit || '';
    form.elements['bp_mem_limit'].value = bp.mem_limit || '';
}

async function handleCreateBlueprint(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const bpId = fd.get('bp_id');
    const method = bpId ? 'PATCH' : 'POST';
    const url = bpId ? `/blueprints/${bpId}` : '/blueprints';

    const cpuLimit = (fd.get('bp_cpu_limit') || '').trim() || null;
    const memLimit = (fd.get('bp_mem_limit') || '').trim() || null;

    try {
        await api(url, {
            method: method,
            body: JSON.stringify({
                name: fd.get('bp_name'),
                image_tag: fd.get('bp_image'),
                default_port: parseInt(fd.get('bp_port')),
                default_env_vars: {},
                cpu_limit: cpuLimit,
                mem_limit: memLimit,
            })
        });
        showToast(bpId ? 'Template updated!' : 'Template created!', 'ok');
        e.target.reset();
        document.getElementById('create-bp-form').style.display = 'none';
        renderBlueprints();
    } catch (e) {
        showToast('Failed: ' + e.message, 'error');
    }
}

async function deleteBlueprint(id) {
    if (!confirm('Delete this template?')) return;
    try {
        await api(`/blueprints/${id}`, { method: 'DELETE' });
        showToast('Template deleted', 'ok');
        renderBlueprints();
    } catch (e) {
        showToast('Delete failed: ' + e.message, 'error');
    }
}


async function renderCreate() {
    state.editingId = null;
    document.getElementById('page-title').textContent = 'Create Environment';
    document.getElementById('create-submit-btn').textContent = 'Create & Launch';
    document.getElementById('view-create').classList.add('view-active');
    document.getElementById('create-form').reset();
    document.getElementById('services-container').innerHTML = '';
    
    // Reset standard state and add the first container
    containerCounter = 0;
    addServiceCard();
}

let containerCounter = 0;

function addServiceCard() {
    containerCounter++;
    const template = document.getElementById('service-card-template');
    const clone = template.content.cloneNode(true);
    
    const card = clone.querySelector('.container-card');
    
    // Setup Radios unique grouping to prevent cross-card interference
    const radioName = `source_type_${containerCounter}`;
    // Handle templates dropdown
    const bpSelect = card.querySelector('.blueprint-select');
    if (bpSelect && state.blueprints) {
        state.blueprints.forEach(bp => {
            const opt = document.createElement('option');
            opt.value = bp.id;
            opt.textContent = `${bp.name} (${bp.image_tag})`;
            bpSelect.appendChild(opt);
        });
        
        bpSelect.addEventListener('change', (e) => {
            const pbId = e.target.value;
            if (!pbId) return; // custom
            
            const bp = state.blueprints.find(b => b.id === pbId);
            if (!bp) return;
            
            card.querySelector('[name="image_tag"]').value = bp.image_tag || '';
            card.querySelector('[name="internal_port"]').value = bp.default_port || '80';
            
            // Populate resource limits from blueprint
            const cpuInput = card.querySelector('[name="cpu_limit"]');
            if (cpuInput) cpuInput.value = bp.cpu_limit || '';
            const memInput = card.querySelector('[name="mem_limit"]');
            if (memInput) memInput.value = bp.mem_limit || '512m';
            
            // clear old envs
            const envContainer = card.querySelector('.env-vars-container');
            if (envContainer) envContainer.innerHTML = '';
            
            // add template envs
            if (bp.default_env_vars && typeof bp.default_env_vars === 'object') {
                for (let [k, v] of Object.entries(bp.default_env_vars)) {
                    addEnvVarToContainer(envContainer || card.querySelector('.env-vars-container'), k, v);
                }
            }
            
            // pre-guess a role based on name
            const roleInput = card.querySelector('[name="role"]');
            if (roleInput && !roleInput.value) {
                if (bp.name.toLowerCase().includes('db') || bp.name.toLowerCase().includes('postgres') || bp.name.toLowerCase().includes('redis')) {
                    roleInput.value = 'db';
                } else {
                    roleInput.value = 'app';
                }
            }
        });
    }

    // Populate volumes dropdown
    const volSelect = card.querySelector('.volume-select');
    if (volSelect && state.volumes) {
        state.volumes.forEach(vol => {
            const opt = document.createElement('option');
            opt.value = vol.name;
            opt.textContent = vol.name;
            volSelect.appendChild(opt);
        });
    }

    const srcImage = card.querySelector('.src-image-radio');
    const srcGit = card.querySelector('.src-git-radio');
    srcImage.name = radioName;
    srcGit.name = radioName;
    
    const imgFields = card.querySelector('.source-image-fields');
    const gitFields = card.querySelector('.source-git-fields');

    const nameInput = card.querySelector('[name="container_name"]');
    if (nameInput) {
        nameInput.addEventListener('input', updateDependencySelects);
    }

    // Radio logic
    srcImage.addEventListener('change', () => {
        imgFields.style.display = 'block';
        gitFields.style.display = 'none';
    });
    srcGit.addEventListener('change', () => {
        imgFields.style.display = 'none';
        gitFields.style.display = 'block';
    });

    // Bulk Paste logic scoped to this specific card
    const bulkArea = card.querySelector('.bulk-env-paste');
    const envContainer = card.querySelector('.env-vars-container');
    
    function parseCardBulkEnv() {
        const raw = bulkArea.value;
        if (!raw.trim()) return;
        const lines = raw.split('\n');
        let added = 0;
        lines.forEach(line => {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith('#')) return;
            const eqIdx = trimmed.indexOf('=');
            if (eqIdx === -1) return;
            const key = trimmed.slice(0, eqIdx).trim();
            const value = trimmed.slice(eqIdx + 1).trim();
            if (!key) return;
            addEnvVarToContainer(envContainer, key, value);
            added++;
        });
        if (added > 0) {
            bulkArea.value = '';
            showToast(`Parsed ${added} variable(s)`, 'ok');
        }
    }
    
    bulkArea.addEventListener('paste', () => setTimeout(parseCardBulkEnv, 0));
    bulkArea.addEventListener('input', () => { if (bulkArea.value.includes('\n')) parseCardBulkEnv(); });

    document.getElementById('services-container').appendChild(card);
    updateDependencySelects();
}

function removeContainerCard(btn) {
    const wrapper = document.getElementById('services-container');
    if (wrapper.children.length <= 1) {
        showToast('You must have at least one container!', 'error');
        return;
    }
    btn.closest('.container-card').remove();
    updateDependencySelects();
}

function updateDependencySelects() {
    const names = [];
    document.querySelectorAll('#services-container .container-card').forEach(card => {
        const nameInput = card.querySelector('[name="container_name"]');
        if (nameInput && nameInput.value.trim()) {
            names.push({ name: nameInput.value.trim(), card: card });
        }
    });

    document.querySelectorAll('#services-container .container-card').forEach(card => {
        const wrapper = card.querySelector('.depends-on-wrapper');
        if (!wrapper) return;
        
        const currentSelected = Array.from(wrapper.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value);
        wrapper.innerHTML = '';
        
        let hasOptions = false;
        names.forEach(n => {
            if (n.card !== card) {
                hasOptions = true;
                const label = document.createElement('label');
                label.className = 'dependency-pill';
                
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.value = n.name;
                cb.style.display = 'none';
                if (currentSelected.includes(n.name)) {
                    cb.checked = true;
                }
                
                const span = document.createElement('span');
                span.textContent = n.name;
                
                label.appendChild(cb);
                label.appendChild(span);
                wrapper.appendChild(label);
                
                cb.addEventListener('change', () => {
                    if (cb.checked) {
                        label.classList.add('selected');
                    } else {
                        label.classList.remove('selected');
                    }
                });
                if (cb.checked) label.classList.add('selected');
            }
        });

        if (!hasOptions) {
            wrapper.innerHTML = '<span style="color: #4a5568; font-size: 13px; margin-top: 4px;">No other services</span>';
        }
    });
}

function addEnvVarToContainer(containerEl, key = '', val = '') {
    const row = document.createElement('div');
    row.className = 'ev-row';
    row.innerHTML = `
        <input type="text" class="fi env-key-input" placeholder="KEY" value="${key}">
        <input type="text" class="fi env-val-input" placeholder="VALUE" value="${val}">
        <button type="button" class="ab" onclick="this.parentElement.remove()" title="Remove" style="color:#f87171;">✕</button>
    `;
    containerEl.appendChild(row);
}

function addEnvVar(btn) {
    const containerEl = btn.parentElement.querySelector('.env-vars-container');
    addEnvVarToContainer(containerEl);
}

// ===== DETAILS VIEW =====
async function renderDetails(id) {
    document.getElementById('view-details').classList.add('view-active');

    async function update() {
        try {
            const deps = await api('/deployments');
            const d = deps.find(x => x.id === id);
            if (!d) return;

            document.getElementById('det-id').textContent = d.id;
            const statusEl = document.getElementById('det-status');
            statusEl.textContent = d.status;
            statusEl.className = `status-badge badge-${d.status}`;

            // New model: show network_name and container list instead of image_tag/port
            const envName = d.network_name || d.id.substring(0, 8);
            if (document.getElementById('det-image')) {
                document.getElementById('det-image').textContent = envName;
            }
            if (document.getElementById('det-port')) {
                const containers = d.containers || [];
                document.getElementById('det-port').textContent =
                    containers.map(c => `${c.role}: ${c.image} (${c.status})`).join(' | ') || '—';
            }

            // Build Public URL from host_port of the app/frontend container
            const link = document.getElementById('det-link');
            if (link) {
                const appContainer = (d.containers || []).find(c =>
                    c.host_port && ['frontend', 'app', 'backend', 'web'].includes(c.role)
                ) || (d.containers || []).find(c => c.host_port);
                if (appContainer && appContainer.host_port) {
                    link.innerHTML = `<a href="http://localhost:${appContainer.host_port}" target="_blank">http://localhost:${appContainer.host_port}</a>`;
                } else {
                    link.innerHTML = 'Pending...';
                }
            }

            try {
                const logsRes = await api(`/deployments/${id}/logs?tail=100`);
                // logsRes.logs is a dict: { containerName: "log text", ... }
                const logsData = logsRes.logs;
                let logsText = '';
                if (typeof logsData === 'string') {
                    logsText = logsData;
                } else if (logsData && typeof logsData === 'object') {
                    logsText = Object.entries(logsData)
                        .map(([name, text]) => `=== ${name} ===\n${text}`)
                        .join('\n\n');
                }
                document.getElementById('det-logs').textContent = logsText || 'No logs yet...';
            } catch { /* no logs yet */ }

        } catch (e) {
            console.error('Details update error', e);
        }
    }

    await update();
    state.pollingInterval = setInterval(update, 3000);
}

// ===== ACTIONS =====
async function editDeployment(id) {
    try {
        const d = state.deployments.find(x => x.id === id);
        if (!d) return;
        state.editingId = id;

        document.getElementById('view-create').classList.add('view-active');
        document.getElementById('page-title').textContent = 'Edit Environment';
        document.getElementById('create-submit-btn').textContent = 'Save & Redeploy';
        
        document.getElementById('create-form').reset();
        document.getElementById('services-container').innerHTML = '';
        document.getElementById('field-network-name').value = d.network_name || '';
        
        containerCounter = 0;
        
        d.containers.forEach(c => {
            addServiceCard();
            const cards = document.querySelectorAll('.container-card');
            const card = cards[cards.length - 1];
            
            card.querySelector('[name="container_name"]').value = c.name;
            card.querySelector('[name="role"]').value = c.role || '';
            
            if (c.image.startsWith('git:')) {
                card.querySelector('.src-git-radio').click();
                card.querySelector('[name="git_url"]').value = c.image.substring(4);
            } else {
                card.querySelector('.src-image-radio').click();
                card.querySelector('[name="image_tag"]').value = c.image;
            }

            // Restore resource limits if stored on the container object
            const cpuInput = card.querySelector('[name="cpu_limit"]');
            if (cpuInput && c.cpu_limit) cpuInput.value = c.cpu_limit;
            const memInput = card.querySelector('[name="mem_limit"]');
            if (memInput) memInput.value = c.mem_limit || '512m';
        });
        updateDependencySelects();
    } catch (e) {
        showToast('Error editing: ' + e.message, 'error');
    }
}

async function handleCreate(e) {
    e.preventDefault();
    const btn = document.getElementById('create-submit-btn');

    const networkName = (document.getElementById('field-network-name').value || '').trim() || undefined;

    const cards = document.querySelectorAll('.container-card');
    const containers = [];

    for (let card of cards) {
        const containerName = (card.querySelector('[name="container_name"]').value || '').trim();
        const role = (card.querySelector('[name="role"]').value || 'app').trim();
        const wrapper = card.querySelector('.depends-on-wrapper');
        const dependsOn = wrapper ? Array.from(wrapper.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value) : [];

        // Extract environment variables
        const envObj = getEnvFromCard(card);

        // Volumes
        const volSelect = card.querySelector('.volume-select');
        const volTarget = card.querySelector('[name="volume_target"]');
        const volumesObj = {};
        
        if (volSelect && volSelect.value && volTarget && volTarget.value) {
            volumesObj[volSelect.value] = {
                "bind": volTarget.value.trim(),
                "mode": "rw"
            };
        }
        const finalVolumes = Object.keys(volumesObj).length > 0 ? volumesObj : undefined;

        const isGit = card.querySelector('.src-git-radio').checked;
        let containerSpec;

        if (isGit) {
            const gitUrl = (card.querySelector('[name="git_url"]').value || '').trim();
            if (!gitUrl) { showToast('Git URL is required for ' + (containerName || 'a container'), 'error'); return; }
            const port = parseInt(card.querySelector('[name="git_internal_port"]').value || '80', 10);
            const cpuLimit = (card.querySelector('[name="cpu_limit"]').value || '').trim() || undefined;
            const memLimit = (card.querySelector('[name="mem_limit"]').value || '').trim() || undefined;
            containerSpec = {
                name: containerName || 'git-app',
                role: role,
                git_url: gitUrl,
                ports: { [port]: port },
                env_vars: envObj,
                volumes: finalVolumes,
                depends_on: dependsOn.length > 0 ? dependsOn : undefined,
                cpu_limit: cpuLimit,
                mem_limit: memLimit,
            };
        } else {
            const imageTag = (card.querySelector('[name="image_tag"]').value || '').trim();
            if (!imageTag) { showToast('Docker image tag is required for ' + (containerName || 'a container'), 'error'); return; }
            const port = parseInt(card.querySelector('[name="internal_port"]').value || '80', 10);
            const cpuLimit = (card.querySelector('[name="cpu_limit"]').value || '').trim() || undefined;
            const memLimit = (card.querySelector('[name="mem_limit"]').value || '').trim() || undefined;
            containerSpec = {
                name: containerName || 'image-app',
                role: role,
                image: imageTag,
                ports: { [port]: port },
                env_vars: envObj,
                volumes: finalVolumes,
                depends_on: dependsOn.length > 0 ? dependsOn : undefined,
                cpu_limit: cpuLimit,
                mem_limit: memLimit,
            };
        }
        containers.push(containerSpec);
    }

    const payload = {
        ...(networkName ? { network_name: networkName } : {}),
        containers: containers,
    };

    try {
        btn.textContent = state.editingId ? 'Redeploying…' : 'Launching…';
        btn.disabled = true;

        if (state.editingId) {
            await api(`/deployments/${state.editingId}`, { method: 'PUT', body: JSON.stringify(payload) });
            state.editingId = null;
            showToast('Environment updated and redeploying!', 'ok');
        } else {
            await api('/deployments', { method: 'POST', body: JSON.stringify(payload) });
            showToast('Environment queued — launching in background!', 'ok');
        }
        window.location.hash = '#dashboard';
    } catch (err) {
        showToast((state.editingId ? 'Update' : 'Creation') + ' failed: ' + err.message, 'error');
    } finally {
        btn.textContent = state.editingId ? 'Save & Redeploy' : 'Create & Launch';
        btn.disabled = false;
    }
}

async function handleAction(id, action) {
    try {
        await api(`/deployments/${id}/${action}`, { method: 'POST' });
        showToast(`Action "${action}" sent`, 'ok');
        renderDashboard();
    } catch (e) {
        showToast('Action failed: ' + e.message, 'error');
    }
}

async function handleDelete(id, projectId = null, redirect = false) {
    if (!confirm('Delete this environment?')) return;
    try {
        await api(`/deployments/${id}`, { method: 'DELETE' });
        // Auto-cleanup: delete the project if it now has no deployments
        if (projectId) {
            try {
                const remaining = await api(`/deployments`);
                const projectStillHasDeployments = remaining.some(d => d.project_id === projectId);
                if (!projectStillHasDeployments) {
                    await api(`/projects/${projectId}`, { method: 'DELETE' });
                }
            } catch (cleanupErr) {
                // Silent — project cleanup is best-effort
            }
        }
        showToast('Environment deleted', 'ok');
        if (redirect) window.location.hash = '#dashboard';
        else renderDashboard();
    } catch (e) {
        showToast('Delete failed: ' + e.message, 'error');
    }
}

// ===== AUTHENTICATION =====
function toggleAuthForm(formType) {
    const loginCard = document.getElementById('login-card');
    const registerCard = document.getElementById('register-card');
    if (formType === 'register') {
        loginCard.style.display = 'none';
        registerCard.style.display = 'block';
    } else {
        loginCard.style.display = 'block';
        registerCard.style.display = 'none';
    }
}

async function handleLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('login-submit-btn');
    const fd = new FormData(e.target);
    const params = new URLSearchParams();
    params.append('username', fd.get('username'));
    params.append('password', fd.get('password'));

    try {
        btn.textContent = 'Authenticating...';
        btn.disabled = true;
        const res = await api('/auth/token', {
            method: 'POST',
            body: params.toString(),
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
        });
        localStorage.setItem('auth_token', res.access_token);
        showToast('Login successful', 'ok');
        e.target.reset();
        window.location.hash = '#dashboard';
    } catch (err) {
        showToast('Login failed: ' + err.message, 'error');
    } finally {
        btn.textContent = 'Authorize';
        btn.disabled = false;
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const btn = document.getElementById('register-submit-btn');
    const fd = new FormData(e.target);
    
    const payload = {
        username: fd.get('username'),
        email: fd.get('email'),
        password: fd.get('password')
    };

    try {
        btn.textContent = 'Creating Account...';
        btn.disabled = true;
        
        await api('/auth/register', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        
        showToast('Registration successful! Please log in.', 'ok');
        e.target.reset();
        
        toggleAuthForm('login');
        document.querySelector('#login-form input[name="username"]').value = payload.username;
        
    } catch (err) {
        showToast('Registration failed: ' + err.message, 'error');
    } finally {
        btn.textContent = 'Create Account';
        btn.disabled = false;
    }
}

// ===== VOLUMES =====
async function renderVolumes() {
    const list = document.getElementById('volume-list');
    try {
        const volumes = await api('/volumes');
        state.volumes = volumes;
        list.innerHTML = '';
        if (volumes.length === 0) {
            list.innerHTML = '<tr><td colspan="3" class="empty-row">No volumes found.</td></tr>';
            return;
        }

        volumes.forEach(vol => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${vol.name}</strong></td>
                <td style="color:#94a3b8; font-size:13px">${new Date(vol.created_at).toLocaleString()}</td>
                <td style="text-align: center;">
                    <button class="ab del" onclick="deleteVolume('${vol.id}')" title="Delete" style="margin: 0 auto;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
                </td>
            `;
            list.appendChild(tr);
        });
    } catch (e) {
        showToast('Error loading volumes: ' + e.message, 'error');
    }
}

async function createVolume() {
    const name = prompt('Enter a name for the new Docker volume:');
    if (!name || !name.trim()) return;

    try {
        await api('/volumes', {
            method: 'POST',
            body: JSON.stringify({ name: name.trim() })
        });
        showToast('Volume created successfully!', 'ok');
        renderVolumes();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteVolume(id) {
    if (!confirm('Delete this standalone volume? Data inside it will be permanently lost!')) return;
    try {
        await api(`/volumes/${id}`, { method: 'DELETE' });
        showToast('Volume deleted.', 'ok');
        renderVolumes();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function promptCreateVolumeForForm(btn) {
    const name = prompt('Enter a name for the new Docker volume:');
    if (!name || !name.trim()) return;

    try {
        const result = await api('/volumes', {
            method: 'POST',
            body: JSON.stringify({ name: name.trim() })
        });
        state.volumes.push(result);
        showToast('Volume created successfully!', 'ok');
        
        // Find select in same card and add the option
        const card = btn.closest('.container-card');
        if (card) {
            const select = card.querySelector('.volume-select');
            if (select) {
                const opt = document.createElement('option');
                opt.value = result.name;
                opt.textContent = result.name;
                select.appendChild(opt);
                select.value = result.name; // Auto-select it
            }
        }
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function logout() {
    localStorage.removeItem('auth_token');
    window.location.hash = '#login';
    showToast('Logged out securely', 'ok');
}


function addEventListeners() {
    document.getElementById('create-form').addEventListener('submit', handleCreate);
    document.getElementById('blueprint-form').addEventListener('submit', handleCreateBlueprint);
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    
    const registerForm = document.getElementById('register-form');
    if (registerForm) {
        registerForm.addEventListener('submit', handleRegister);
    }
    
    // Attach listener to Add Service button
    const addServiceBtn = document.getElementById('add-service-btn');
    if (addServiceBtn) {
        addServiceBtn.addEventListener('click', addServiceCard);
    }
}

function getEnvFromCard(card) {
    const envObj = {};
    const envKeys = card.querySelectorAll('.env-key-input');
    const envVals = card.querySelectorAll('.env-val-input');
    envKeys.forEach((keyEl, i) => {
        const key = keyEl.value.trim();
        if (key) envObj[key] = envVals[i] ? envVals[i].value : '';
    });
    return envObj;
}

// ===== INIT =====
window.addEventListener('hashchange', navigate);
window.addEventListener('load', () => {
    addEventListeners();
    navigate();
});

