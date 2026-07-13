from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, messaging, firestore
from dotenv import load_dotenv
import os
import json
from datetime import datetime, timezone

# Charger les variables d'environnement
load_dotenv()

# Initialiser Firebase
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
firebase_creds_path = os.getenv("FIREBASE_CREDENTIALS_PATH")

if firebase_creds_json:
    cred = credentials.Certificate(json.loads(firebase_creds_json))
elif firebase_creds_path:
    cred = credentials.Certificate(firebase_creds_path)
else:
    raise ValueError("Aucune credential Firebase trouvée.")

firebase_admin.initialize_app(cred)

# Initialiser Firestore
db = firestore.client()

# Clé secrète pour le webhook
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET_KEY")

app = FastAPI(title="e-Signal Notifications API")


# Vérification du Bearer Token
def verify_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Format Bearer Token invalide")
    token = authorization.replace("Bearer ", "")
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Token invalide")
    return token


# Modèles
class FCMTokenRequest(BaseModel):
    user_id: str
    fcm_token: str
    platform: str = "android"
    organization_id: str


class WebhookRequest(BaseModel):
    user_id: str
    title: str
    body: str
    data: dict = {}


# Endpoint 1 — Enregistrer le FCM token
@app.post("/api/notifications/register-token")
async def register_token(request: FCMTokenRequest):
    try:
        db.collection("fcmTokens").document(request.user_id).set({
            "token": request.fcm_token,
            "platform": request.platform,
            "organization_id": request.organization_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return {"status": "success", "message": "Token enregistré avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 2 — Webhook sécurisé pour envoyer une notification
@app.post("/api/webhooks/send")
async def send_notification(
    request: WebhookRequest,
    token: str = Depends(verify_token)
):
    try:
        # Récupérer le token FCM depuis Firestore
        doc = db.collection("fcmTokens").document(request.user_id).get()

        if not doc.exists:
            raise HTTPException(
                status_code=404,
                detail="Token FCM non trouvé pour cet utilisateur"
            )

        fcm_token = doc.to_dict()["token"]

        # Envoyer la notification via Firebase
        message = messaging.Message(
            notification=messaging.Notification(
                title=request.title,
                body=request.body,
            ),
            data=request.data,
            token=fcm_token,
        )
        response = messaging.send(message)

        # Sauvegarder dans l'historique Firestore
        db.collection("users").document(request.user_id)\
          .collection("notifications").add({
            "title": request.title,
            "body": request.body,
            "data": request.data,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent",
            "message_id": response,
        })

        return {
            "status": "success",
            "message_id": response,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 3 — Santé du serveur
@app.get("/health")
async def health():
    return {"status": "ok", "service": "e-Signal Notifications"}