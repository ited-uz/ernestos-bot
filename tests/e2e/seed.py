"""Seed a throwaway database for the browser checks and write signed initData.

Usage: python tests/e2e/seed.py <initdata-out-file>  (DATABASE_URL, BOT_TOKEN set)
"""
import os, sys, json, time, hmac, hashlib  # noqa: E401
from urllib.parse import urlencode
from datetime import timedelta
sys.path.insert(0, os.getcwd())
import db, services as svc  # noqa: E401,E402
db.init_db()
UID = 777001
with db.SessionLocal() as s:
    svc.get_or_create_user(s, UID, first_name="Ernest")
    u = s.get(db.User, UID); u.onboarded = True; u.language = "uz"; s.commit()
    ws = svc.workspace_id_for(s, UID)
    today = svc.today_local()
    svc.add_habit(s, ws, "Kitob o'qish")
    svc.add_habit(s, ws, "Sport")
    svc.add_task(s, ws, "Eski hisobot", deadline=today - timedelta(days=3), priority="high")
    svc.add_task(s, ws, "Eski xat", deadline=today - timedelta(days=2))
    svc.add_task(s, ws, "Maqola", deadline=today, timer_minutes=60)
    svc.add_debt(s, ws, "Aziz", 500000, "lent")
token = os.environ["BOT_TOKEN"]
fields = {"user": json.dumps({"id": UID, "first_name": "Ernest"}, separators=(",", ":")),
          "auth_date": str(int(time.time()))}
check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
open(sys.argv[1], "w").write(urlencode(fields))
print("seeded")
