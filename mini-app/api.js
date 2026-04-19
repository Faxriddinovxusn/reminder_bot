// API Client for Mini App
// Auto-detect API base URL from current page origin (works with any host/tunnel)
const BASE_URL = window.location.origin + "/api";
window.API_BASE_URL = BASE_URL;

// Helper function to show loading spinner
function showLoading() {
    const spinner = document.createElement('div');
    spinner.id = 'apiLoadingSpinner';
    spinner.className = 'loading-spinner';
    spinner.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 9999;';
    document.body.appendChild(spinner);
}

// Helper function to hide loading spinner
function hideLoading() {
    const spinner = document.getElementById('apiLoadingSpinner');
    if (spinner) spinner.remove();
}

// Helper function for API requests with error handling
async function apiRequest(endpoint, options = {}) {
    // Add cache-busting for GET requests so we always get fresh data
    const method = options.method || 'GET';
    let finalEndpoint = endpoint;
    if (method === 'GET') {
        const sep = endpoint.includes('?') ? '&' : '?';
        finalEndpoint = endpoint + sep + 't=' + Date.now();
    }
    
    const url = `${BASE_URL}${finalEndpoint}`;
    const headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store',
        ...options.headers,
    };
    
    // Add Telegram auth token if available
    if (window.Telegram?.WebApp?.initData) {
        headers['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
    }
    
    try {
        showLoading();
        
        const response = await fetch(url, {
            ...options,
            headers,
            cache: 'no-store',
        });
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.message || errorData.detail || `HTTP ${response.status}`);
        }
        
        const data = await response.json();
        hideLoading();
        return data;
    } catch (error) {
        hideLoading();
        console.error(`API Error [${endpoint}]:`, error.message);
        throw error;
    }
}

