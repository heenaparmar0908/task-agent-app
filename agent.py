"""AI Agent — classifies messages, creates tasks, responds intelligently."""

import re
from datetime import date, timedelta
from models import (
    get_tasks, create_task, update_task, get_task,
    add_message, get_messages, get_users, get_user,
)


def extract_share_note(text):
    lower = text.lower()
    for p in [r"share (?:it |this )?with (\w+)", r"send (?:it |this )?to (\w+)", r"show (?:it )?to (\w+)"]:
        m = re.search(p, lower)
        if m:
            return m.group(1).capitalize()
    return None


def split_multiple_items(text):
    """Split 'X and Y' or 'X, Y and Z' into separate items."""
    # Remove common prefixes first
    lower = text.lower().strip()
    prefixes = [
        r"^(?:today |tonight |tomorrow )?(?:i |im |i'm |i am )?(?:going to |gonna |will |start |starting )?(?:working on |work on )?(?:the )?",
        r"^(?:need to |have to |gotta |should |planning to )(?:the )?",
    ]
    cleaned = lower
    for p in prefixes:
        cleaned = re.sub(p, "", cleaned, count=1).strip()

    # Remove trailing share/send clauses
    cleaned = re.sub(r"\s+and\s+(?:by\s+)?(?:end|eod)?\s*(?:will|i'll)?\s*(?:share|send|show).*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+by\s+(?:end|eod|tonight|tomorrow|friday|monday|tuesday|wednesday|thursday).*$", "", cleaned).strip()

    # Split on " and " or ", "
    items = re.split(r"\s+and\s+|,\s*", cleaned)
    # Clean each item: remove priority words from title
    priority_words = ["urgent", "critical", "asap", "p1", "p2", "p3", "high priority", "low priority", "no rush"]
    result = []
    for i in items:
        clean = i.strip()
        for pw in priority_words:
            clean = re.sub(r"\b" + pw + r"\b", "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"^[-\s]+|[-\s]+$", "", clean).strip()
        if clean and len(clean) > 1:
            result.append(clean.title())
    return result


def detect_priority(text):
    lower = text.lower()
    if any(w in lower for w in ["urgent", "critical", "asap", "immediately", "p1", "high priority", "blocker"]):
        return "P1"
    if any(w in lower for w in ["low priority", "p3", "when you can", "no rush", "nice to have"]):
        return "P3"
    return "P2"


def detect_deadline(text):
    lower = text.lower()
    today = date.today()
    days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

    if "by end" in lower or "by eod" in lower or "by tonight" in lower:
        return today.isoformat()
    if "tomorrow" in lower:
        return (today + timedelta(days=1)).isoformat()
    if "next week" in lower:
        return (today + timedelta(days=7)).isoformat()

    for day_name, day_num in days_map.items():
        if day_name in lower:
            current = today.weekday()
            diff = (day_num - current) % 7
            if diff == 0:
                diff = 7
            return (today + timedelta(days=diff)).isoformat()

    m = re.search(r"(?:oct|october|nov|november|dec|december)\s+(\d{1,2})", lower)
    if m:
        day = int(m.group(1))
        month = 10 if "oct" in lower else 11 if "nov" in lower else 12
        try:
            return date(today.year, month, day).isoformat()
        except ValueError:
            pass

    return (today + timedelta(days=5)).isoformat()


# Words that indicate the message is NOT a task
IGNORE_WORDS = [
    "hi", "hello", "hey", "good morning", "good evening", "thanks", "thank you",
    "ok", "okay", "sure", "yes", "no", "cool", "nice", "great", "awesome",
    "brb", "lol", "haha", "😂", "👍", "🙏",
]


def is_general_chat(text):
    lower = text.lower().strip()
    for w in IGNORE_WORDS:
        if lower == w or lower.startswith(w + " ") or lower.endswith(" " + w):
            if len(lower.split()) <= 4:
                return True
    return False


def is_reminder_message(text):
    """Detect time-based reminders: call me, meet me, remind me, etc."""
    lower = text.lower().strip()
    time_pattern = r"\d{1,2}\s*(?::\d{2})?\s*(?:pm|am|PM|AM)?\s*(?:ist|pst|est|utc|gmt)?"
    reminder_triggers = [
        r"call\s+(?:me|him|her|them|us|\w+)\s+(?:at|by|around|before)",
        r"meet\s+(?:me|him|her|them|us|\w+)\s+(?:at|by|around)",
        r"remind\s+(?:me|him|her|them|us|\w+)",
        r"(?:call|meet|sync|standup|meeting|huddle|catch up).*" + time_pattern,
        r"(?:at|by|before|around)\s+" + time_pattern,
        r"schedule\s+(?:a\s+)?(?:call|meet|sync|meeting)",
        r"(?:don'?t forget|remember)\s+to",
    ]
    for p in reminder_triggers:
        if re.search(p, lower):
            return True
    return False


def extract_reminder(text, sender_name):
    """Extract a reminder task from messages like 'Call me at 5 PM IST'."""
    lower = text.lower().strip()

    # Extract time
    time_match = re.search(r"(\d{1,2})\s*(?::(\d{2}))?\s*(pm|am)?\s*(ist|pst|est|utc|gmt)?", lower)
    time_str = ""
    if time_match:
        hour = time_match.group(1)
        mins = time_match.group(2) or "00"
        ampm = (time_match.group(3) or "").upper()
        tz = (time_match.group(4) or "").upper()
        time_str = f"{hour}:{mins} {ampm} {tz}".strip()

    # Build title from the message
    title = text.strip()
    # Clean up for title
    title = re.sub(r"\b(?:please|pls|plz|asap)\b", "", title, flags=re.IGNORECASE).strip()

    return {"title": title, "time_str": time_str}


def process_message(channel_id, user_id, text):
    """Process a message and return agent response(s)."""
    lower = text.lower().strip()
    responses = []
    user = get_user(user_id)
    user_name = user["name"] if user else user_id

    # --- Skip general chat early ---
    if is_general_chat(text):
        return responses

    # --- Completed tasks (completed task: X, Y, start: Z) ---
    completed_patterns = ["completed task:", "completed:", "finished:", "done:"]
    for pattern in completed_patterns:
        if pattern in lower:
            after = lower.split(pattern, 1)[1].strip()
            items = [i.strip() for i in re.split(r"[,;]", after) if i.strip()]
            done_names = []
            started_names = []

            for item in items:
                if item.startswith("start:") or item.startswith("start "):
                    start_title = re.sub(r"^start:?\s*", "", item).strip().title()
                    if start_title:
                        priority = detect_priority(item)
                        deadline = detect_deadline(item)
                        tid = create_task(channel_id, start_title, user_id, priority, deadline, "in_progress")
                        started_names.append((start_title, tid))
                    continue

                tasks = get_tasks(channel_id)
                matched = False
                for t in tasks:
                    if t["status"] == "done":
                        continue
                    title_words = [w for w in t["title"].lower().split() if len(w) > 2]
                    if any(w in item for w in title_words):
                        update_task(t["id"], status="done")
                        done_names.append((t["title"], t["id"]))
                        matched = True
                        break
                if not matched and len(item) > 2:
                    tid = create_task(channel_id, item.title(), user_id, "P2", date.today().isoformat(), "done")
                    done_names.append((item.title(), tid))

            parts = []
            for name, tid in done_names:
                parts.append(f"✅ *{name}* (T-{tid}) marked done")
            for name, tid in started_names:
                parts.append(f"🔵 *{name}* (T-{tid}) started")
            if parts:
                responses.append({"text": "\n".join(parts), "msg_type": "agent"})
            return responses

    # --- Done/finished single task ---
    done_words = ["is done", "is finished", "is completed", "is shipped", "all set", "wrapped up"]
    for pattern in done_words:
        if pattern in lower:
            tasks = get_tasks(channel_id)
            for t in tasks:
                if t["status"] == "done":
                    continue
                title_words = [w for w in t["title"].lower().split() if len(w) > 2]
                if any(w in lower for w in title_words):
                    update_task(t["id"], status="done")
                    responses.append({"text": f"✅ *{t['title']}* (T-{t['id']}) marked done.", "msg_type": "agent"})
                    return responses

    # --- Working on / starting (handles "X and Y" → two tasks) ---
    working_patterns = ["working on", "start working on", "i start", "im start", "i'm start",
                        "starting", "picked up", "taking on", "im working", "i'm working"]
    for pattern in working_patterns:
        if pattern in lower:
            note = extract_share_note(text)
            priority = detect_priority(text)
            deadline = detect_deadline(text)
            items = split_multiple_items(text)

            if not items:
                continue

            created = []
            matched_ids = set()
            for title in items:
                if len(title) < 2:
                    continue
                # Check existing task (exact or high overlap match, skip already matched)
                tasks = get_tasks(channel_id)
                matched = False
                for t in tasks:
                    if t["status"] == "done" or t["id"] in matched_ids:
                        continue
                    # Require >50% word overlap for a match
                    t_words = set(w.lower() for w in t["title"].split() if len(w) > 2)
                    new_words = set(w.lower() for w in title.split() if len(w) > 2)
                    if t_words and new_words and len(t_words & new_words) / max(len(t_words), len(new_words)) > 0.5:
                        update_task(t["id"], status="in_progress", assignee=user_id)
                        matched_ids.add(t["id"])
                        created.append(f"🔵 *{t['title']}* (T-{t['id']}) — {user_name} is on it.")
                        matched = True
                        break
                if not matched:
                    tid = create_task(channel_id, title, user_id, priority, deadline, "in_progress")
                    matched_ids.add(tid)
                    created.append(f"🔵 *{title}* (T-{tid}) — {user_name} is on it. Due {deadline}.")

            if created:
                msg = "Added to the list:\n" + "\n".join(created) if len(created) > 1 else f"Added to the list · T-{tid}\n" + created[0]
                if note:
                    msg += f"\n📌 Share with {note} when done."
                responses.append({"text": msg, "msg_type": "task_added"})
            return responses

    # --- Explicit task creation with "by" deadline ---
    create_patterns = [
        r"(?:i'll|i will|need to|have to|going to|gonna)\s+(.+?)(?:\s+by\s+)",
        r"(?:add task|new task|create task)[:\s]+(.+)",
    ]
    for p in create_patterns:
        match = re.search(p, lower)
        if match:
            title = match.group(1).strip().title()
            if len(title) > 3:
                priority = detect_priority(text)
                deadline = detect_deadline(text)
                tid = create_task(channel_id, title, user_id, priority, deadline)
                responses.append({"text": f"Added to the list · T-{tid}\n*{title}* — due {deadline}, assigned to {user_name}.", "msg_type": "task_added"})
                return responses

    # --- Deadline changes ---
    if any(w in lower for w in ["push", "extend", "move", "postpone", "reschedule"]):
        tasks = get_tasks(channel_id)
        for t in tasks:
            if t["status"] == "done":
                continue
            title_words = [w for w in t["title"].lower().split() if len(w) > 2]
            if any(w in lower for w in title_words):
                new_deadline = detect_deadline(text)
                update_task(t["id"], deadline=new_deadline)
                responses.append({"text": f"📅 *{t['title']}* (T-{t['id']}) deadline moved to {new_deadline}.", "msg_type": "agent"})
                return responses

    # --- Priority changes ---
    if "priority" in lower or ("urgent" in lower and any(w in lower for w in ["mark", "make", "set"])):
        tasks = get_tasks(channel_id)
        for t in tasks:
            if t["status"] == "done":
                continue
            title_words = [w for w in t["title"].lower().split() if len(w) > 2]
            if any(w in lower for w in title_words):
                new_priority = detect_priority(text)
                update_task(t["id"], priority=new_priority)
                responses.append({"text": f"*{t['title']}* (T-{t['id']}) priority changed to {new_priority}.", "msg_type": "agent"})
                return responses

    # --- Bulk list ---
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    bullet_lines = [l for l in lines if re.match(r"^[-*•\d.)\[\]]+\s", l)]
    if len(bullet_lines) >= 2:
        created = []
        for line in bullet_lines:
            title = re.sub(r"^[-*•\d.)\[\]\s]+", "", line).strip()
            if title and len(title) > 2:
                priority = detect_priority(title)
                deadline = detect_deadline(title)
                title_clean = re.sub(r"\s+by\s+.*$", "", title, flags=re.IGNORECASE).strip().title()
                tid = create_task(channel_id, title_clean, user_id, priority, deadline)
                created.append(f"✅ *{title_clean}* (T-{tid}) — due {deadline}")
        if created:
            responses.append({"text": f"Added {len(created)} tasks:\n" + "\n".join(created), "msg_type": "task_added"})
            return responses

    # --- Time-based reminders (call me at 5 PM, meet at 3, remind me to...) ---
    if is_reminder_message(text):
        reminder = extract_reminder(text, user_name)
        title = reminder["title"]
        time_str = reminder["time_str"]
        priority = "P1"  # reminders are high priority
        deadline = date.today().isoformat()  # same day
        tid = create_task(channel_id, title, user_id, priority, deadline, "up_next")
        msg = f"⏰ Reminder set · T-{tid}\n*{title}*"
        if time_str:
            msg += f" at {time_str}"
        msg += f" — assigned to {user_name}."
        responses.append({"text": msg, "msg_type": "task_added"})
        return responses

    # --- Short task-like messages (2-5 words, looks like a task name) ---
    # e.g. "D3 UI", "Testing", "Bug fix", "Code review"
    words = text.strip().split()
    if 1 <= len(words) <= 5 and not text.strip().endswith("?"):
        # Check it's not a greeting or general word
        if not is_general_chat(text) and text.strip()[0].isupper():
            priority = detect_priority(text)
            deadline = detect_deadline(text)
            title = text.strip()
            tid = create_task(channel_id, title, user_id, priority, deadline, "up_next")
            responses.append({
                "text": f"Added to the list · T-{tid}\n*{title}* — assigned to {user_name}. Due {deadline}.",
                "msg_type": "task_added"
            })
            return responses

    return responses
