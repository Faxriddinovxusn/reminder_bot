"""
PlanAI — Production Launcher (Railway/VPS)
Runs Bot and API Server simultaneously.
"""
import subprocess
import sys
import os
import signal
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_DIR = os.path.join(BASE_DIR, "plan-reminder")

processes = []

def start_all():
    print("=" * 50)
    print("  🚀 PlanAI — Server ishga tushmoqda")
    print("=" * 50)
    
    port = os.getenv("PORT", "8000")
    
    # 1. API Server (Mini App + Dashboard + API)
    print(f"\n📡 API Server ishga tushmoqda (port {port})...")
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "bot.api.routes:app", "--host", "0.0.0.0", "--port", port],
        cwd=PLAN_DIR
    )
    processes.append(api_proc)
    time.sleep(3)
    
    # 2. Telegram Bot
    print("🤖 Telegram Bot ishga tushmoqda (10 soniya kutilyapti - Railway eski konteynerni o'chirishi uchun)...")
    time.sleep(10)
    bot_proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=PLAN_DIR
    )
    processes.append(bot_proc)
    
    print("\n" + "=" * 50)
    print("  ✅ Hammasi tayyor!")
    print(f"  🌐 API is running on port {port}")
    print("=" * 50 + "\n")

def stop_all():
    print("\n🛑 Barcha xizmatlar to'xtatilmoqda...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    print("👋 Hammasi to'xtatildi.")

if __name__ == "__main__":
    try:
        start_all()
        # Monitor processes
        while True:
            time.sleep(1)
            for p in processes:
                if p.poll() is not None:
                    print(f"⚠️  Jarayon to'xtadi (PID: {p.pid})")
                    sys.exit(1)
    except KeyboardInterrupt:
        stop_all()
