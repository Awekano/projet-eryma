from .. import db
from ..models import Event

def create_event(kind: str, video_path: str | None = None, screenshot_path: str | None = None):
    event = Event(
        kind=kind,
        video_path=video_path,
        screenshot_path=screenshot_path
    )
    db.session.add(event)
    db.session.commit()
    return event
