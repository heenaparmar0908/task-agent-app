"""Task Agent App — Full project management platform with AI agent."""

import json
import os
import threading
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
)
from agent import process_message

PORT = 4500

HTML = open(os.path.join(os.path.dirname(__file__), "index.html")).read()


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
        elif path == "/api/tasks/update":
            pass  # handled in POST
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        parsed = urlparse(self.path)
        path = parsed.path

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
