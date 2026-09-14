import os
import json
import time
import secrets
import aiohttp
import discord
from discord.ext import commands
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import uvicorn
import re # Import pour détecter les URLs

# Config
TOKEN = str(os.getenv("DISCORD_TOKEN"))

# --- OAuth2 Discord (pour "Se connecter avec Discord") ---
# Le Client ID = l'ID de l'application (= l'ID du bot). Le secret reste côté serveur.
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1464725799205601320").strip().strip('"').strip("'")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip().strip('"').strip("'")

# Salon Discord autorisé pour la commande !pop (0 = pas de restriction)
MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID", "1464726876604862528"))

# --- Salons (channels) LiveChat : chaque client s'abonne à un ou plusieurs salons ---
CHANNELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
DEFAULT_CHANNELS = ["general"]
# IDs Discord autorisés à créer/supprimer des salons (admins). EWEN par défaut.
ADMIN_IDS = set(x.strip() for x in os.getenv("ADMIN_IDS", "355828855765729285").split(",") if x.strip())
# Jetons admin (émis après une connexion OAuth VÉRIFIÉE d'un admin). En mémoire → effacés au redémarrage.
ADMIN_TOKENS = {}  # token -> discord_id

# Contrôle d'accès à !pop : nom du rôle Discord qui autorise à envoyer des pops.
# Tant que ce rôle n'existe PAS sur le serveur, tout le monde peut pop (comportement par défaut).
# Dès qu'il existe, seuls ceux qui l'ont (+ les admins) peuvent pop.
POP_ROLE_NAME = os.getenv("POP_ROLE_NAME", "livechat").strip().lower()
# Durée de vie du cache d'avatars (sec) : au-delà on re-vérifie la pp Discord (elles changent)
AVATAR_TTL = int(os.getenv("AVATAR_TTL", "1800"))  # 30 min

def can_pop(member) -> bool:
    if str(getattr(member, "id", "")) in ADMIN_IDS:
        return True  # admins toujours autorisés
    guild = getattr(member, "guild", None)
    roles = getattr(member, "roles", None)
    if guild is None or roles is None:
        return True  # contexte sans rôles (ex: DM) → on n'bloque pas
    gate = next((r for r in guild.roles if r.name.strip().lower() == POP_ROLE_NAME), None)
    if gate is None:
        return True  # rôle non créé → accès ouvert à tous
    return gate in roles

def load_channels() -> list:
    try:
        with open(CHANNELS_PATH, "r", encoding="utf-8") as f:
            ch = json.load(f)
            if isinstance(ch, list) and ch:
                out = [str(c).strip().lower() for c in ch if str(c).strip()]
                if "general" not in out:
                    out.insert(0, "general")
                return out
    except Exception:
        pass
    return list(DEFAULT_CHANNELS)

def save_channels(channels: list):
    try:
        with open(CHANNELS_PATH, "w", encoding="utf-8") as f:
            json.dump(channels, f, ensure_ascii=False)
    except Exception:
        pass

# --- Auto-update du client ---
# Le dossier updates/ contient : version.txt (numéro) + latest.exe (le client à jour)
UPDATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updates")

# Suivi des alertes pour les votes (en mémoire, transitoire)
alert_authors = {}   # alert_id -> author_discord_id
alert_votes = {}     # alert_id -> {voter_key: value (-1/0/1)} ; sert à compter 👍/👎 en live
alert_order = []     # historique borné des alert_id

app = FastAPI()

# --- Extraction vidéo CÔTÉ SERVEUR (yt-dlp) + hébergement des médias ---
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")  # sert /media/xxx.mp4
COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "https://srv1346932.hstgr.cloud")
DIRECT_MEDIA_EXTS = (".mp4", ".mov", ".avi", ".webm", ".gif", ".png", ".jpg", ".jpeg", ".webp")
_download_sem = asyncio.Semaphore(2)  # max 2 téléchargements simultanés (VPS 1 cœur)

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Table de correspondance : pseudo affiché (= ce que le client envoie) -> ID Discord.
# À remplir dans discord_links.json, ex : {"skeel": "123456789012345678", "Daoud": "987..."}
# Un pseudo absent s'affiche sans avatar (pseudo brut). Les potes n'ont rien à faire.
DISCORD_LINKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_links.json")

