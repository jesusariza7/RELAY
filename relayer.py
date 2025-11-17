from fastapi import FastAPI, Request, HTTPException
from web3 import Web3
from typing import Optional
import os
import requests
import traceback

app = FastAPI()

# ================== CONFIGURACIÓN BLOCKCHAIN ==================
RPC_URL = os.getenv("RPC_URL")
if not RPC_URL:
    raise ValueError("RPC_URL no está definida en variables de entorno")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("PRIVATE_KEY no está definida en variables de entorno")

CONTRACT_ADDRESS_RAW = os.getenv("CONTRACT_ADDRESS")
if not CONTRACT_ADDRESS_RAW:
    raise ValueError("CONTRACT_ADDRESS no está definida en variables de entorno")
CONTRACT_ADDRESS = Web3.to_checksum_address(CONTRACT_ADDRESS_RAW)

CHAIN_ID_STR = os.getenv("CHAIN_ID")
if not CHAIN_ID_STR:
    raise ValueError("CHAIN_ID no está definida en variables de entorno")
CHAIN_ID = int(CHAIN_ID_STR)

PINATA_JWT = os.getenv("PINATA_JWT")

# ABI CORRECTO (SIN CID)
ABI_JSON = [
    {
        "inputs": [
            {"internalType": "string", "name": "deviceId", "type": "string"},
            {"internalType": "int16", "name": "temperatureTimes10", "type": "int16"},
            {"internalType": "uint16", "name": "humidityTimes10", "type": "uint16"},
            {"internalType": "uint256", "name": "timestampMs", "type": "uint256"}
        ],
        "name": "storeReading",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

PINATA_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"

# ================== INICIALIZACIÓN WEB3 ==================
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("No se pudo conectar al nodo RPC. Revisa RPC_URL / Internet.")

account = w3.eth.account.from_key(PRIVATE_KEY)
print("[INFO] Relayer usando cuenta:", account.address)
print("[INFO] Saldo actual:", w3.eth.get_balance(account.address))

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=ABI_JSON)


@app.get("/")
def root():
    return {"status": "ok", "message": "Relayer funcionando"}


def subir_a_pinata(payload: dict) -> Optional[str]:
    if not PINATA_JWT:
        print("[WARN] PINATA_JWT vacío, no se subirá a IPFS.")
        return None

    headers = {
        "Authorization": f"Bearer {PINATA_JWT}",
        "Content-Type": "application/json",
    }

    print("[DEBUG] Subiendo a Pinata:", payload)

    try:
        r = requests.post(PINATA_URL, json=payload, headers=headers, timeout=30)
        print("[DEBUG] Respuesta completa de Pinata:", r.text)

        r.raise_for_status()
        cid = r.json().get("IpfsHash")

        print("[INFO] Subido a Pinata, CID:", cid)
        return cid

    except Exception as e:
        print("[ERROR] Error subiendo a Pinata:", e)
        return None


@app.post("/api/lecturas")
async def recibir_lectura(req: Request):
    try:
        data = await req.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON inválido: {e}")

    print("[DEBUG] Payload recibido:", data)
    device_id = data.get("device_id", "unknown-device")

    # Validación de campos obligatorios
    if "temperature" not in data:
        raise HTTPException(status_code=400, detail="Campo 'temperature' requerido")
    if "humidity" not in data:
        raise HTTPException(status_code=400, detail="Campo 'humidity' requerido")
    if "timestamp_ms" not in data:
        raise HTTPException(status_code=400, detail="Campo 'timestamp_ms' requerido")

    # Conversión de tipos
    try:
        temp_c = float(data["temperature"])
        hum = float(data["humidity"])
        timestamp_ms = int(data["timestamp_ms"])
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Error en tipos de datos: {e}")

    # Escalamiento para el contrato
    temp_times10 = int(round(temp_c * 10))
    hum_times10 = int(round(hum * 10))

    # Subir a Pinata
    cid = subir_a_pinata({
        "device_id": device_id,
        "temperature_c": temp_c,
        "humidity_percent": hum,
        "timestamp_ms": timestamp_ms,
    }) or ""

    # Debug
    print("====== DATOS QUE SE ENVIAN AL CONTRATO ======")
    print("device_id:", device_id)
    print("temp_times10:", temp_times10)
    print("hum_times10:", hum_times10)
    print("timestamp_ms:", timestamp_ms)
    print("CID guardado en Pinata:", cid)
    print("================================================")

    try:
        nonce = w3.eth.get_transaction_count(account.address)
        print("[INFO] Nonce actual:", nonce)

        tx = contract.functions.storeReading(
            device_id,
            temp_times10,
            hum_times10,
            timestamp_ms
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "gas": 300000,
                "gasPrice": w3.eth.gas_price,
                "chainId": CHAIN_ID,
            }
        )

        print("[DEBUG] TX Construida:", tx)

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)

        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print("[INFO] Tx enviada:", tx_hash.hex())

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("[INFO] Tx minada en bloque:", receipt.blockNumber)

        return {
            "status": "ok",
            "tx_hash": tx_hash.hex(),
            "block": receipt.blockNumber,
            "cid": cid,
        }

    except Exception as e:
        print("[ERROR] Exception completa al enviar tx:")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error al enviar transacción: {str(e)}"
        )
