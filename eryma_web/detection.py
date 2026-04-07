from mailer import send_alert_email
from app.services.events import create_event

video_name = "motion_demo_admin_20260329_164103.webm"

create_event(
    kind="motion",
    video_path=video_name
)

try:
    send_alert_email(video_name, "video_recorded")
except Exception as e:
    print(f"Erreur envoi mail : {e}")