def load_discord_links() -> dict:
    try:
        with open(DISCORD_LINKS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

async def exchange_oauth(code: str, code_verifier: str, redirect_uri: str) -> dict | None:
    # Échange le code OAuth contre l'identité Discord (fait côté serveur, secret jamais exposé).
    if not code or not DISCORD_CLIENT_SECRET:
        return None
    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        token_data["code_verifier"] = code_verifier
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://discord.com/api/oauth2/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                token = await resp.json()
            access = token.get("access_token")
            if not access:
                return None
            async with session.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access}"},
            ) as resp:
                me = await resp.json()
        uid = me.get("id")
        if not uid:
            return None
        return {
            "discord_id": str(uid),
            "name": (me.get("global_name") or me.get("username") or "Discord")[:32],
        }
    except Exception:
        return None

class ConnectionManager:
    def __init__(self):
        # Une identité par connexion : {websocket: {"name": str, "discord_id": str|None}}
        self.active_connections: dict[WebSocket, dict] = {}
        # Cache des identités Discord résolues : ID -> {"name":.., "avatar":..} (succès uniquement)
        self._avatar_cache: dict[str, tuple] = {}  # discord_id -> (timestamp, {name, avatar})

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = {"name": "Anonyme", "discord_id": None, "channels": {"general"}, "verified": False, "client_id": None}

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)

    def set_identity(self, websocket: WebSocket, name=None, discord_id=None, verified=None):
        entry = self.active_connections.get(websocket)
        if entry is None:
            return
        if name is not None:
            entry["name"] = (name or "Anonyme").strip()[:32] or "Anonyme"
        if discord_id is not None:
            entry["discord_id"] = str(discord_id)
        if verified is not None:
            entry["verified"] = bool(verified)

    def is_admin(self, entry: dict) -> bool:
        # Admin uniquement si l'identité Discord a été VÉRIFIÉE (OAuth ou jeton), pas juste déclarée
        return bool(entry.get("verified")) and entry.get("discord_id") in ADMIN_IDS

    async def _resolve(self, entry: dict) -> dict:
        # Donne {nom Discord, avatar}. Priorité à l'ID Discord (OAuth), sinon table pseudo->ID.
        raw_name = entry.get("name", "Anonyme")
        discord_id = entry.get("discord_id")
        if not discord_id:
            links = load_discord_links()
            key = (raw_name or "").strip().lower()
            discord_id = next((v for k, v in links.items() if k.strip().lower() == key), None)
        if not discord_id:
            return {"name": raw_name, "avatar": None}
        # Cache avec TTL : on re-vérifie l'avatar toutes les AVATAR_TTL sec (les pp Discord changent)
        cached = self._avatar_cache.get(discord_id)
        if cached and (time.time() - cached[0]) < AVATAR_TTL:
            return cached[1]
        try:
            user = await bot.fetch_user(int(discord_id))
            info = {
                "name": user.global_name or user.name,
                "avatar": str(user.display_avatar.replace(size=64, static_format="png").url),
            }
            self._avatar_cache[discord_id] = (time.time(), info)  # (timestamp, info)
            return info
        except Exception:
            # Échec du refetch : on renvoie l'ancienne valeur si on l'a, sinon le nom brut
            return cached[1] if cached else {"name": raw_name, "avatar": None}

    async def get_users(self) -> list[dict]:
        # Snapshot : on copie avant d'itérer car _resolve fait un await (fetch_user)
        # pendant lequel une connexion peut se fermer et modifier le dict.
        entries = list(self.active_connections.values())
        users = [await self._resolve(entry) for entry in entries]
        return sorted(users, key=lambda u: u["name"].lower())

    async def broadcast(self, message: dict):
        dead = []
        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_json(message)
            except:  # noqa: E722
                dead.append(connection)
        # Nettoie les connexions mortes pour éviter qu'elles bloquent les diffusions
        for connection in dead:
            self.disconnect(connection)

    async def broadcast_presence(self):
        await self.broadcast({"type": "presence", "users": await self.get_users()})

    # --- Salons ---
    def set_channels(self, websocket: WebSocket, channels):
        entry = self.active_connections.get(websocket)
        if entry is None:
            return
        valid = set(load_channels())
        chosen = {c.strip().lower() for c in channels if isinstance(c, str)}
        chosen = {c for c in chosen if c in valid}
        entry["channels"] = chosen or {"general"}

    async def broadcast_to_channel(self, channel: str, message: dict):
        dead = []
        for ws, entry in list(self.active_connections.items()):
            if channel in entry.get("channels", set()):
                try:
                    await ws.send_json(message)
                except:  # noqa: E722
                    dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def channels_payload(self, websocket: WebSocket) -> dict:
        entry = self.active_connections.get(websocket, {})
        return {
            "type": "channels",
            "list": load_channels(),
            "is_admin": self.is_admin(entry),
            "subscribed": sorted(entry.get("channels", {"general"})),
        }

    async def broadcast_channels(self):
        for ws in list(self.active_connections.keys()):
            try:
                await ws.send_json(self.channels_payload(ws))
            except Exception:
                pass

    # --- Compteurs de votes 👍/👎 (live, par pop) ---
    async def broadcast_vote_counts(self, alert_id):
        votes = alert_votes.get(alert_id, {})
        up = sum(1 for v in votes.values() if v > 0)
        down = sum(1 for v in votes.values() if v < 0)
        await self.broadcast({"type": "vote_counts", "alert_id": alert_id, "up": up, "down": down})

