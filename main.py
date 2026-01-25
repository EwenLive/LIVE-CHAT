import os
import discord
from discord.ext import commands
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio
import uvicorn
import re # Import pour détecter les URLs

# Config
TOKEN = str(os.getenv("DISCORD_TOKEN"))

app = FastAPI()
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:  # noqa: E722
                pass

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@bot.command()
async def pop(ctx, *, texte: str = ""):
    video_url = None
    final_text = texte

    # 1. Vérifier s'il y a un fichier attaché (prioritaire)
    if ctx.message.attachments:
        video_url = ctx.message.attachments[0].url
    
    # 2. Sinon, chercher une URL dans le texte
    else:
        # Cette regex trouve les liens commençant par http ou https
        url_match = re.search(r'(https?://\S+)', texte)
        if url_match:
            video_url = url_match.group(0)
            # On retire l'URL du texte pour ne pas l'afficher sur l'overlay
            final_text = texte.replace(video_url, "").strip()

    if video_url:
        # Envoi au client (Overlay)
        await manager.broadcast({"url": video_url, "text": final_text})
        await ctx.send(f"🚀 Pop envoyé ! (Source: {video_url})")
    else:
        await ctx.send("⚠️ Erreur : Envoie un fichier MP4 OU un lien (YouTube/Direct) !")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.start(TOKEN)) 

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)