"""Task Agent App — Full project management platform with AI agent."""

import json
import mimetypes
import os
import re as _re
import threading
import uuid
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import date

from models import (
    init_db, seed_data,
    get_users, get_user, add_user,
    get_channels, get_channel, add_channel, add_member_to_channel, remove_member_from_channel,
    get_messages, add_message,
    get_tasks, get_tasks_by_status, create_task, update_task, get_task, get_task_stats,
    add_file, get_file, get_channel_files,
)
from agent import process_message

PORT = 4500

HTML = open(os.path.join(os.path.dirname(__file__), "index.html")).read()
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
MAX_UPLOAD = 10 * 1024 * 1024  # 10MB


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._json_or_html(HTML, "text/html")
        elif path == "/api/users":
            self._json(get_users())
        elif path == "/api/channels":
            self._json(get_channels())
        elif path.startswith("/api/channels/") and path.endswith("/messages"):
            ch_id = path.split("/")[3]
            self._json(get_messages(ch_id))
        elif path.startswith("/api/channels/") and path.endswith("/tasks"):
            ch_id = path.split("/")[3]
            self._json(get_tasks(ch_id))
        elif path.startswith("/api/channels/") and path.endswith("/stats"):
            ch_id = path.split("/")[3]
            self._json(get_task_stats(ch_id))
        elif path.startswith("/api/channels/") and path.endswith("/files"):
            ch_id = path.split("/")[3]
            self._json(get_channel_files(ch_id))
        elif path.startswith("/uploads/"):
            self._serve_file(path[9:])
        elif path == "/api/tasks/update":
            pass  # handled in POST
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            self._handle_upload()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/message":
            channel_id = body["channel_id"]
            user_id = body["user_id"]
            text = body["text"]

            # Save user message
            msg_id = add_message(channel_id, user_id, text, "user")

            # Process with agent
            agent_responses = process_message(channel_id, user_id, text)
            for resp in agent_responses:
                add_message(channel_id, "agent", resp["text"], resp["msg_type"])

            self._json({
                "msg_id": msg_id,
                "agent_responses": agent_responses,
                "tasks": get_tasks(channel_id),
                "stats": get_task_stats(channel_id),
            })

        elif path == "/api/tasks/update":
            task_id = body["task_id"]
            updates = {k: v for k, v in body.items() if k != "task_id"}
            update_task(task_id, **updates)
            task = get_task(task_id)
            self._json(task)

        elif path == "/api/tasks/create":
            tid = create_task(
                body["channel_id"], body["title"],
                body.get("assignee"), body.get("priority", "P2"),
                body.get("deadline"), body.get("status", "up_next"),
            )
            self._json(get_task(tid))

        elif path == "/api/channels/create":
            ch = add_channel(
                body["name"],
                body.get("description", ""),
                body.get("channel_type", "project"),
                body.get("client_name"),
                body.get("members", []),
            )
            self._json(ch)

        elif path == "/api/channels/add-member":
            add_member_to_channel(body["channel_id"], body["user_id"])
            self._json(get_channel(body["channel_id"]))

        elif path == "/api/channels/remove-member":
            remove_member_from_channel(body["channel_id"], body["user_id"])
            self._json(get_channel(body["channel_id"]))

        elif path == "/api/users/create":
            user = add_user(
                body["id"],
                body["name"],
                body.get("role", "member"),
            )
            self._json(user)

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _json_or_html(self, content, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(content.encode())

    def _serve_file(self, filename):
        if ".." in filename or "/" in filename:
            self.send_response(403)
            self.end_headers()
            return
        filepath = os.path.join(UPLOAD_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            return
        mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(os.path.getsize(filepath)))
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD:
            self._json({"error": "File too large (max 10MB)"})
            return
        body = self.rfile.read(length)
        if "multipart/form-data" not in content_type or "boundary=" not in content_type:
            self._json({"error": "Invalid upload"})
            return
        boundary = content_type.split("boundary=")[1].strip()
        fields, files = self._parse_multipart(boundary, body)
        if "file" not in files:
            self._json({"error": "No file provided"})
            return
        f = files["file"]
        ext = os.path.splitext(f["filename"])[1].lower()
        stored = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as out:
            out.write(f["data"])
        channel_id = fields.get("channel_id", "")
        user_id = fields.get("user_id", "")
        file_id = add_file(channel_id, user_id, f["filename"], stored, len(f["data"]), f["content_type"])
        file_url = f"/uploads/{stored}"
        file_info = json.dumps({"file_id": file_id, "filename": f["filename"], "url": file_url, "size": len(f["data"]), "type": f["content_type"]})
        msg_id = add_message(channel_id, user_id, file_info, "file")
        self._json({"file_id": file_id, "msg_id": msg_id, "filename": f["filename"], "url": file_url, "size": len(f["data"]), "type": f["content_type"]})

    def _parse_multipart(self, boundary, body):
        parts = body.split(b"--" + boundary.encode())
        fields, files = {}, {}
        for part in parts:
            if not part or part.strip() in (b"", b"--", b"--\r\n"):
                continue
            if b"\r\n\r\n" not in part:
                continue
            raw_headers, content = part.split(b"\r\n\r\n", 1)
            if content.endswith(b"\r\n"):
                content = content[:-2]
            hdr = raw_headers.decode("utf-8", errors="replace")
            nm = _re.search(r'name="([^"]+)"', hdr)
            fm = _re.search(r'filename="([^"]*)"', hdr)
            if not nm:
                continue
            if fm and fm.group(1):
                ct = _re.search(r"Content-Type:\s*(.+?)(?:\r\n|$)", hdr)
                files[nm.group(1)] = {"filename": fm.group(1), "data": content, "content_type": ct.group(1).strip() if ct else "application/octet-stream"}
            else:
                fields[nm.group(1)] = content.decode("utf-8", errors="replace")
        return fields, files

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    db_path = os.path.join(os.path.dirname(__file__), "app.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    init_db()
    seed_data()
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    print(f"\n  ⚡ Task Agent App running at http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop.\n")

    server = HTTPServer(("0.0.0.0", PORT), AppHandler)
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
