from mailer import send_alert_email
video_name = "motion_demo_admin_20260329_164103.webm"

add_event(
    event_type="motion",
    description="Détection de mouvement",
    video_filename=video_name
)

try:
    send_alert_email(video_name, "video_recorded")
except Exception as e:
    print(f"Erreur envoi mail : {e}")
