# get_jwt.py — Free Fire OB51 JWT Generator
# Author: ChatGPT (2025)
# Tested with Python 3.10+

import httpx
import asyncio
import json
import base64
import sys
from typing import Tuple
from google.protobuf import json_format, message
from Crypto.Cipher import AES
from ff_proto import freefire_pb2  # make sure ff_proto/freefire_pb2.py exists

# ========== OB51 CONSTANTS ==========
MAIN_KEY = base64.b64decode('UjNkJDcleUhxI1AydEB2IQ==')  # b'R3d$7%yHq#P2t@v!'
MAIN_IV = base64.b64decode('RnZUIjl6UDAxcUBiNnckM0w=')   # b'FvT!9zP0q@b6w$3L'
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"
OAUTH_URL = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
LOGIN_URL = "https://loginbp.ggblueshark.com/MajorLogin"


# ========== AES + PROTO HELPERS ==========
def pad_pkcs7(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len]) * pad_len

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad_pkcs7(plaintext))

async def json_to_proto(json_data: str, proto_message: message.Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def decode_protobuf(encoded_data: bytes, message_type):
    inst = message_type()
    inst.ParseFromString(encoded_data)
    return inst


# ========== ACCESS TOKEN FETCH ==========
async def getAccess_Token(uid: str, password: str):
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
        r = await client.post(OAUTH_URL, data=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"OAUTH HTTP {r.status_code}: {r.text}")
        try:
            data = r.json()
        except Exception:
            raise RuntimeError("Invalid JSON in token response")

        access_token = data.get("access_token", "0")
        open_id = data.get("open_id", "0")
        if access_token == "0":
            raise ValueError("Invalid guest credentials or access_token not returned.")
        return access_token, open_id


# ========== JWT CREATOR ==========
async def create_jwt(uid: str, password: str) -> Tuple[str, str, str]:
    access_token, open_id = await getAccess_Token(uid, password)

    json_data = json.dumps({
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4"
    })

    encoded_proto = await json_to_proto(json_data, freefire_pb2.LoginReq())
    encrypted_payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded_proto)

    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream",
        'Expect': "100-continue",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': RELEASEVERSION
    }

    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(LOGIN_URL, data=encrypted_payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"MajorLogin HTTP {r.status_code}: {r.text}")
        resp_bytes = r.content

    try:
        login_res = decode_protobuf(resp_bytes, freefire_pb2.LoginRes)
        login_json = json.loads(json_format.MessageToJson(login_res))
    except Exception as e:
        raise RuntimeError(f"Error parsing LoginRes: {e}")

    token = login_json.get("token", "")
    region = login_json.get("lockRegion", "UNKNOWN")
    server_url = login_json.get("serverUrl", "UNKNOWN")

    if not token:
        raise ValueError(f"Failed to obtain JWT. Response: {login_json}")

    return token, region, server_url


# ========== MAIN EXECUTION ==========
async def main():
    print("\n--- Free Fire JWT Generator (OB51) ---")

    uid = input("Enter your UID: ").strip()
    password = input("Enter your password: ").strip()

    if not uid or not password:
        print("UID and password cannot be empty.")
        sys.exit(1)

    try:
        print("\nGenerating JWT...")
        token, region, server_url = await create_jwt(uid, password)

        print("\n--- JWT Created Successfully ---")
        print(f"Token: {token}")
        print(f"Region: {region}")
        print(f"Server URL: {server_url}")

    except Exception as e:
        print(f"\n❌ An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())
