import os
import subprocess
import sys

def test_cors_default_origins():
    # Run a script that imports main and prints app's CORS middleware allow_origins
    script = """
import sys
from main import app
from fastapi.middleware.cors import CORSMiddleware
for m in app.user_middleware:
    if m.cls == CORSMiddleware:
        print(','.join(m.kwargs['allow_origins']))
        sys.exit(0)
"""
    env = os.environ.copy()
    env.pop("CORS_ALLOW_ORIGINS", None)
    out = subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
    origins = out.strip().split(',')
    assert "https://app.marastation.com" in origins
    assert "https://vexonhq-ocr.vercel.app" in origins
    assert "http://localhost:3000" in origins

def test_cors_custom_origins():
    script = """
import sys
from main import app
from fastapi.middleware.cors import CORSMiddleware
for m in app.user_middleware:
    if m.cls == CORSMiddleware:
        print(','.join(m.kwargs['allow_origins']))
        sys.exit(0)
"""
    env = os.environ.copy()
    env["CORS_ALLOW_ORIGINS"] = "https://staging.marastation.com, http://localhost:8080 ,  "
    out = subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
    origins = out.strip().split(',')
    # Should strip whitespace and empty strings, and ignore '*' if we prevented it
    assert origins == ["https://staging.marastation.com", "http://localhost:8080"]

def test_scheduler_disabled_by_default():
    script = """
from line_bot_routes import _scheduler
print(_scheduler.state)
"""
    env = os.environ.copy()
    env.pop("ENABLE_SCHEDULER", None)
    out = subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
    # 0 = STATE_STOPPED, 1 = STATE_RUNNING, 2 = STATE_PAUSED
    assert out.strip() == "0"

def test_scheduler_enabled():
    script = """
from line_bot_routes import _scheduler
print(_scheduler.state)
"""
    env = os.environ.copy()
    env["ENABLE_SCHEDULER"] = "true"
    out = subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
    assert out.strip() == "1"
