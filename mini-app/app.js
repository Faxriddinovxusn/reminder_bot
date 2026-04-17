console.log('app.js loaded');

// Initialize Telegram Web App
let tg = window.Telegram.WebApp;
tg.expand();

// State management
let currentUserId = tg.initDataUnsafe?.user?.id || 'test_user_123';
let currentLanguage = localStorage.getItem('language') || 'en';
let currentTab = 'tasks';
let selectedPriority = 'medium';
let tasks = [];
let notes = [];

const offlineTranslations = {
    uz: { banner: "Offline rejim 📴", action: "Internet yo'q. Bajarilmadi 📶" },
    ru: { banner: "Офлайн режим 📴", action: "Нет интернета. Не выполнено 📶" },
    en: { banner: "Offline mode 📴", action: "No internet. Action failed 📶" }
};


// DOM Elements
const tabBtns = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');
const taskInput = document.getElementById('taskInput');
const addTaskBtn = document.getElementById('addTaskBtn');
const filterBtns = document.querySelectorAll('.filter-btn');
const tasksList = document.getElementById('tasksList');
const noteBtn = document.getElementById('addNoteBtn');
const notesList = document.getElementById('notesList');
const priorityBtns = document.querySelectorAll('.priority-btn');
const taskOptionsModal = document.getElementById('taskOptionsModal');
const noteEditorModal = document.getElementById('noteEditorModal');
const modalOverlay = document.getElementById('modalOverlay');
const settingsBtn = document.querySelector('.btn-icon');
const settingsPanel = document.getElementById('settingsPanel');
const languageButtons = document.querySelectorAll('.lang-btn');
const toast = document.getElementById('toast');

// Tab Switching
tabBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
        const tabName = btn.getAttribute('data-tab');
        switchTab(tabName);
    });
});

function switchTab(tabName) {
    // Hide all tabs
    tabContents.forEach((tab) => tab.classList.remove('active'));

    // Remove active class from all nav buttons
    tabBtns.forEach((btn) => btn.classList.remove('active'));

    // Show selected tab
    document.getElementById(tabName).classList.add('active');

    // Add active class to clicked button
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');

    currentTab = tabName;
}

// Initialize - set Tasks tab as active
switchTab('tasksTab');

// Task Management
addTaskBtn && addTaskBtn.addEventListener('click', addTask);
taskInput && taskInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') addTask();
});

async function addTask() {
    if (!navigator.onLine) {
        showToast(offlineTranslations[currentLanguage].action, 'error');
        return;
    }

    const title = taskInput.value.trim();
    if (!title) {
        showToast('Please enter a task', 'error');
        return;
    }

    try {
        // Send to backend first to get the real MongoDB _id
        const savedTask = await api.addTask(currentUserId, title, selectedPriority, null);
        
        if (savedTask) {
            // Use the real backend ID for the task
            const task = {
                id: savedTask.id || savedTask._id,
                title: savedTask.title || title,
                priority: savedTask.priority || selectedPriority,
                time: savedTask.scheduled_time || savedTask.time || null,
                createdAt: savedTask.created_at || new Date().toISOString(),
                done: false
            };
            tasks.push(task);
        } else {
            // Fallback: add locally with temp ID
            tasks.push({
                id: 'temp_' + Date.now(),
                title,
                priority: selectedPriority,
                time: null,
                createdAt: new Date().toISOString(),
                done: false
            });
        }
    } catch (error) {
        console.error('Failed to save task:', error);
        // Still add locally even if backend fails
        tasks.push({
            id: 'temp_' + Date.now(),
            title,
            priority: selectedPriority,
            time: null,
            createdAt: new Date().toISOString(),
            done: false
        });
    }

    // Reset form
    if (taskInput) taskInput.value = '';
    selectedPriority = 'medium';
    updatePriorityButtons();

    // Refresh UI
    renderTasks();
}

// Priority Selection
priorityBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
        selectedPriority = btn.getAttribute('data-priority');
        updatePriorityButtons();
    });
});

