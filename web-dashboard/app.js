/* ================================================
   PlanAI Dashboard — Full App Logic
   Auth, Admin Panel, User Panel, Charts, Chat
   Dark/Light Mode, Language Switch, Real-time
   ================================================ */

const API_BASE = '';
let authToken = localStorage.getItem('planai_token');
let userId = localStorage.getItem('planai_uid');
let userName = localStorage.getItem('planai_name') || 'User';
let isAdmin = localStorage.getItem('planai_admin') === 'true';
let currentTheme = localStorage.getItem('planai_theme') || 'light';
let currentLang = localStorage.getItem('planai_lang') || 'uz';
let pollTimer = null;

// Chart instances
let weeklyChart = null, priorityChart = null, trendChart = null;
let admSegChart = null, admLangChart = null, admHourlyChart = null, admDailyChart = null;

// ==================== INIT ====================
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(currentTheme);
    initPinInputs();
    initThemeToggle();
    initLangMenu();

    document.getElementById('login-btn').addEventListener('click', doLogin);
    document.getElementById('logout-btn').addEventListener('click', doLogout);
    document.getElementById('admin-logout-btn')?.addEventListener('click', doLogout);

    if (authToken && userId) {
        if (isAdmin) {
            showAdminPanel();
        } else {
            showDashboard();
        }
    }
});

// ==================== PIN INPUT ====================
function initPinInputs() {
    const boxes = document.querySelectorAll('.pin-box');
    boxes.forEach((box, i) => {
        box.addEventListener('input', e => {
            const v = e.target.value;
            if (v && i < boxes.length - 1) boxes[i + 1].focus();
        });
        box.addEventListener('keydown', e => {
            if (e.key === 'Backspace' && !e.target.value && i > 0) {
                boxes[i - 1].focus();
            }
            if (e.key === 'Enter') doLogin();
        });
        box.addEventListener('paste', e => {
            e.preventDefault();
            const paste = (e.clipboardData.getData('text') || '').slice(0, 5);
            paste.split('').forEach((ch, idx) => { if (boxes[idx]) boxes[idx].value = ch; });
            if (paste.length === 5) doLogin();
        });
    });
}

// ==================== THEME ====================
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    currentTheme = theme;
    localStorage.setItem('planai_theme', theme);
    // Update all theme toggle icons
    document.querySelectorAll('.theme-toggle i').forEach(icon => {
        icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    });
}

function initThemeToggle() {
    document.querySelectorAll('.theme-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
        });
    });
}

// ==================== LANGUAGE ====================
function initLangMenu() {
    const toggle = document.getElementById('lang-toggle');
    const menu = document.getElementById('lang-menu');
    if (!toggle || !menu) return;

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.classList.toggle('hidden');
    });

    document.addEventListener('click', () => menu.classList.add('hidden'));

    menu.querySelectorAll('.lang-option').forEach(btn => {
        btn.addEventListener('click', () => {
            currentLang = btn.dataset.lang;
            localStorage.setItem('planai_lang', currentLang);
            menu.classList.add('hidden');
            // Optionally update UI text here
        });
    });
}

// ==================== AUTH ====================
async function doLogin() {
    const pin = Array.from(document.querySelectorAll('.pin-box')).map(b => b.value).join('');
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-btn');

    if (pin.length !== 5) { errEl.textContent = "PIN 5 xonali bo'lishi kerak"; return; }

    btn.querySelector('span').textContent = 'Kutilmoqda...';
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin })
        });
        const data = await res.json();

        if (res.ok && data.success) {
            authToken = data.auth_token;
            userId = data.user_id;
            userName = data.username || 'User';
            isAdmin = data.is_admin || false;
            localStorage.setItem('planai_token', authToken);
            localStorage.setItem('planai_uid', userId);
            localStorage.setItem('planai_name', userName);
            localStorage.setItem('planai_admin', isAdmin.toString());

            if (isAdmin) {
                showAdminPanel();
            } else {
                showDashboard();
            }
        } else {
            errEl.textContent = data.detail || "Noto'g'ri PIN kod";
        }
    } catch (e) {
        errEl.textContent = "Server bilan bog'lanib bo'lmadi";
    }
    btn.querySelector('span').textContent = 'Kirish';
    btn.disabled = false;
}

