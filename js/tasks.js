/* =================================================================
 * TradeAlgo Pro · Task Management dashboard (vanilla JS)
 * ----------------------------------------------------------------
 *   - List/Create tasks grouped by status with priority
 *   - Update status (todo → inprogress → done)
 *   - Subtasks with progress % on parent
 *   - All API calls use the Supabase access_token from localStorage
 * ================================================================= */

(() => {
    'use strict';

    // ---- Auth gate (mirrors the gate in index.html but inline so
    //      /tasks is its own page; redirects to /login on 401.)
    const TOKEN_KEY = 'traderalgopro.auth';
    const NEXT_PARAM = 'next';

    function getAccessToken() {
        try {
            const raw = localStorage.getItem(TOKEN_KEY);
            if (!raw) return null;
            // Supabase stores its session under a key like
            // `traderalgopro.auth-<project_ref>`; the bare TOKEN_KEY
            // is sometimes a code-verifier. Try both shapes.
            const direct = JSON.parse(raw);
            if (direct?.access_token) return direct.access_token;
        } catch (_) { /* fall through */ }
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (!k || !k.startsWith(TOKEN_KEY)) continue;
                const v = JSON.parse(localStorage.getItem(k) || 'null');
                if (v?.access_token) return v.access_token;
            }
        } catch (_) { /* fall through */ }
        return null;
    }

    function gateOrRedirect() {
        const token = getAccessToken();
        if (token) return token;
        const next = encodeURIComponent('/tasks');
        window.location.replace('/login?' + NEXT_PARAM + '=' + next);
        return null;
    }

    // ---- Boot
    const accessToken = gateOrRedirect();
    if (!accessToken) return;

    // ---- DOM refs
    const $ = (id) => document.getElementById(id);

    const els = {
        columns: {
            todo:       $('colTodo'),
            inprogress: $('colProgress'),
            done:       $('colDone'),
        },
        counts: {
            todo:       $('countTodo'),
            inprogress: $('countProgress'),
            done:       $('countDone'),
        },
        form:      $('taskForm'),
        title:     $('taskTitle'),
        desc:      $('taskDesc'),
        due:       $('taskDue'),
        priority:  $('taskPriority'),
        status:    $('taskStatus'),
        summary:   $('taskSummary'),
        submit:    $('taskSubmit'),
        cancel:    $('taskCancel'),
        toastHost: $('tasksToastHost'),
        userChip:  $('tasksUserChip'),
        logout:    $('tasksLogout'),
        search:    $('taskSearch'),
        filterPri: $('filterPriority'),
        newBtn:    $('newTaskBtn'),
    };

    // ---- State
    const state = {
        tasks: [],
        filtering: { text: '', priority: 'all' },
    };

    // ---- Helpers
    function showToast(title, sub, kind = 'info', ms = 3500) {
        if (!els.toastHost) return;
        const node = document.createElement('div');
        node.className = 'toast toast-' + kind;
        node.innerHTML = '<div class="toast-title">' +
            sanitize(title) + '</div>' +
            (sub ? '<div class="toast-sub">' + sanitize(sub) + '</div>' : '');
        els.toastHost.appendChild(node);
        setTimeout(() => {
            node.classList.add('toast-leaving');
            setTimeout(() => node.remove(), 350);
        }, ms);
    }

    function sanitize(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        })[c]);
    }

    function fmtDate(iso) {
        if (!iso) return null;
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const today = new Date();
        today.setHours(0,0,0,0);
        const cmp   = new Date(d); cmp.setHours(0,0,0,0);
        const diff  = Math.round((cmp - today) / 86400000);
        if (diff === 0)  return 'Today';
        if (diff === 1)  return 'Tomorrow';
        if (diff === -1) return 'Yesterday';
        if (diff  > 0 && diff < 7) return 'in ' + diff + ' days';
        if (diff  < 0 && diff > -7) return Math.abs(diff) + ' days ago';
        return d.toLocaleDateString(undefined, { day:'2-digit', month:'short' });
    }

    function nextStatus(cur) {
        return ({ todo:'inprogress', inprogress:'done', done:'todo' })[cur] || 'todo';
    }

    function statusLabel(s) {
        return ({ todo:'To-do', inprogress:'In-Progress', done:'Done' })[s] || s;
    }
    function priorityLabel(p) {
        return ({ low:'Low', medium:'Medium', high:'High' })[p] || p;
    }

    // ---- API
    async function api(path, opts = {}) {
        const headers = Object.assign(
            { 'Authorization': 'Bearer ' + accessToken },
            opts.body ? { 'Content-Type': 'application/json' } : {},
            opts.headers || {},
        );
        let resp;
        try {
            resp = await fetch(path, Object.assign({ method: 'GET', headers }, opts));
        } catch (e) {
            throw new Error('Network error — ' + (e?.message || e));
        }
        if (resp.status === 401) {
            showToast('🔒 Session expired', 'Please sign in again.', 'error', 4000);
            setTimeout(() => window.location.assign('/login?next=' + encodeURIComponent('/tasks')), 1200);
            throw new Error('unauthorized');
        }
        if (resp.status >= 400) {
            let detail = 'HTTP ' + resp.status;
            try { const j = await resp.json(); detail = j.detail || j.message || JSON.stringify(j); }
            catch (_) { try { detail = await resp.text(); } catch (_) {} }
            throw new Error(detail);
        }
        if (opts.method === 'DELETE') return null;
        return resp.json();
    }

    async function fetchAll() {
        try {
            state.tasks = await api('/api/tasks') || [];
            render();
        } catch (e) {
            if (e.message !== 'unauthorized') showToast('⚠️ Load failed', e.message, 'error');
        }
    }

    async function createTask(payload) {
        try {
            const t = await api('/api/tasks', { method: 'POST', body: JSON.stringify(payload) });
            state.tasks.unshift(t);
            render();
            showToast('✅ Task created', t.title, 'success', 1800);
        } catch (e) { showToast('⚠️ Create failed', e.message, 'error'); }
    }

    async function patchTask(id, payload) {
        try {
            const t = await api('/api/tasks/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify(payload) });
            replaceInState(t);
            render();
        } catch (e) { showToast('⚠️ Update failed', e.message, 'error'); }
    }

    async function deleteTask(id) {
        if (!confirm('Delete this task and its subtasks?')) return;
        try {
            await api('/api/tasks/' + encodeURIComponent(id), { method: 'DELETE' });
            state.tasks = state.tasks.filter(t => t.id !== id);
            render();
            showToast('🗑️ Task deleted', '', 'info', 1500);
        } catch (e) { showToast('⚠️ Delete failed', e.message, 'error'); }
    }

    async function addSubtask(taskId, title) {
        try {
            const t = await api('/api/tasks/' + encodeURIComponent(taskId) + '/subtasks',
                { method: 'POST', body: JSON.stringify({ title }) });
            replaceInState(t);
            render();
        } catch (e) { showToast('⚠️ Subtask add failed', e.message, 'error'); }
    }

    async function toggleSubtask(taskId, subId, done) {
        try {
            const t = await api('/api/tasks/' + encodeURIComponent(taskId) + '/subtasks/' + encodeURIComponent(subId),
                { method: 'PATCH', body: JSON.stringify({ done }) });
            replaceInState(t);
            render();
        } catch (e) { showToast('⚠️ Subtask update failed', e.message, 'error'); }
    }

    function replaceInState(updated) {
        const i = state.tasks.findIndex(t => t.id === updated.id);
        if (i === -1) state.tasks.unshift(updated); else state.tasks[i] = updated;
    }

    // ---- Rendering
    function filteredTasks() {
        const q = state.filtering.text.trim().toLowerCase();
        const p = state.filtering.priority;
        return state.tasks.filter(t => {
            if (p !== 'all' && t.priority !== p) return false;
            if (!q) return true;
            if ((t.title || '').toLowerCase().includes(q)) return true;
            if ((t.description || '').toLowerCase().includes(q)) return true;
            if ((t.subtasks || []).some(s => (s.title || '').toLowerCase().includes(q))) return true;
            return false;
        });
    }

    function taskCardHtml(t) {
        const subs       = (t.subtasks || []);
        const subsTotal  = subs.length;
        const subsDone   = subs.filter(s => s.done).length;
        const progress   = subsTotal ? Math.round((subsDone / subsTotal) * 100) : 0;
        const due        = fmtDate(t.due_date);
        const overdue    = t.due_date && new Date(t.due_date) < new Date(new Date().toDateString())
                            && t.status !== 'done';
        return `
<article class="task-card" data-id="${t.id}" data-status="${t.status}" data-priority="${t.priority}">
    <header class="task-head">
        <span class="task-priority pri-${t.priority}" title="Priority: ${priorityLabel(t.priority)}">${priorityLabel(t.priority)}</span>
        <h3 class="task-title">${sanitize(t.title)}</h3>
        <button class="task-x" title="Delete task" aria-label="Delete task">×</button>
    </header>
    ${t.description ? `<p class="task-desc">${sanitize(t.description)}</p>` : ''}
    <footer class="task-meta">
        ${due ? `<span class="task-due ${overdue ? 'due-overdue' : ''}">${overdue ? '⚠️ ' : '📅 '}${due}</span>` : ''}
        <span class="task-status-pill status-${t.status}">${statusLabel(t.status)}</span>
    </footer>

    <section class="task-subs">
        <header class="subs-head">
            <span class="subs-label">Subtasks</span>
            ${subsTotal ? `<span class="subs-progress">${subsDone}/${subsTotal} · ${progress}%</span>` : ''}
        </header>
        ${subsTotal ? `
            <div class="subs-bar"><div class="subs-bar-fill" style="width:${progress}%"></div></div>
            <ul class="subs-list">
                ${subs.map(s => `
                    <li class="sub-row ${s.done ? 'sub-done' : ''}">
                        <label>
                            <input type="checkbox" class="sub-toggle" data-sub-id="${s.id}" ${s.done ? 'checked' : ''}/>
                            <span>${sanitize(s.title)}</span>
                        </label>
                    </li>
                `).join('')}
            </ul>` : ''}
        <form class="sub-add" data-task-id="${t.id}">
            <input type="text" name="title" placeholder="Add a subtask…" maxlength="200" required />
            <button type="submit" class="sub-add-btn">Add</button>
        </form>
    </section>

    <footer class="task-actions">
        <button class="task-status-btn" title="Advance status">${({todo:'Start', inprogress:'Done', done:'Reopen'})[t.status]}</button>
    </footer>
</article>`;
    }

    function render() {
        ['todo','inprogress','done'].forEach(status => {
            const col   = els.columns[status];
            const count = els.counts[status];
            if (!col) return;
            const items = filteredTasks().filter(t => t.status === status);
            col.innerHTML = items.length
                ? items.map(taskCardHtml).join('')
                : `<div class="col-empty">No tasks.</div>`;
            if (count) count.textContent = items.length;
        });
        // Summary
        const total  = state.tasks.length;
        const done   = state.tasks.filter(t => t.status === 'done').length;
        const active = total - done;
        els.summary.textContent = total
            ? active + ' active · ' + done + ' done · ' + total + ' total'
            : 'No tasks yet — create one above.';
    }

    // ---- Event wiring
    function wireEvents() {
        // Create form
        els.form.addEventListener('submit', async e => {
            e.preventDefault();
            const payload = {
                title:       els.title.value.trim(),
                description: els.desc.value.trim(),
                due_date:    els.due.value.trim() || null,
                priority:    els.priority.value,
                status:      els.status.value,
            };
            if (!payload.title) { showToast('📝 Title required', '', 'warn'); return; }
            await createTask(payload);
            els.form.reset();
            els.priority.value = 'medium';
            els.status.value   = 'todo';
        });

        els.cancel?.addEventListener('click', () => {
            els.form.reset();
            els.priority.value = 'medium';
            els.status.value   = 'todo';
        });

        // Search & filter
        els.search?.addEventListener('input', () => {
            state.filtering.text = els.search.value;
            render();
        });
        els.filterPri?.addEventListener('change', () => {
            state.filtering.priority = els.filterPri.value;
            render();
        });

        // Per-card delegated handlers
        document.addEventListener('click', e => {
            const card = e.target.closest('.task-card');
            if (!card) return;
            const id = card.dataset.id;

            if (e.target.classList.contains('task-x')) {
                deleteTask(id);
            } else if (e.target.classList.contains('task-status-btn')) {
                const task = state.tasks.find(t => t.id === id);
                if (task) patchTask(id, { status: nextStatus(task.status) });
            }
        });

        document.addEventListener('change', e => {
            if (e.target.classList.contains('sub-toggle')) {
                const card   = e.target.closest('.task-card');
                const taskId = card?.dataset.id;
                const subId  = e.target.dataset.subId;
                if (taskId && subId) toggleSubtask(taskId, subId, e.target.checked);
            }
        });

        // Subtask add
        document.addEventListener('submit', e => {
            const form = e.target.closest('.sub-add');
            if (!form) return;
            e.preventDefault();
            const title = form.querySelector('input[name=title]').value.trim();
            if (!title) return;
            addSubtask(form.dataset.taskId, title);
            form.reset();
        });

        // Logout
        els.logout?.addEventListener('click', () => {
            try {
                for (let i = localStorage.length - 1; i >= 0; i--) {
                    const k = localStorage.key(i);
                    if (k && (k.startsWith('traderalgopro.auth') || k === 'traderalgopro.auth.sessionDeadline')) {
                        localStorage.removeItem(k);
                    }
                }
            } catch (_) {}
            window.location.assign('/login');
        });
    }

    // ---- Boot
    function paintUserChip() {
        try {
            let email = null;
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (!k || !k.startsWith(TOKEN_KEY)) continue;
                const v = JSON.parse(localStorage.getItem(k) || 'null');
                if (v?.user?.email) { email = v.user.email; break; }
                if (v?.currentSession?.user?.email) { email = v.currentSession.user.email; break; }
            }
            if (email && els.userChip) els.userChip.textContent = email;
        } catch (_) {}
    }

    document.addEventListener('DOMContentLoaded', async () => {
        paintUserChip();
        wireEvents();
        await fetchAll();
    });
})();