manager = ConnectionManager()

# --- Votes 👍/👎 (compteurs live, plus de points ni de rôles) ---
async def handle_vote(websocket, data):
    alert_id = data.get("alert_id")
    if alert_id not in alert_authors:
        return  # alerte inconnue / expirée
    raw = data.get("value", 0)
    value = 1 if raw > 0 else (-1 if raw < 0 else 0)
    entry = manager.active_connections.get(websocket, {})
    # Identité de vote STABLE : discord_id (OAuth) sinon client_id persistant sinon la socket.
    # Évite qu'une reconnexion (nouvelle socket) recompte le même votant en double.
    voter_key = entry.get("discord_id") or entry.get("client_id") or f"ws:{id(websocket)}"
    # NB : voter sur son PROPRE pop est autorisé (comportement global, self-like compte pour tous).
    votes = alert_votes.setdefault(alert_id, {})
    prev = votes.get(voter_key, 0)
    if value == prev:
        return
    if value == 0:
        votes.pop(voter_key, None)
    else:
        votes[voter_key] = value
    await manager.broadcast_vote_counts(alert_id)
    # Événement de vote (pour l'anim pp côté client) : on envoie l'avatar du votant. Pas d'anim sur une annulation.
    if value != 0:
        info = await manager._resolve(entry)
        await manager.broadcast({"type": "vote_event", "alert_id": alert_id, "value": value,
                                 "avatar": info.get("avatar"), "name": info.get("name")})

@bot.event
async def on_ready():
    print(f"✅ Bot prêt ({bot.user}).")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # Envoie la liste des noms connus pour le menu déroulant "Qui es-tu ?" + la liste des salons
    try:
        names = sorted(load_discord_links().keys(), key=str.lower)
        await websocket.send_json({"type": "roster", "names": names})
        await websocket.send_json(manager.channels_payload(websocket))
    except Exception:
        pass
    await manager.broadcast_presence()  # Informe tout le monde de la nouvelle connexion
    try:
        while True:
            raw = await websocket.receive_text()
            # Le client peut envoyer son pseudo : {"type": "hello", "name": "..."}
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            mtype = data.get("type")
            if mtype == "hello":
                name = str(data.get("name", "Anonyme"))
                discord_id = data.get("discord_id")
                verified = None
                # Un jeton admin valide prouve une connexion OAuth antérieure -> identité vérifiée
                token = data.get("admin_token")
                if token and token in ADMIN_TOKENS:
                    discord_id = ADMIN_TOKENS[token]
                    verified = True
                manager.set_identity(websocket, name=name, discord_id=discord_id, verified=verified)
                # Identité de vote stable : on mémorise le client_id persistant du client
                cid = data.get("client_id")
                entry = manager.active_connections.get(websocket)
                if entry is not None and cid:
                    entry["client_id"] = str(cid)
                await manager.broadcast_presence()
                try:
                    await websocket.send_json(manager.channels_payload(websocket))  # is_admin à jour
                    # Resynchro : si le client affiche un pop, on lui renvoie son compteur de votes à jour
                    a_id = data.get("alert_id")
                    if a_id and a_id in alert_authors:
                        votes = alert_votes.get(a_id, {})
                        up = sum(1 for v in votes.values() if v > 0)
                        down = sum(1 for v in votes.values() if v < 0)
                        await websocket.send_json({"type": "vote_counts", "alert_id": a_id, "up": up, "down": down})
                except Exception:
                    pass
            elif mtype == "subscribe":
                manager.set_channels(websocket, data.get("channels", []))
            elif mtype == "admin_channel":
                entry = manager.active_connections.get(websocket, {})
                if manager.is_admin(entry):
                    action = data.get("action")
                    name = "".join(c for c in str(data.get("name", "")).strip().lower() if c.isalnum() or c in "-_")[:24]
                    chans = load_channels()
                    if action == "add" and name and name not in chans:
                        chans.append(name)
                        save_channels(chans)
                    elif action == "del" and name in chans and name != "general":
                        chans.remove(name)
                        save_channels(chans)
                    await manager.broadcast_channels()
            elif mtype == "oauth":
                # Échange le code OAuth puis renvoie l'identité Discord au client
                identity = await exchange_oauth(
                    data.get("code", ""),
                    data.get("code_verifier", ""),
                    data.get("redirect_uri", ""),
                )
                if identity:
                    did = identity["discord_id"]
                    # OAuth réussi = identité vérifiée
                    manager.set_identity(websocket, name=identity["name"], discord_id=did, verified=True)
                    payload = {"type": "identity", **identity}
                    # Si c'est un admin, on lui donne un jeton réutilisable (persiste ses droits sans re-OAuth)
                    if did in ADMIN_IDS:
                        token = secrets.token_urlsafe(24)
                        ADMIN_TOKENS[token] = did
                        payload["admin_token"] = token
                    try:
                        await websocket.send_json(payload)
                        await websocket.send_json(manager.channels_payload(websocket))  # is_admin à jour
                    except Exception:
                        pass
                    await manager.broadcast_presence()
            elif mtype == "vote":
                await handle_vote(websocket, data)
    except (WebSocketDisconnect, RuntimeError):
        # Déconnexion normale ou socket déjà fermée (vieux clients qui churnent) : on ignore
        pass
    finally:
        manager.disconnect(websocket)
        try:
            await manager.broadcast_presence()  # Informe tout le monde de la déconnexion
        except Exception:
            pass

