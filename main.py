from fastapi import FastAPI, HTTPException, Query
import httpx
import asyncio
import binascii
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any
from pydantic import BaseModel
from get_jwt import create_jwt
from encrypt_like_body import create_like_payload
from count_likes import GetAccountInformation
from guests_manager.count_guest import count

# ---------------------- #
# FastAPI app setup
# ---------------------- #
app = FastAPI(title="FF Like Sender API", version="1.0.0")

# Paths and constants
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")
endpoint = "/GetPlayerPersonalShow"

# Ensure directory exists
os.makedirs(usage_dir, exist_ok=True)

# Load existing usage data
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# ---------------------- #
# Utility Functions
# ---------------------- #
def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0}

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    used_guests = usage_by_target[target_uid]["used_guests"]
    now_ts = time.time()

    # Clean expired (older than 24h)
    expired_keys = [k for k, v in used_guests.items() if now_ts - v > 86400]
    for key in expired_keys:
        del used_guests[key]

    usage_by_target[target_uid]["total_likes"] = len(used_guests)
    return guest_uid in used_guests

def mark_used(target_uid: str, guest_uid: str, ts: float):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

def get_base_url(server_name: str) -> str:
    server_name = server_name.upper()
    base_urls = {
        "IND": "https://client.ind.freefiremobile.com",
        "BR": "https://client.us.freefiremobile.com",
        "US": "https://client.us.freefiremobile.com",
        "SAC": "https://client.us.freefiremobile.com",
        "NA": "https://client.us.freefiremobile.com",
    }
    if server_name not in base_urls:
        raise ValueError("Unsupported server. Supported: IND, BR, US, SAC, NA")
    return base_urls[server_name]

# ---------------------- #
# Models
# ---------------------- #
class LikeResponse(BaseModel):
    success: int
    total_planned: int
    target_uid: str
    initial_likes: int
    final_likes: int
    increase: int
    message: str

# ---------------------- #
# Async worker
# ---------------------- #
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest["uid"])
    guest_pass = guest["password"]

    if guest_used_for_target(target_uid, guest_uid):
        print(f"[{guest_uid}] Already used within 24h for {target_uid}, skipping...")
        return False

    async with semaphore:
        try:
            jwt, region, _ = await create_jwt(guest_uid, guest_pass)
            payload = create_like_payload(target_uid, region)
            if isinstance(payload, str):
                payload = binascii.unhexlify(payload)

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

            async with httpx.AsyncClient() as client:
                response = await client.post(f"{BASE_URL}/LikeProfile", data=payload, headers=headers, timeout=30)
                response.raise_for_status()

            print(f"[{guest_uid}] Sent like to {target_uid} ✅")
            mark_used(target_uid, guest_uid, time.time())
            return True

        except Exception as e:
            print(f"[{guest_uid}] Error: {e}")
            return False

# ---------------------- #
# Main Endpoint
# ---------------------- #
@app.get("/send-likes", response_model=LikeResponse)
async def send_likes(
    uid: str = Query(..., description="Target UID"),
    server: str = Query(..., description="Server (IND, BR, US, SAC, NA)"),
    num_likes: int = Query(100, description="Number of likes (max 100)"),
    concurrent: int = Query(20, description="Concurrent limit (1–50)"),
):
    uid_to_like = uid.strip()
    server_name = server.strip().upper()
    requested_likes = min(100, max(1, num_likes))
    MAX_CONCURRENT = min(50, max(1, concurrent))

    if not uid_to_like:
        raise HTTPException(status_code=400, detail="UID required")

    BASE_URL = get_base_url(server_name)

    # Get target info
    try:
        info = await GetAccountInformation(uid_to_like, "0", server_name, endpoint)
        if info.get("error"):
            raise HTTPException(status_code=400, detail=info["message"])
        current_likes = info.get("basicInfo", {}).get("liked", 0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching account info: {e}")

    guest_count = count()
    print(f"{guest_count} guests found")

    with open(guests_file, "r") as f:
        guests = json.load(f)

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g["uid"]))]
    if not available_guests:
        raise HTTPException(status_code=400, detail="No available guests. Wait 24h reset.")

    likes_planned = min(requested_likes, len(available_guests))
    print(f"Sending {likes_planned} likes to {uid_to_like}...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [like_with_guest(g, uid_to_like, BASE_URL, semaphore) for g in available_guests[:likes_planned]]
    results = await asyncio.gather(*tasks)
    save_usage()

    success = sum(1 for r in results if r)
    print(f"Success: {success}/{likes_planned}")

    try:
        info_after = await GetAccountInformation(uid_to_like, "0", server_name, endpoint)
        new_likes = info_after.get("basicInfo", {}).get("liked", 0)
        diff = new_likes - current_likes
    except Exception:
        new_likes, diff = current_likes, 0

    return LikeResponse(
        success=success,
        total_planned=likes_planned,
        target_uid=uid_to_like,
        initial_likes=current_likes,
        final_likes=new_likes,
        increase=diff,
        message=f"Sent {success}/{likes_planned} likes. Likes increased by {diff}. Resets in 24h.",
    )

# ---------------------- #
# Health Check
# ---------------------- #
@app.get("/health")
def health():
    return {"status": "healthy"}

# ---------------------- #
# Local Dev Run (ignored on Vercel)
# ---------------------- #
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
