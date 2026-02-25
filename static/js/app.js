const API_BASE = '/api';

const state = {
    deployments: [],
    projects: [],
    currentView: 'dashboard',
    pollingInterval: null
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
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...options.headers }
    });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Error ${response.status}`);
    return data;
}

// ===== ROUTER =====
function navigate() {
    const hash = window.location.hash || '#dashboard';
    const parts = hash.split('/');
    const view = parts[0].substring(1);
    const id = parts[1];

    document.querySelectorAll('.view-container').forEach(el => el.classList.remove('view-active'));
    if (state.pollingInterval) { clearInterval(state.pollingInterval); state.pollingInterval = null; }

    // Nav highlight
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    if (view === 'create') {
        document.getElementById('page-title').textContent = 'Create Environment';
        renderCreate();
    } else if (view === 'blueprints') {
        document.getElementById('nav-blueprints') && document.getElementById('nav-blueprints').classList.add('active');
        document.getElementById('page-title').textContent = 'Image Templates';
        renderBlueprints();
    } else if (view === 'details' && id) {
        document.getElementById('page-title').textContent = 'Environment Details';
        renderDetails(id);
    } else {
        document.getElementById('nav-environments') && document.getElementById('nav-environments').classList.add('active');
        document.getElementById('page-title').textContent = 'Environments';
        renderDashboard();
    }
}

// ===== DASHBOARD =====
async function renderDashboard() {
    document.getElementById('view-dashboard').classList.add('view-active');

    try {
        const [deployments, projects] = await Promise.all([
            api('/deployments'),
            api('/projects').catch(() => [])
        ]);
        state.deployments = deployments;
        state.projects = projects;

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
                const projectName = projectMap[d.project_id] || d.image_tag.split(':')[0];
                const containerId = d.container_id ? d.container_id.substring(0, 12) : d.id.substring(0, 8);
                const statusKey = (d.status || '').toUpperCase();
                return `
                <tr>
                    <td>
                        <div class="env-name">${projectName}</div>
                        <div class="env-id">ID: ${containerId}</div>
                    </td>
                    <td style="color:#94a3b8;font-size:13px;">${d.image_tag}</td>
                    <td><span class="badge badge-${d.status}">${d.status}</span></td>
                    <td style="font-size:13px;color:#94a3b8;">${d.external_port ? ':' + d.external_port : '\u2014'}</td>
                    <td>
                        <div class="actions">
                            <a href="#details/${d.id}" class="ab" title="Manage">&#128203;</a>
                            ${statusKey === 'STOPPED' ? `<button class="ab" onclick="handleAction('${d.id}','start')" title="Start">&#9654;</button>` : ''}
                            ${statusKey === 'RUNNING' ? `<button class="ab" onclick="handleAction('${d.id}','stop')" title="Stop">&#9209;</button>` : ''}
                            <button class="ab del" onclick="handleDelete('${d.id}','${d.project_id}')" title="Delete">&#128465;</button>
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
                        <button class="ab del" onclick="deleteBlueprint('${bp.id}')" title="Delete">&#128465;</button>
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
}

async function handleCreateBlueprint(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const bpId = fd.get('bp_id');
    const method = bpId ? 'PATCH' : 'POST';
    const url = bpId ? `/blueprints/${bpId}` : '/blueprints';

    try {
        await api(url, {
            method: method,
            body: JSON.stringify({
                name: fd.get('bp_name'),
                image_tag: fd.get('bp_image'),
                default_port: parseInt(fd.get('bp_port')),
                default_env_vars: {}
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
    document.getElementById('view-create').classList.add('view-active');
    document.getElementById('create-form').reset();
    document.getElementById('env-vars-container').innerHTML = '';

    try {
        const blueprints = await api('/blueprints');
        const select = document.getElementById('blueprint-select');
        select.innerHTML = '<option value="" disabled selected>Select a blueprint...</option>' +
            blueprints.map(bp => `<option value="${bp.id}">${bp.name} (${bp.image_tag})</option>`).join('');
    } catch (e) {
        console.warn('Could not load blueprints', e);
    }

    // Mode toggle
    document.getElementsByName('mode').forEach(radio => {
        radio.addEventListener('change', (e) => {
            document.getElementById('blueprint-fields').style.display = e.target.value === 'blueprint' ? 'block' : 'none';
            document.getElementById('manual-fields').style.display = e.target.value === 'manual' ? 'block' : 'none';
        });
    });
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
            document.getElementById('det-image').textContent = d.image_tag;
            document.getElementById('det-port').textContent = d.internal_port;

            const link = document.getElementById('det-link');
            link.innerHTML = d.external_port
                ? `<a href="http://localhost:${d.external_port}" target="_blank">http://localhost:${d.external_port}</a>`
                : 'N/A';

            const statusKey = (d.status || '').toUpperCase();
            document.getElementById('det-controls').innerHTML = `
                ${statusKey === 'RUNNING' ? `<button class="action-btn btn-stop" onclick="handleAction('${d.id}','stop')">⏹ Stop</button>` : ''}
                ${statusKey === 'STOPPED' ? `<button class="action-btn btn-start" onclick="handleAction('${d.id}','start')">▶ Start</button>` : ''}
                <button class="action-btn btn-delete" onclick="handleDelete('${d.id}', true)">🗑 Delete</button>
            `;

            try {
                const logsRes = await api(`/deployments/${id}/logs?tail=100`);
                document.getElementById('det-logs').textContent = logsRes.logs || 'No logs yet...';
            } catch { /* no logs yet */ }

        } catch (e) {
            console.error('Details update error', e);
        }
    }

    await update();
    state.pollingInterval = setInterval(update, 3000);
}

// ===== ACTIONS =====
async function handleCreate(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const mode = formData.get('mode');

    try {
        const project = await api('/projects', {
            method: 'POST',
            body: JSON.stringify({ name: formData.get('name') })
        });

        const deployPayload = { project_id: project.id };

        if (mode === 'blueprint') {
            deployPayload.blueprint_id = formData.get('blueprint_id');
        } else {
            deployPayload.image_tag = formData.get('image_tag');
            deployPayload.internal_port = parseInt(formData.get('internal_port'));
            const envKeys = document.getElementsByName('env_key[]');
            const envVals = document.getElementsByName('env_val[]');
            const envObj = {};
            for (let i = 0; i < envKeys.length; i++) {
                if (envKeys[i].value) envObj[envKeys[i].value] = envVals[i].value;
            }
            deployPayload.env_vars = envObj;
        }

        await api('/deployments', { method: 'POST', body: JSON.stringify(deployPayload) });
        showToast('Environment launching!', 'ok');
        window.location.hash = '#dashboard';

    } catch (e) {
        showToast('Creation failed: ' + e.message, 'error');
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

function addEnvVar() {
    const row = document.createElement('div');
    row.className = 'env-var-row';
    row.innerHTML = `
        <input type="text" name="env_key[]" class="form-input" placeholder="KEY">
        <input type="text" name="env_val[]" class="form-input" placeholder="VALUE">
        <button type="button" class="action-btn danger" onclick="this.parentElement.remove()" title="Remove">✕</button>
    `;
    document.getElementById('env-vars-container').appendChild(row);
}

// ===== INIT =====
window.addEventListener('hashchange', navigate);
window.addEventListener('load', () => {
    document.getElementById('create-form').addEventListener('submit', handleCreate);
    document.getElementById('blueprint-form').addEventListener('submit', handleCreateBlueprint);
    navigate();
});
