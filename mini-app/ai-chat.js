console.log('ai-chat.js loaded');

// AI Chat Module
function detectLanguage() {
    const raw = (localStorage.getItem('language') || 'en').toLowerCase();
    if (raw.startsWith('uz')) return 'uz';
    if (raw.startsWith('ru')) return 'ru';
    return 'en';
}

let aiChatState = {
    isOpen: false,
    isListening: false,
    messageHistory: [], // Last 12 pairs (24 messages)
    language: detectLanguage(),
    pendingTasks: null,
    planState: 'idle'
};

// DOM Elements
const floatingAiBtn = document.getElementById('floatingAiBtn');
const aiChatPanel = document.getElementById('aiChatPanel');
const aiCloseBtn = document.getElementById('aiCloseBtn');
const aiMessages = document.getElementById('aiMessages');
const aiInput = document.getElementById('aiInput');
const aiSendBtn = document.getElementById('aiSendBtn');
const aiVoiceBtn = document.getElementById('aiVoiceBtn');

// Web Speech API
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;

function getLanguageCode(shortLang) {
    const langMap = { uz: 'uz-UZ', ru: 'ru-RU', en: 'en-US' };
    return langMap[shortLang] || 'en-US';
}

function syncRecognitionLanguage() {
    aiChatState.language = detectLanguage();
    if (recognition) {
        recognition.lang = getLanguageCode(aiChatState.language);
    }
}

if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    syncRecognitionLanguage();
    
    recognition.onstart = () => {
        aiChatState.isListening = true;
        aiVoiceBtn.classList.add('recording');
        aiVoiceBtn.textContent = '🛑';
    };
    
    recognition.onend = () => {
        aiChatState.isListening = false;
        aiVoiceBtn.classList.remove('recording');
        aiVoiceBtn.textContent = '🎤';
    };
    
    recognition.onresult = (event) => {
        let transcription = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            transcription += event.results[i][0].transcript;
        }
        if (transcription) {
            aiInput.value = transcription;
            setTimeout(() => sendAiMessage(), 500);
        }
    };
    
    recognition.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        if (typeof showToast === 'function') {
            showToast('Voice input failed', 'error');
        }
    };
} else {
    if (typeof showToast === 'function') {
        showToast("🎤 Ovoz yozish bu qurilmada qo'llab-quvvatlanmaydi", 'error');
    }
}

// Event Listeners
floatingAiBtn.addEventListener('click', toggleAiChat);
aiCloseBtn.addEventListener('click', closeAiChat);
aiSendBtn.addEventListener('click', sendAiMessage);
aiInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendAiMessage();
});
aiVoiceBtn.addEventListener('click', toggleVoiceInput);

function toggleAiChat() {
    if (aiChatState.isOpen) {
        closeAiChat();
    } else {
        openAiChat();
    }
}

// Greeting messages per language
const aiGreetingHtml = `Hello! I'm here to help you with your tasks and productivity. 👋 How can I assist you today?<br><br>Kuningzni rejalashtirish va vaqtni tejash uchun <span style="cursor:pointer; color: #8b5cf6; background: #ede9fe; padding: 2px 6px; border-radius: 4px; font-weight: bold; display: inline-block;" onclick="window.sendAiCommand('/plan')">/plan</span> commandini yuboring va AI bilan reja tuzing`;

const AI_GREETINGS = {
    uz: aiGreetingHtml,
    ru: aiGreetingHtml,
    en: aiGreetingHtml
};

// Global function to allow click on /plan command
window.sendAiCommand = function(cmd) {
    const aiInput = document.getElementById('aiInput');
    if (aiInput) {
        aiInput.value = cmd;
        if (typeof sendAiMessage === 'function') sendAiMessage();
    }
};

function openAiChat() {
    aiChatState.isOpen = true;
    aiChatPanel.classList.add('active');
    aiInput.focus();
    
    // Load initial greeting if first time — use user's language
    if (aiChatState.messageHistory.length === 0) {
        const lang = detectLanguage();
        displayAiMessage(AI_GREETINGS[lang] || AI_GREETINGS.en);
    }
}

function closeAiChat() {
    aiChatState.isOpen = false;
    aiChatPanel.classList.remove('active');
    if (aiChatState.isListening && recognition) {
        recognition.abort();
    }
}

function toggleVoiceInput() {
    if (!recognition) {
        if (typeof showToast === 'function') {
            showToast("🎤 Ovoz yozish bu qurilmada qo'llab-quvvatlanmaydi", 'error');
        }
        return;
    }
    
    if (aiChatState.isListening) {
        recognition.abort();
    } else {
        syncRecognitionLanguage();
        recognition.start();
    }
}

