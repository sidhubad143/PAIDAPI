from fastapi import FastAPI, HTTPException, Query
import httpx
import asyncio
import binascii
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from pydantic import BaseModel
import uvicorn
from get_jwt import create_jwt
from encrypt_like_body import create_like_payload
from count_likes import GetAccountInformation
from guests_manager.count_guest import count

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

def get_current_time():
    return datetime.now(IST)

# FastAPI app
app = FastAPI(title="FF Like Sender API", version="1.0.0")

# Paths
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")

# Ensure dirs
os.makedirs(usage_dir, exist_ok=True)

# Load usage file
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# Helpers
def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0, "last_reset_time": 0}

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

# Determine Base URL
def get_base_url(server_name: str) -> str:
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        raise ValueError(f"Unsupported server: {server_name}")

# Simplified Pydantic model
class LikeResponse(BaseModel):
    target_uid: str
    likes_sent: int
    message: str

# Async worker
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest["uid"])
    guest_pass = guest["password"]

    if guest_used_for_target(target_uid, guest_uid):
        return False

    async with semaphore:
        for attempt in range(3):
            try:
                jwt, region, server_url_from_jwt = await create_jwt(guest_uid, guest_pass)
                payload = create_like_payload(target_uid, region)
                if isinstance(payload, str):
                    payload = binascii.unhexlify(payload)

                headers = {
                    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)",
                    "Connection": "Keep-Alive",
                    "Accept-Encoding": "gzip",
                    "Content-Type": "application/octet-stream",
                    "Expect": "100-continue",
                    "Authorization": f"Bearer {jwt}",
                    "X-Unity-Version": "2018.4.11f1",
                    "X-GA": "v1 1",
                    "ReleaseVersion": "OB50",
                }

                async with httpx.AsyncClient() as client:
                    url = f"{BASE_URL}/LikeProfile"
                    response = await client.post(url, data=payload, headers=headers, timeout=30)
                    response.raise_for_status()

                mark_used(target_uid, guest_uid, time.time())
                return True

            except httpx.HTTPStatusError as err:
                if err.response.status_code == 401:
                    return False
                if attempt == 2:
                    return False
            except Exception:
                if attempt == 2:
                    return False

        return False

@app.get("/")
async def root():
    return {"message": "I am alive"}

@app.get("/send-likes", response_model=LikeResponse)
async def send_likes(
    uid: str = Query(..., description="Target UID"),
    server: str = Query(..., description="Server: IND, BR, US, SAC, NA"),
    num_likes: int = Query(100, description="Number of likes (max 100)"),
    concurrent: int = Query(100, description="Concurrent requests")  # Increased for speed
):
    uid_to_like = uid.strip()
    server_name_in = server.strip().upper()
    requested_likes = min(100, max(1, num_likes))
    MAX_CONCURRENT = min(100, max(1, concurrent))

    if not uid_to_like:
        raise HTTPException(status_code=400, detail="UID is required")

    if server_name_in not in {"IND", "BR", "US", "SAC", "NA"}:
        raise HTTPException(status_code=400, detail="Invalid server")

    BASE_URL = get_base_url(server_name_in)

    # Daily reset
    reset_if_needed(uid_to_like)

    # Check limit
    total_used = usage_by_target[uid_to_like]["total_likes"]
    remaining = 100 - total_used
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="your like already claim")

    requested_likes = min(requested_likes, remaining)

    # Load guests
    with open(guests_file, "r") as f:
        guests = json.load(f)

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g["uid"]))]

    if len(available_guests) < requested_likes:
        raise HTTPException(status_code=400, detail="Insufficient guests")

    # No buffer, direct attempt for speed
    selected_guests = available_guests[:requested_likes]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [like_with_guest(g, uid_to_like, BASE_URL, semaphore) for g in selected_guests]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    save_usage()

    success = sum(1 for r in results if isinstance(r, bool) and r)

    return LikeResponse(
        target_uid=uid_to_like,
        likes_sent=success,
        message=f"Likes sent to {uid_to_like}: {success}/{requested_likes}"
    )

@app.get("/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