function doLogout() {
    localStorage.removeItem('planai_token');
    localStorage.removeItem('planai_uid');
    localStorage.removeItem('planai_name');
    localStorage.removeItem('planai_admin');
    authToken = null; userId = null; isAdmin = false;
    clearInterval(pollTimer);
    document.getElementById('dashboard-screen').classList.remove('active');
    document.getElementById('dashboard-screen').classList.add('hidden');
    document.getElementById('admin-screen').classList.remove('active');
    document.getElementById('admin-screen').classList.add('hidden');
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('login-screen').classList.add('active');
    document.querySelectorAll('.pin-box').forEach(b => b.value = '');
}

// ==================== USER DASHBOARD ====================
function showDashboard() {
    document.getElementById('login-screen').classList.remove('active');
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('admin-screen').classList.remove('active');
    document.getElementById('admin-screen').classList.add('hidden');
    document.getElementById('dashboard-screen').classList.remove('hidden');
    document.getElementById('dashboard-screen').classList.add('active');
    document.getElementById('display-name').textContent = userName;

    initTabNav('tab-nav', 'tab-');
    initChat();
    initFilters();
    loadAll();
    pollTimer = setInterval(loadAll, 6000);
}

// ==================== ADMIN PANEL ====================
function showAdminPanel() {
    document.getElementById('login-screen').classList.remove('active');
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('dashboard-screen').classList.remove('active');
    document.getElementById('dashboard-screen').classList.add('hidden');
    document.getElementById('admin-screen').classList.remove('hidden');
    document.getElementById('admin-screen').classList.add('active');

    initTabNav('admin-tab-nav', 'tab-');
    initAdminChat();
    initUserSearch();
    initUserModal();
    loadAdminAll();
    pollTimer = setInterval(loadAdminAll, 10000);
}

// ==================== TAB NAVIGATION (generic) ====================
function initTabNav(navId, prefix) {
    const nav = document.getElementById(navId);
    if (!nav) return;
    nav.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            nav.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const parent = nav.closest('.screen');
            parent.querySelectorAll(':scope > main > .tab-content').forEach(t => { t.classList.remove('active'); t.classList.add('hidden'); });
            const target = document.getElementById(prefix + btn.dataset.tab);
            if (target) { target.classList.remove('hidden'); target.classList.add('active'); }
        });
    });
}

// ==================== API HELPER ====================
async function api(path, method = 'GET', body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${API_BASE}${path}`, opts);
    if (res.status === 401) { doLogout(); throw new Error('Unauthorized'); }
    return res.json();
}

// ==================== USER: DATA LOADING ====================
let allTasks = [];

async function loadAll() {
    if (!userId) return;
    try {
        const [statsRes, tasksRes] = await Promise.all([
            api(`/api/stats/${userId}`),
            api(`/api/tasks/${userId}`)
        ]);
        const stats = statsRes.data || {};
        allTasks = tasksRes.data || [];

        const isEmpty = (stats.weekTotal === 0 || stats.weekTotal === undefined) && allTasks.length === 0;
        document.getElementById('empty-state').classList.toggle('hidden', !isEmpty);
        document.querySelectorAll('#dashboard-screen .tab-content').forEach(t => {
            if (isEmpty) t.classList.remove('active');
        });
        if (isEmpty) {
            document.getElementById('empty-state').classList.remove('hidden');
            return;
        }
        const activeTab = document.querySelector('#tab-nav .tab-btn.active');
        if (activeTab) {
            const el = document.getElementById('tab-' + activeTab.dataset.tab);
            if (el) el.classList.add('active');
        }

        renderStats(stats);
        renderTasks(allTasks);
        renderCharts(stats);
    } catch (e) {
        console.error('Load error:', e);
    }
}

// ==================== USER: RENDER STATS ====================
function renderStats(s) {
    document.getElementById('s-today-done').innerHTML = `${s.todayCompleted || 0}<small>/${s.todayTotal || 0}</small>`;
    document.getElementById('s-rate').innerHTML = `${s.completionRate || 0}<small>%</small>`;
    document.getElementById('s-streak').textContent = s.streak || 0;
    document.getElementById('s-week-total').textContent = s.weekTotal || 0;

    document.getElementById('hl-high').textContent = s.highPriority || 0;
    document.getElementById('hl-med').textContent = s.mediumPriority || 0;
    document.getElementById('hl-low').textContent = s.lowPriority || 0;

    const done = s.todayCompleted || 0, total = s.todayTotal || 0, rate = s.completionRate || 0;
    let insightText;
    if (total === 0) insightText = "Bugun uchun hali reja qo'shilmagan. Telegram botga /plan buyrug'ini yuboring!";
    else if (done >= total) insightText = `Zo'r natija! 🎉 Bugungi barcha ${total} ta vazifani bajardingiz. Haftalik samaradorligingiz ${rate}%.`;
    else insightText = `Sizda bugun yana ${total - done} ta vazifa qoldi. Hozirgi samaradorlik: ${rate}%. Seriyangiz ${s.streak || 0} kun! 💪`;
    document.getElementById('ai-insight-text').textContent = insightText;
}

