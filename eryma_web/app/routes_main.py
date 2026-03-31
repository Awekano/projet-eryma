import mimetypes
import os
import time
from datetime import datetime
from pathlib import Path
from flask import jsonify
from werkzeug.utils import secure_filename
import cv2
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from flask_login import current_user, login_required

from .models import Event
from .services.audit import audit

main_bp = Blueprint("main", __name__)

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".avi", ".mov"}
INLINE_VIDEO_EXT = {".mp4",".mkv",".webm", ".mov"}


@main_bp.get("/")
@login_required
def index():
    return render_template("index.html")


@main_bp.get("/events")
@login_required
def events():
    kind = request.args.get("kind")
    q = Event.query.order_by(Event.created_at.desc())
    if kind in ("detection", "alarme"):
        q = q.filter_by(kind=kind)
    rows = q.limit(200).all()
    audit("view_events", username=current_user.username)
    return render_template("events.html", events=rows, kind=kind)


@main_bp.get("/live")
@login_required
def live():
    audit("view_live", username=current_user.username)
    return render_template("live.html")


@main_bp.get("/live_feed")
@login_required
def live_feed():
    rtsp_url = current_app.config.get("RTSP_URL")
    if not rtsp_url:
        audit("live_feed_missing_rtsp_url", username=current_user.username)
        return Response("RTSP_URL non configurée", status=500, mimetype="text/plain")

    audit("open_live_feed", username=current_user.username)
    return Response(
        stream_with_context(_mjpeg_stream(rtsp_url)),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@main_bp.get("/download/<int:event_id>")
@login_required
def download(event_id: int):
    ev = Event.query.get_or_404(event_id)
    if not ev.video_path or not os.path.exists(ev.video_path):
        abort(404)
    audit(f"download_video:{event_id}", username=current_user.username)
    return send_file(ev.video_path, as_attachment=True)



def _recordings_dir() -> str:
    return os.path.abspath(os.path.join(current_app.root_path, "..", "recordings"))


@main_bp.get("/recordings")
@login_required
def recordings():
    base_dir = _recordings_dir()
    rows = []

    if not os.path.isdir(base_dir):
        audit("view_recordings_folder_missing", username=current_user.username, extra={"dir": base_dir})
        return render_template(
            "recordings.html",
            rows=[],
            error=f"Dossier introuvable : {base_dir}",
        )

    for root, _, files in os.walk(base_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXT:
                continue

            abs_path = os.path.abspath(os.path.join(root, name))
            if not abs_path.startswith(base_dir + os.sep):
                continue

            st = os.stat(abs_path)
            rel_path = os.path.relpath(abs_path, base_dir).replace("\\", "/")

            rows.append(
                {
                    "rel_path": rel_path,
                    "name": name,
                    "ext": ext,
                    "size": st.st_size,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "dt": datetime.fromtimestamp(st.st_mtime),
                    "can_preview": ext in INLINE_VIDEO_EXT,
                }
            )

    rows.sort(key=lambda r: r["dt"], reverse=True)
    audit("view_recordings_folder", username=current_user.username, extra={"count": len(rows)})
    return render_template("recordings.html", rows=rows, error=None)


@main_bp.get("/recordings/view/<path:rel_path>")
@login_required
def recordings_view(rel_path: str):
    base_dir = _recordings_dir()
    abs_path = os.path.abspath(os.path.join(base_dir, rel_path))

    if not abs_path.startswith(base_dir + os.sep):
        abort(403)
    if not os.path.isfile(abs_path):
        abort(404)

    ext = os.path.splitext(abs_path)[1].lower()
    guessed_mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    inline = ext in INLINE_VIDEO_EXT

    audit("view_recording_file", username=current_user.username, extra={"file": rel_path})
    return send_file(
        abs_path,
        mimetype=guessed_mime,
        as_attachment=not inline,
        conditional=True,
        etag=True,
        last_modified=os.path.getmtime(abs_path),
        max_age=0,
    )


@main_bp.get("/recordings/download/<path:rel_path>")
@login_required
def recordings_download(rel_path: str):
    base_dir = _recordings_dir()
    abs_path = os.path.abspath(os.path.join(base_dir, rel_path))

    if not abs_path.startswith(base_dir + os.sep):
        abort(403)
    if not os.path.isfile(abs_path):
        abort(404)

    audit("download_recording_file", username=current_user.username, extra={"file": rel_path})
    return send_file(abs_path, as_attachment=True, conditional=True)



def _mjpeg_stream(rtsp_url: str):
    transport = current_app.config.get("RTSP_TRANSPORT", "tcp").lower()
    jpeg_quality = int(current_app.config.get("MJPEG_QUALITY", 80))

    options = []
    if transport == "tcp":
        options.append("rtsp_transport;tcp")

    if options:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(options)

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

    if not cap.isOpened():
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + _error_frame("Flux RTSP inaccessible")
            + b"\r\n"
        )
        return

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.2)
                continue

            success, buffer = cv2.imencode(".jpg", frame, encode_params)
            if not success:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )
    finally:
        cap.release()



def _error_frame(text: str) -> bytes:
    import numpy as np

    frame = np.zeros((480, 854, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        text,
        (30, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    ok, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if ok else b""
@main_bp.post("/upload_webcam_recording")
@login_required
def upload_webcam_recording():
    file = request.files.get("video")
    if not file:
        return jsonify({"error": "Aucun fichier reçu"}), 400

    original_name = secure_filename(file.filename or "")
    ext = Path(original_name).suffix.lower()

    allowed_ext = {".webm", ".mp4"}
    if ext not in allowed_ext:
        return jsonify({"error": f"Extension non autorisée : {ext}"}), 400

    recordings_dir = _recordings_dir()
    os.makedirs(recordings_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%d-%m-%Y_%Hh%Mmin%S")
    filename = f"video_{current_user.username}_{timestamp}{ext}"
    save_path = os.path.join(recordings_dir, filename)

    file.save(save_path)

    audit("upload_webcam_recording", username=current_user.username, extra={"file": filename})
    from app.services.events import create_event

create_event(
    kind="video_recorded",
    video_path=video_filepath,
    screenshot_path=screenshot_filepath
)
    return jsonify({
        "message": "Vidéo enregistrée",
        "filename": filename
    }), 201
from flask_login import login_required, current_user
from .models import AuditLog

@main_bp.get("/audit-logs")
@login_required
def audit_logs():
    if current_user.role != "admin":
        abort(403)

    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return render_template("audit_logs.html", logs=logs)
