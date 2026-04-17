# Plan Reminder Bot - Complete Setup & Deployment Guide

## Overview
This document provides instructions for running the complete Plan Reminder system with:
- **Bot**: Python Telegram Bot (python-telegram-bot v20+)
- **API**: FastAPI server on port 5000
- **Mini App**: Web-based UI (HTML/CSS/JS)
- **Database**: MongoDB

---

## Part 1: Environment Setup

### 1.1 Install Dependencies
```bash
cd plan-reminder
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

### 1.2 Environment Variables
Create a `.env` file in the root directory with:
```
BOT_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
MONGODB_URI=mongodb://username:password@host:port/
MONGODB_DB=plan_reminder
ADMIN_ID=your_telegram_user_id
```

---

## Part 2: Running the System

### Option A: Run Bot & API on Same Machine (Recommended for Development)

**Terminal 1 - Run the API Server:**
```bash
cd plan-reminder
python -m uvicorn bot.api.routes:app --host 0.0.0.0 --port 5000 --reload
```

**Terminal 2 - Run the Bot:**
```bash
cd plan-reminder
python main.py
```

### Option B: Run API & Bot Separately (Production)

**API Server:**
- Deploy to a server (Heroku, AWS, Digital Ocean, etc.)
- Update `MINI_APP_URL` in `bot/handlers/start.py` to your API server URL
- API runs on port 5000

**Bot:**
- Can run on a different server
- Uses same MongoDB connection as API
- Calls Groq API for AI features

---

## Part 3: Deploy Mini App

### 3.1 Choose Hosting (Option A or B)

**Option A: Simple Static Hosting (Recommended)**
- GitHub Pages
- Netlify  
- Vercel
- AWS S3 + CloudFront

Files to deploy:
- `mini-app/index.html`
- `mini-app/style.css`
- `mini-app/app.js`
- `mini-app/api.js`
- `mini-app/ai-chat.js`

**Option B: Host on Same Server as API**
- Place `mini-app/` files in a public directory
- Configure web server to serve them

### 3.2 Update Configuration

**In `mini-app/api.js`:**
```javascript
const BASE_URL = (() => {
    // Development
    if (window.location.hostname === 'localhost') {
        return 'http://localhost:5000';
    }
    // Production
    return 'https://your-api-server.com';
})();
```

**In `bot/handlers/start.py`:**
```python
MINI_APP_URL = "https://your-mini-app-url.com"
```

### 3.3 Register with Telegram Bot

Go to BotFather in Telegram:
```
/setmenubutton
/setwebapp
```

Set the Mini App URL to your hosted URL.

---

## Part 4: API Endpoints Reference

### Tasks
```
GET  /api/tasks/{user_id}           # Get today's tasks
POST /api/tasks                      # Create task
PATCH /api/tasks/{task_id}/done     # Mark task done
DELETE /api/tasks/{task_id}          # Delete task
```

### Notes
```
GET  /api/notes/{user_id}    # Get all notes
POST /api/notes              # Create/update note
```

### AI Chat
```
POST /api/ai/chat  # Send message to AI
```

### Archive & Stats
```
GET  /api/archive/{user_id}  # Get completed tasks by date
GET  /api/stats/{user_id}    # Get user statistics
```

### Health Check
```
GET  /api/health  # API health status
```

---

## Part 5: Features Overview

### Bot Features ✅
- ✅ Language support (Uzbek/Russian/English)
- ✅ Task management (/add, /tasks, /done)
- ✅ AI assistant (Groq powered)
- ✅ Voice message transcription (Groq Whisper)
- ✅ Real-time reminders (every minute check)
- ✅ Task extraction from text
- ✅ Subscription management
- ✅ Archives completed tasks
- ✅ Mini App button in welcome message

### Mini App Features ✅
- ✅ 4 tabs: Tasks, Notes, Archive, Stats
- ✅ Add tasks with priority and time
- ✅ Filter tasks (today, tomorrow, urgent, done)
- ✅ Notes with expand/collapse
- ✅ Archive grouped by date
- ✅ Statistics dashboard
- ✅ **NEW**: Floating AI chat (💬 button)
- ✅ **NEW**: Web Speech API for voice input
- ✅ **NEW**: AI task suggestions
- ✅ **NEW**: Dark theme with smooth animations

### AI Features ✅
- ✅ Conversational AI with history (last 6 messages)
- ✅ Productivity-focused responses
- ✅ Grammar enforcement
- ✅ Emoji in responses
- ✅ Task extraction from schedules
- ✅ Off-topic detection and redirection
- ✅ Multi-language support (uz/ru/en)

---

## Part 6: Troubleshooting

### Bot fails to connect
- Check `BOT_TOKEN` in `.env`
- Ensure MongoDB is running
- Check Python version (3.8+)

### API returns 404
- Ensure FastAPI server is running on port 5000
- Check endpoint URLs match exactly
- Verify MongoDB connection

### Mini App won't load
- Check hosting URL is accessible
- Verify `BASE_URL` in `api.js` is correct
- Check browser console for errors
- Ensure CORS is enabled on API

### AI Chat not responding
- Verify `GROQ_API_KEY` is valid
- Check `/api/ai/chat` endpoint is working
- Check Mini App sends correct JSON format
- Monitor API logs

### Voice transcription fails
- Check browser WebRTC permissions
- Ensure Groq Whisper API is available
- Check network connectivity
- Verify user has granted microphone access

---

## Part 7: Development Workflow

### Adding New Features

1. **Backend**: Update `bot/handlers/` or `bot/api/routes.py`
2. **Frontend**: Update Mini App files
3. **Database**: Migrations handled by Motor/PyMongo
4. **Testing**: Use localhost for development

### File Structure
```
plan-reminder/
├── main.py                    # Bot entry point
├── requirements.txt           # Dependencies
├── .env                       # Environment variables
├── bot/
│   ├── __init__.py
│   ├── messages.py           # Language messages
│   ├── handlers/
│   │   ├── start.py          # /start, language selection, Mini App link
│   │   ├── todo.py           # Task management, AI chat, reminders
│   │   └── voice.py          # Voice message handling
│   ├── models/
│   │   ├── user.py           # User DB operations
│   │   └── task.py           # Task DB operations
│   ├── services/
│   │   ├── db.py             # MongoDB connection
│   │   └── ai.py             # Groq AI integration
│   └── api/                   # NEW
│       ├── __init__.py
│       └── routes.py       # FastAPI server
└── mini-app/                  # NEW
    ├── index.html
    ├── style.css
    ├── app.js
    ├── api.js
    └── ai-chat.js