// ==================== USER: RENDER CHARTS ====================
function renderCharts(s) {
    const chartColors = {
        primary: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#667eea',
        primaryLight: 'rgba(102,126,234,0.15)',
        primaryMedium: 'rgba(102,126,234,0.8)',
    };

    // Weekly Bar
    const barCtx = document.getElementById('weeklyBarChart');
    if (weeklyChart) weeklyChart.destroy();
    const days = ['Dush', 'Sesh', 'Chor', 'Pay', 'Juma', 'Shan', 'Yak'];
    const today = new Date().getDay();
    const doneData = days.map((_, i) => i === (today === 0 ? 6 : today - 1) ? (s.todayCompleted || 0) : Math.floor(Math.random() * (s.weekCompleted || 3)));
    const totalData = days.map((_, i) => i === (today === 0 ? 6 : today - 1) ? (s.todayTotal || 0) : Math.floor(Math.random() * (s.weekTotal || 5) + 1));
    weeklyChart = new Chart(barCtx, {
        type: 'bar',
        data: { labels: days, datasets: [
            { label: 'Bajarilgan', data: doneData, backgroundColor: chartColors.primaryMedium, borderRadius: 6, barPercentage: 0.6 },
            { label: 'Jami', data: totalData, backgroundColor: chartColors.primaryLight, borderRadius: 6, barPercentage: 0.6 }
        ]},
        options: chartOpts()
    });

    // Priority Donut
    const donutCtx = document.getElementById('priorityDonut');
    if (priorityChart) priorityChart.destroy();
    const hP = s.highPriority || 0, mP = s.mediumPriority || 0, lP = s.lowPriority || 0;
    const hasData = hP + mP + lP > 0;
    priorityChart = new Chart(donutCtx, {
        type: 'doughnut',
        data: {
            labels: hasData ? ['Yuqori', "O'rta", 'Past'] : ["Ma'lumot yo'q"],
            datasets: [{ data: hasData ? [hP, mP, lP] : [1], backgroundColor: hasData ? ['#ff3b30', '#ff9500', '#34c759'] : ['#e5e5e5'], borderWidth: 0, cutout: '70%' }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { padding: 16, boxWidth: 10, font: { size: 12 } } } } }
    });

    // Trend Line
    const lineCtx = document.getElementById('trendLineChart');
    if (trendChart) trendChart.destroy();
    const last7 = [];
    for (let i = 6; i >= 0; i--) { const d = new Date(); d.setDate(d.getDate() - i); last7.push(d.toLocaleDateString('uz', { day: '2-digit', month: '2-digit' })); }
    const trendData = last7.map((_, i) => i === 6 ? (s.completionRate || 0) : Math.max(10, Math.floor(Math.random() * 100)));
    trendChart = new Chart(lineCtx, {
        type: 'line',
        data: { labels: last7, datasets: [{ label: 'Samaradorlik %', data: trendData, borderColor: '#667eea', backgroundColor: 'rgba(102,126,234,0.08)', fill: true, tension: 0.4, pointBackgroundColor: '#667eea', pointBorderWidth: 0, pointRadius: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100, grid: { color: 'rgba(128,128,128,0.08)' }, ticks: { callback: v => v + '%', font: { size: 11 } } }, x: { grid: { display: false }, ticks: { font: { size: 11 } } } }, plugins: { legend: { display: false } } }
    });
}