async def download_media(url: str, alert_id: str):
    # Télécharge une vidéo (YouTube/TikTok/Insta/X/...) via yt-dlp, retourne le nom de fichier ou None
    outtmpl = os.path.join(MEDIA_DIR, f"{alert_id}.%(ext)s")
    is_tiktok = "tiktok.com" in url.lower()
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        "--no-playlist", "--quiet", "--no-warnings", "--no-check-certificate",
    ]
    if not is_tiktok:
        cmd += ["--match-filter", "duration <= 5400"]  # max 90 min (sauf TikTok)
    if os.path.exists(COOKIES_PATH):
        cmd += ["--cookies", COOKIES_PATH]
    cmd.append(url)
    async with _download_sem:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
        except Exception as e:
            print(f"⚠️ yt-dlp exception: {e}")
            return None
    if proc.returncode != 0:
        print(f"⚠️ yt-dlp échec ({url}): {(err or b'')[:200]!r}")
        return None
    for f in os.listdir(MEDIA_DIR):  # retrouve le fichier produit
        if f.startswith(alert_id + "."):
            return f
    return None

async def _twitter_image(url: str):
    # Fallback : récupère l'image d'un tweet via fxtwitter
    try:
        api = url.replace("twitter.com", "api.fxtwitter.com").replace("x.com", "api.fxtwitter.com")
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json()
        photos = data.get("tweet", {}).get("media", {}).get("photos", [])
        if photos:
            return photos[0].get("url")
    except Exception:
        pass
    return None

