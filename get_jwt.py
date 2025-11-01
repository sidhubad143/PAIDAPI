import httpx
import asyncio
import json
import base64
import sys
from typing import Tuple
from google.protobuf import json_format, message
from Crypto.Cipher import AES

# IMPORTANT: This script requires 'freefire_pb2.py' to be in the same directory.
from ff_proto import freefire_pb2

# --- Global Constants (Updated for OB51)
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB51"  # Updated version
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
SUPPORTED_REGIONS = ["IND", "BR", "SG", "RU", "ID", "TW", "US", "VN", "TH", "ME", "PK", "CIS"]

# Updated API endpoints for OB51
LOGIN_URL = "https://loginbp.ggblueshark.com/MajorLogin"
OAUTH_URL = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"

# Client credentials (may need updates for OB51)
CLIENT_ID = "100067"
CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
CLIENT_TYPE = "2"

# --- Helper Functions
async def json_to_proto(json_data: str, proto_message: message.Message) -> bytes:
    """Convert JSON data to Protocol Buffer format"""
    json_format.ParseDict(json.loads(json_data), proto_message)
    serialized_data = proto_message.SerializeToString()
    return serialized_data

def pad(text: bytes) -> bytes:
    """Apply PKCS7 padding to the text"""
    padding_length = AES.block_size - (len(text) % AES.block_size)
    padding = bytes([padding_length] * padding_length)
    return text + padding

def unpad(text: bytes) -> bytes:
    """Remove PKCS7 padding from the text"""
    padding_length = text[-1]
    return text[:-padding_length]

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """Encrypt data using AES CBC mode"""
    aes = AES.new(key, AES.MODE_CBC, iv)
    padded_plaintext = pad(plaintext)
    ciphertext = aes.encrypt(padded_plaintext)
    return ciphertext

def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """Decrypt data using AES CBC mode"""
    aes = AES.new(key, AES.MODE_CBC, iv)
    plaintext = aes.decrypt(ciphertext)
    return unpad(plaintext)

def decode_protobuf(encoded_data: bytes, message_type: message.Message) -> message.Message:
    """Decode Protocol Buffer data"""
    message_instance = message_type()
    message_instance.ParseFromString(encoded_data)
    return message_instance

# --- Core Authentication
async def getAccess_Token(uid: str, password: str) -> Tuple[str, str]:
    """
    Get access token from Garena OAuth
    Returns: (access_token, open_id)
    """
    payload = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": CLIENT_TYPE,
        "client_secret": CLIENT_SECRET,
        "client_id": CLIENT_ID
    }
    
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(OAUTH_URL, data=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            access_token = data.get("access_token", "0")
            open_id = data.get("open_id", "0")
            
            if access_token == "0" or open_id == "0":
                error_msg = data.get("error_description", "Unknown error")
                raise ValueError(f"OAuth failed: {error_msg}")
            
            return access_token, open_id
            
    except httpx.HTTPError as e:
        raise ConnectionError(f"Network error during OAuth: {e}")
    except json.JSONDecodeError:
        raise ValueError("Invalid response from OAuth server")

async def create_jwt(uid: str, password: str) -> Tuple[str, str, str]:
    """
    Create JWT token for Free Fire authentication
    Returns: (token, region, serverUrl)
    """
    # Step 1: Get access token
    print("â†’ Getting access token...")
    access_token, open_id = await getAccess_Token(uid, password)
    print(f"âœ“ Access token obtained for OpenID: {open_id}")
    
    # Step 2: Prepare login request
    json_data = json.dumps({
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4"
    })
    
    # Step 3: Convert to protobuf and encrypt
    print("â†’ Encoding and encrypting request...")
    encoded_result = await json_to_proto(json_data, freefire_pb2.LoginReq())
    payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded_result)
    
    # Step 4: Send login request
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
    
    try:
        print("â†’ Sending login request...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(LOGIN_URL, content=payload, headers=headers)
            response.raise_for_status()
            
            # Step 5: Decode response
            response_content = response.content
            decoded_message = decode_protobuf(response_content, freefire_pb2.LoginRes())
            message = json.loads(json_format.MessageToJson(decoded_message))
            
            # Extract data
            token = message.get("token", "0")
            region = message.get("lockRegion", "UNKNOWN")
            serverUrl = message.get("serverUrl", "")
            
            if token == "0":
                error_code = message.get("errorCode", "Unknown")
                error_msg = message.get("errorMsg", "Failed to obtain JWT")
                raise ValueError(f"Login failed: {error_msg} (Code: {error_code})")
            
            print("âœ“ JWT created successfully")
            return token, region, serverUrl
            
    except httpx.HTTPError as e:
        raise ConnectionError(f"Network error during login: {e}")
    except Exception as e:
        raise ValueError(f"Failed to process login response: {e}")

# --- Validation Functions
def validate_uid(uid: str) -> bool:
    """Validate UID format"""
    if not uid.isdigit():
        return False
    if len(uid) < 8 or len(uid) > 12:
        return False
    return True

def validate_region(region: str) -> bool:
    """Check if region is supported"""
    return region in SUPPORTED_REGIONS

# --- Main Program
async def main():
    print("\n" + "="*50)
    print("   Free Fire JWT Generator - OB51")
    print("="*50 + "\n")
    
    # Get user input
    uid = input("Enter your UID: ").strip()
    password = input("Enter your password: ").strip()
    
    # Validate input
    if not uid or not password:
        print("\nâœ— Error: UID and password cannot be empty.")
        sys.exit(1)
    
    if not validate_uid(uid):
        print("\nâœ— Error: Invalid UID format. UID should be 8-12 digits.")
        sys.exit(1)
    
    try:
        print(f"\n{'â”€'*50}")
        print("Starting authentication process...")
        print(f"{'â”€'*50}\n")
        
        # Generate JWT
        token, lock_region, server_url = await create_jwt(uid, password)
        
        # Display results
        print(f"\n{'='*50}")
        print("   âœ“ JWT CREATED SUCCESSFULLY")
        print(f"{'='*50}\n")
        
        print(f"Token:\n{token}\n")
        print(f"Locked Region: {lock_region}")
        
        if not validate_region(lock_region):
            print(f"âš  Warning: Region '{lock_region}' is not in supported list")
        
        print(f"Server URL: {server_url}")
        print(f"\n{'='*50}\n")
        
        # Save to file option
        save = input("Save token to file? (y/n): ").strip().lower()
        if save == 'y':
            with open(f'jwt_token_{uid}.txt', 'w') as f:
                f.write(f"UID: {uid}\n")
                f.write(f"Token: {token}\n")
                f.write(f"Region: {lock_region}\n")
                f.write(f"Server: {server_url}\n")
                f.write(f"Generated: {asyncio.get_event_loop().time()}\n")
            print(f"âœ“ Token saved to jwt_token_{uid}.txt")
        
    except ValueError as e:
        print(f"\nâœ— Validation Error: {e}")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\nâœ— Connection Error: {e}")
        print("Please check your internet connection and try again.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nâœ— Operation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nâœ— Unexpected Error: {e}")
        print("Please try again or contact support.")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
