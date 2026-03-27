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
    const logoutBtn = document.getElementById('logout-btn');
    const sidebar = document.getElementById('app-sidebar');

    if (logoutBtn) logoutBtn.style.display = token ? 'inline-block' : 'none';

    if (view === 'login') {
        if (sidebar) sidebar.classList.add('d-none');
        document.getElementById('page-title').textContent = 'Authentication Required';
        topbarRight.style.display = 'none';
        document.getElementById('view-login').classList.add('view-active');
    } else {
        if (sidebar) sidebar.classList.remove('d-none');

        if (view === 'create') {
            document.getElementById('page-title').textContent = 'Create Environment';
            topbarRight.style.display = 'none';
            renderCreate();
        } else if (view === 'blueprints') {
            document.getElementById('nav-blueprints') && document.getElementById('nav-blueprints').classList.add('active');
            document.getElementById('page-title').textContent = 'Image Templates';
            topbarRight.innerHTML = `<button class="btn-create" onclick="showCreateBlueprint()">+ New Template</button>`;
            topbarRight.style.display = 'flex';
            renderBlueprints();
        } else if (view === 'details' && id) {
            document.getElementById('page-title').textContent = 'Environment Details';
            topbarRight.style.display = 'none';
            renderDetails(id);
        } else {
            document.getElementById('nav-environments') && document.getElementById('nav-environments').classList.add('active');
            document.getElementById('page-title').textContent = 'Environments';
            topbarRight.innerHTML = `
            <div class="search-box">
                <span style="color:#4a5568;font-size:12px;">🔍</span>
                <input type="text" id="search-input" placeholder="Search environments..." oninput="filterTable()">
            </div>
            <button class="btn-create" onclick="window.location.hash='#create'">+ Create New Environment</button>
        `;
            topbarRight.style.display = 'flex';
            renderDashboard();
        }
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
                            ${statusKey === 'STOPPED' ? `<button class="ab" onclick="handleAction('${d.id}','start')" title="Start">&#9654;</button>` : ''}
                            ${statusKey === 'RUNNING' ? `<button class="ab" onclick="handleAction('${d.id}','stop')" title="Stop">&#9209;</button>` : ''}
                            <button class="ab del" onclick="handleDelete('${d.id}')" title="Delete">&#128465;</button>
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

    // Restore default visibility on reset
    document.getElementById('source-image-fields').style.display = 'block';
    document.getElementById('source-git-fields').style.display = 'none';

    // Wire up source-type toggle
    document.querySelectorAll('input[name="source_type"]').forEach(radio => {
        // Remove stale listeners by cloning
        const fresh = radio.cloneNode(true);
        radio.parentNode.replaceChild(fresh, radio);
        fresh.addEventListener('change', () => {
            const isGit = fresh.value === 'git';
            document.getElementById('source-image-fields').style.display = isGit ? 'none' : 'block';
            document.getElementById('source-git-fields').style.display = isGit ? 'block' : 'none';
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
async function handleCreate(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const btn = document.getElementById('create-submit-btn');

    // Collect env vars
    const envObj = {};
    document.getElementsByName('env_key[]').forEach((keyEl, i) => {
        const valEl = document.getElementsByName('env_val[]')[i];
        if (keyEl.value.trim()) envObj[keyEl.value.trim()] = valEl ? valEl.value : '';
    });

    // Determine source: image or git_url
    const sourceType = fd.get('source_type');         // 'image' | 'git'
    const containerName = (fd.get('container_name') || '').trim();
    const role = (fd.get('role') || 'app').trim();

    let containerSpec;
    if (sourceType === 'git') {
        const gitUrl = (fd.get('git_url') || '').trim();
        if (!gitUrl) { showToast('Git URL is required', 'error'); return; }
        const port = parseInt(fd.get('git_internal_port') || '80', 10);
        containerSpec = {
            name: containerName,
            role: role,
            git_url: gitUrl,
            ports: { [port]: port },   // { host_port: container_port }
            env_vars: envObj,
        };
    } else {
        const imageTag = (fd.get('image_tag') || '').trim();
        if (!imageTag) { showToast('Docker image tag is required', 'error'); return; }
        const port = parseInt(fd.get('internal_port') || '80', 10);
        containerSpec = {
            name: containerName,
            role: role,
            image: imageTag,
            ports: { [port]: port },
            env_vars: envObj,
        };
    }

    // Build the DeploymentCreate payload
    const networkName = (fd.get('network_name') || '').trim() || undefined;
    const payload = {
        ...(networkName ? { network_name: networkName } : {}),
        containers: [containerSpec],
    };

    try {
        btn.textContent = 'Launching…';
        btn.disabled = true;

        await api('/deployments', { method: 'POST', body: JSON.stringify(payload) });
        showToast('Environment queued — launching in background!', 'ok');
        window.location.hash = '#dashboard';

    } catch (err) {
        showToast('Creation failed: ' + err.message, 'error');
    } finally {
        btn.textContent = 'Create & Launch';
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

function addEnvVar() {
    const row = document.createElement('div');
    row.className = 'ev-row';
    row.innerHTML = `
        <input type="text" name="env_key[]" class="fi" placeholder="KEY">
        <input type="text" name="env_val[]" class="fi" placeholder="VALUE">
        <button type="button" class="ab" onclick="this.parentElement.remove()" title="Remove" style="color:#f87171;">✕</button>
    `;
    document.getElementById('env-vars-container').appendChild(row);
}

function parseBulkEnv() {
    const textarea = document.getElementById('bulk-env-paste');
    if (!textarea) return;

    const raw = textarea.value;
    if (!raw.trim()) return;

    const lines = raw.split('\n');
    let added = 0;

    lines.forEach(line => {
        const trimmed = line.trim();
        // Skip blank lines and comments
        if (!trimmed || trimmed.startsWith('#')) return;

        // Split only on the FIRST '=' so values like BASE64=abc=def= work correctly
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx === -1) return;  // no '=' found — skip malformed line

        const key = trimmed.slice(0, eqIdx).trim();
        const value = trimmed.slice(eqIdx + 1).trim();

        if (!key) return;

        addEnvVar();
        added++;

        // Fill the row that was just appended
        const container = document.getElementById('env-vars-container');
        const rows = container.querySelectorAll('.ev-row');
        const lastRow = rows[rows.length - 1];
        lastRow.querySelector('[name="env_key[]"]').value = key;
        lastRow.querySelector('[name="env_val[]"]').value = value;
    });

    if (added > 0) {
        textarea.value = '';
        showToast(`Parsed ${added} variable${added === 1 ? '' : 's'} from .env`, 'ok');
    } else {
        showToast('No valid KEY=VALUE pairs found', 'error');
    }
}


// ===== AUTHENTICATION =====
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

function logout() {
    localStorage.removeItem('auth_token');
    window.location.hash = '#login';
    showToast('Logged out securely', 'ok');
}

// ===== INIT =====
window.addEventListener('hashchange', navigate);
window.addEventListener('load', () => {
    document.getElementById('create-form').addEventListener('submit', handleCreate);
    document.getElementById('blueprint-form').addEventListener('submit', handleCreateBlueprint);
    document.getElementById('login-form').addEventListener('submit', handleLogin);

    // Bulk env-paste: fire on paste (Ctrl+V) and on input (drag-drop / programmatic)
    // Use setTimeout so the textarea value is already updated when paste fires.
    const bulkArea = document.getElementById('bulk-env-paste');
    if (bulkArea) {
        bulkArea.addEventListener('paste', () => setTimeout(parseBulkEnv, 0));
        bulkArea.addEventListener('input', () => { if (bulkArea.value.includes('\n')) parseBulkEnv(); });
    }

    navigate();
});

