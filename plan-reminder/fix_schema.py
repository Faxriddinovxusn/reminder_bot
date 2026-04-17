import sys

file_path = r'c:\Users\HP\OneDrive\Рабочий стол\remonder bptAI bot\plan-reminder\bot\api\routes.py'
with open(file_path, 'r', encoding='utf-8') as f:
    text = f.read()

# 1. get_tasks fix
text = text.replace(
    'task["id"] = str(task["_id"])\n        task["createdAt"] = task.get("created_at", now).isoformat()\n        tasks_data.append(task)',
    'task["id"] = str(task["_id"])\n        task["createdAt"] = task.get("created_at", now).isoformat()\n        task["done"] = task.get("done", task.get("is_done", False) or task.get("status") == "done")\n        p = task.get("priority", "medium")\n        if p == "normal": p = "medium"\n        task["priority"] = p\n        tasks_data.append(task)'
)

# 2. get_archive MongoDB filter fix
text = text.replace(
    '"user_id": user["_id"],\n        "done": True',
    '"user_id": user["_id"],\n        "$or": [{"done": True}, {"is_done": True}, {"status": "done"}]'
)

# 3. get_archive mapping fix
text = text.replace(
    'task["id"] = str(task["_id"])\n        grouped[date_str].append(task)',
    'task["id"] = str(task["_id"])\n        task["done"] = True\n        p = task.get("priority", "medium")\n        if p == "normal": p = "medium"\n        task["priority"] = p\n        grouped[date_str].append(task)'
)

# 4. get_stats list comprehensions
text = text.replace(
    'len([t for t in today_tasks if t.get("done")])',
    'len([t for t in today_tasks if t.get("done") or t.get("is_done", False) or t.get("status") == "done"])'
)
text = text.replace(
    'len([t for t in week_tasks if t.get("done")])',
    'len([t for t in week_tasks if t.get("done") or t.get("is_done", False) or t.get("status") == "done"])'
)
text = text.replace(
    'len([t for t in week_tasks if t.get("priority") == "high" and not t.get("done")])',
    'len([t for t in week_tasks if t.get("priority") == "high" and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])'
)
text = text.replace(
    'len([t for t in week_tasks if t.get("priority") == "medium" and not t.get("done")])',
    'len([t for t in week_tasks if (t.get("priority") in ("medium", "normal")) and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])'
)
text = text.replace(
    'len([t for t in week_tasks if t.get("priority") == "low" and not t.get("done")])',
    'len([t for t in week_tasks if t.get("priority") == "low" and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])'
)
text = text.replace(
    'not any(t.get("done") for t in date_tasks)',
    'not any((t.get("done") or t.get("is_done", False) or t.get("status") == "done") for t in date_tasks)'
)

print("Matches modified!")
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(text)
print("Done")