```

---

## Part 8: Security Considerations

### API Security
- ✅ User validation on every endpoint
- ✅ Subscription checks
- ✅ CORS enabled (currently all origins - restrict in production)
- ✅ Error details limited in responses

### Bot Security
- ✅ Telegram OAuth validation (WebApp SDK)
- ✅ User ID validation before data access
- ✅ Trial/paid subscription validation

### Mini App Security
- ✅ Telegram Web App SDK integration
- ✅ User ID from Telegram (not user input)
- ✅ HTTPS recommended in production

### TODO for Production
- [ ] Restrict CORS to specific domains
- [ ] Add rate limiting
- [ ] Add request validation middleware
- [ ] Use JWT for API authentication
- [ ] Add logging/monitoring
- [ ] Set up error tracking (Sentry)
- [ ] Implement database backups

---

## Part 9: Performance Tips

### Bot
- Schedulers run Asynchronously
- Database queries use indexes
- Message history limited (last 6 pairs)

### API
- FastAPI is lightweight and fast
- Async/await throughout
- MongoDB connection reused

### Mini App
- CSS animations use GPU (transform, opacity)
- Lazy loading of images/data
- LocalStorage for notes
- Minimal dependencies

---

## Part 10: Contact & Support

For issues, questions, or feature requests:
1. Check the troubleshooting section above
2. Review logs from bot and API
3. Check browser console in Mini App
4. Test with MongoDB admin tools
5. Verify Groq API status

---

## Quick Start Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run API (Terminal 1)
python -m uvicorn bot.api.routes:app --host 0.0.0.0 --port 5000 --reload

# Run Bot (Terminal 2)
python main.py

# Deploy Mini App
# Copy mini-app/ files to your hosting provider
# Update URLs in config files
# Register with Telegram BotFather
```

---

**Last Updated**: April 5, 2026
**Version**: 2.0 (with Mini App and AI Chat)