// API Client Object
const api = {
    /**
     * Get all tasks for user
     * @param {number} userId - Telegram user ID
     * @returns {Promise<Array>} Array of tasks
     */
    getTasks: async function(userId) {
        try {
            const response = await apiRequest(`/tasks/${userId}`);
            localStorage.setItem(`cache_tasks_${userId}`, JSON.stringify(response.data || []));
            return response.data || [];
        } catch (error) {
            console.log('Using cached tasks (offline)');
            const cached = localStorage.getItem(`cache_tasks_${userId}`);
            if (cached) return JSON.parse(cached);
            return [];
        }
    },
    
    /**
     * Create new task
     * @param {number} userId - Telegram user ID
     * @param {string} title - Task title
     * @param {string} priority - Priority level (high/medium/low)
     * @param {string} time - Task time (ISO string or null)
     * @returns {Promise<Object>} Created task
     */
    addTask: async function(userId, title, priority = 'medium', time = null) {
        try {
            const response = await apiRequest('/tasks', {
                method: 'POST',
                body: JSON.stringify({
                    userId,
                    title,
                    priority,
                    time,
                }),
            });
            
            if (typeof showToast === 'function') {
                showToast('Task added successfully ✓', 'success');
            }
            return response.data;
        } catch (error) {
            if (typeof showToast === 'function') {
                showToast('Failed to add task', 'error');
            }
            console.error('addTask error:', error);
            throw error;
        }
    },
    
    /**
     * Mark task as done/undone
     * @param {string} taskId - Task ID
     * @param {boolean} done - Is task done
     * @returns {Promise<Object>} Updated task
     */
    markDone: async function(taskId, done = true) {
        try {
            const response = await apiRequest(`/tasks/${taskId}/done`, {
                method: 'PATCH',
                body: JSON.stringify({ done }),
            });
            return response.data;
        } catch (error) {
            if (typeof showToast === 'function') {
                showToast('Failed to update task', 'error');
            }
            console.error('markDone error:', error);
            throw error;
        }
    },
    
    /**
     * Update task details
     * @param {string} taskId - Task ID
     * @param {Object} updates - Task updates (title, priority, time)
     * @returns {Promise<Object>} Updated task
     */
    updateTask: async function(taskId, updates) {
        try {
            const response = await apiRequest(`/tasks/${taskId}`, {
                method: 'PUT',
                body: JSON.stringify(updates),
            });
            
            if (typeof showToast === 'function') {
                showToast('Task updated ✓', 'success');
            }
            return response.data;
        } catch (error) {
            if (typeof showToast === 'function') {
                showToast('Failed to update task', 'error');
            }
            console.error('updateTask error:', error);
            throw error;
        }
    },
    
    /**
     * Delete task
     * @param {string} taskId - Task ID
     * @returns {Promise<Object>} Deleted task info
     */
    deleteTask: async function(taskId) {
        try {
            const response = await apiRequest(`/tasks/${taskId}`, {
                method: 'DELETE',
            });
            
            if (typeof showToast === 'function') {
                showToast('Task deleted ✓', 'success');
            }
            return response.data;
        } catch (error) {
            console.error('deleteTask error:', error);
            throw error;
        }
    },
    
    /**
     * Get all notes for user
     * @param {number} userId - Telegram user ID
     * @returns {Promise<Array>} Array of notes
     */
    getNotes: async function(userId) {
        try {
            const response = await apiRequest(`/notes/${userId}`);
            localStorage.setItem(`cache_notes_${userId}`, JSON.stringify(response.data || []));
            return response.data || [];
        } catch (error) {
            console.log('Using cached notes (offline)');
            const cached = localStorage.getItem(`cache_notes_${userId}`);
            if (cached) return JSON.parse(cached);
            return [];
        }
    },
    
    /**
     * Save or update note
     * @param {number} userId - Telegram user ID
     * @param {string} title - Note title
     * @param {string} content - Note content
     * @returns {Promise<Object>} Saved note
     */
    saveNote: async function(userId, title, content = '') {
        try {
            const response = await apiRequest('/notes', {
                method: 'POST',
                body: JSON.stringify({
                    userId,
                    title,
                    content,
                }),
            });
            
            if (typeof showToast === 'function') {
                showToast('Note saved ✓', 'success');
            }
            return response.data;
        } catch (error) {
            if (typeof showToast === 'function') {
                showToast('Failed to save note', 'error');
            }
            console.error('saveNote error:', error);
            throw error;
        }
    },
    
    /**
     * Get archived tasks for user
     * @param {number} userId - Telegram user ID
     * @returns {Promise<Array>} Array of archive groups
     */
    getArchive: async function(userId) {
        try {
            const response = await apiRequest(`/archive/${userId}`);
            localStorage.setItem(`cache_archive_${userId}`, JSON.stringify(response.data || []));
            return response.data || [];
        } catch (error) {
            console.log('Using cached archive (offline)');
            const cached = localStorage.getItem(`cache_archive_${userId}`);
            if (cached) return JSON.parse(cached);
            return [];
        }
    },

    /**
     * Get user stats
     * @param {number} userId - Telegram user ID
     * @returns {Promise<Object>} User statistics
     */
    getStats: async function(userId) {
        try {
            const response = await apiRequest(`/stats/${userId}`);
            localStorage.setItem(`cache_stats_${userId}`, JSON.stringify(response.data));
            return response.data;
        } catch (error) {
            console.log('Using cached stats (offline)');
            const cached = localStorage.getItem(`cache_stats_${userId}`);
            if (cached) return JSON.parse(cached);
            return {
                totalTasks: 0,
                completedTasks: 0,
                completionRate: 0,
                highPriorityTasks: 0,
            };
        }
    },

    /**
     * Get future planned tasks grouped by date
     * @param {number} userId - Telegram user ID
     * @returns {Promise<Array>} Array of date groups with tasks
     */
    getFutureTasks: async function(userId) {
        try {
            const response = await apiRequest(`/tasks/${userId}/future`);
            localStorage.setItem(`cache_future_${userId}`, JSON.stringify(response.data || []));
            return response.data || [];
        } catch (error) {
            console.log('Using cached future tasks (offline)');
            const cached = localStorage.getItem(`cache_future_${userId}`);
            if (cached) return JSON.parse(cached);
            return [];
        }
    },
};

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
}
