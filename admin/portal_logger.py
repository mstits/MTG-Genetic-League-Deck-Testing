"""Admin portal logger for capturing fidelity crashes and engine anomalies."""

import os
import json
import traceback
from datetime import datetime
from dataclasses import dataclass, asdict

CRASH_REPORT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "admin",
    "admin_crash_reports.json"
)

@dataclass
class FidelityCrashReport:
    timestamp: str
    card_id: str
    crash_type: str
    message: str
    stack_trace: str
    game_state_dump: dict

def log_fidelity_crash(card_id: str, crash_type: str, message: str, game_state: dict, exc: Exception = None):
    """Log a severe engine failure (like infinite loop or unresolved SBA) to the admin portal."""
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(CRASH_REPORT_FILE), exist_ok=True)
    
    st = ""
    if exc:
        st = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        
    report = FidelityCrashReport(
        timestamp=datetime.now().isoformat(),
        card_id=card_id,
        crash_type=crash_type,
        message=message,
        stack_trace=st,
        game_state_dump=game_state
    )
    
    reports = []
    if os.path.exists(CRASH_REPORT_FILE):
        try:
            with open(CRASH_REPORT_FILE, "r") as f:
                reports = json.load(f)
        except json.JSONDecodeError:
            pass
            
    reports.append(asdict(report))
    
    with open(CRASH_REPORT_FILE, "w") as f:
        json.dump(reports, f, indent=2)
        
    print(f"🚨 ADMIN ALERT: {crash_type} logged for card {card_id}. Check admin_crash_reports.json")
