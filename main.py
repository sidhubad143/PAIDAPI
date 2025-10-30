from fastapi import FastAPI, Form, Query
from fastapi.responses import JSONResponse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from encrypt_like_body import create_like_payload
from get_jwt import create_jwt
from send_like import get_base_url
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
def get_current_ms():
    return int(datetime.now(IST).timestamp() * 1000)

def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0, "last_reset_time": 0}
    if "last_reset_time" not in usage_by_target[target_uid]:
        usage_by_target[target_uid]["last_reset_time"] = 0

def needs_reset(target_uid: str) -> bool:
    ensure_target(target_uid)
    current = get_current_ms()
    return current - usage_by_target[target_uid]["last_reset_time"] > 25 * 3600 * 1000

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    return guest_uid in usage_by_target[target_uid]["used_guests"]

def mark_used(target_uid: str, guest_uid: str, ts_ms: int):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts_ms
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

def load_guests():
    if not os.path.exists(guests_file):
        return []
    with open(guests_file, "r") as f:
        return json.load(f)

# --------------------------
# 🔹 Like Sending Logic
# --------------------------
async def send_single_like(target_uid: str, server_name: str, guest: dict, client: httpx.AsyncClient):
    guest_uid = str(guest["uid"])
    guest_pass = guest.get("password", "")

    if guest_used_for_target(target_uid, guest_uid):
        return False, "Guest already used for this target."

    try:
        jwt, region, _ = await create_jwt(guest_uid, guest_pass)
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
async def send_likes_batch(target_uid: str, server_name: str, guests: list, max_likes: int):
    ensure_target(target_uid)
    current = get_current_ms()
    if needs_reset(target_uid):
        usage_by_target[target_uid]["used_guests"] = {}
        usage_by_target[target_uid]["total_likes"] = 0
        usage_by_target[target_uid]["last_reset_time"] = current

    if usage_by_target[target_uid]["total_likes"] >= max_likes:
        return {"success": False, "detail": "Limit of 100 likes reached. Please try after 25 hours."}

    available_guests = [g for g in guests if not guest_used_for_target(target_uid, str(g["uid"]))]
    original_num_to_send = min(max_likes - usage_by_target[target_uid]["total_likes"], len(available_guests))

    if original_num_to_send == 0:
        return {"success": False, "detail": "No available guests to send likes or limit reached."}

    sent = 0
    candidate_guests = available_guests[:original_num_to_send]

    async with httpx.AsyncClient(http2=True) as client:
        for retry_round in range(3):  # 3 attempts total (initial + 2 retries)
            if sent >= original_num_to_send or not candidate_guests:
                break

            batch_size = min(len(candidate_guests), original_num_to_send - sent)
            tasks = [send_single_like(target_uid, server_name, candidate_guests[j], client) for j in range(batch_size)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            new_candidates = []
            batch_sent = 0
            for j in range(batch_size):
                res = results[j]
                g = candidate_guests[j]
                # Check success: always tuple (bool, str) unless exception (rare)
                if isinstance(res, Exception) or (isinstance(res, tuple) and len(res) == 2 and not res[0]):
                    new_candidates.append(g)
                else:
                    mark_used(target_uid, str(g["uid"]), current)
                    batch_sent += 1

            sent += batch_sent
            candidate_guests = new_candidates

            if batch_sent == 0:
                break  # No progress, stop retrying

    failed = original_num_to_send - sent

    # Update last_reset_time only if likes were sent
    if sent > 0:
        usage_by_target[target_uid]["last_reset_time"] = get_current_ms()

    save_usage()
    return {
        "success": True,
        "detail": f"Sent {sent}/{original_num_to_send} likes, failed: {failed}"
    }

# --------------------------
# 🔹 API Endpoints
# --------------------------
@app.get("/")
async def root():
    return {"message": "I am alive, please support", "support_link": "https://t.me/PBX_CHAT"}

@app.get("/like")
async def send_likes(uid: str = Query(...), server_name: str = Query("IND")):
    guests = load_guests()
    return await send_likes_batch(uid, server_name, guests, max_likes=100)

@app.get("/profile")
async def get_profile(uid: str = Query(...), region: str = Query("IND")):
    try:
        url = f"http://47.129.201.23:5000/profile/{uid}?region={region}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "data": data}
            else:
                return {"success": False, "detail": f"Failed to fetch profile (Status {response.status_code})"}
    except Exception as e:
        return {"success": False, "detail": f"Network error: {str(e)}"}

@app.get("/reset")
async def reset_for_uid(uid: str = Query(...)):
    ensure_target(uid)
    current = get_current_ms()
    if current - usage_by_target[uid]["last_reset_time"] > 25 * 3600 * 1000:
        usage_by_target[uid]["used_guests"] = {}
        usage_by_target[uid]["total_likes"] = 0
        usage_by_target[uid]["last_reset_time"] = current
        save_usage()
        return {"success": True, "detail": f"Daily limit reset for UID {uid}. You can now send likes."}
    else:
        hours_left = (25 * 3600 * 1000 - (current - usage_by_target[uid]["last_reset_time"])) / 3600000
        return {"success": False, "detail": f"Please wait {hours_left:.2f} hours before resetting."}

@app.post("/send_single_like")
async def send_single_like_endpoint(target_uid: str = Form(...), server_name: str = Form("IND")):
    ensure_target(target_uid)
    if needs_reset(target_uid):
        usage_by_target[target_uid]["used_guests"] = {}
        usage_by_target[target_uid]["total_likes"] = 0
        usage_by_target[target_uid]["last_reset_time"] = get_current_ms()

    if usage_by_target[target_uid]["total_likes"] >= 100:
        return {"success": False, "detail": "Limit of 100 likes reached. Please try after 25 hours."}

    guests = load_guests()
    available = [g for g in guests if not guest_used_for_target(target_uid, str(g["uid"]))]
    if not available:
        return {"success": False, "detail": "No available guests to send likes or limit reached."}

    async with httpx.AsyncClient(http2=True) as client:
        success, detail = await send_single_like(target_uid, server_name, available[0], client)
        if success:
            mark_used(target_uid, str(available[0]["uid"]), get_current_ms())
            save_usage()
        return {"success": success, "detail": detail}

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