@bot.command()
async def pop(ctx, *, texte: str = ""):
    # Restriction : !pop autorisé uniquement dans le salon média
    if MEDIA_CHANNEL_ID and ctx.channel.id != MEDIA_CHANNEL_ID:
        await ctx.send(f"⚠️ Les `!pop` se font uniquement dans <#{MEDIA_CHANNEL_ID}>.", delete_after=6)
        return

    # Contrôle d'accès : seuls les membres avec le rôle « livechat » (+ admins) peuvent envoyer des pops
    if not can_pop(ctx.author):
        await ctx.send(f"⛔ Tu n'as pas accès à LiveChat. Demande le rôle **{POP_ROLE_NAME}** à un admin.", delete_after=6)
        return

    # Salon cible : si le 1er mot est un salon existant, c'est la cible (sinon "general")
    channels = load_channels()
    target = "general"
    parts = texte.split(maxsplit=1)
    if parts and parts[0].strip().lower() in channels:
        target = parts[0].strip().lower()
        texte = parts[1] if len(parts) > 1 else ""

    video_url = None
    final_text = texte
    user_name = ctx.author.name

    if ctx.message.attachments:
        video_url = ctx.message.attachments[0].url
    else:
        url_match = re.search(r'(https?://\S+)', texte)
        if url_match:
            video_url = url_match.group(0)
            final_text = texte.replace(video_url, "").strip()

    if not video_url:
        await ctx.send("⚠️ Erreur : Envoie un fichier ou un lien !")
        return

    alert_id = secrets.token_hex(8)
    author_id = str(ctx.author.id)

    # Média direct (fichier Discord, .mp4/.gif/image) = tel quel ; sinon le SERVEUR télécharge
    url_clean = video_url.lower().split("?")[0]
    if ctx.message.attachments or url_clean.endswith(DIRECT_MEDIA_EXTS):
        media_url = video_url
    else:
        try:
            await ctx.message.add_reaction("⏳")
        except Exception:
            pass
        fname = await download_media(video_url, alert_id)
        try:
            await ctx.message.remove_reaction("⏳", bot.user)
        except Exception:
            pass
        if fname:
            media_url = f"{PUBLIC_BASE}/media/{fname}"
        elif "twitter.com" in url_clean or "x.com" in url_clean:
            media_url = await _twitter_image(video_url)
        else:
            media_url = None
        if not media_url:
            await ctx.send("❌ Impossible de récupérer cette vidéo/lien.", delete_after=6)
            return

    alert_authors[alert_id] = author_id
    alert_order.append(alert_id)
    if len(alert_order) > 300:  # borne mémoire
        old = alert_order.pop(0)
        alert_authors.pop(old, None)
        alert_votes.pop(old, None)
    user_avatar = str(ctx.author.display_avatar.replace(size=128, static_format="png").url)
    await manager.broadcast_to_channel(target, {
        "type": "alert",
        "channel": target,
        "alert_id": alert_id,
        "author_id": author_id,
        "url": media_url,
        "text": final_text,
        "user": user_name,
        "user_avatar": user_avatar
    })
    await ctx.send(f"✅ → salon **{target}**")

@bot.command()
async def status(ctx):
    # Vérif rapide "est-ce que LiveChat est en ligne ?" — utilisable par tout le monde dans Discord.
    # Si le bot NE répond PAS à cette commande => le serveur/bot est tombé.
    conns = list(manager.active_connections.values())
    names = ", ".join(sorted((e.get("name") or "?") for e in conns)) or "personne"
    gate = None
    if ctx.guild:
        gate = next((r for r in ctx.guild.roles if r.name.strip().lower() == POP_ROLE_NAME), None)
    acces = f"restreint au rôle **{POP_ROLE_NAME}**" if gate else "ouvert à tous"
    await ctx.send(
        f"🟢 LiveChat en ligne — **{len(conns)}** client(s) connecté(s) : {names}\n"
        f"🔐 Accès !pop : {acces}"
    )

@app.get("/version")
async def get_version():
    # Dernière version dispo du client (lue depuis updates/version.txt)
    try:
        with open(os.path.join(UPDATES_DIR, "version.txt"), "r", encoding="utf-8") as f:
            return JSONResponse({"version": int(f.read().strip())})
    except Exception:
        return JSONResponse({"version": 0})

@app.get("/download")
async def download_client():
    # Sert le dernier exe (updates/latest.exe)
    path = os.path.join(UPDATES_DIR, "latest.exe")
    if os.path.exists(path):
        return FileResponse(path, filename="LiveChat.exe", media_type="application/octet-stream")
    return JSONResponse({"error": "no update"}, status_code=404)

async def cleanup_media_task():
    # Supprime les vidéos téléchargées de plus de 15 min (évite de remplir le disque)
    while True:
        try:
            now = time.time()
            for f in os.listdir(MEDIA_DIR):
                p = os.path.join(MEDIA_DIR, f)
                try:
                    if os.path.isfile(p) and now - os.path.getmtime(p) > 900:
                        os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(300)  # toutes les 5 min

async def ytdlp_updater_task():
    # Met yt-dlp à jour au démarrage puis chaque jour (YouTube s'auto-répare)
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "-U", "yt-dlp",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            v = await asyncio.create_subprocess_exec("yt-dlp", "--version", stdout=asyncio.subprocess.PIPE)
            out, _ = await v.communicate()
            print(f"🔄 yt-dlp à jour : {out.decode().strip()}")
        except Exception as e:
            print(f"⚠️ update yt-dlp: {e}")
        await asyncio.sleep(86400)  # toutes les 24h

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.start(TOKEN))
    asyncio.create_task(cleanup_media_task())
    asyncio.create_task(ytdlp_updater_task())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)