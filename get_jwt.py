import httpx
import asyncio
import json
import base64
import sys
from typing import Tuple
from google.protobuf import json_format, message
from Crypto.Cipher import AES

from ff_proto import freefire_pb2

# --- Modify these if changed in OB51 ---
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')   # check if key changed in OB51
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')    # check IV too
RELEASEVERSION = "OB51"  # changed from OB50
USERAGENT = ("Dalvik/2.1.0 (Linux; U; Android 13; "
             "CPH2095 Build/RKQ1.211119.001)")
SUPPORTED_REGIONS = ["IND", "BR", "SG", "RU", "ID", "TW", "US", "VN", "TH", "ME", "PK", "CIS"]

async def json_to_proto(json_data: str, proto_message: message.Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(plaintext)
    return aes.encrypt(padded)

def decode_protobuf(encoded_data: bytes, message_type: message.Message) -> message.Message:
    inst = message_type()
    inst.ParseFromString(encoded_data)
    return inst

async def get_access_token(uid: str, password: str) -> Tuple[str, str]:
    """
    Modify URL / client_id / client_secret for OB51 if changed.
    """
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": "2",
        # You must replace these if OB51 updated them:
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload, headers=headers)
        # Debug prints
        print("DEBUG — status code:", response.status_code)
        print("DEBUG — response headers:", response.headers)
        print("DEBUG — response body:", response.text)
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise ValueError(f"Response not JSON: {response.text}")
        access_token = data.get("access_token")
        open_id = data.get("open_id")
        return access_token or "", open_id or ""

async def create_jwt(uid: str, password: str) -> Tuple[str, str, str]:
    access_token, open_id = await get_access_token(uid, password)
    if not access_token:
        raise ValueError(f"Failed to obtain access token. open_id={open_id!r}")

    login_req = {
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4"
    }
    json_data = json.dumps(login_req)
    proto_bytes = await json_to_proto(json_data, freefire_pb2.LoginReq())
    encrypted = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, proto_bytes)

    jwt_url = "https://loginbp.ggblueshark.com/MajorLogin"
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
    async with httpx.AsyncClient() as client:
        resp = await client.post(jwt_url, data=encrypted, headers=headers)
        print("DEBUG — JWT request status:", resp.status_code)
        print("DEBUG — JWT response body (raw bytes):", resp.content[:200], "...")  # first bytes
        res_msg = decode_protobuf(resp.content, freefire_pb2.LoginRes)
        res_dict = json.loads(json_format.MessageToJson(res_msg))
        token = res_dict.get("token")
        region = res_dict.get("lockRegion")
        server_url = res_dict.get("serverUrl")
        if not token:
            raise ValueError(f"Failed to obtain JWT, resp: {res_dict!r}")
        return token, region, server_url

async def main():
    print("--- Free Fire JWT Generator (OB51 template) ---")
    uid = input("Enter your UID: ").strip()
    password = input("Enter your password: ").strip()
    if not uid or not password:
        print("UID / password empty")
        sys.exit(1)
    try:
        print("Generating JWT...")
        token, region, server_url = await create_jwt(uid, password)
        print("\n--- JWT Created Successfully ---")
        print("Token:", token)
        print("Region:", region)
        print("Server URL:", server_url)
    except Exception as ex:
        print("An error occurred:", ex)

if __name__ == "__main__":
    asyncio.run(main())