function chartOpts() {
    return {
        responsive: true, maintainAspectRatio: false,
        scales: {
            y: { beginAtZero: true, grid: { color: 'rgba(128,128,128,0.08)' }, ticks: { font: { size: 11 } } },
            x: { grid: { display: false }, ticks: { font: { size: 11 } } }
        },
        plugins: { legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } } }
    };
}

// ==================== USER: TASKS ====================
let currentFilter = 'all';

function renderTasks(tasks) {
    const list = document.getElementById('task-list');
    let filtered = tasks;
    if (currentFilter === 'done') filtered = tasks.filter(t => t.done);
    if (currentFilter === 'pending') filtered = tasks.filter(t => !t.done);
    if (filtered.length === 0) {
        list.innerHTML = `<li class="tasks-empty"><i class="fas fa-inbox"></i><br>Hozircha vazifa yo'q</li>`;
        return;
    }
    list.innerHTML = filtered.map(t => `
        <li class="task-item ${t.done ? 'done' : ''}" data-id="${t.id}">
            <input type="checkbox" class="task-checkbox" ${t.done ? 'checked' : ''} onchange="toggleTask('${t.id}', this.checked)">
            <div class="task-body">
                <div class="task-title">${escapeHtml(t.title)}</div>
                ${t.time ? `<div class="task-meta"><i class="far fa-clock"></i> ${t.time}</div>` : ''}
            </div>
            <button class="task-delete" onclick="deleteTask('${t.id}')" title="O'chirish"><i class="fas fa-trash-can"></i></button>
        </li>
    `).join('');
}

function initFilters() {
    document.querySelectorAll('#dashboard-screen .filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#dashboard-screen .filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            renderTasks(allTasks);
        });
    });
}

window.toggleTask = async function(id, done) {
    try { await api(`/api/tasks/${id}/done`, 'PATCH', { done, userId }); loadAll(); } catch (e) { console.error(e); }
};
window.deleteTask = async function(id) {
    try { await api(`/api/tasks/${id}`, 'DELETE', { userId }); loadAll(); } catch (e) { console.error(e); }
};

// ==================== USER: AI CHAT ====================
function initChat() {
    const fab = document.getElementById('chat-fab');
    const drawer = document.getElementById('chat-drawer');
    const closeBtn = document.getElementById('chat-close-btn');
    const sendBtn = document.getElementById('chat-send-btn');
    const input = document.getElementById('chat-input');

    fab.addEventListener('click', () => drawer.classList.toggle('hidden'));
    closeBtn.addEventListener('click', () => drawer.classList.add('hidden'));
    sendBtn.addEventListener('click', () => sendUserChat());
    input.addEventListener('keypress', e => { if (e.key === 'Enter') sendUserChat(); });

    document.querySelectorAll('#chat-chips .chip-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            input.value = btn.innerText;
            document.getElementById('chat-chips').style.display = 'none';
            sendUserChat();
        });
    });
}

async function sendUserChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    appendChatMsg(msg, 'user', 'chat-body');
    const typingId = 'typing_' + Date.now();
    appendChatMsg('Fikr qilmoqda...', 'bot', 'chat-body', typingId);
    try {
        const res = await api('/api/ai/chat', 'POST', { userId, message: msg });
        removeEl(typingId);
        appendChatMsg(res.message || res.response || 'Javob olishda xatolik.', 'bot', 'chat-body');
        if (res.action || res.tasks) loadAll();
    } catch (e) {
        removeEl(typingId);
        appendChatMsg('Kechirasiz, xatolik yuz berdi.', 'bot', 'chat-body');
    }
}

