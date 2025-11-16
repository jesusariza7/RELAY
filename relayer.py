import os
import json
import requests
from fastapi import FastAPI, UploadFile, Form
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# ============================
# 1. CONFIGURACIONES
# ============================

RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
CHAIN_ID = int(os.getenv("CHAIN_ID"))
PINATA_JWT = os.getenv("PINATA_JWT")

if not all([RPC_URL, PRIVATE_KEY, CONTRACT_ADDRESS, CHAIN_ID, PINATA_JWT]):
    raise Exception("ERROR: Faltan variables de entorno en Render.")

# Inicializar Web3
web3 = Web3(Web3.HTTPProvider(RPC_URL))

# Cuenta
account = web3.eth.account.from_key(PRIVATE_KEY)
wallet_address = account.address

# ABI DEL CONTRATO – CAMBIA POR TU ABI REAL
ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "_ipfsHash", "type": "string"},
            {"internalType": "string", "name": "_data", "type": "string"},
        ],
        "name": "storeReading",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

contract = web3.eth.contract(address=CONTRACT_ADDRESS, abi=ABI)

# INICIAR API
app = FastAPI()


# ============================
# 2. SUBIR ARCHIVO A PINATA
# ============================

def upload_to_pinata(file_bytes, filename):
    url = "https://api.pinata.cloud/pinning/pinFileToIPFS"
    headers = {
        "Authorization": f"Bearer {PINATA_JWT}"
    }

    files = {
        "file": (filename, file_bytes)
    }

    response = requests.post(url, headers=headers, files=files)

    if response.status_code != 200:
        raise Exception(f"Error subiendo a Pinata: {response.text}")

    ipfs_hash = response.json()["IpfsHash"]
    return f"https://gateway.pinata.cloud/ipfs/{ipfs_hash}"


# ============================
# 3. ENDPOINT PARA GUARDAR LECTURA
# ============================

@app.post("/api/save-reading")
async def save_reading(
    image: UploadFile,
    data: str = Form(...)
):
    try:
        # 1. Subir imagen a IPFS (Pinata)
        file_bytes = await image.read()
        ipfs_url = upload_to_pinata(file_bytes, image.filename)

        # 2. Crear transacción al smart contract
        tx = contract.functions.storeReading(
            ipfs_url,
            data
        ).build_transaction({
            "chainId": CHAIN_ID,
            "gas": 250000,
            "gasPrice": web3.eth.gas_price,
            "nonce": web3.eth.get_transaction_count(wallet_address)
        })

        # 3. Firmar transacción
        signed_tx = web3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)

        # 4. Enviar transacción
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        return {
            "status": "success",
            "tx_hash": tx_hash.hex(),
            "ipfs_url": ipfs_url
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================
# 4. HOME
# ============================

@app.get("/")
def home():
    return {"status": "Relayer running", "wallet": wallet_address}