function updatePriorityButtons() {
    priorityBtns.forEach((btn) => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-priority') === selectedPriority) {
            btn.classList.add('active');
        }
    });
}

// Filter Tasks
filterBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
        filterBtns.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const filter = btn.getAttribute('data-filter');
        filterTasks(filter);
    });
});

function filterTasks(filter) {
    let filtered = tasks;

    switch (filter) {
        case 'all':
            filtered = tasks;
            break;
        case 'active':
            filtered = tasks.filter(task => !task.done);
            break;
        case 'done':
            filtered = tasks.filter(task => task.done);
            break;
    }

    renderTasksList(filtered);
}

function renderTasks() {
    renderTasksList(tasks);
}

function renderTasksList(tasksToShow) {
    tasksList.innerHTML = '';

    // Add today's date header
    const now = new Date();
    const dayNames = ['Yakshanba', 'Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba'];
    const dayName = dayNames[now.getDay()];
    const dd = String(now.getDate()).padStart(2, '0');
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const yyyy = now.getFullYear();
    const dateHeader = document.createElement('div');
    dateHeader.className = 'date-header';
    dateHeader.innerHTML = `<span class="date-header-text">${dayName}, ${dd}.${mm}.${yyyy} 👇</span>`;
    tasksList.appendChild(dateHeader);

    if (tasksToShow.length === 0) {
        tasksList.innerHTML += '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No tasks yet 📋</p>';
        return;
    }

    tasksToShow.forEach((task) => {
        const taskEl = createTaskElement(task);
        tasksList.appendChild(taskEl);
    });
}

