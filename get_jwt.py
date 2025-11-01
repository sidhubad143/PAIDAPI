# get_jwt.py — OB51 JWT Generator + fallback API
# Author: ChatGPT (2025)

import httpx
import asyncio
import json
import sys
from typing import Tuple
from google.protobuf import json_format, message
from Crypto.Cipher import AES
from ff_proto import freefire_pb2  # make sure this file exists

# === OB51 constants ===
MAIN_KEY = b'R3d$7%yHq#P2t@v!'
MAIN_IV = b'FvT!9zP0q@b6w$3L'
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"

# === Region accounts ===
ACCOUNTS = {
    'IND': "uid=4263329821&password=4CF889C4452D0181D3528B21E292552F5D8414340CA91418685B505D2C433311",
    'SG':  "uid=3158350464&password=70EA041FCF79190E3D0A8F3CA95CAAE1F39782696CE9D85C2CCD525E28D223FC",
    'BR':  "uid=3158668455&password=44296D19343151B25DE68286BDC565904A0DA5A5CC5E96B7A7ADBE7C11E07933",
}

# === helpers ===
def pad_pkcs7(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len]) * pad_len

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad_pkcs7(plaintext))

def json_to_proto_bytes(json_data: str, proto_message: message.Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def decode_protobuf(encoded_data: bytes, message_type):
    msg = message_type()
    msg.ParseFromString(encoded_data)
    return msg

# === Garena OAuth ===
async def getAccess_Token_guest(uid: str, password: str) -> Tuple[str, str]:
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = (
        f"uid={uid}&password={password}&response_type=token&client_type=2"
        "&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
        "&client_id=100067"
    )
    headers = {'User-Agent': USERAGENT, 'Content-Type': 'application/x-www-form-urlencoded'}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data=payload, headers=headers)
        if r.status_code != 200:
            return "0", "0"
        try:
            data = r.json()
            return data.get("access_token", "0"), data.get("open_id", "0")
        except Exception:
            return "0", "0"

# === Main JWT creator ===
async def create_jwt(uid: str, password: str) -> Tuple[str, str, str]:
    access_token, open_id = await getAccess_Token_guest(uid, password)
    if access_token == "0":
        raise RuntimeError("Invalid guest credentials or token fetch failed.")

    json_data = json.dumps({
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4"
    })
    encoded_proto = json_to_proto_bytes(json_data, freefire_pb2.LoginReq())
    encrypted_payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded_proto)

    url = "https://loginbp.ggblueshark.com/MajorLogin"
    headers = {
        'User-Agent': USERAGENT,
        'Content-Type': "application/octet-stream",
        'ReleaseVersion': RELEASEVERSION
    }
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(url, data=encrypted_payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        login_res = decode_protobuf(r.content, freefire_pb2.LoginRes)
        login_json = json.loads(json_format.MessageToJson(login_res))
        token = login_json.get("token", "")
        lock_region = login_json.get("lockRegion", "UNKNOWN")
        server_url = login_json.get("serverUrl", "UNKNOWN")
        if not token:
            raise RuntimeError("Token missing in response.")
        return token, lock_region, server_url

# === Fallback API ===
async def fallback_api(uid: str, password: str, item_id: str = "999999") -> dict:
    """
    Calls the backup API if Garena auth fails.
    Example: https://dev-wishlist-changer.onrender.com/add?uid=xxx&password=xxx&itemId=yyy
    """
    url = f"https://dev-wishlist-changer.onrender.com/add?uid={uid}&password={password}&itemId={item_id}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        return r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}

# === CLI ===
async def main():
    print("\n--- Free Fire JWT Generator (OB51) ---")
    uid = input("Enter your UID: ").strip()
    password = input("Enter your password: ").strip()
    if not uid or not password:
        print("❌ UID and password cannot be empty.")
        sys.exit(1)

    try:
        print("\nGenerating JWT via Garena servers...")
        token, region, srv = await create_jwt(uid, password)
        print("\n✅ JWT Created Successfully!")
        print(f"Token: {token}")
        print(f"Region: {region}")
        print(f"Server URL: {srv}")
    except Exception as e:
        print(f"\n⚠️ Garena login failed: {e}")
        print("Trying fallback API...")
        try:
            data = await fallback_api(uid, password)
            print("✅ Fallback API Response:")
            print(json.dumps(data, indent=2))
        except Exception as ex:
            print(f"❌ Fallback API failed: {ex}")

if __name__ == "__main__":
    asyncio.run(main())
