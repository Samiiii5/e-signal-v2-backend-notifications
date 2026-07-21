from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
import firebase_admin
from firebase_admin import credentials, messaging, firestore
from dotenv import load_dotenv
import os
import json
from datetime import datetime, timezone

# Charger les variables d'environnement
load_dotenv()

firebase_web_config = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID"),
    "vapidKey": os.getenv("FIREBASE_VAPID_KEY"),
}

# Initialiser Firebase
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
firebase_creds_path = os.getenv("FIREBASE_CREDENTIALS_PATH")

if firebase_creds_json:
    cred = credentials.Certificate(json.loads(firebase_creds_json))
elif firebase_creds_path:
    cred = credentials.Certificate(firebase_creds_path)
else:
    raise ValueError("Aucune credential Firebase trouvée.")

firebase_admin.initialize_app(cred)  # ← CORRIGÉ

# Initialiser Firestore
db = firestore.client()

# API Key secrète
API_KEY = os.getenv("API_KEY")

app = FastAPI(title="e-Signal Notifications API")


# Vérification de l'API Key
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API Key invalide")
    return x_api_key


# Modèles
class FCMTokenRequest(BaseModel):
    user_id: str
    fcm_token: str
    platform: str = "android"
    organization_id: str


class WebhookRequest(BaseModel):
    organization_id: str
    title: str
    body: str
    data: dict = {}
    user_id: Optional[str] = None


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
    api_key: str = Depends(verify_api_key)
):
    try:
        # CAS 1 — user_id fourni → notifier un seul utilisateur
        if request.user_id:
            token_doc = db.collection("fcmTokens")\
                .document(request.user_id).get()

            if not token_doc.exists:
                raise HTTPException(
                    status_code=404,
                    detail="Aucun token FCM trouvé pour cet utilisateur"
                )

            fcm_token = token_doc.to_dict()["token"]

            message = messaging.Message(
                notification=messaging.Notification(
                    title=request.title,
                    body=request.body,
                ),
                data=request.data,
                token=fcm_token,
            )
            messaging.send(message)

            db.collection("users").document(request.user_id)\
              .collection("notifications").add({
                "title": request.title,
                "body": request.body,
                "data": request.data,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "status": "sent",
                "organization_id": request.organization_id,
            })

            return {
                "status": "success",
                "sent": 1,
                "failed": 0,
            }

        # CAS 2 — pas de user_id → notifier toute l'organisation
        tokens_ref = db.collection("fcmTokens")\
            .where("organization_id", "==", request.organization_id)\
            .stream()

        fcm_tokens = []
        user_ids = []
        for doc in tokens_ref:
            data = doc.to_dict()
            fcm_tokens.append(data["token"])
            user_ids.append(doc.id)

        if not fcm_tokens:
            raise HTTPException(
                status_code=404,
                detail="Aucun token FCM trouvé pour cette organisation"
            )

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=request.title,
                body=request.body,
            ),
            data=request.data,
            tokens=fcm_tokens,
        )
        response = messaging.send_each_for_multicast(message)

        for user_id in user_ids:
            db.collection("users").document(user_id)\
              .collection("notifications").add({
                "title": request.title,
                "body": request.body,
                "data": request.data,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "status": "sent",
                "organization_id": request.organization_id,
            })

        return {
            "status": "success",
            "sent": response.success_count,
            "failed": response.failure_count,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 3 — Historique des notifications d'un utilisateur
@app.get("/api/notifications/history/{user_id}")
async def get_notification_history(
    user_id: str,
    api_key: str = Depends(verify_api_key)
):
    try:
        notifications_ref = db.collection("users").document(user_id)\
            .collection("notifications")\
            .order_by("sent_at", direction=firestore.Query.DESCENDING)\
            .limit(50)

        docs = notifications_ref.stream()

        history = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            history.append(data)

        return {
            "status": "success",
            "user_id": user_id,
            "count": len(history),
            "notifications": history
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 4 — Santé du serveur
@app.get("/health")
async def health():
    return {"status": "ok", "service": "e-Signal Notifications"}


# Endpoint 5 — Configuration Firebase pour l'app web
@app.get("/api/config/firebase")
async def get_firebase_config(
    api_key: str = Depends(verify_api_key)
):
    return firebase_web_config