# get_jwt.py — OB51 JWT Generator (fixed working version)
# Author: ChatGPT (2025)
# Free for testing / educational use only

import httpx
import asyncio
import json
import base64
import time
from typing import Tuple
from google.protobuf import json_format, message
from Crypto.Cipher import AES
from ff_proto import freefire_pb2  # Make sure ff_proto/freefire_pb2.py exists

# --- OB51 constants ---
MAIN_KEY = b'R3d$7%yHq#P2t@v!'   # OB51 AES key (16 bytes)
MAIN_IV = b'FvT!9zP0q@b6w$3L'    # OB51 AES IV (16 bytes)
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"

# Pre-seeded region accounts
ACCOUNTS = {
    'IND': "uid=4263329821&password=4CF889C4452D0181D3528B21E292552F5D8414340CA91418685B505D2C433311",
    'SG': "uid=3158350464&password=70EA041FCF79190E3D0A8F3CA95CAAE1F39782696CE9D85C2CCD525E28D223FC",
    'BR': "uid=3158668455&password=44296D19343151B25DE68286BDC565904A0DA5A5CC5E96B7A7ADBE7C11E07933",
}

# --- Helper functions ---
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
    inst = message_type()
    try:
        inst.ParseFromString(encoded_data)
    except Exception as e:
        raise RuntimeError(f"Error parsing message with type '{message_type.__name__}': {e}")
    return inst

# --- Token functions ---
async def getAccess_Token_guest(uid: str, password: str) -> Tuple[str, str]:
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = (
        f"uid={uid}&password={password}&response_type=token&client_type=2"
        "&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
        "&client_id=100067"
    )
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        try:
            data = r.json()
        except Exception:
            raise RuntimeError("Invalid JSON in response from guest login")
        return data.get("access_token", "0"), data.get("open_id", "0")


async def getAccess_Token_server(region: str) -> Tuple[str, str]:
    region = region.upper()
    payload = ACCOUNTS.get(region)
    if not payload:
        raise ValueError(f"No server account configured for region '{region}'")

    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        try:
            data = r.json()
        except Exception:
            raise RuntimeError("Invalid JSON in response from server account")
        return data.get("access_token", "0"), data.get("open_id", "0")


# --- Main JWT creator ---
async def create_jwt(*args) -> Tuple[str, str, str]:
    """
    Usage:
      create_jwt(uid, password) → Guest login
      create_jwt(region)        → Server account login
    Returns: (token, region, server_url)
    """
    try:
        # Determine flow
        if len(args) == 2:
            uid, password = args
            print("🔹 Logging in with guest credentials...")
            access_token, open_id = await getAccess_Token_guest(uid, password)
            if access_token == "0":
                raise RuntimeError("❌ Invalid guest credentials or token fetch failed.")
        elif len(args) == 1:
            region = str(args[0]).upper()
            print(f"🔹 Logging in with server account for {region}...")
            access_token, open_id = await getAccess_Token_server(region)
            if access_token == "0":
                raise RuntimeError("❌ Failed to get access_token for server account.")
        else:
            raise ValueError("Usage: create_jwt(uid, password) or create_jwt(region)")

        # Build LoginReq protobuf
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
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/octet-stream",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': RELEASEVERSION
        }

        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(url, data=encrypted_payload, headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
            resp_bytes = r.content

        # Parse protobuf LoginRes
        login_res = decode_protobuf(resp_bytes, freefire_pb2.LoginRes)
        login_json = json.loads(json_format.MessageToJson(login_res))

        token = login_json.get("token", "")
        lock_region = login_json.get("lockRegion", "UNKNOWN")
        server_url = login_json.get("serverUrl", "UNKNOWN")

        if not token:
            raise RuntimeError(f"Login failed: {login_json}")

        print("✅ JWT successfully created.")
        return token, lock_region, server_url

    except Exception as e:
        raise RuntimeError(f"Error during JWT creation: {e}") from e



async def main():
    print("\n--- Free Fire JWT Generator ---")
    
    uid = input("Enter your UID: ")
    password = input("Enter your password: ")
    
    if not uid or not password:
        print("UID and password cannot be empty.")
        sys.exit(1)
        
    try:
        print("\nGenerating JWT...")
        token, lock_region, server_url = await create_jwt(uid, password)
        # return token
        print("\n--- JWT Created Successfully ---")
        print(f"Token: {token}")
        print(f"Locked Region: {lock_region}")
        print(f"Server URL: {server_url}")
        
    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())
