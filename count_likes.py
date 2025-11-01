# Protective Source License v1.0 (PSL-1.0)
# Copyright (c) 2025 Kaif
# Unauthorized removal of credits or use for abusive/illegal purposes
# will terminate all rights granted under this license.

# Original author: 0xMe
# GitHub: https://github.com/0xMe/FreeFire-Api
# Modifications and fixes by kaifcodec & sukhwinder (2025)

import httpx
import asyncio
import json
import base64
import sys
from typing import Tuple
from Crypto.Cipher import AES
from google.protobuf import json_format, message
from google.protobuf.message import Message
from ff_proto import freefire_pb2, core_pb2, account_show_pb2

# --- Constants ---
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"
SUPPORTED_REGIONS = ["IND", "BR", "SG", "RU", "ID", "TW", "US", "VN", "TH", "ME", "PK", "CIS"]

# --- Account Mapping ---
ACCOUNTS = {
    'IND': "uid=4104125669&password=E5655A0D14EF812A908726152BDD38021BEF528801AA42B16CFA4ED67141C4CA",
    'SG': "uid=3158350464&password=70EA041FCF79190E3D0A8F3CA95CAAE1F39782696CE9D85C2CCD525E28D223FC",
    'RU': "uid=3301239795&password=DD40EE772FCBD61409BB15033E3DE1B1C54EDA83B75DF0CDD24C34C7C8798475",
    'ID': "uid=3301269321&password=D11732AC9BBED0DED65D0FED7728CA8DFF408E174202ECF1939E328EA3E94356",
    'TW': "uid=3301329477&password=359FB179CD92C9C1A2A917293666B96972EF8A5FC43B5D9D61A2434DD3D7D0BC",
    'US': "uid=3301387397&password=BAC03CCF677F8772473A09870B6228ADFBC1F503BF59C8D05746DE451AD67128",
    'VN': "uid=3301447047&password=044714F5B9284F3661FB09E4E9833327488B45255EC9E0CCD953050E3DEF1F54",
    'TH': "uid=3301470613&password=39EFD9979BD6E9CCF6CBFF09F224C4B663E88B7093657CB3D4A6F3615DDE057A",
    'ME': "uid=3301535568&password=BEC9F99733AC7B1FB139DB3803F90A7E78757B0BE395E0A6FE3A520AF77E0517",
    'PK': "uid=3301828218&password=3A0E972E57E9EDC39DC4830E3D486DBFB5DA7C52A4E8B0B8F3F9DC4450899571",
    'CIS': "uid=3309128798&password=412F68B618A8FAEDCCE289121AC4695C0046D2E45DB07EE512B4B3516DDA8B0F",
    'BR': "uid=3158668455&password=44296D19343151B25DE68286BDC565904A0DA5A5CC5E96B7A7ADBE7C11E07933"
}

# --- Utils ---
def pad(text: bytes) -> bytes:
    pad_len = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([pad_len] * pad_len)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

async def json_to_proto(json_data: str, proto_message: Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def decode_protobuf(encoded_data: bytes, message_type: message.Message):
    """Decodes protobuf safely with JSON fallback"""
    try:
        instance = message_type()
        instance.ParseFromString(encoded_data)
        return instance
    except Exception:
        try:
            return json.loads(encoded_data.decode("utf-8"))
        except Exception as e:
            return {"error": f"Failed to decode response: {str(e)}"}

# --- Network Functions ---
async def safe_post(url, data, headers, retries=3):
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, data=data, headers=headers)
                response.raise_for_status()
                return response
        except (httpx.RequestError, httpx.TimeoutException) as e:
            if attempt < retries - 1:
                await asyncio.sleep(2)
                continue
            raise Exception(f"Request failed after {retries} retries: {e}")

async def getAccess_Token(account):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = (
        account
        + "&response_type=token&client_type=2"
        + "&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
        + "&client_id=100067"
    )
    headers = {
        "User-Agent": USERAGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip",
    }
    response = await safe_post(url, data=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {}
    return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(region: str) -> Tuple[str, str, str]:
    account = ACCOUNTS.get(region)
    if not account:
        raise ValueError(f"Region '{region}' not supported.")

    access_token, open_id = await getAccess_Token(account)
    json_data = json.dumps({
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4"
    })

    encoded = await json_to_proto(json_data, freefire_pb2.LoginReq())
    payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded)

    headers = {
        "User-Agent": USERAGENT,
        "Content-Type": "application/octet-stream",
        "ReleaseVersion": RELEASEVERSION,
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1"
    }
    response = await safe_post("https://loginbp.ggblueshark.com/MajorLogin", data=payload, headers=headers)

    decoded = decode_protobuf(response.content, freefire_pb2.LoginRes)
    if isinstance(decoded, dict):
        token = decoded.get("token", "0")
        region_resp = decoded.get("lockRegion", region)
        server_url = decoded.get("serverUrl", "")
    else:
        token_json = json.loads(json_format.MessageToJson(decoded))
        token = token_json.get("token", "0")
        region_resp = token_json.get("lockRegion", region)
        server_url = token_json.get("serverUrl", "")

    return f"Bearer {token}", region_resp, server_url

async def GetAccountInformation(ID, UNKNOWN_ID, regionMain, endpoint):
    regionMain = regionMain.upper()
    if regionMain not in SUPPORTED_REGIONS:
        return {"error": "Invalid request", "message": f"Supported regions: {', '.join(SUPPORTED_REGIONS)}"}

    json_data = json.dumps({"a": ID, "b": UNKNOWN_ID})
    encoded = await json_to_proto(json_data, core_pb2.GetPlayerPersonalShow())
    payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded)
    token, region, server_url = await create_jwt(regionMain)

    headers = {
        "User-Agent": USERAGENT,
        "Authorization": token,
        "Content-Type": "application/octet-stream",
        "ReleaseVersion": RELEASEVERSION,
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1"
    }
    response = await safe_post(server_url + endpoint, data=payload, headers=headers)
    decoded = decode_protobuf(response.content, account_show_pb2.AccountPersonalShowInfo)
    if isinstance(decoded, dict):
        return decoded
    return json.loads(json_format.MessageToJson(decoded))

# --- CLI Main Program ---
async def main():
    while True:
        print("\n--- Free Fire API Tool ---")
        print("1. Create JWT")
        print("2. Get Account Information")
        print("3. Exit")
        choice = input("Enter your choice (1-3): ").strip()

        if choice == "1":
            region = input(f"Enter region ({', '.join(SUPPORTED_REGIONS)}): ").upper()
            try:
                token, lock_region, server_url = await create_jwt(region)
                print("\n✅ JWT Created Successfully:")
                print(f"Token: {token}")
                print(f"Locked Region: {lock_region}")
                print(f"Server URL: {server_url}")
            except Exception as e:
                print(f"❌ Error creating JWT: {e}")

        elif choice == "2":
            region = input(f"Enter region ({', '.join(SUPPORTED_REGIONS)}): ").upper()
            player_id = input("Enter Player ID: ")
            unknown_id = input("Enter UNKNOWN_ID (default 0): ") or "0"
            try:
                info = await GetAccountInformation(player_id, unknown_id, region, "/GetPlayerPersonalShow")
                print("\n✅ Account Info:")
                print(json.dumps(info, indent=4))
            except Exception as e:
                print(f"❌ Error fetching info: {e}")

        elif choice == "3":
            print("👋 Exiting...")
            sys.exit(0)
        else:
            print("⚠️ Invalid choice, please select 1-3.")

if __name__ == "__main__":
    asyncio.run(main())