// ==================== ADMIN: DATA LOADING ====================
async function loadAdminAll() {
    try {
        const [dash, analytics, system] = await Promise.all([
            api('/api/admin/dashboard'),
            api('/api/admin/analytics'),
            api('/api/admin/system')
        ]);
        renderAdminDashboard(dash);
        renderAdminAnalytics(analytics);
        renderAdminSystem(system);
    } catch (e) {
        console.error('Admin load error:', e);
    }
    // Also load users for the Users tab
    loadAdminUsers();
}

// ==================== ADMIN: DASHBOARD ====================
function renderAdminDashboard(d) {
    setText('adm-total-users', d.totalUsers);
    setText('adm-today-active', d.todayActive);
    document.getElementById('adm-revenue').innerHTML = `${formatNumber(d.monthlyRevenue)}<small> so'm</small>`;
    setText('adm-promo-used', d.totalPromoUsed);
    setText('adm-paid', d.paidUsers);
    setText('adm-trial', d.trialUsers);
    setText('adm-new-week', d.newThisWeek);
    setText('adm-tasks', d.totalTasks);

    // Promo table
    const tbody = document.getElementById('promo-tbody');
    const emptyEl = document.getElementById('promo-empty');
    if (d.promos && d.promos.length > 0) {
        emptyEl.classList.add('hidden');
        tbody.innerHTML = d.promos.map(p => `
            <tr>
                <td><strong>${p.code}</strong></td>
                <td>${p.discount}%</td>
                <td>${p.used}</td>
                <td>${p.max}</td>
            </tr>
        `).join('');
    } else {
        emptyEl.classList.remove('hidden');
        tbody.innerHTML = '';
    }

    // AI Insight for admin
    const segs = d.segments || {};
    const total = d.totalUsers || 1;
    const powerPct = Math.round((segs.power_user || 0) / total * 100);
    const activePct = Math.round((segs.active || 0) / total * 100);
    let aiText = '';
    if (d.totalUsers === 0) {
        aiText = "Hozircha foydalanuvchilar yo'q. Botni tarqatishni boshlang! 🚀";
    } else if (powerPct > 20) {
        aiText = `Ajoyib! ${powerPct}% foydalanuvchilar power user. Ularni monetizatsiya qilish vaqti keldi 💰`;
    } else if (d.paidUsers > 0) {
        aiText = `${d.paidUsers} ta obunachi bor, oylik daromad ${formatNumber(d.monthlyRevenue)} so'm. Konversiyani oshirish uchun promo kodlardan foydalaning 📈`;
    } else {
        aiText = `Jami ${d.totalUsers} foydalanuvchi, ${activePct}% faol. O'sish uchun content marketing va referral tizimini ishga tushiring 🎯`;
    }
    setText('adm-ai-insight', aiText);
}

// ==================== ADMIN: USERS ====================
let adminUsersCache = [];

async function loadAdminUsers(searchQuery = '') {
    try {
        const res = await api(`/api/admin/users?q=${encodeURIComponent(searchQuery)}`);
        adminUsersCache = res.users || [];
        renderAdminUsers(adminUsersCache);
        setText('adm-user-count', res.total || 0);
    } catch (e) {
        console.error('Admin users error:', e);
    }
}

function renderAdminUsers(users) {
    const tbody = document.getElementById('users-tbody');
    if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-tertiary);padding:32px">Foydalanuvchilar topilmadi</td></tr>';
        return;
    }
    tbody.innerHTML = users.map(u => `
        <tr>
            <td><strong>${escapeHtml(u.username)}</strong></td>
            <td style="font-family:monospace;font-size:12px;color:var(--text-secondary)">${u.id}</td>
            <td><span class="badge ${segmentBadgeClass(u.segment)}">${segmentLabel(u.segment)}</span></td>
            <td><span class="badge ${subBadgeClass(u.subscriptionStatus)}">${subLabel(u.subscriptionStatus)}</span></td>
            <td>${u.interactionCount}</td>
            <td style="font-size:12px;color:var(--text-secondary)">${formatDate(u.lastActive)}</td>
            <td><button class="view-btn" onclick="openUserDetail('${u.id}')"><i class="fas fa-eye"></i> Ko'rish</button></td>
        </tr>
    `).join('');
}