function createTaskElement(task) {
    const div = document.createElement('div');
    div.className = `task-card ${task.done ? 'done' : ''}`;
    div.id = `task-${task.id}`;

    const priorityColor = {
        high: '#EF4444',
        medium: '#F59E0B',
        low: '#10B981'
    };

    const priorityLabel = {
        high: 'High',
        medium: 'Medium',
        low: 'Low'
    };

    let timeDisplay = '';
    if (task.time) {
        // Handle both HH:MM strings and ISO date strings
        if (task.time.includes('T') || task.time.includes('-')) {
            try {
                const taskDate = new Date(task.time);
                timeDisplay = `🕒 ${taskDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
            } catch (e) {
                timeDisplay = `🕒 ${task.time}`;
            }
        } else {
            timeDisplay = `🕒 ${task.time}`;
        }
    }

    const p = task.priority || 'medium';

    div.innerHTML = `
        <div class="task-checkbox" data-task-id="${task.id}">
            ${task.done ? '✅' : ''}
        </div>
        <div class="task-content">
            <div class="task-header">
                <span class="task-title">${escapeHtml(task.title)}</span>
                <span class="priority-badge" style="background-color: ${(priorityColor[p] || priorityColor.medium)}33; color: ${priorityColor[p] || priorityColor.medium};">${priorityLabel[p] || 'Medium'}</span>
            </div>
            <div class="task-meta">
                <span class="task-time">${timeDisplay}</span>
            </div>
        </div>
        <div class="task-menu" data-task-id="${task.id}">⋮</div>
    `;

    // Checkbox click — toggle done with backend ID
    div.querySelector('.task-checkbox').addEventListener('click', async () => {
        if (!navigator.onLine) {
            showToast(offlineTranslations[currentLanguage].action, 'error');
            return;
        }
        task.done = !task.done;

        div.classList.toggle('done');
        div.querySelector('.task-checkbox').innerHTML = task.done ? '✅' : '';
        try {
            await api.markDone(task.id, task.done);
        } catch (error) {
            console.error('Failed to update task:', error);
            // Revert on failure
            task.done = !task.done;
            div.classList.toggle('done');
            div.querySelector('.task-checkbox').innerHTML = task.done ? '✅' : '';
        }
    });

    // Menu click
    div.querySelector('.task-menu').addEventListener('click', (e) => {
        e.stopPropagation();
        openTaskMenu(task);
    });

    return div;
}

// Task Menu Modal
function openTaskMenu(task) {
    const titleInput = document.getElementById('taskTitleEditInput');
    const timeInput = document.getElementById('taskTimeInput');
    const priorityBtnsModal = document.querySelectorAll('#taskOptionsModal .priority-btn');
    
    // Apply Dynamic Translations
    const dict = {
        uz: { title: "Vazifa nomi", save: "💾 Saqlash", del: "🗑 O'chirish", delMsg: "Vazifa o'chirildi" },
        ru: { title: "Название", save: "💾 Сохранить", del: "🗑 Удалить", delMsg: "Задача удалена" },
        en: { title: "Title", save: "💾 Save Task", del: "🗑 Delete", delMsg: "Task deleted" }
    };
    const langDict = dict[currentLanguage] || dict.en;
    const titleLabel = document.getElementById('taskTitleLabel');
    const saveBtn = document.getElementById('saveTaskBtn');
    const deleteBtn = document.getElementById('deleteTaskBtn');
    
    if (titleLabel) titleLabel.textContent = langDict.title;
    if (saveBtn) saveBtn.textContent = langDict.save;
    if (deleteBtn) deleteBtn.textContent = langDict.del;

    // Set initial values
    if (titleInput) titleInput.value = task.title;
    
    let currentTaskPriority = task.priority || 'medium';
    priorityBtnsModal.forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-priority') === currentTaskPriority) {
            btn.classList.add('active');
        }
        // Add click listener specifically for this modal's priority buttons
        btn.onclick = () => {
            priorityBtnsModal.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentTaskPriority = btn.getAttribute('data-priority');
        };
    });

    if (timeInput) {
        if (task.time && task.time.includes('T')) {
            const date = new Date(task.time);
            timeInput.value = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
        } else if (task.time) {
            timeInput.value = task.time;
        } else {
            timeInput.value = '';
        }
    }

    // Set up save button
    saveBtn.onclick = async () => {
        if (!navigator.onLine) {
            showToast(offlineTranslations[currentLanguage].action, 'error');
            return;
        }
        
        const newTitle = titleInput ? titleInput.value.trim() : task.title;
        if (!newTitle) {
            const err = dict[currentLanguage] ? dict[currentLanguage].title : "Title";
            showToast(`${err} ?`, 'error');
            return;
        }
        
        const newTime = timeInput ? timeInput.value : task.time;
        
        // Update locally
        task.title = newTitle;
        task.priority = currentTaskPriority;
        task.time = newTime;
        
        renderTasks();
        closeModals();
        
        try {
            await api.updateTask(task.id, {
                title: task.title,
                priority: task.priority,
                time: task.time
            });
        } catch (error) {
            console.error('Failed to update task API:', error);
        }
    };

    // Set up delete button
    deleteBtn.onclick = async () => {
        if (!navigator.onLine) {
            showToast(offlineTranslations[currentLanguage].action, 'error');
            return;
        }
        try {
            await api.deleteTask(task.id);
        } catch (error) {
            console.error('Failed to delete task:', error);
        }
        tasks = tasks.filter(t => t.id !== task.id);
        renderTasks();
        closeModals();
        showToast(langDict.delMsg, 'success');
    };

    taskOptionsModal.classList.add('active');
    modalOverlay.classList.add('active');
}

// Note Management
noteBtn && noteBtn.addEventListener('click', openAddNoteModal);

function openAddNoteModal() {
    const titleInput = document.getElementById('noteTitleInput');
    const contentInput = document.getElementById('noteContentInput');
    if (titleInput) titleInput.value = '';
    if (contentInput) contentInput.value = '';

    const saveBtn = document.getElementById('saveNoteBtn');
    saveBtn.onclick = async () => {
        if (!navigator.onLine) {
            showToast(offlineTranslations[currentLanguage].action, 'error');
            return;
        }
        const title = titleInput.value.trim();
        if (!title) {
            showToast('Please enter a note title', 'error');
            return;
        }
        const content = contentInput.value || '';

        const note = {
            id: Date.now(),
            title,
            content,
            createdAt: new Date().toISOString()
        };

        notes.push(note);
        renderNotes();
        closeModals();
        showToast('Note created ✅', 'success');

        try {
            const savedNote = await api.saveNote(currentUserId, note.title, note.content);
            if (savedNote && savedNote.id) {
                // Update with real backend ID
                note.id = savedNote.id;
            }
        } catch (error) {
            console.error('Failed to save note:', error);
        }
    };

    noteEditorModal.classList.add('active');
    modalOverlay.classList.add('active');
}

function renderNotes() {
    notesList.innerHTML = '';

    if (notes.length === 0) {
        notesList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No notes yet</p>';
        return;
    }

    notes.forEach((note) => {
        const noteCard = document.createElement('div');
        noteCard.className = 'note-card';
        noteCard.innerHTML = `
            <div class="note-header" data-note-id="${note.id}">
                <span class="note-toggle">▶</span>
                <span class="note-title">${escapeHtml(note.title)}</span>
                <span style="cursor: pointer; font-size: 16px;" data-edit-note="${note.id}">✎</span>
            </div>
            <div class="note-content" data-note-content="${note.id}" style="display: none;">
                ${note.content ? note.content.split('\n').map(line => escapeHtml(line)).join('<br>') : '<em>No content</em>'}
            </div>
        `;

        // Toggle expand
        const header = noteCard.querySelector('.note-header');
        header.addEventListener('click', () => {
            header.classList.toggle('expanded');
            const content = noteCard.querySelector('.note-content');
            const isExpanded = content.style.display === 'block';
            content.style.display = isExpanded ? 'none' : 'block';
        });

        // Edit button
        noteCard.querySelector('[data-edit-note]').addEventListener('click', (e) => {
            e.stopPropagation();
            openNoteEditor(note);
        });

        notesList.appendChild(noteCard);
    });
}

// Note Editor Modal
function openNoteEditor(note) {
    const titleInput = document.getElementById('noteTitleInput');
    const contentInput = document.getElementById('noteContentInput');
    if (titleInput) titleInput.value = note.title;
    if (contentInput) contentInput.value = note.content;

    const saveBtn = document.getElementById('saveNoteBtn');
    saveBtn.onclick = async () => {
        if (!navigator.onLine) {
            showToast(offlineTranslations[currentLanguage].action, 'error');
            return;
        }
        if (titleInput) note.title = titleInput.value.trim() || note.title;
        note.content = contentInput.value;
        renderNotes();
        closeModals();
        showToast('Note saved ✅', 'success');
        
        try {
            await api.saveNote(currentUserId, note.title, note.content);
        } catch (error) {
            console.error('Failed to save note:', error);
        }
    };

    noteEditorModal.classList.add('active');
    modalOverlay.classList.add('active');
}

// Archive — fetch from API instead of using local data
async function renderArchive() {
    const archiveList = document.getElementById('archiveList');
    archiveList.innerHTML = '<div class="loading-spinner"></div>';

    try {
        const archiveData = await api.getArchive(currentUserId);
        archiveList.innerHTML = '';

        if (!archiveData || archiveData.length === 0) {
            archiveList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No completed tasks</p>';
            return;
        }

        archiveData.forEach((group) => {
            const dayDiv = document.createElement('div');
            dayDiv.className = 'archive-day';

            const tasksCount = group.count || group.tasks.length;
            dayDiv.innerHTML = `
                <div class="archive-day-header">
                    <span class="archive-day-date">${group.date}</span>
                    <span class="archive-day-stats">${tasksCount} task${tasksCount > 1 ? 's' : ''} completed</span>
                </div>
                <div class="archive-tasks"></div>
            `;

            const tasksList = dayDiv.querySelector('.archive-tasks');
            group.tasks.forEach((task) => {
                const taskEl = document.createElement('div');
                taskEl.className = 'archive-task';
                taskEl.textContent = task.title;
                tasksList.appendChild(taskEl);
            });

            // Toggle expand
            const header = dayDiv.querySelector('.archive-day-header');
            header.addEventListener('click', () => {
                header.classList.toggle('expanded');
                tasksList.classList.toggle('expanded');
            });

            archiveList.appendChild(dayDiv);
        });
    } catch (e) {
        console.error('renderArchive error:', e);
        archiveList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">Failed to load archive</p>';
    }
}

// Future Days
async function renderFutureDays() {
    const futureList = document.getElementById('futureList');
    futureList.innerHTML = '<div class="loading-spinner"></div>';

    try {
        const futureData = await api.getFutureTasks(currentUserId);
        futureList.innerHTML = '';

        if (!futureData || futureData.length === 0) {
            futureList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">Kelasi kunlarga reja yo\'q 📅</p>';
            return;
        }

        const dayNamesUz = ['Yakshanba', 'Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba'];

        futureData.forEach((group) => {
            const dayDiv = document.createElement('div');
            dayDiv.className = 'future-day';

            const dateObj = new Date(group.date + 'T00:00:00');
            const dayName = dayNamesUz[dateObj.getDay()];
            const dd = String(dateObj.getDate()).padStart(2, '0');
            const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
            const yyyy = dateObj.getFullYear();
            const formattedDate = `${dayName}, ${dd}.${mm}.${yyyy}`;
            const tasksCount = group.count || group.tasks.length;

            dayDiv.innerHTML = `
                <div class="future-day-header">
                    <span class="future-day-date">📅 ${formattedDate}</span>
                    <span class="future-day-stats">${tasksCount} ta vazifa</span>
                </div>
                <div class="future-tasks" style="display: none;"></div>
            `;

            const tasksContainer = dayDiv.querySelector('.future-tasks');
            group.tasks.forEach((task) => {
                const taskEl = document.createElement('div');
                taskEl.className = `task-card ${task.done ? 'done' : ''}`;
                const priorityColor = { high: '#EF4444', medium: '#F59E0B', low: '#10B981' };
                const p = task.priority || 'medium';
                let timeDisplay = '';
                if (task.time) {
                    if (task.time.includes('T') || task.time.includes('-')) {
                        try {
                            const taskDate = new Date(task.time);
                            timeDisplay = `🕒 ${taskDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
                        } catch (e) { timeDisplay = `🕒 ${task.time}`; }
                    } else {
                        timeDisplay = `🕒 ${task.time}`;
                    }
                }
                taskEl.innerHTML = `
                    <div class="task-checkbox" data-task-id="${task.id}">${task.done ? '✅' : ''}</div>
                    <div class="task-content">
                        <div class="task-header">
                            <span class="task-title">${escapeHtml(task.title)}</span>
                            <span class="priority-badge" style="background-color: ${(priorityColor[p] || priorityColor.medium)}33; color: ${priorityColor[p] || priorityColor.medium};">${p}</span>
                        </div>
                        <div class="task-meta"><span class="task-time">${timeDisplay}</span></div>
                    </div>
                    <div class="task-menu" data-task-id="${task.id}">⋮</div>
                `;

                // Toggle done
                taskEl.querySelector('.task-checkbox').addEventListener('click', async () => {
                    if (!navigator.onLine) { showToast(offlineTranslations[currentLanguage].action, 'error'); return; }
                    task.done = !task.done;
                    taskEl.classList.toggle('done');
                    taskEl.querySelector('.task-checkbox').innerHTML = task.done ? '✅' : '';
                    try { await api.markDone(task.id, task.done); } catch (e) {
                        task.done = !task.done;
                        taskEl.classList.toggle('done');
                        taskEl.querySelector('.task-checkbox').innerHTML = task.done ? '✅' : '';
                    }
                });

                // Edit menu
                taskEl.querySelector('.task-menu').addEventListener('click', (e) => {
                    e.stopPropagation();
                    openTaskMenu(task);
                });

                tasksContainer.appendChild(taskEl);
            });

            // Toggle expand
            const header = dayDiv.querySelector('.future-day-header');
            header.addEventListener('click', () => {
                header.classList.toggle('expanded');
                const container = dayDiv.querySelector('.future-tasks');
                container.style.display = container.style.display === 'none' ? 'block' : 'none';
            });

            futureList.appendChild(dayDiv);
        });
    } catch (e) {
        console.error('renderFutureDays error:', e);
        futureList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">Failed to load future plans</p>';
    }
}

