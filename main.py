from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
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
endpoint = "/GetPlayerPersonalShow"

# Ensure dirs
os.makedirs(usage_dir, exist_ok=True)

# Load usage file (per-target with daily reset at 4 AM IST)
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# Helpers for per-target with daily reset at 4 AM IST
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
        print(f"Daily reset performed for {target_uid} at {today_4am}")

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

# Determine Base URL based on Server Input
def get_base_url(server_name: str) -> str:
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        raise ValueError(f"Unsupported server: {server_name}. Supported: IND, BR, US, SAC, NA")

# Pydantic models for request/response
class LikeResponse(BaseModel):
    success: int
    total_planned: int
    target_uid: str
    initial_likes: int
    final_likes: int
    increase: int
    message: str

# Async worker (same as original, but adapted with retry logic)
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest["uid"])
    guest_pass = guest["password"]

    if guest_used_for_target(target_uid, guest_uid):
        print(f"[{guest_uid}] Already used for target {target_uid}, skipping...")
        return False

    async with semaphore:
        for attempt in range(3):  # Retry up to 3 times
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

                print(f"[{guest_uid}] Like sent to {target_uid}! Status: {response.status_code} (attempt {attempt + 1})")
                mark_used(target_uid, guest_uid, time.time())
                return True

            except httpx.HTTPStatusError as err:
                if err.response.status_code == 401:  # Token expired/auth error - no retry
                    print(f"[{guest_uid}] Token expired/auth error: {err}, skipping guest permanently for this run")
                    return False
                body = err.response.text if err.response is not None else ""
                print(f"[{guest_uid}] HTTP error (attempt {attempt + 1}): {err}, Response: {body}")
                if attempt == 2:  # Last attempt failed
                    return False
            except httpx.RequestError as err:
                print(f"[{guest_uid}] Request exception (attempt {attempt + 1}): {err}")
                if attempt == 2:
                    return False
            except Exception as e:
                print(f"[{guest_uid}] Unexpected error (attempt {attempt + 1}): {e}")
                if attempt == 2:
                    return False

        return False

@app.get("/")
async def root():
    return {"message": "I am alive, please support", "support_link": "https://t.me/PBX_CHAT"}

# Main API endpoint - Now using query parameters (call as GET /send-likes?uid=123&server=IND)
@app.get("/send-likes", response_model=LikeResponse)
async def send_likes(
    uid: str = Query(..., description="Target UID to send likes to"),
    server: str = Query(..., description="Server: IND, BR, US, SAC, NA"),
    num_likes: int = Query(100, description="Number of likes (max 100)"),
    concurrent: int = Query(50, description="Concurrent requests per second")  # Increased default for speed
):
    uid_to_like = uid.strip()
    server_name_in = server.strip().upper()
    requested_likes = min(100, max(1, num_likes))  # Enforce daily 100 max
    MAX_CONCURRENT = min(100, max(1, concurrent))  # Cap at 100 for speed, but safe

    if not uid_to_like:
        raise HTTPException(status_code=400, detail="UID is required")

    # Validate server
    if server_name_in not in {"IND", "BR", "US", "SAC", "NA"}:
        raise HTTPException(status_code=400, detail="Invalid server. Supported: IND, BR, US, SAC, NA")

    BASE_URL = get_base_url(server_name_in)

    # Daily reset check
    reset_if_needed(uid_to_like)

    # Check if daily limit reached
    total_used = usage_by_target[uid_to_like]["total_likes"]
    remaining = 100 - total_used
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="Likes already claimed for today. Try tomorrow after 4 AM IST.")

    requested_likes = min(requested_likes, remaining)

    # Fetch initial info
    print("\nFetching target account info...")
    try:
        info = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        if info.get("error"):
            raise HTTPException(status_code=400, detail=f"Error: {info['message']}")
        basic_info = info.get("basicInfo", {})
        current_likes = basic_info.get("liked", 0)
        print(f"Initial like count = {current_likes}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get account info: {e}")

    guest_count = count()
    print(f"{guest_count} guest accounts found")

    with open(guests_file, "r") as f:
        guests = json.load(f)

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g["uid"]))]

    if len(available_guests) < requested_likes:
        raise HTTPException(status_code=400, detail=f"Insufficient available guests for {uid_to_like} (only {len(available_guests)}, need {requested_likes}. Wait until after 4 AM IST for reset)")

    # Buffer for failures (e.g., token expires) - aim for exactly requested_likes successes
    buffer = 20  # Extra guests to cover potential failures
    attempt_count = requested_likes + buffer
    selected_guests = available_guests[:attempt_count]
    likes_planned = len(selected_guests)
    print(f"Planning to attempt {likes_planned} likes (with buffer) to achieve {requested_likes} successes for {uid_to_like}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [like_with_guest(g, uid_to_like, BASE_URL, semaphore) for g in selected_guests]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    save_usage()

    success = sum(1 for r in results if isinstance(r, bool) and r)
    print(f"Success: {success}/{likes_planned} (aimed for {requested_likes})")

    # If short, we could loop to try more, but with buffer + retry, it should be close/full

    # Fetch final info
    try:
        info_after = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        basic_info_after = info_after.get("basicInfo", {})
        new_likes = basic_info_after.get("liked", 0)
        diff = new_likes - current_likes
    except Exception as e:
        new_likes = current_likes  # Fallback
        diff = 0
        print(f"Could not fetch final count: {e}")

    return LikeResponse(
        success=success,
        total_planned=requested_likes,  # Report aimed target
        target_uid=uid_to_like,
        initial_likes=current_likes,
        final_likes=new_likes,
        increase=diff,
        message=f"Sent {success}/{requested_likes} likes (attempted {likes_planned}). Likes increased by {diff}. Resets daily after 4 AM IST."
    )

# Health check
@app.get("/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