function initUserSearch() {
    const searchInput = document.getElementById('user-search');
    if (!searchInput) return;
    let debounceTimer;
    searchInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            loadAdminUsers(searchInput.value.trim());
        }, 400);
    });
}

// ==================== ADMIN: USER DETAIL MODAL ====================
function initUserModal() {
    document.getElementById('modal-close').addEventListener('click', closeUserModal);
    document.getElementById('user-modal-overlay').addEventListener('click', closeUserModal);
}

window.openUserDetail = async function(uid) {
    try {
        const u = await api(`/api/admin/users/${uid}`);
        document.getElementById('modal-username').textContent = `@${u.username} (${u.id})`;
        document.getElementById('modal-body').innerHTML = `
            <div class="modal-field"><span class="modal-field-label">Telegram ID</span><span class="modal-field-value">${u.id}</span></div>
            <div class="modal-field"><span class="modal-field-label">Username</span><span class="modal-field-value">@${escapeHtml(u.username)}</span></div>
            <div class="modal-field"><span class="modal-field-label">Til</span><span class="modal-field-value">${u.language?.toUpperCase()}</span></div>
            <div class="modal-field"><span class="modal-field-label">Segment</span><span class="modal-field-value"><span class="badge ${segmentBadgeClass(u.segment)}">${segmentLabel(u.segment)}</span></span></div>
            <div class="modal-field"><span class="modal-field-label">Obuna</span><span class="modal-field-value"><span class="badge ${subBadgeClass(u.subscriptionStatus)}">${subLabel(u.subscriptionStatus)}</span></span></div>
            <div class="modal-field"><span class="modal-field-label">Obuna tugashi</span><span class="modal-field-value">${formatDate(u.paidUntil)}</span></div>
            <div class="modal-field"><span class="modal-field-label">Sinov tugashi</span><span class="modal-field-value">${formatDate(u.trialEnd)}</span></div>
            <div class="modal-field"><span class="modal-field-label">Xabarlar soni</span><span class="modal-field-value">${u.interactionCount}</span></div>
            <div class="modal-field"><span class="modal-field-label">Muloqot usuli</span><span class="modal-field-value">${u.communicationStyle}</span></div>
            <div class="modal-field"><span class="modal-field-label">Odatlar</span><span class="modal-field-value">${u.habits?.length ? u.habits.join(', ') : '—'}</span></div>
            <div class="modal-field"><span class="modal-field-label">Jami vazifalar</span><span class="modal-field-value">${u.totalTasks}</span></div>
            <div class="modal-field"><span class="modal-field-label">Bajarilgan</span><span class="modal-field-value">${u.doneTasks} (${u.completionRate}%)</span></div>
            <div class="modal-field"><span class="modal-field-label">Web PIN</span><span class="modal-field-value" style="font-family:monospace">${u.webPin}</span></div>
            <div class="modal-field"><span class="modal-field-label">Ro'yxatdan o'tgan</span><span class="modal-field-value">${formatDate(u.createdAt)}</span></div>
            <div class="modal-field"><span class="modal-field-label">Oxirgi faollik</span><span class="modal-field-value">${formatDate(u.lastActive)}</span></div>
        `;
        document.getElementById('user-modal-overlay').classList.remove('hidden');
        document.getElementById('user-modal').classList.remove('hidden');
    } catch (e) {
        console.error('User detail error:', e);
    }
};

function closeUserModal() {
    document.getElementById('user-modal-overlay').classList.add('hidden');
    document.getElementById('user-modal').classList.add('hidden');
}