async function sendAiMessage() {
    const message = aiInput.value.trim();
    if (!message) return;

    syncRecognitionLanguage();
    
    // Display user message
    displayUserMessage(message);
    aiInput.value = '';
    
    // Add to history
    aiChatState.messageHistory.push({ role: 'user', content: message });
    if (aiChatState.messageHistory.length > 24) {
        aiChatState.messageHistory.shift(); // Keep last 12 pairs (24 messages)
    }
    
    try {
        // Show loading state
        const loadingId = 'msg-loading-' + Date.now();
        displayAiMessage('...', loadingId);
        
        // Get user ID from Telegram
        const userId = String(window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'guest');
        
        // Use global BASE_URL from api.js (auto-detected, no hardcoding)
        const apiBaseUrl = window.API_BASE_URL || (window.location.origin + "/api");
        
        // Build headers
        const fetchHeaders = {
            'Content-Type': 'application/json',
        };
        if (window.Telegram?.WebApp?.initData) {
            fetchHeaders['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
        }

        // If user is editing a plan, inject system info into the API payload invisibly
        let apiMessage = message;
        if (aiChatState.planState === 'awaiting_plan_edit' && aiChatState.pendingTasks) {
            const tasksStr = JSON.stringify(aiChatState.pendingTasks);
            apiMessage = `<system>SYSTEM INFO: The user is currently editing the following pending plan. You MUST output action 'propose_tasks' JSON block with the updated list of tasks.\nCURRENT TASKS: ${tasksStr}</system>\n\nUSER EDITS: ${message}`;
        }

        // Send to backend
        const response = await fetch(`${apiBaseUrl}/ai/chat`, {
            method: 'POST',
            headers: fetchHeaders,
            body: JSON.stringify({
                userId,
                message: apiMessage,
                history: aiChatState.messageHistory.slice(-20), // Send last 10 pairs
                language: aiChatState.language || 'en',
            }),
        });
        
        if (!response.ok) {
            let errMsg = `HTTP ${response.status}`;
            try {
                const errBody = await response.text();
                if (errBody) errMsg += ` - ${errBody}`;
            } catch (e) {}
            throw new Error(errMsg);
        }
        
        const data = await response.json();
        const aiResponse = data.message || "Sorry, error occurred.";
        const action = data.action;
        const actionData = data.data;
        
        // Remove loading message
        const loadingMsg = document.getElementById(loadingId);
        if (loadingMsg) loadingMsg.remove();
        
        // Display AI response
        displayAiMessage(aiResponse);
        
        // Execute action
        if (action) {
            await executeAiAction(action, actionData);
        }
        
        // Add to history
        aiChatState.messageHistory.push({ role: 'assistant', content: aiResponse });
        if (aiChatState.messageHistory.length > 24) aiChatState.messageHistory.shift();
        
        // Show task suggestions if tasks were extracted (legacy fallback)
        const extractedTasks = data.tasks || [];
        if (extractedTasks.length > 0 && action !== 'tasks_added') {
            displayTaskSuggestions(extractedTasks);
        }
    } catch (error) {
        console.error('Failed to send message:', error);
        // Remove loading message on error
        const loadingMsgs = document.querySelectorAll('[id^="msg-loading-"]');
        loadingMsgs.forEach(el => el.remove());
        
        // Show detailed error for debugging
        const errorText = error.message.substring(0, 100); // Truncate if too long
        displayAiMessage(`⚠️ Xatolik: ${errorText}`);
        
        if (typeof showToast === 'function') {
            showToast('Failed to get AI response', 'error');
        }
    }
}

async function executeAiAction(action, data) {
    try {
        switch(action) {
            case "propose_tasks":
                if (data && data.length > 0) {
                    aiChatState.pendingTasks = data;
                    aiChatState.planState = 'awaiting_confirmation';
                    displayPlanConfirmation(data);
                } else {
                    showAiActionFeedback("🤷 Hech qanday vazifa yozilmadi.");
                }
                break;
                
            case "tasks_added":
                if (data && data.length > 0) {
                    // Reload tasks in background
                    if (window.loadTasks && window.renderTasks) {
                        try {
                            const fetchedTasks = await window.loadTasks();
                            window.tasks = fetchedTasks || [];
                            window.renderTasks();
                        } catch(e) {
                            console.error('Reload fail', e);
                        }
                    }
                    // Switch to tasks tab to show result
                    const tasksNavBtn = document.querySelector('[data-tab="tasksTab"]');
                    if (tasksNavBtn) {
                        setTimeout(() => {
                            tasksNavBtn.click();
                        }, 1000);
                    }
                    showAiActionFeedback(`✅ ${data.length} ta vazifa qo'shildi!`);
                }
                break;
            
            case "delete_requested":
                showAiActionFeedback("🗑 Qaysi vazifani o'chirmoqchisiz? Vazifa nomini ayting.");
                break;
            
            case "mark_done_requested":
                showAiActionFeedback("✅ Qaysi vazifani bajardingiz? Nomini ayting.");
                break;
            
            case "note_requested":
                // Switch to notes tab
                const notesBtn = document.querySelector('[data-tab="notesTab"]');
                if (notesBtn) notesBtn.click();
                showAiActionFeedback("📝 Notes bo'limiga o'tdingiz!");
                break;
        }
    } catch(e) {
        console.error("Action error:", e);
    }
}

function showAiActionFeedback(text) {
    const feedbackDiv = document.createElement("div");
    feedbackDiv.className = "ai-action-feedback";
    feedbackDiv.style.cssText = `
        background: var(--color-background-success, #10b981);
        color: white;
        padding: 8px 12px;
        border-radius: 8px;
        font-size: 13px;
        margin: 4px 0;
        animation: fadeIn 0.3s ease;
    `;
    feedbackDiv.textContent = text;
    
    const aiMessages = document.getElementById("aiMessages");
    if (aiMessages) {
        aiMessages.appendChild(feedbackDiv);
        aiMessages.scrollTop = aiMessages.scrollHeight;
        // Remove after 3 seconds
        setTimeout(() => feedbackDiv.remove(), 3000);
    }
}

function displayUserMessage(text) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'ai-message user';
    messageDiv.innerHTML = `
        <div class="ai-message-bubble">${escapeHtml(text)}</div>
    `;
    aiMessages.appendChild(messageDiv);
    scrollAiMessagesToBottom();
}