// Stats
async function renderStats() {
    try {
        // Fetch real stats from API
        const stats = await api.getStats(currentUserId);

        // Update week stats
        const weekStatsEl = document.getElementById('weekStats');
        const weekBarEl = document.getElementById('weekBar');
        if (weekStatsEl) {
            weekStatsEl.textContent = `${stats.weekCompleted || 0}/${stats.weekTotal || 0}`;
        }
        if (weekBarEl) {
            const weekRate = stats.weekTotal > 0 ? Math.round((stats.weekCompleted / stats.weekTotal) * 100) : 0;
            weekBarEl.style.width = weekRate + '%';
        }

        // Update streak
        const streakEl = document.getElementById('streakStats');
        if (streakEl) {
            streakEl.textContent = `${stats.streak || 0} days`;
        }

        // Update best day (completion rate)
        const bestDayEl = document.getElementById('bestDayStats');
        if (bestDayEl) {
            bestDayEl.textContent = `${stats.completionRate || 0}%`;
        }

        // Update chart with priority distribution
        const chartEl = document.getElementById('chart');
        if (chartEl) {
            const high = stats.highPriority || 0;
            const medium = stats.mediumPriority || 0;
            const low = stats.lowPriority || 0;
            const maxVal = Math.max(high, medium, low, 1);
            chartEl.innerHTML = `
                <div style="display:flex;align-items:flex-end;gap:12px;height:80px;padding:8px 0;">
                    <div style="flex:1;text-align:center;">
                        <div style="background:#ef4444;border-radius:4px;height:${Math.round((high / maxVal) * 60)}px;min-height:4px;"></div>
                        <small style="color:var(--text-secondary);font-size:11px;">High (${high})</small>
                    </div>
                    <div style="flex:1;text-align:center;">
                        <div style="background:#f59e0b;border-radius:4px;height:${Math.round((medium / maxVal) * 60)}px;min-height:4px;"></div>
                        <small style="color:var(--text-secondary);font-size:11px;">Med (${medium})</small>
                    </div>
                    <div style="flex:1;text-align:center;">
                        <div style="background:#22c55e;border-radius:4px;height:${Math.round((low / maxVal) * 60)}px;min-height:4px;"></div>
                        <small style="color:var(--text-secondary);font-size:11px;">Low (${low})</small>
                    </div>
                </div>
            `;
        }
    } catch (e) {
        console.error('renderStats error', e);
    }
}