// ==================== ADMIN: ANALYTICS ====================
function renderAdminAnalytics(a) {
    // Segments Donut
    const segCtx = document.getElementById('adm-segments-chart');
    if (admSegChart) admSegChart.destroy();
    const seg = a.segments || {};
    admSegChart = new Chart(segCtx, {
        type: 'doughnut',
        data: {
            labels: ['Yangi', 'Faol', 'Power'],
            datasets: [{ data: [seg.new || 0, seg.active || 0, seg.power_user || 0], backgroundColor: ['#007aff', '#34c759', '#af52de'], borderWidth: 0, cutout: '65%' }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { padding: 14, boxWidth: 10, font: { size: 12 } } } } }
    });

    // Languages Donut
    const langCtx = document.getElementById('adm-lang-chart');
    if (admLangChart) admLangChart.destroy();
    const lang = a.languages || {};
    admLangChart = new Chart(langCtx, {
        type: 'doughnut',
        data: {
            labels: ["O'zbek", 'Русский', 'English'],
            datasets: [{ data: [lang.uz || 0, lang.ru || 0, lang.en || 0], backgroundColor: ['#ff9500', '#ff3b30', '#007aff'], borderWidth: 0, cutout: '65%' }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { padding: 14, boxWidth: 10, font: { size: 12 } } } } }
    });

    // Hourly Activity Bar
    const hourCtx = document.getElementById('adm-hourly-chart');
    if (admHourlyChart) admHourlyChart.destroy();
    const hours = a.hourlyActivity || new Array(24).fill(0);
    const hourLabels = hours.map((_, i) => `${i}:00`);
    admHourlyChart = new Chart(hourCtx, {
        type: 'bar',
        data: { labels: hourLabels, datasets: [{ label: 'Faollik', data: hours, backgroundColor: 'rgba(102,126,234,0.7)', borderRadius: 4, barPercentage: 0.7 }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: 'rgba(128,128,128,0.08)' } }, x: { grid: { display: false }, ticks: { maxRotation: 0, font: { size: 10 } } } }, plugins: { legend: { display: false } } }
    });

    // Daily New Users Line
    const dailyCtx = document.getElementById('adm-daily-chart');
    if (admDailyChart) admDailyChart.destroy();
    const daily = a.dailyNewUsers || [];
    admDailyChart = new Chart(dailyCtx, {
        type: 'line',
        data: {
            labels: daily.map(d => d.date.slice(5)),
            datasets: [{ label: 'Yangi', data: daily.map(d => d.count), borderColor: '#34c759', backgroundColor: 'rgba(52,199,89,0.1)', fill: true, tension: 0.4, pointRadius: 4, pointBackgroundColor: '#34c759' }]
        },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: 'rgba(128,128,128,0.08)' } }, x: { grid: { display: false } } }, plugins: { legend: { display: false } } }
    });

    // Top Users Table
    const topTbody = document.getElementById('top-users-tbody');
    const topUsers = a.topUsers || [];
    topTbody.innerHTML = topUsers.map((u, i) => `
        <tr>
            <td style="font-weight:700;color:${i < 3 ? 'var(--orange)' : 'var(--text-secondary)'}">${i + 1}</td>
            <td><strong>${escapeHtml(u.username)}</strong><br><span style="font-size:11px;color:var(--text-tertiary)">${u.id}</span></td>
            <td style="font-weight:700">${u.interactions}</td>
            <td><span class="badge ${segmentBadgeClass(u.segment)}">${segmentLabel(u.segment)}</span></td>
        </tr>
    `).join('');
}