function displayAiMessage(text, id = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'ai-message ai';
    if (id) messageDiv.id = id;
    
    // In order to allow HTML inside, we shouldn't fully escape everything if it contains safe known HTML like our button.
    // However, for user input AI echoes, we should be careful. 
    // Here we'll do simple check: if text contains our specific greeting, render as HTML, else normal escape to prevent XSS.
    const isWelcomeText = text.includes("Kuningzni rejalashtirish");
    
    messageDiv.innerHTML = `
        <div class="ai-message-bubble">${isWelcomeText ? text : escapeHtml(text)}</div>
    `;
    aiMessages.appendChild(messageDiv);
    scrollAiMessagesToBottom();
}

function displayTaskSuggestions(tasks) {
    const suggestionsDiv = document.createElement('div');
    suggestionsDiv.className = 'ai-task-suggestions';
    suggestionsDiv.innerHTML = `
        <div class="ai-task-suggestions-title">✨ Suggested tasks:</div>
    `;
    
    tasks.forEach((task, index) => {
        const taskEl = document.createElement('div');
        taskEl.className = 'ai-task-item';
        taskEl.innerHTML = `
            <div class="ai-task-item-text">
                <strong>${escapeHtml(task.title)}</strong><br>
                <small>Priority: ${task.priority || 'medium'}</small>
            </div>
            <button class="ai-add-task-btn" data-task-index="${index}">➕ Rejaga qo'shish</button>
        `;
        
        taskEl.querySelector('button').addEventListener('click', () => {
            addTaskFromAi(task);
        });
        
        suggestionsDiv.appendChild(taskEl);
    });
    
    aiMessages.appendChild(suggestionsDiv);
    scrollAiMessagesToBottom();
}

