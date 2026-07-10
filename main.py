from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, messaging
from dotenv import load_dotenv
import os
import json

# Charger les variables d'environnement
load_dotenv()

# Initialiser Firebase
cred = credentials.Certificate(os.getenv("FIREBASE_CREDENTIALS_PATH"))
firebase_admin.initialize_app(cred)

app = FastAPI(title="e-Signal Notifications API")

# Fichier de stockage des tokens
TOKENS_FILE = "fcm_tokens.json"

def load_tokens():
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


# Modèles
class FCMTokenRequest(BaseModel):
    user_id: str
    fcm_token: str
    organization_id: str


class NotificationRequest(BaseModel):
    organization_id: str
    title: str
    body: str
    data: dict = {}


# Endpoint 1 — Enregistrer le FCM token
@app.post("/api/notifications/register-token")
async def register_token(request: FCMTokenRequest):
    try:
        tokens = load_tokens()
        tokens[request.user_id] = {
            "user_id": request.user_id,
            "fcm_token": request.fcm_token,
            "organization_id": request.organization_id,
        }
        save_tokens(tokens)
        return {"status": "success", "message": "Token enregistré avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 2 — Envoyer une notification à une organisation
@app.post("/api/notifications/send")
async def send_notification(request: NotificationRequest):
    try:
        tokens = load_tokens()
        
        # Filtrer les tokens par organization_id
        org_tokens = [
            v["fcm_token"] 
            for v in tokens.values() 
            if v["organization_id"] == request.organization_id
        ]

        if not org_tokens:
            raise HTTPException(
                status_code=404,
                detail="Aucun token trouvé pour cette organisation"
            )

        # Envoyer la notification
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=request.title,
                body=request.body,
            ),
            data=request.data,
            tokens=org_tokens,
        )
        response = messaging.send_each_for_multicast(message)
        return {
            "status": "success",
            "sent": response.success_count,
            "failed": response.failure_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 3 — Santé du serveur
@app.get("/health")
async def health():
    return {"status": "ok", "service": "e-Signal Notifications"}