// Modal Controls
if (modalOverlay) {
    modalOverlay.addEventListener('click', closeModals);
}

document.querySelectorAll('.modal-close').forEach((btn) => {
    btn.addEventListener('click', closeModals);
});

function closeModals() {
    taskOptionsModal.classList.remove('active');
    noteEditorModal.classList.remove('active');
    modalOverlay.classList.remove('active');
}

// Toast Notification
function showToast(message, type = 'success') {
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Settings button
settingsBtn.addEventListener('click', () => {
    if (!settingsPanel) return;
    const isHidden = settingsPanel.style.display === 'none' || !settingsPanel.style.display;
    settingsPanel.style.display = isHidden ? 'block' : 'none';
});

languageButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
        const lang = btn.getAttribute('data-lang');
        currentLanguage = lang;
        localStorage.setItem('language', lang);
        if (typeof setAiLanguage === 'function') {
            setAiLanguage(lang);
        }
        showToast(`Language set to ${btn.textContent}`, 'success');
    });
});

async function loadTasks() {
    const path = `/tasks/${currentUserId}`;
    console.log('loadTasks start', { userId: currentUserId, path });
    try {
        const fetchedTasks = await api.getTasks(currentUserId);
        console.log('loadTasks success', { count: fetchedTasks?.length ?? 0 });
        // Normalize task data from backend
        return (fetchedTasks || []).map(t => ({
            id: t.id || t._id || String(t._id),
            title: t.title || '',
            priority: (t.priority === 'normal' ? 'medium' : t.priority) || 'medium',
            time: t.scheduled_time || t.time || null,
            createdAt: t.createdAt || t.created_at || new Date().toISOString(),
            done: t.done || t.is_done || t.status === 'done' || false
        }));
    } catch (error) {
        console.error('loadTasks error', error);
        showToast('Failed to load tasks', 'error');
        return [];
    }
}

