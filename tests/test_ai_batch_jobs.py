import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

from line_bot_routes import _scheduler
import cron_heartbeat as ch


def test_ai_batch_jobs_registered():
    # Retrieve jobs from the scheduler
    jobs = _scheduler.get_jobs()
    job_ids = [j.id for j in jobs]
    
    # Assert they are in the list
    assert "ai_cashflow_categorize" in job_ids
    assert "ai_anomaly_scan" in job_ids
    
    # Assert trigger matches daily 02:00 BKK
    cat_job = _scheduler.get_job("ai_cashflow_categorize")
    assert cat_job is not None
    # APScheduler CronTrigger has fields for hour, minute, etc.
    assert str(cat_job.trigger.fields[5]) == "2"  # hour
    assert str(cat_job.trigger.fields[6]) == "0"  # minute
    
    # Assert trigger matches daily 03:00 BKK
    anomaly_job = _scheduler.get_job("ai_anomaly_scan")
    assert anomaly_job is not None
    assert str(anomaly_job.trigger.fields[5]) == "3"  # hour
    assert str(anomaly_job.trigger.fields[6]) == "0"  # minute
    
    # Verify registered in cron_heartbeat sets
    assert "ai_cashflow_categorize" in ch._REGISTERED_JOBS
    assert "ai_anomaly_scan" in ch._REGISTERED_JOBS
