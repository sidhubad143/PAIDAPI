from fastapi import FastAPI, Form, Query
from fastapi.responses import JSONResponse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from encrypt_like_body import create_like_payload
from get_jwt import create_jwt
import httpx
import binascii
from contextlib import asynccontextmanager

# --------------------------
# 🔹 Lifespan Manager
# --------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # No scheduler or startup/shutdown tasks needed

# --------------------------
# 🔹 FastAPI App Initialize
# --------------------------
app = FastAPI(lifespan=lifespan)

# --------------------------
# 🔹 File Paths
# --------------------------
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")
os.makedirs(usage_dir, exist_ok=True)

# --------------------------
# 🔹 IST Timezone
# --------------------------
IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------
# 🔹 Load usage file
# --------------------------
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# --------------------------
# 🔹 Helper Functions
# --------------------------
def get_current_time():
    return datetime.now(IST)

def get_current_ts():
    return get_current_time().timestamp()

def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0, "last_reset_time": 0}
    if "last_reset_time" not in usage_by_target[target_uid]:
        usage_by_target[target_uid]["last_reset_time"] = 0

def reset_if_needed(target_uid: str):
    ensure_target(target_uid)
    current = get_current_time()
    today_4am = current.replace(hour=4, minute=0, second=0, microsecond=0)
    if current.hour < 4:
        today_4am -= timedelta(days=1)
    last_reset_ts = usage_by_target[target_uid]["last_reset_time"]
    if last_reset_ts < today_4am.timestamp():
        usage_by_target[target_uid]["used_guests"] = {}
        usage_by_target[target_uid]["total_likes"] = 0
        usage_by_target[target_uid]["last_reset_time"] = today_4am.timestamp()

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    return guest_uid in usage_by_target[target_uid]["used_guests"]

def mark_used(target_uid: str, guest_uid: str, ts: float):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

def load_guests():
    if not os.path.exists(guests_file):
        return []
    with open(guests_file, "r") as f:
        return json.load(f)

def get_base_url(server_name: str) -> str:
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        raise ValueError(f"Unsupported server: {server_name}")

# --------------------------
# 🔹 Like Sending Logic (Single)
# --------------------------
async def send_single_like(target_uid: str, server_name: str, guest: dict, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    guest_uid = str(guest["uid"])
    guest_pass = guest.get("password", "")

    if guest_used_for_target(target_uid, guest_uid):
        return False, "Guest already used for this target."

    async with semaphore:
        try:
            jwt, region, _ = await create_jwt(int(guest_uid), guest_pass)
            payload = create_like_payload(target_uid, region)
            if isinstance(payload, str):
                payload = binascii.unhexlify(payload)

            base_url = get_base_url(server_name)
            url = f"{base_url}/LikeProfile"

            headers = {
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/octet-stream",
                "Authorization": f"Bearer {jwt}",
                "X-Unity-Version": "2018.4.11f1",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB50",
            }

            response = await client.post(url, data=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return True, f"Like sent successfully (Status {response.status_code})"

        except Exception as e:
            return False, f"Failed to send like: {str(e)}"

# --------------------------
# 🔹 Batch Like Sending
# --------------------------
async def send_likes_batch(target_uid: str, server_name: str, guests: list):
    reset_if_needed(target_uid)
    total_likes = usage_by_target[target_uid]["total_likes"]
    if total_likes >= 100:
        return {"success": False, "detail": "Daily limit of 100 likes reached. Wait for reset at 4 AM IST."}

    available_guests = [g for g in guests if not guest_used_for_target(target_uid, str(g["uid"]))]
    num_to_send = min(100 - total_likes, len(available_guests))

    if num_to_send == 0:
        return {"success": False, "detail": "No available guests or limit reached."}

    sent = 0
    candidate_guests = available_guests[:num_to_send]
    semaphore = asyncio.Semaphore(100)  # High concurrency for speed

    async with httpx.AsyncClient(http2=True) as client:
        for retry_round in range(3):  # Up to 3 attempts for failed ones
            if sent >= num_to_send or not candidate_guests:
                break

            batch_size = min(len(candidate_guests), num_to_send - sent)
            tasks = [send_single_like(target_uid, server_name, candidate_guests[j], client, semaphore) for j in range(batch_size)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            new_candidates = []
            batch_sent = 0
            current_ts = get_current_ts()
            for j in range(batch_size):
                res = results[j]
                g = candidate_guests[j]
                # Check success: always tuple (bool, str) unless exception (rare)
                if isinstance(res, Exception) or (isinstance(res, tuple) and len(res) == 2 and not res[0]):
                    new_candidates.append(g)
                else:
                    mark_used(target_uid, str(g["uid"]), current_ts)
                    batch_sent += 1

            sent += batch_sent
            candidate_guests = new_candidates

            if batch_sent == 0:
                break  # No progress, stop retrying

    save_usage()
    return {
        "success": True,
        "detail": f"Sent {sent}/{num_to_send} likes successfully"
    }

# --------------------------
# 🔹 API Endpoints
# --------------------------
@app.get("/")
async def root():
    return {"message": "I am alive, please support", "support_link": "https://t.me/PBX_CHAT"}

@app.get("/send-likes")
async def send_likes(uid: str = Query(...), server_name: str = Query("IND")):
    guests = load_guests()
    return await send_likes_batch(uid, server_name, guests)

@app.get("/reset")
async def reset_for_uid(uid: str = Query(...)):
    ensure_target(uid)
    current = get_current_time()
    today_4am = current.replace(hour=4, minute=0, second=0, microsecond=0)
    if current.hour < 4:
        today_4am -= timedelta(days=1)
    reset_time = today_4am.timestamp()
    last = usage_by_target[uid]["last_reset_time"]
    if last < reset_time:
        usage_by_target[uid]["used_guests"] = {}
        usage_by_target[uid]["total_likes"] = 0
        usage_by_target[uid]["last_reset_time"] = reset_time
        save_usage()
        return {"success": True, "detail": f"Daily limit reset for UID {uid}. You can now send likes."}
    else:
        next_reset = today_4am + timedelta(days=1) if current.hour >= 4 else today_4am
        next_reset = next_reset.replace(hour=4, minute=0, second=0, microsecond=0)
        hours_left = (next_reset.timestamp() - current.timestamp()) / 3600
        return {"success": False, "detail": f"Please wait {hours_left:.2f} hours for next reset."}

@app.get("/targets")
async def get_targets():
    return {"usage": usage_by_target}

# --------------------------
# 🔹 Safe Run Guard
# --------------------------
if __name__ == "__main__":
    import sys
    if not any(mod.startswith("uvicorn") or mod.startswith("api") for mod in sys.modules):
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=5000)