// Load initial data
window.loadTasks = loadTasks;
window.renderTasks = renderTasks;

async function initializeApp() {
    try {
        tasks = await loadTasks() || [];
        renderTasks();
        renderStats();
    } catch (error) {
        console.error('Failed to load tasks:', error);
        showToast('Failed to load tasks', 'error');
    }

    try {
        const fetchedNotes = await api.getNotes(currentUserId);
        notes = (fetchedNotes || []).map(n => ({
            id: n.id || n._id || String(n._id),
            title: n.title || '',
            content: n.content || '',
            createdAt: n.created_at || new Date().toISOString()
        }));
        renderNotes();
    } catch (error) {
        console.error('Failed to load notes:', error);
    }
}

// Utility function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
} else {
    initializeApp();
}

// Handle tab switching to update stats/archive
tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        if (tab === 'futureTab') {
            setTimeout(() => renderFutureDays(), 0);
        } else if (tab === 'archiveTab') {
            setTimeout(() => renderArchive(), 0);
        } else if (tab === 'statsTab') {
            setTimeout(() => renderStats(), 0);
        }
    });
});

window.loadAndRenderTasks = async function () {
    try {
        const fetchedTasks = await loadTasks();
        tasks = fetchedTasks || [];
        renderTasks();
    } catch (e) {
        console.error("loadAndRenderTasks error:", e);
    }
}

