import os
import asyncio
import requests
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = FastAPI()

# -------------------------
# CONFIG
# -------------------------
API_KEY = os.getenv("CLICKUP_API_KEY")
BASE_URL = "https://api.clickup.com/api/v2"

HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json"
}

# In-memory lock (use Redis in production)
task_locks = {}

# -------------------------
# CLICKUP CLIENT
# -------------------------

class ClickUpClient:

    def get_task(self, task_id):
        url = f"{BASE_URL}/task/{task_id}"
        params = {"include_subtasks": "true"}
        res = requests.get(url, headers=HEADERS, params=params)
        return res.json()

    def update_task(self, task_id, payload):
        url = f"{BASE_URL}/task/{task_id}"
        return requests.put(url, headers=HEADERS, json=payload).json()

    def get_custom_field(self, task, field_name):
        for field in task.get("custom_fields", []):
            if field["name"] == field_name:
                return field.get("value"), field.get("id")
        return None, None


client = ClickUpClient()

# -------------------------
# HELPERS
# -------------------------

def get_month(date_ms):
    if not date_ms:
        return None
    return datetime.fromtimestamp(int(date_ms) / 1000).strftime("%B")


# -------------------------
# CORE LOGIC
# -------------------------

async def process_task(task_id):
    try:
        print(f"\n🚀 Processing task: {task_id}")

        # small delay (debounce)
        await asyncio.sleep(5)

        task = client.get_task(task_id)

        if not task:
            print("❌ Task not found")
            return

        # skip if subtask trigger
        if task.get("parent"):
            print("⏭ Skipping subtask trigger")
            return

        subtasks = task.get("subtasks", [])

        for sub in subtasks:
            sub_id = sub["id"]
            print(f"\n➡️ Subtask: {sub['name']}")

            start = sub.get("start_date")
            due = sub.get("due_date")

            if not start or not due:
                print("⏭ Missing dates, skipping")
                continue

            new_start_month = get_month(start)
            new_due_month = get_month(due)

            old_start_val, old_start_id = client.get_custom_field(sub, "oldStartDate")
            old_due_val, old_due_id = client.get_custom_field(sub, "oldduedate")

            old_start_month = get_month(old_start_val)
            old_due_month = get_month(old_due_val)

            print(f"Start: {old_start_month} → {new_start_month}")
            print(f"Due: {old_due_month} → {new_due_month}")

            update_fields = []

            # -------------------------
            # BUSINESS LOGIC
            # -------------------------

            if old_start_id and new_start_month != old_start_month:
                update_fields.append({
                    "id": old_start_id,
                    "value": start
                })

            if old_due_id and new_due_month != old_due_month:
                update_fields.append({
                    "id": old_due_id,
                    "value": due
                })

            # -------------------------
            # UPDATE CLICKUP
            # -------------------------

            if update_fields:
                print(f"✅ Updating subtask {sub_id}")

                client.update_task(sub_id, {
                    "custom_fields": update_fields
                })
            else:
                print("✔ No change")

    except Exception as e:
        print(f"🔥 Error: {str(e)}")

    finally:
        # release lock
        task_locks.pop(task_id, None)
        print(f"🔓 Released lock for {task_id}")


# -------------------------
# WEBHOOK
# -------------------------

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    task_id = data.get("task_id") or data.get("payload", {}).get("id")

    if not task_id:
        return {"status": "no task id"}

    # lock check
    if task_locks.get(task_id):
        return {"status": "already processing"}

    task_locks[task_id] = True

    asyncio.create_task(process_task(task_id))

    return {"status": "processing started"}