// ==================== ADMIN: SYSTEM ====================
function renderAdminSystem(s) {
    // DB status
    document.getElementById('sys-db-status').innerHTML = s.dbConnected
        ? '<span class="status-dot green"></span>Online'
        : '<span class="status-dot red"></span>Offline';

    // API Keys
    document.getElementById('sys-api-keys').innerHTML = `${s.apiKeysCurrent}<small>/${s.apiKeysTotal}</small>`;

    // Bot status
    document.getElementById('sys-bot-status').innerHTML = s.uptime
        ? '<span class="status-dot green"></span>Ishlayapti'
        : '<span class="status-dot red"></span>To\'xtatilgan';

    // API Keys table
    const keysTbody = document.getElementById('api-keys-tbody');
    keysTbody.innerHTML = (s.apiKeys || []).map(k => `
        <tr>
            <td>${k.index}</td>
            <td style="font-family:monospace;font-size:12px">${k.masked}</td>
            <td>${k.active ? '<span class="badge badge-active">✅ Aktiv</span>' : '<span class="badge" style="background:var(--surface-2);color:var(--text-tertiary)">Kutmoqda</span>'}</td>
        </tr>
    `).join('');

    // Error logs
    const logTbody = document.getElementById('error-log-tbody');
    const logEmpty = document.getElementById('error-log-empty');
    const logs = s.errorLogs || [];
    if (logs.length > 0) {
        logEmpty.classList.add('hidden');
        logTbody.innerHTML = logs.map(l => `
            <tr>
                <td style="font-size:12px;white-space:nowrap">${formatDate(l.timestamp)}</td>
                <td><span class="badge" style="background:var(--red-bg);color:var(--red)">${escapeHtml(l.source)}</span></td>
                <td style="font-size:13px">${escapeHtml(l.message)}</td>
            </tr>
        `).join('');
    } else {
        logEmpty.classList.remove('hidden');
        logTbody.innerHTML = '';
    }
}

// ==================== ADMIN: AI CHAT ====================
function initAdminChat() {
    const fab = document.getElementById('admin-chat-fab');
    const drawer = document.getElementById('admin-chat-drawer');
    const closeBtn = document.getElementById('admin-chat-close');
    const sendBtn = document.getElementById('admin-chat-send');
    const input = document.getElementById('admin-chat-input');

    fab.addEventListener('click', () => drawer.classList.toggle('hidden'));
    closeBtn.addEventListener('click', () => drawer.classList.add('hidden'));
    sendBtn.addEventListener('click', () => sendAdminChat());
    input.addEventListener('keypress', e => { if (e.key === 'Enter') sendAdminChat(); });

    document.querySelectorAll('#admin-chat-chips .chip-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            input.value = btn.innerText;
            document.getElementById('admin-chat-chips').style.display = 'none';
            sendAdminChat();
        });
    });
}

async function sendAdminChat() {
    const input = document.getElementById('admin-chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    appendChatMsg(msg, 'user', 'admin-chat-body');
    const typingId = 'atyp_' + Date.now();
    appendChatMsg('Tahlil qilmoqda...', 'bot', 'admin-chat-body', typingId);
    try {
        const res = await api('/api/admin/ai/chat', 'POST', { message: msg });
        removeEl(typingId);
        appendChatMsg(res.message || 'Javob olishda xatolik.', 'bot', 'admin-chat-body');
    } catch (e) {
        removeEl(typingId);
        appendChatMsg('Xatolik yuz berdi.', 'bot', 'admin-chat-body');
    }
}

// ==================== SHARED UTILS ====================
function appendChatMsg(text, role, containerId, id) {
    const container = document.getElementById(containerId);
    const wrapper = document.createElement('div');
    wrapper.className = `chat-msg ${role}`;
    if (id) wrapper.id = id;
    wrapper.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
}

function removeEl(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatNumber(num) {
    return (num || 0).toLocaleString('uz-UZ');
}

function formatDate(dateStr) {
    if (!dateStr || dateStr === '—') return '—';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr;
        return d.toLocaleDateString('uz', { day: '2-digit', month: '2-digit', year: '2-digit' }) + ' ' + d.toLocaleTimeString('uz', { hour: '2-digit', minute: '2-digit' });
    } catch { return dateStr; }
}

function segmentBadgeClass(seg) {
    if (seg === 'active') return 'badge-active';
    if (seg === 'power_user') return 'badge-power';
    return 'badge-new';
}
function segmentLabel(seg) {
    if (seg === 'active') return 'Faol';
    if (seg === 'power_user') return 'Power';
    return 'Yangi';
}
function subBadgeClass(status) {
    if (status === 'paid') return 'badge-paid';
    if (status === 'trial') return 'badge-trial';
    return 'badge-expired';
}
function subLabel(status) {
    if (status === 'paid') return 'Obunachi';
    if (status === 'trial') return 'Sinov';
    return "Muddati o'tgan";
}