window.loadAndRenderNotes = async function () {
    try {
        const fetchedNotes = await api.getNotes(currentUserId);
        notes = (fetchedNotes || []).map(n => ({
            id: n.id || n._id || String(n._id),
            title: n.title || '',
            content: n.content || '',
            createdAt: n.created_at || new Date().toISOString()
        }));
        renderNotes();
    } catch (e) {
        console.error("loadAndRenderNotes error:", e);
    }
}

// Apply saved language to AI chat after ai-chat.js loads
// (setAiLanguage is defined in ai-chat.js which loads after app.js)
setTimeout(() => {
    if (typeof setAiLanguage === 'function') {
        setAiLanguage(currentLanguage);
    }
}, 100);

// Global Offline UI handling
function updateOfflineUI() {
    const banner = document.getElementById('offlineBanner');
    const refreshBtn = document.getElementById('refreshBtn');
    
    if (!navigator.onLine) {
        if (banner) {
            banner.textContent = offlineTranslations[currentLanguage]?.banner || 'Offline mode 📴';
            banner.style.display = 'block';
        }
        if (refreshBtn) {
            refreshBtn.style.display = 'inline-block';
        }
    } else {
        if (banner) banner.style.display = 'none';
        if (refreshBtn) refreshBtn.style.display = 'none';
    }
}

// Ensure offline UI is up to date immediately and on events
updateOfflineUI();
window.addEventListener('online', updateOfflineUI);
window.addEventListener('offline', updateOfflineUI);

// Language change handler (update banner immediately if offline)
languageButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
        updateOfflineUI();
    });
});

// Refresh button handler
const refreshBtn = document.getElementById('refreshBtn');
if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
        if (navigator.onLine) {
            if (typeof loadAndRenderTasks === 'function') loadAndRenderTasks();
            if (typeof loadAndRenderNotes === 'function') loadAndRenderNotes();
            showToast('Loading fresh data... 🔄', 'success');
        } else {
            showToast(offlineTranslations[currentLanguage].action, 'error');
        }
    });
}