function displayPlanConfirmation(tasks) {
    const confirmationDiv = document.createElement('div');
    confirmationDiv.className = 'ai-plan-confirmation';
    
    let html = `<div class="ai-plan-list">`;
    tasks.forEach((task, idx) => {
        const timeStr = task.time ? ` 🕒 <b>${task.time}</b>` : '';
        html += `<div class="ai-plan-item"><strong>${idx + 1}.</strong> ${escapeHtml(task.title)}${timeStr}</div>`;
    });
    html += `</div>`;
    
    const uiTexts = {
        uz: { confirm: "✅ To'g'ri", edit: "✏️ O'zgartirish" },
        ru: { confirm: "✅ Подтвердить", edit: "✏️ Изменить" },
        en: { confirm: "✅ Confirm", edit: "✏️ Edit" }
    };
    const t = uiTexts[aiChatState.language] || uiTexts.en;

    html += `
        <div class="ai-plan-actions">
            <button class="ai-plan-btn confirm-btn">${t.confirm}</button>
            <button class="ai-plan-btn edit-btn">${t.edit}</button>
        </div>
    `;
    
    confirmationDiv.innerHTML = html;
    
    confirmationDiv.querySelector('.confirm-btn').addEventListener('click', async () => {
        confirmationDiv.style.opacity = '0.5';
        confirmationDiv.style.pointerEvents = 'none';
        
        try {
            // Save all tasks in parallel using existing globally exposed api methods if available, or fetch
            const addPromises = aiChatState.pendingTasks.map(task => {
                // If api.addTask is available globally
                if (typeof api !== 'undefined' && api.addTask) {
                    const userId = String(window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'guest');
                    return api.addTask(userId, task.title, task.priority || 'medium', task.time);
                } else {
                    return saveSuggestedTask(task);
                }
            });
            await Promise.all(addPromises);
            
            const msgTexts = {
                uz: `${aiChatState.pendingTasks.length} ta vazifa qo'shildi ✅`,
                ru: `Добавлено ${aiChatState.pendingTasks.length} задач ✅`,
                en: `Added ${aiChatState.pendingTasks.length} tasks ✅`
            };
            if (typeof showToast === 'function') {
                showToast(msgTexts[aiChatState.language] || msgTexts.en, 'success');
            }
            
            // Reload and switch to Tasks tab
            if (window.loadTasks && window.renderTasks) {
                try {
                    const fetchedTasks = await window.loadTasks();
                    window.tasks = fetchedTasks || [];
                    window.renderTasks();
                } catch(e) {}
            }
            
            // Clean up state
            aiChatState.pendingTasks = null;
            aiChatState.planState = 'idle';
            
            const tasksNavBtn = document.querySelector('[data-tab="tasksTab"]');
            if (tasksNavBtn) {
                setTimeout(() => tasksNavBtn.click(), 300);
            }
            closeAiChat();
            
        } catch (error) {
            console.error('Failed to confirm tasks:', error);
            if (typeof showToast === 'function') {
                showToast('Failed to save tasks', 'error');
            }
            confirmationDiv.style.opacity = '1';
            confirmationDiv.style.pointerEvents = 'auto';
        }
    });

    confirmationDiv.querySelector('.edit-btn').addEventListener('click', () => {
        aiChatState.planState = 'awaiting_plan_edit';
        const msgTexts = {
            uz: "Nimasini o'zgartiramiz? (masalan, 'yugurish soat 8 da')",
            ru: "Что нужно изменить? (например, 'бег в 8')",
            en: "What needs to be changed? (e.g., 'run at 8')"
        };
        displayAiMessage(msgTexts[aiChatState.language] || msgTexts.en);
        // Disable buttons
        confirmationDiv.style.opacity = '0.5';
        confirmationDiv.style.pointerEvents = 'none';
        
        // Auto focus input
        const aiInput = document.getElementById('aiInput');
        if (aiInput) aiInput.focus();
    });
    
    aiMessages.appendChild(confirmationDiv);
    scrollAiMessagesToBottom();
}

function addTaskFromAi(task) {
    // Add to local tasks array
    if (typeof tasks !== 'undefined') {
        const newTask = {
            id: Date.now(),
            title: task.title,
            priority: task.priority || 'medium',
            time: task.time || null,
            createdAt: new Date().toISOString(),
            done: false
        };
        
        tasks.push(newTask);
        if (typeof renderTasks === 'function') {
            renderTasks();
        }
        
        // Send to backend
        saveSuggestedTask(task).catch(error => console.error('Failed to add task:', error));
        
        if (typeof showToast === 'function') {
            showToast(`✅ "${task.title}" added to tasks`, 'success');
        }
    }
}

async function saveSuggestedTask(task) {
    try {
        const apiBaseUrl = window.API_BASE_URL || (window.location.origin + "/api");
        const userId = window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'guest';
        
        // Build headers with auth
        const headers = {
            'Content-Type': 'application/json',
        };
        if (window.Telegram?.WebApp?.initData) {
            headers['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
        }
        
        await fetch(`${apiBaseUrl}/tasks`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                userId: String(userId),
                title: task.title,
                priority: task.priority || 'medium',
                time: task.time || null,
            }),
        });
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast('Failed to save task', 'error');
        }
        throw e;
    }
}

function scrollAiMessagesToBottom() {
    setTimeout(() => {
        aiMessages.scrollTop = aiMessages.scrollHeight;
    }, 0);
}

// Language detection and preference
function setAiLanguage(lang) {
    aiChatState.language = lang;
    if (recognition) {
        recognition.lang = getLanguageCode(lang);
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        toggleAiChat,
        openAiChat,
        closeAiChat,
        setAiLanguage,
    };
}
