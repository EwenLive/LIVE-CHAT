import sys
import json
import asyncio
import threading
import websockets
import os
import time
import random
import ssl
import certifi
import getpass
import requests
import tempfile
import uuid
import secrets
import hashlib
import base64
import webbrowser
import urllib.parse
import winreg
import subprocess
import ctypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QGraphicsDropShadowEffect, QPushButton, QGraphicsOpacityEffect
from PyQt6.QtCore import (Qt, QUrl, pyqtSignal, QObject, QTimer, QSize, QVariantAnimation, QEasingCurve,
                          QPropertyAnimation, QParallelAnimationGroup, QPoint)
from PyQt6.QtGui import QColor, QPixmap, QImage, QMovie, QPainter, QPainterPath, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction

from PyQt6.QtWidgets import (QStyle,
    QVBoxLayout, QHBoxLayout, QCheckBox, QComboBox, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QMessageBox
)

from PyQt6.QtWidgets import QSlider


# --- CONFIGURATION ---
SERVER_WS_URL = "wss://srv1346932.hstgr.cloud/ws"  # VPS Hostinger (Traefik + Let's Encrypt), 24/7
IMAGE_DURATION = 5000

# --- AUTO-UPDATE ---
APP_VERSION = 33  # version interne de ce build (le serveur annonce la dernière dispo)
UPDATE_BASE = "https://srv1346932.hstgr.cloud"

# --- PSEUDO / CONFIG LOCALE ---
# Stocke le pseudo dans %APPDATA%\LiveChat\config.json (persiste entre les sessions)
CONFIG_PATH = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "LiveChat", "config.json")
LOG_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "livechat.log")

def log(msg):
    # Journalise dans %APPDATA%/LiveChat/livechat.log (pour diagnostiquer les coupures) + console
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 500_000:  # rotation simple
            open(LOG_PATH, "w").close()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass

def update_config(**kw):
    cfg = load_config()
    cfg.update(kw)
    save_config(cfg)

_cfg = load_config()
# Pseudo par défaut = nom de session Windows
CURRENT_USER = (_cfg.get("pseudo") or getpass.getuser() or "Anonyme").strip()[:32]
# ID Discord mémorisé après une connexion OAuth (rend les lancements suivants automatiques)
CURRENT_DISCORD_ID = _cfg.get("discord_id")
# Jeton admin (donné par le serveur après OAuth si admin ; réutilisé au lancement pour garder les droits)
CURRENT_ADMIN_TOKEN = _cfg.get("admin_token")
# Salons auxquels on est abonné (persisté)
CURRENT_CHANNELS = _cfg.get("channels") or ["general"]
# Identité de vote STABLE (persistée) : indépendante des reconnexions -> dédup fiable des votes
CURRENT_CLIENT_ID = _cfg.get("client_id")
if not CURRENT_CLIENT_ID:
    CURRENT_CLIENT_ID = uuid.uuid4().hex
    update_config(client_id=CURRENT_CLIENT_ID)
# alert_id du pop actuellement affiché + mon vote courant (partagés GUI -> thread WS pour la
# resynchro à la reconnexion : on re-joue mon vote si la coupure l'a fait perdre)
CURRENT_ALERT_ID = ""
CURRENT_MY_VOTE = 0

# --- OAuth2 Discord ---
DISCORD_CLIENT_ID = "1464725799205601320"   # = ID de l'application (le secret reste sur le serveur)
OAUTH_PORT = 53134
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_PORT}/callback"

# --- Démarrage automatique avec Windows (clé de registre Run) ---
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "LiveChat"

def get_app_path():
    # En .exe PyInstaller : sys.executable ; en .py : le script
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])

def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, AUTOSTART_NAME)
            return True
    except Exception:
        return False

def set_autostart(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, f'"{get_app_path()}"')
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False

# Partagé entre le thread GUI et le thread WebSocket
ws_loop = None      # boucle asyncio du thread d'écoute
ws_current = None   # websocket actif


class Communicate(QObject):
    # Le signal reçoit : URL, Texte, Pseudo, URL de la pp, et l'id d'alerte (pour les votes)
    signal = pyqtSignal(str, str, str, str, str)
    # Liste des pseudos connectés à LiveChat
    presence = pyqtSignal(list)
    # Liste des noms connus (pour le menu déroulant "Qui es-tu ?")
    roster = pyqtSignal(list)
    # Identité Discord renvoyée par le serveur après OAuth ({discord_id, name})
    identity = pyqtSignal(dict)
    # Liste des salons + statut admin ({list, is_admin, subscribed})
    channels = pyqtSignal(dict)
    # Compteurs de votes live d'un pop : (alert_id, up, down)
    vote_counts = pyqtSignal(str, int, int)
    # Événement de vote (pour l'anim pp) : (alert_id, value, avatar_url)
    vote_event = pyqtSignal(str, int, str)
    # Avatar téléchargé prêt à animer (revient du thread de fond) : (avatar_url, value, alert_id)
    pp_ready = pyqtSignal(str, int, str)
    # Une mise à jour est disponible (n° de version) -> affiche le bouton "Mettre à jour"
    update_available = pyqtSignal(int)

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.quality = "720"
        self.target_screen = None  # None = écran principal par défaut

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # ne vole pas le focus
        self.setFixedSize(1280, 720)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setGeometry(0, 0, 1280, 720)
        self.video_widget.hide()

        self.image_label = QLabel(self)
        self.image_label.setGeometry(0, 0, 1280, 720)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.hide()

        self.text_window = QWidget()
        self.text_window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput  # click-through : ne capture plus la souris
        )
        self.text_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.text_window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # ne vole pas le focus
        self.text_window.setFixedSize(1280, 720)
        
        # --- LOGIQUE PSEUDO (HAUT GAUCHE) ---
        self.user_label = QLabel("", self.text_window)
        self.user_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.user_label.setStyleSheet("font-family: 'Impact'; font-size: 35px; color: white; background: transparent;")
        self.user_label.setGeometry(30, 20, 1000, 60) 

        self.user_shadow = QGraphicsDropShadowEffect()
        self.user_shadow.setBlurRadius(1)
        self.user_shadow.setXOffset(3)
        self.user_shadow.setYOffset(3)
        self.user_shadow.setColor(QColor(0, 0, 0))
        self.user_label.setGraphicsEffect(self.user_shadow)

        # --- AVATAR DE L'AUTEUR (HAUT GAUCHE, à gauche du pseudo) ---
        self.user_avatar_label = QLabel("", self.text_window)
        self.user_avatar_label.setGeometry(30, 14, 64, 64)
        self.user_avatar_label.setStyleSheet("background: transparent;")
        self.user_avatar_label.hide()
        self._overlay_avatar_cache = {}  # url -> QPixmap rond (évite de re-télécharger)

        # --- LABEL MESSAGE (BAS) ---
        self.label = QLabel("", self.text_window)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True) 
        self.label.setStyleSheet("font-family: 'Impact'; font-size: 65px; color: yellow; background: transparent;")
        self.label.setGeometry(50, 430, 1180, 250) 

        # --- FENÊTRE DE CONTRÔLES (bas-droite) : SEUL élément cliquable, le reste est click-through ---
        self.controls_window = QWidget()
        self.controls_window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.controls_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.controls_window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # ne vole pas le focus
        controls_layout = QHBoxLayout(self.controls_window)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        # --- VOTES 👍 / 👎 (compteurs live du pop, affichés sur le bouton) ---
        self.current_alert_id = ""
        self.my_vote = 0
        self.up_count = 0
        self.down_count = 0
        self.vote_up_btn = QPushButton()
        self.vote_up_btn.setFixedSize(64, 38)
        self.vote_up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vote_up_btn.clicked.connect(lambda: self._vote(1))
        self.vote_down_btn = QPushButton()
        self.vote_down_btn.setFixedSize(64, 38)
        self.vote_down_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vote_down_btn.clicked.connect(lambda: self._vote(-1))
        controls_layout.addWidget(self.vote_up_btn)
        controls_layout.addWidget(self.vote_down_btn)
        self._update_vote_buttons()

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(38, 38)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet("background-color: rgba(255, 0, 0, 180); color: white; border-radius: 19px; border: 2px solid white; font-size: 15px;")
        self.close_btn.clicked.connect(self._on_alert_finished)
        controls_layout.addWidget(self.close_btn)
        self.controls_window.adjustSize()
        self.controls_window.hide()

        # --- FENÊTRE D'ANIMATION DES VOTES (pp qui montent façon TikTok) : click-through, au-dessus de la vidéo ---
        self.anim_window = QWidget()
        self.anim_window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.anim_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.anim_window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.anim_window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # ne capture pas la souris
        self.anim_window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.anim_window.resize(170, 380)
        self.anim_window.hide()
        self._pp_anims = []        # anims en cours (garde une réf sinon GC)
        self._pp_img_cache = {}    # url -> QImage (téléchargée en fond, thread-safe)

        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(1)
        self.shadow.setXOffset(4)
        self.shadow.setYOffset(4)
        self.shadow.setColor(QColor(0, 0, 0))
        self.label.setGraphicsEffect(self.shadow)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

        self.image_timer = QTimer()
        self.image_timer.setSingleShot(True)
        self.image_timer.timeout.connect(self._on_alert_finished)

        # Ré-affirme le 1er plan EN CONTINU pendant un pop (les jeux plein écran reprennent le dessus)
        self._topmost_timer = QTimer()
        self._topmost_timer.setInterval(450)
        self._topmost_timer.timeout.connect(self._raise_overlays)

        self.movie = None
        self.temp_gif_path = None

        # File d'attente des alertes (pour enchaîner au lieu d'écraser)
        self.alert_queue = []
        self.is_playing = False

        self.hide()
        self.text_window.hide()

    def update_resolution(self, width, height):
        # Redimensionne les fenêtres principales
        self.setFixedSize(width, height)
        self.text_window.setFixedSize(width, height)
        self.video_widget.setGeometry(0, 0, width, height)
        self.image_label.setGeometry(0, 0, width, height)
        
        # Repositionne le message (en bas)
        self.label.setGeometry(50, height - 250, width - 100, 200)

        print(f"📏 Interface redimensionnée en : {width}x{height}")

    # Ajoute ceci dans la classe Overlay
    def toggle_bot(self, reason=None):
        # On inverse l'état (si c'est masqué, on ne traite plus les alertes)
        if self.isEnabled():
            self.setEnabled(False)
            print("🚫 Bot Désactivé")
            return "Activer le Bot"
        else:
            self.setEnabled(True)
            print("✅ Bot Activé")
            return "Désactiver le Bot"


    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.show_all()
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self._on_alert_finished()

    def set_screen(self, screen):
        # Définit le moniteur sur lequel l'overlay s'affichera
        self.target_screen = screen
        if screen:
            geo = screen.geometry()
            print(f"🖥️ Écran de sortie : {screen.name()} ({geo.width()}x{geo.height()})")

    def show_all(self):
        self._set_overlay_opacity(1.0)  # au cas où un fondu précédent a été interrompu
        screen = self.target_screen or QApplication.primaryScreen()
        pos_x = pos_y = 0
        if screen:
            geo = screen.geometry()
            # On utilise les dimensions actuelles de la fenêtre (self.width() et self.height())
            pos_x = geo.left() + (geo.width() - self.width()) // 2
            pos_y = geo.top() + (geo.height() - self.height()) // 2
            self.move(pos_x, pos_y)
            self.text_window.move(pos_x, pos_y)

        # PAS de activateWindow() -> on ne vole plus le focus / la souris
        self.show()
        self.raise_()
        self.text_window.show()
        self.text_window.raise_()

        # Contrôles (croix + votes) en bas-droite de l'overlay = seule zone cliquable
        self.controls_window.adjustSize()
        cx = pos_x + self.width() - self.controls_window.width() - 25
        cy = pos_y + self.height() - self.controls_window.height() - 25
        self.controls_window.move(cx, cy)
        self.controls_window.show()
        self.controls_window.raise_()

        # Zone d'anim des votes : juste AU-DESSUS des boutons (les pp montent depuis les boutons)
        ax = cx + self.controls_window.width() - self.anim_window.width()
        ay = cy - self.anim_window.height()
        self.anim_window.move(ax, ay)
        self.anim_window.show()
        self.anim_window.raise_()

        # La surface vidéo native peut passer AU-DESSUS du texte -> on ré-affirme l'ordre
        # plusieurs fois (corrige le "vidéo affichée mais pas l'interface/texte/votes")
        self._raise_overlays()
        for d in (120, 400, 900, 1600):
            QTimer.singleShot(d, self._raise_overlays)
        self._topmost_timer.start()  # + ré-affirme en continu (jeux plein écran)

    def _force_topmost(self, w):
        # Force la fenêtre au 1er plan absolu SANS lui donner le focus (garde les commandes du jeu)
        # SetWindowPos(hwnd, HWND_TOPMOST=-1, 0,0,0,0, SWP_NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW)
        try:
            hwnd = int(w.winId())
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        except Exception:
            pass

    def _raise_overlays(self):
        # Passe l'overlay + le texte + les contrôles + l'anim au 1er plan (vidéo < texte < contrôles/anim)
        for w in (self, self.text_window, self.controls_window, self.anim_window):
            if w.isVisible():
                self._force_topmost(w)

    def play_alert(self, url, text, user, user_avatar="", alert_id=""):
        if not self.isEnabled():
            print(f"🔇 Bot en pause : Alerte de {user} ignorée.")
            return
        # File d'attente : on empile et on enchaîne sans écraser l'alerte en cours
        self.alert_queue.append((url, text, user, user_avatar, alert_id))
        if not self.is_playing:
            self._start_next()
        else:
            print(f"📥 Alerte de {user} mise en file ({len(self.alert_queue)} en attente)")

    def _start_next(self):
        if not self.alert_queue:
            self.is_playing = False
            return
        self.is_playing = True
        url, text, user, user_avatar, alert_id = self.alert_queue.pop(0)
        self._play_now(url, text, user, user_avatar, alert_id)

    def _maybe_start_next(self):
        # Relance la file seulement si rien d'autre n'a démarré entre-temps
        if not self.is_playing:
            self._start_next()

    def _on_alert_finished(self):
        # Fin d'une alerte (fin vidéo, timer image, croix, ou échec) -> on passe à la suivante
        if not self.is_playing:
            return
        self.is_playing = False
        if self.isVisible():
            self._fade_out_and_hide()  # fondu de sortie doux
        else:
            self.hide_overlay()
            QTimer.singleShot(350, self._maybe_start_next)

    def _set_overlay_opacity(self, v):
        # Applique l'opacité aux 3 fenêtres de l'overlay (vidéo/image, texte, contrôles)
        for w in (self, self.text_window, self.controls_window):
            try:
                w.setWindowOpacity(float(v))
            except Exception:
                pass

    def _fade_out_and_hide(self):
        # Fondu de sortie (opacité 1 -> 0) puis nettoyage réel + passage à la suite
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(550)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.valueChanged.connect(self._set_overlay_opacity)

        def done():
            self.hide_overlay()
            self._set_overlay_opacity(1.0)  # réarme l'opacité pour la prochaine alerte
            QTimer.singleShot(350, self._maybe_start_next)

        anim.finished.connect(done)
        self._fade_anim = anim  # garde une référence (sinon ramassé par le GC -> pas d'animation)
        anim.start()

    def _play_now(self, url, text, user, user_avatar="", alert_id=""):
        print(f"📩 Alerte de {user} : {url}")
        self.hide_overlay()
        # Nouvelle alerte -> on réinitialise l'état de vote
        global CURRENT_ALERT_ID, CURRENT_MY_VOTE
        self.current_alert_id = alert_id
        CURRENT_ALERT_ID = alert_id  # partagé au thread WS pour la resynchro à la reconnexion
        CURRENT_MY_VOTE = 0
        self.my_vote = 0
        self.up_count = 0
        self.down_count = 0
        self._update_vote_buttons()
        self.label.setText(text.upper())
        self.user_label.setText(user.upper())
        self._show_user_avatar(user_avatar)

        # On nettoie l'URL pour tester l'extension (ex: retire les paramètres après le ?)
        url_clean = url.lower().split('?')[0]

        # --- LOGIQUE D'AFFICHAGE ---
        # (Les liens plateformes YouTube/TikTok/... sont maintenant résolus CÔTÉ SERVEUR :
        #  le client reçoit directement une URL de média à lire.)

        # GIF
        if url_clean.endswith('.gif'):
            self.display_gif(url)
        # IMAGES
        elif any(url_clean.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
            self.display_image(url)
        # VIDÉO (fichier direct ou vidéo hébergée par le serveur)
        else:
            self.display_video(url)

    def display_gif(self, url):
        try:
            response = requests.get(url, timeout=10)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".gif")
            self.temp_gif_path = temp_file.name
            temp_file.write(response.content)
            temp_file.close()
            self.movie = QMovie(self.temp_gif_path)
            self.image_label.setMovie(self.movie)
            self.video_widget.hide()
            self.image_label.show()
            self.movie.start()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except:
            self._on_alert_finished()

    def display_image(self, url):
        try:
            response = requests.get(url, timeout=10)
            image = QImage.fromData(response.content)
            # Utilise self.width() et self.height() au lieu de valeurs fixes
            pixmap = QPixmap.fromImage(image).scaled(self.width(), self.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pixmap)

            self.video_widget.hide()
            self.image_label.show()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except:
            self._on_alert_finished()

    def display_video(self, path):
        self.image_label.hide()
        self.video_widget.show()
        # URL distante (serveur/Discord) -> streaming ; sinon fichier local
        if path.startswith("http://") or path.startswith("https://"):
            self.player.setSource(QUrl(path))
        else:
            self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def hide_overlay(self):
        if self.player.source().isLocalFile():
            local_path = self.player.source().toLocalFile()
            self.player.stop()
            self.player.setSource(QUrl("")) 
            QTimer.singleShot(500, lambda: self._delete_file(local_path)) 
        else:
            self.player.stop()

        if self.movie:
            self.movie.stop()
            self.movie = None
            self.image_label.setMovie(None)

        if hasattr(self, 'temp_gif_path') and self.temp_gif_path:
            path_to_del = self.temp_gif_path
            self.temp_gif_path = None
            QTimer.singleShot(500, lambda: self._delete_file(path_to_del))
            
        self.image_timer.stop()
        self._topmost_timer.stop()  # on arrête de forcer le 1er plan quand le pop est fini
        self.image_label.clear()
        self.user_label.setText("") # Reset le pseudo
        self.user_avatar_label.clear()
        self.user_avatar_label.hide()
        self.image_label.hide()
        self.video_widget.hide()
        self.text_window.hide()
        self.controls_window.hide()
        # Stoppe les animations de vote en cours et masque leur fenêtre
        for grp in list(self._pp_anims):
            try:
                grp.stop()
            except Exception:
                pass
        self._pp_anims.clear()
        for child in self.anim_window.findChildren(QLabel):
            child.deleteLater()
        self.anim_window.hide()
        self.hide()

    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except: pass

    _VOTE_IDLE = "background-color: rgba(40,40,60,210); color: white; border-radius: 19px; border: 2px solid white; font-size: 15px; font-weight: bold;"
    _VOTE_UP_ON = "background-color: rgba(64,200,110,235); color: white; border-radius: 19px; border: 2px solid white; font-size: 15px; font-weight: bold;"
    _VOTE_DOWN_ON = "background-color: rgba(235,80,110,235); color: white; border-radius: 19px; border: 2px solid white; font-size: 15px; font-weight: bold;"

    def _update_vote_buttons(self):
        self.vote_up_btn.setText(f"👍 {self.up_count}")
        self.vote_down_btn.setText(f"👎 {self.down_count}")
        self.vote_up_btn.setStyleSheet(self._VOTE_UP_ON if self.my_vote == 1 else self._VOTE_IDLE)
        self.vote_down_btn.setStyleSheet(self._VOTE_DOWN_ON if self.my_vote == -1 else self._VOTE_IDLE)

    def on_vote_counts(self, alert_id, up, down):
        # Compteurs live reçus du serveur : ne mettre à jour que le pop en cours
        if alert_id != self.current_alert_id:
            return
        self.up_count = up
        self.down_count = down
        self._update_vote_buttons()

    def _vote(self, value):
        if not self.current_alert_id:
            return
        global CURRENT_MY_VOTE
        self.my_vote = 0 if self.my_vote == value else value
        CURRENT_MY_VOTE = self.my_vote  # partagé au thread WS (re-joué si reconnexion)
        send_vote(self.current_alert_id, self.my_vote)
        # Les compteurs viennent UNIQUEMENT du serveur (broadcast vote_counts) -> tout le monde
        # voit le même total. On ne met à jour ici que la surbrillance de mon propre bouton.
        self._update_vote_buttons()

    def _show_user_avatar(self, url):
        # Affiche la photo de profil de l'auteur en haut-gauche et décale le pseudo
        pix = self._fetch_round_avatar(url) if url else None
        if pix is not None:
            self.user_avatar_label.setPixmap(pix)
            self.user_avatar_label.show()
            # Même bande verticale que l'avatar (y=14, h=64) -> pseudo centré avec la pp
            self.user_label.setGeometry(105, 14, 1000, 64)
        else:
            self.user_avatar_label.hide()
            self.user_label.setGeometry(30, 14, 1000, 64)

    def _fetch_round_avatar(self, url, size=64):
        if url in self._overlay_avatar_cache:
            return self._overlay_avatar_cache[url]
        pix = None
        try:
            data = requests.get(url, timeout=4).content
            src = QPixmap()
            src.loadFromData(data)
            if not src.isNull():
                pix = self._round_pixmap(src, size)
        except Exception:
            pix = None
        self._overlay_avatar_cache[url] = pix
        return pix

    def _round_pixmap(self, src, size):
        # Découpe l'image en cercle
        src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0.0, 0.0, float(size), float(size))
        painter.setClipPath(path)
        painter.drawPixmap((size - src.width()) // 2, (size - src.height()) // 2, src)
        painter.end()
        return out

    # --- ANIMATION DES VOTES (pp qui monte et s'estompe, façon like TikTok) ---
    def on_vote_event(self, alert_id, value, avatar_url):
        if alert_id != self.current_alert_id or not self.anim_window.isVisible() or value == 0:
            return
        img = self._pp_img_cache.get(avatar_url) if avatar_url else None
        if img is not None or not avatar_url:
            self._spawn_vote_pp(img, value)  # déjà en cache, ou aucun avatar -> placeholder direct
        else:
            # Télécharge PUIS anime -> la vraie pp s'affiche dès le 1er vote de la personne
            threading.Thread(target=self._dl_then_spawn, args=(avatar_url, value, alert_id), daemon=True).start()

    def _dl_then_spawn(self, url, value, alert_id):
        self._dl_pp_img(url)                       # remplit le cache (thread de fond)
        comm.pp_ready.emit(url, value, alert_id)   # repasse sur le thread GUI pour animer

    def _on_pp_ready(self, url, value, alert_id):
        if alert_id != self.current_alert_id or not self.anim_window.isVisible():
            return
        self._spawn_vote_pp(self._pp_img_cache.get(url), value)

    def _dl_pp_img(self, url):
        # Thread de fond : télécharge l'avatar en QImage (thread-safe) et le met en cache
        try:
            data = requests.get(url, timeout=4).content
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                self._pp_img_cache[url] = img
        except Exception:
            pass

    def _round_pp(self, img, size, value):
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(2.0, 2.0, float(size - 4), float(size - 4))
        p.setClipPath(path)
        if img is not None and not img.isNull():
            src = QPixmap.fromImage(img).scaled(size - 4, size - 4,
                                                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((size - src.width()) // 2, (size - src.height()) // 2, src)
        else:
            p.fillRect(0, 0, size, size, QColor(70, 70, 95))  # placeholder si pas d'avatar
        p.setClipping(False)
        pen = QPen(QColor(64, 200, 110) if value > 0 else QColor(235, 80, 110))
        pen.setWidth(3)
        p.setPen(pen)
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.end()
        return out

    def _spawn_vote_pp(self, img, value):
        size = 46
        lbl = QLabel(self.anim_window)
        lbl.setPixmap(self._round_pp(img, size, value))
        lbl.resize(size, size)
        w = self.anim_window.width()
        h = self.anim_window.height()
        x0 = w - size - 12 + random.randint(-8, 8)   # côté droit, près des boutons
        y0 = h - size - 6
        lbl.move(x0, y0)
        lbl.show()
        lbl.raise_()
        eff = QGraphicsOpacityEffect(lbl)
        lbl.setGraphicsEffect(eff)
        grp = QParallelAnimationGroup(self)
        move = QPropertyAnimation(lbl, b"pos")
        move.setDuration(2100)  # plus lent
        move.setStartValue(QPoint(x0, y0))
        move.setEndValue(QPoint(x0 + random.randint(-18, 4), y0 - 170))  # monte moins haut
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(eff, b"opacity")
        fade.setDuration(2100)
        fade.setStartValue(1.0)
        fade.setKeyValueAt(0.3, 1.0)
        fade.setEndValue(0.0)
        grp.addAnimation(move)
        grp.addAnimation(fade)

        def _done():
            lbl.deleteLater()
            if grp in self._pp_anims:
                self._pp_anims.remove(grp)
        grp.finished.connect(_done)
        self._pp_anims.append(grp)
        grp.start()

comm = Communicate()

def _cleanup_old_update():
    # Supprime l'ancien exe laissé après une mise à jour
    try:
        old = sys.executable + ".old"
        if os.path.exists(old):
            os.remove(old)
    except Exception:
        pass

def _get_latest_version():
    # Renvoie le n° de la dernière version dispo côté serveur, ou None si indispo.
    try:
        r = requests.get(UPDATE_BASE + "/version", timeout=8, verify=certifi.where())
        return int(r.json().get("version", 0))
    except Exception:
        return None

def apply_update():
    # Télécharge la dernière version et relance PROPREMENT via un petit script .bat :
    # le script attend que CE process soit fermé, remplace l'exe, puis relance la nouvelle version.
    # -> plus de course de fichiers/_MEI entre l'ancienne et la nouvelle instance = plus d'erreur Python.
    if not getattr(sys, "frozen", False):
        return  # pas d'update en dev (.py)
    try:
        data = requests.get(UPDATE_BASE + "/download", timeout=300, verify=certifi.where()).content
    except Exception as e:
        log(f"Téléchargement MAJ échoué: {e}")
        return
    if len(data) < 5_000_000 or data[:2] != b"MZ":
        log("MAJ ignorée : fichier invalide")
        return
    cur = sys.executable
    new_tmp = cur + ".new"
    bat = cur + ".update.bat"
    try:
        with open(new_tmp, "wb") as f:
            f.write(data)
        # Le script : attend ~3s que CE process se ferme, remplace l'exe (move /Y = écrase),
        # réessaie une fois si encore verrouillé, relance, puis se supprime lui-même. Pas de boucle infinie.
        with open(bat, "w", encoding="ascii") as f:
            f.write(
                "@echo off\r\n"
                "ping 127.0.0.1 -n 4 >nul\r\n"
                f'move /Y "{new_tmp}" "{cur}" >nul 2>&1\r\n'
                f'if exist "{new_tmp}" ( ping 127.0.0.1 -n 3 >nul & move /Y "{new_tmp}" "{cur}" >nul 2>&1 )\r\n'
                f'start "" "{cur}"\r\n'
                'del "%~f0" >nul 2>&1\r\n'
            )
        log("✅ MAJ téléchargée, relance via script...")
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000, close_fds=True)
        os._exit(0)
    except Exception as e:
        log(f"MAJ échouée: {e}")

def check_update_startup():
    # Au lancement : s'il existe une version plus récente, on prévient l'UI (bouton), sans relance surprise.
    if not getattr(sys, "frozen", False):
        return
    latest = _get_latest_version()
    if latest and latest > APP_VERSION:
        log(f"⬆️ MAJ dispo au lancement : v{latest} (actuelle v{APP_VERSION})")
        comm.update_available.emit(latest)

def update_watcher():
    # Pendant que l'app tourne : vérifie toutes les 5 min -> signale un bouton si une MAJ sort.
    if not getattr(sys, "frozen", False):
        return
    while True:
        time.sleep(300)
        latest = _get_latest_version()
        if latest and latest > APP_VERSION:
            comm.update_available.emit(latest)

def make_ssl_context():
    # On force l'utilisation du magasin de certificats fourni par 'certifi' (toujours à jour)
    # au lieu du magasin Windows, qui sur certaines machines est périmé et provoque
    # l'erreur "certificate has expired" -> empêche la connexion WSS pour ces utilisateurs.
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

def _ws_send(payload):
    # Envoie un message au serveur depuis le thread GUI (via la boucle du thread d'écoute)
    if ws_current is None or ws_loop is None:
        return False
    async def _send():
        try:
            await ws_current.send(json.dumps(payload))
        except Exception:
            pass
    try:
        asyncio.run_coroutine_threadsafe(_send(), ws_loop)
        return True
    except Exception:
        return False

def send_hello(name):
    # Pousse le pseudo (+ ID Discord + jeton admin mémorisés) vers le serveur
    _ws_send({"type": "hello", "name": name, "discord_id": CURRENT_DISCORD_ID, "admin_token": CURRENT_ADMIN_TOKEN,
              "client_id": CURRENT_CLIENT_ID, "alert_id": CURRENT_ALERT_ID})

def send_subscribe(channels):
    # Dit au serveur à quels salons on veut être abonné
    _ws_send({"type": "subscribe", "channels": channels})

def send_admin_channel(action, name):
    # Admin only : crée/supprime un salon
    _ws_send({"type": "admin_channel", "action": action, "name": name})

def send_vote(alert_id, value):
    # Vote sur un pop (👍 / 👎, comptés en live)
    _ws_send({"type": "vote", "alert_id": alert_id, "value": value})

def send_oauth(code, code_verifier):
    if not _ws_send({"type": "oauth", "code": code, "code_verifier": code_verifier, "redirect_uri": OAUTH_REDIRECT_URI}):
        print("❌ Pas connecté au serveur — réessaie la connexion Discord dans un instant.")

def start_discord_login():
    # Lance le flux OAuth dans un thread pour ne pas geler l'interface
    threading.Thread(target=_discord_login_flow, daemon=True).start()

def _discord_login_flow():
    # 1) PKCE + URL d'autorisation
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": "identify",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://discord.com/api/oauth2/authorize?" + urllib.parse.urlencode(params)

    # 2) Petit serveur local qui attend le retour de Discord
    holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            holder["code"] = qs.get("code", [None])[0]
            holder["state"] = qs.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body style='font-family:sans-serif;background:#1e1e2e;color:#fff;"
                "text-align:center;padding-top:60px'><h2>Connecté à LiveChat ✅</h2>"
                "<p>Tu peux fermer cette fenêtre.</p></body></html>".encode("utf-8")
            )

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("localhost", OAUTH_PORT), Handler)
    except OSError:
        print(f"❌ Port {OAUTH_PORT} occupé, impossible de lancer la connexion Discord.")
        return
    server.timeout = 180
    webbrowser.open(auth_url)
    server.handle_request()  # bloque jusqu'au retour de Discord (un seul appel)
    server.server_close()

    # 3) On envoie le code au serveur (qui détient le secret et fait l'échange)
    if not holder.get("code") or holder.get("state") != state:
        print("❌ Connexion Discord annulée ou échouée.")
        return
    send_oauth(holder["code"], code_verifier)

async def websocket_listener():
    global ws_current, ws_loop
    ws_loop = asyncio.get_running_loop()
    headers = {"ngrok-skip-browser-warning": "true"}
    ssl_ctx = make_ssl_context()
    log(f"▶️ Démarrage écoute WebSocket (client v{APP_VERSION})")
    while True:
        hb = None
        try:
            # keepalive : ping toutes les 20s, coupe si pas de pong sous 20s (détecte les connexions mortes)
            async with websockets.connect(SERVER_WS_URL, additional_headers=headers, ssl=ssl_ctx,
                                          ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                ws_current = ws
                log("✅ Connecté au serveur")
                # On s'annonce au serveur (pseudo + ID Discord + jeton admin + client_id stable ;
                # alert_id = pop en cours -> le serveur renvoie son compte à jour = resynchro reconnexion)
                await ws.send(json.dumps({"type": "hello", "name": CURRENT_USER, "discord_id": CURRENT_DISCORD_ID,
                                          "admin_token": CURRENT_ADMIN_TOKEN, "client_id": CURRENT_CLIENT_ID,
                                          "alert_id": CURRENT_ALERT_ID}))
                # On indique nos salons abonnés
                await ws.send(json.dumps({"type": "subscribe", "channels": CURRENT_CHANNELS}))
                # Si j'avais voté sur le pop en cours, je re-joue mon vote (une coupure a pu le perdre)
                if CURRENT_ALERT_ID and CURRENT_MY_VOTE:
                    await ws.send(json.dumps({"type": "vote", "alert_id": CURRENT_ALERT_ID, "value": CURRENT_MY_VOTE}))

                async def _heartbeat(sock):
                    # Heartbeat applicatif : garde la connexion active ET détecte une socket morte
                    try:
                        while True:
                            await asyncio.sleep(25)
                            await sock.send(json.dumps({"type": "ping"}))
                    except Exception:
                        pass  # la boucle de réception gérera la reconnexion
                hb = asyncio.ensure_future(_heartbeat(ws))

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    mtype = data.get("type")
                    if mtype == "presence":
                        comm.presence.emit(data.get("users", []))
                    elif mtype == "roster":
                        comm.roster.emit(data.get("names", []))
                    elif mtype == "identity":
                        comm.identity.emit(data)
                    elif mtype == "channels":
                        comm.channels.emit(data)
                    elif mtype == "vote_counts":
                        comm.vote_counts.emit(data.get("alert_id", ""), int(data.get("up", 0)), int(data.get("down", 0)))
                    elif mtype == "vote_event":
                        comm.vote_event.emit(data.get("alert_id", ""), int(data.get("value", 0)), data.get("avatar") or "")
                    elif "url" in data:  # alerte média (type "alert" ou ancien format)
                        comm.signal.emit(data['url'], data.get('text', ''), data.get('user', 'ANONYME'), data.get('user_avatar', ''), data.get('alert_id', ''))
        except Exception as e:
            ws_current = None
            comm.presence.emit([])  # On vide la liste tant qu'on est déconnecté
            log(f"⚠️ Déconnecté ({type(e).__name__}: {e}) — nouvelle tentative dans 5s")
            await asyncio.sleep(5)
        finally:
            if hb is not None:
                hb.cancel()

class ControlWindow(QWidget):
    def __init__(self, overlay):
        super().__init__()
        self.overlay = overlay
        self._avatar_cache = {}  # url avatar -> QIcon (évite de re-télécharger)
        self._cfg = load_config()  # réglages persistés (volume, qualité, écran...)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(270, 495)

        # Container principal avec bord arrondi et ombre
        self.container = QWidget(self)
        self.container.setGeometry(5, 5, 260, 485)
        self.container.setStyleSheet("""
            QWidget {
                background-color: #1e1e2e;
                border-radius: 15px;
                border: 1px solid #313244;
            }
            QLabel {
                color: white;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
                border: none;
            }
        """)

        layout = QVBoxLayout(self.container)
        layout.setSpacing(10) # Ajoute 10 pixels d'espace entre chaque widget
        layout.setContentsMargins(15, 15, 15, 15) # Marges intérieures plus confortables

        # --- BOUTON MISE À JOUR (caché ; apparaît quand une nouvelle version est dispo) ---
        self.update_btn = QPushButton("⬆️ Mettre à jour")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setStyleSheet("QPushButton { background:#a6e3a1; color:#11111b; font-weight:bold; border-radius:8px; padding:8px; } QPushButton:hover { background:#c0f0bb; }")
        self.update_btn.clicked.connect(self._do_update)
        self.update_btn.hide()
        layout.addWidget(self.update_btn)

        # --- LIGNE IDENTITÉ : "Je suis" (fallback, caché si connecté via Discord) ---
        self.pseudo_row = QWidget()
        self.pseudo_row.setObjectName("pseudoRow")
        self.pseudo_row.setStyleSheet("#pseudoRow { background: transparent; border: none; }")
        pseudo_layout = QHBoxLayout(self.pseudo_row)
        pseudo_layout.setContentsMargins(0, 0, 0, 0)
        self.label_pseudo = QLabel("Je suis :")
        self.pseudo_combo = QComboBox()
        self.pseudo_combo.setFixedWidth(165)
        self.pseudo_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pseudo_combo.setStyleSheet("""
            QComboBox {
                background-color: #313244;
                color: white;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 3px 8px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QComboBox:hover { border: 1px solid #89b4fa; }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background-color: #1e1e2e;
                color: white;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
                border: 1px solid #45475a;
                outline: none;
            }
        """)
        # En attendant la liste du serveur, on met au moins le choix courant
        self.pseudo_combo.addItem(CURRENT_USER)
        self.pseudo_combo.currentTextChanged.connect(self.change_pseudo)
        pseudo_layout.addWidget(self.label_pseudo)
        pseudo_layout.addStretch()
        pseudo_layout.addWidget(self.pseudo_combo)
        layout.addWidget(self.pseudo_row)
        # Caché d'emblée si on est déjà connecté via Discord (le bouton suffit)
        self.pseudo_row.setVisible(not CURRENT_DISCORD_ID)

        # --- BOUTON "Se connecter avec Discord" (récupère pseudo + pp automatiquement) ---
        self.discord_btn = QPushButton("Se connecter avec Discord")
        self.discord_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discord_btn.setStyleSheet("""
            QPushButton {
                background-color: #5865F2;
                color: white;
                font-weight: bold;
                border-radius: 8px;
                padding: 7px;
            }
            QPushButton:hover { background-color: #6b76f0; }
        """)
        self.discord_btn.clicked.connect(lambda: start_discord_login())
        if CURRENT_DISCORD_ID:
            self.discord_btn.setText(f"✅ Connecté : {CURRENT_USER}")
        layout.addWidget(self.discord_btn)

        # Ligne du Switch
        switch_layout = QHBoxLayout()
        self.label_status = QLabel("Bot Actif")
        
        self.switch = QCheckBox()
        self.switch.setChecked(True)
        self.switch.setFixedSize(50, 25)
        self.switch.setCursor(Qt.CursorShape.PointingHandCursor)
        # CSS pour transformer la Checkbox en Slider (Switch)
        self.switch.setStyleSheet("""
            QCheckBox::indicator { width: 50px; height: 25px; }
            QCheckBox::indicator:unchecked {
                image: url(''); /* Vide pour le dessin custom */
                background-color: #45475a;
                border-radius: 12px;
            }
            QCheckBox::indicator:checked {
                background-color: #a6e3a1;
                border-radius: 12px;
            }
        """)
        # Note: Pour un vrai rond qui bouge, on utilise souvent une image ou un dessin Paint
        # Ici on simplifie avec la couleur, mais on peut l'améliorer.

        self.switch.stateChanged.connect(self.toggle_bot)

        # --- Ligne Qualité (SOUS LE SWITCH BOT) ---
        quality_layout = QHBoxLayout()
        self.label_quality = QLabel("Qualité: 720p")
        
        self.quality_switch = QCheckBox()
        self.quality_switch.setFixedSize(50, 25)
        self.quality_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quality_switch.setStyleSheet("""
            QCheckBox::indicator { width: 50px; height: 25px; }
            QCheckBox::indicator:unchecked {
                background-color: #45475a;
                border-radius: 12px;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa; /* Bleu pour la HD */
                border-radius: 12px;
            }
        """)
        self.quality_switch.stateChanged.connect(self.toggle_quality)
        if str(self._cfg.get("quality", "720")) == "1080":
            self.quality_switch.setChecked(True)  # applique 1080p via toggle_quality

        quality_layout.addWidget(self.label_quality)
        quality_layout.addStretch()
        quality_layout.addWidget(self.quality_switch)
        layout.addLayout(quality_layout) # On l'ajoute au layout principal

        # --- LIGNE ÉCRAN DE SORTIE ---
        screen_layout = QHBoxLayout()
        self.label_screen = QLabel("Écran :")
        self.screen_combo = QComboBox()
        self.screen_combo.setFixedWidth(165)
        self.screen_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.screen_combo.setStyleSheet("""
            QComboBox {
                background-color: #313244;
                color: white;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 3px 8px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QComboBox:hover { border: 1px solid #89b4fa; }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background-color: #1e1e2e;
                color: white;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
                border: 1px solid #45475a;
                outline: none;
            }
        """)
        self.populate_screens()
        self.screen_combo.currentIndexChanged.connect(self.change_screen)

        screen_layout.addWidget(self.label_screen)
        screen_layout.addStretch()
        screen_layout.addWidget(self.screen_combo)
        layout.addLayout(screen_layout)

        switch_layout.addWidget(self.label_status)
        switch_layout.addStretch()
        switch_layout.addWidget(self.switch)
        layout.addLayout(switch_layout)

        # --- LIGNE DÉMARRAGE AUTOMATIQUE AVEC WINDOWS ---
        autostart_layout = QHBoxLayout()
        self.label_autostart = QLabel("Démarrer avec Windows")
        self.autostart_switch = QCheckBox()
        self.autostart_switch.setFixedSize(50, 25)
        self.autostart_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.autostart_switch.setStyleSheet("""
            QCheckBox::indicator { width: 50px; height: 25px; }
            QCheckBox::indicator:unchecked { background-color: #45475a; border-radius: 12px; }
            QCheckBox::indicator:checked { background-color: #a6e3a1; border-radius: 12px; }
        """)
        self.autostart_switch.setChecked(is_autostart_enabled())  # avant connect = pas de trigger
        self.autostart_switch.stateChanged.connect(self.toggle_autostart)
        autostart_layout.addWidget(self.label_autostart)
        autostart_layout.addStretch()
        autostart_layout.addWidget(self.autostart_switch)
        layout.addLayout(autostart_layout)

        # Bouton Quitter
        self.quit_btn = QPushButton("Quitter le Bot")
        self.quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quit_btn.setStyleSheet("""
            QPushButton {
                background-color: #f38ba8;
                color: #11111b;
                font-weight: bold;
                border-radius: 8px;
                padding: 8px;
                margin-top: 10px;
            }
            QPushButton:hover { background-color: #eba0ac; }
        """)


        # --- LIGNE VOLUME ---
        volume_layout = QHBoxLayout()
        self.label_volume = QLabel("Volume: 50%")

        # ON FIXE LA LARGEUR ICI (pour éviter que le slider ne bouge)
        self.label_volume.setFixedWidth(100)
        
        saved_volume = int(self._cfg.get("volume", 50))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(saved_volume)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #313244;
                height: 4px;
                background: #45475a;
                margin: 2px 0;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #89b4fa;
                border: 1px solid #89b4fa;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
        """)
        
        self.volume_slider.valueChanged.connect(self.update_volume)
        # Applique + affiche le volume sauvegardé au démarrage
        self.label_volume.setText(f"Volume: {saved_volume}%")
        self.overlay.audio.setVolume(saved_volume / 100.0)

        volume_layout.addWidget(self.label_volume)
        volume_layout.addStretch()
        volume_layout.addWidget(self.volume_slider)
        layout.addLayout(volume_layout)

        # --- SALONS (channels) : on coche ceux qu'on veut recevoir ---
        self.is_admin = False
        self.label_channels = QLabel("Salons :")
        self.label_channels.setStyleSheet("color: #89b4fa; font-weight: bold;")
        layout.addWidget(self.label_channels)

        self.channels_scroll = QScrollArea()
        self.channels_scroll.setWidgetResizable(True)
        self.channels_scroll.setFixedHeight(80)
        self.channels_scroll.setStyleSheet("QScrollArea { background:#181825; border:1px solid #313244; border-radius:8px; }")
        self.channels_container = QWidget()
        self.channels_container.setStyleSheet("background: transparent; border: none;")
        self.channels_layout = QVBoxLayout(self.channels_container)
        self.channels_layout.setContentsMargins(8, 6, 8, 6)
        self.channels_layout.setSpacing(4)
        self.channels_scroll.setWidget(self.channels_container)
        layout.addWidget(self.channels_scroll)

        # Ligne admin (créer un salon) — visible seulement pour EWEN
        self.admin_row = QWidget()
        self.admin_row.setObjectName("adminRow")
        self.admin_row.setStyleSheet("#adminRow { background: transparent; border: none; }")
        admin_layout = QHBoxLayout(self.admin_row)
        admin_layout.setContentsMargins(0, 0, 0, 0)
        self.new_channel_edit = QLineEdit()
        self.new_channel_edit.setPlaceholderText("nouveau salon")
        self.new_channel_edit.setStyleSheet("QLineEdit { background:#313244; color:white; border:1px solid #45475a; border-radius:6px; padding:3px 6px; font-size:12px; }")
        self.new_channel_btn = QPushButton("Créer")
        self.new_channel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_channel_btn.setStyleSheet("QPushButton { background:#a6e3a1; color:#11111b; font-weight:bold; border-radius:6px; padding:4px 10px; } QPushButton:hover { background:#94d3a2; }")
        self.new_channel_btn.clicked.connect(self._admin_create_channel)
        admin_layout.addWidget(self.new_channel_edit)
        admin_layout.addWidget(self.new_channel_btn)
        layout.addWidget(self.admin_row)
        self.admin_row.hide()

        # --- LISTE DES PERSONNES CONNECTÉES À LIVECHAT ---
        self.label_connected = QLabel("Connectés (0)")
        self.label_connected.setStyleSheet("color: #89b4fa; font-weight: bold;")
        layout.addWidget(self.label_connected)

        self.users_list = QListWidget()
        self.users_list.setFixedHeight(105)
        self.users_list.setIconSize(QSize(24, 24))
        self.users_list.setStyleSheet("""
            QListWidget {
                background-color: #181825;
                color: white;
                border: 1px solid #313244;
                border-radius: 8px;
                font-size: 13px;
                padding: 4px;
            }
            QListWidget::item { padding: 3px 4px; }
        """)
        layout.addWidget(self.users_list)

        self.quit_btn.clicked.connect(app.quit)
        layout.addWidget(self.quit_btn)

        # Version (petit, discret, en bas à droite)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.version_label.setStyleSheet("color: #6c7086; font-size: 10px; border: none; background: transparent;")
        layout.addWidget(self.version_label)

        self._refresh_height()

    def _refresh_height(self):
        # Hauteur dynamique selon les sections visibles
        h = 512  # inclut le petit label de version en bas
        if self.pseudo_row.isVisible():
            h += 40
        h += 110  # section salons (label + liste scrollable)
        if getattr(self, "is_admin", False):
            h += 40  # ligne "créer un salon" (admin)
        if getattr(self, "update_btn", None) is not None and self.update_btn.isVisible():
            h += 48  # bouton "Mettre à jour"
        self.setFixedSize(270, h)
        self.container.setGeometry(5, 5, 260, h - 10)

    def on_update_available(self, latest):
        # Une MAJ est dispo : on affiche le bouton + une notif systray
        self.update_btn.setText(f"⬆️ Mettre à jour (v{latest})")
        self.update_btn.show()
        self._refresh_height()
        try:
            tray_icon.showMessage("LiveChat",
                                  f"Mise à jour v{latest} dispo — clique sur l'icône puis « Mettre à jour ».",
                                  QSystemTrayIcon.MessageIcon.Information, 8000)
        except Exception:
            pass

    def _do_update(self):
        # Lance la mise à jour (télécharge + relance via script). apply_update() termine par os._exit.
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Mise à jour en cours…")
        threading.Thread(target=apply_update, daemon=True).start()

    def toggle_bot(self, state):
        is_active = (state == 2) # 2 = Checked
        self.overlay.setEnabled(is_active)
        self.label_status.setText("Bot Actif" if is_active else "Bot en Pause")
        self.label_status.setStyleSheet("color: #a6e3a1;" if is_active else "color: #f38ba8;")

    def toggle_autostart(self, state):
        if not set_autostart(state == 2):
            print("❌ Impossible de modifier le démarrage automatique")

    def toggle_quality(self, state):
        is_hd = (state == 2) # Checked = 1080p
        if is_hd:
            self.overlay.quality = "1080"
            self.overlay.update_resolution(1920, 1080)
        else:
            self.overlay.quality = "720"
            self.overlay.update_resolution(1280, 720)
            
        self.label_quality.setText(f"Qualité: {self.overlay.quality}p")
        update_config(quality=self.overlay.quality)

    def populate_screens(self):
        # Mémorise l'écran actuellement sélectionné pour le conserver après rafraîchissement
        prev = None
        try:
            if hasattr(self, 'screens_list') and 0 <= self.screen_combo.currentIndex() < len(self.screens_list):
                prev = self.screens_list[self.screen_combo.currentIndex()]
        except RuntimeError:
            prev = None

        self.screen_combo.blockSignals(True)
        self.screen_combo.clear()
        self.screens_list = QApplication.screens()
        primary = QApplication.primaryScreen()
        select_index = 0
        for i, sc in enumerate(self.screens_list):
            geo = sc.geometry()
            star = " ★" if sc == primary else ""
            self.screen_combo.addItem(f"Écran {i + 1} ({geo.width()}x{geo.height()}){star}")
            if sc == primary:
                select_index = i

        # Conserve le choix précédent s'il existe toujours
        try:
            if prev is not None and prev in self.screens_list:
                select_index = self.screens_list.index(prev)
        except RuntimeError:
            pass

        # Première fois : applique l'écran sauvegardé (par son nom)
        if prev is None:
            saved = self._cfg.get("screen")
            if saved:
                for i, sc in enumerate(self.screens_list):
                    if sc.name() == saved:
                        select_index = i
                        break

        self.screen_combo.setCurrentIndex(select_index)
        self.screen_combo.blockSignals(False)
        self.change_screen(select_index)

    def change_screen(self, index):
        if 0 <= index < len(self.screens_list):
            screen = self.screens_list[index]
            self.overlay.set_screen(screen)
            update_config(screen=screen.name())

    def change_pseudo(self, name=None):
        global CURRENT_USER, CURRENT_DISCORD_ID, CURRENT_ADMIN_TOKEN
        name = (name if name is not None else self.pseudo_combo.currentText()).strip()[:32]
        if not name:
            return
        CURRENT_USER = name
        # Choisir un nom manuellement = on quitte l'identité OAuth (et les droits admin)
        CURRENT_DISCORD_ID = None
        CURRENT_ADMIN_TOKEN = None
        update_config(pseudo=name, discord_id=None, admin_token=None)
        send_hello(name)  # Met à jour le pseudo côté serveur en direct

    def on_identity(self, data):
        # Reçu après une connexion Discord réussie : on mémorise l'ID (+ jeton admin) pour la suite
        global CURRENT_USER, CURRENT_DISCORD_ID, CURRENT_ADMIN_TOKEN
        CURRENT_DISCORD_ID = data.get("discord_id")
        CURRENT_USER = data.get("name", CURRENT_USER)
        if data.get("admin_token"):
            CURRENT_ADMIN_TOKEN = data["admin_token"]
        update_config(pseudo=CURRENT_USER, discord_id=CURRENT_DISCORD_ID, admin_token=CURRENT_ADMIN_TOKEN)
        # Reflète le nom dans le menu déroulant
        self.pseudo_combo.blockSignals(True)
        if self.pseudo_combo.findText(CURRENT_USER) < 0:
            self.pseudo_combo.insertItem(0, CURRENT_USER)
        self.pseudo_combo.setCurrentText(CURRENT_USER)
        self.pseudo_combo.blockSignals(False)
        self.discord_btn.setText(f"✅ Connecté : {CURRENT_USER}")
        self.pseudo_row.hide()  # plus besoin du menu déroulant une fois connecté
        self._refresh_height()
        print(f"✅ Identité Discord : {CURRENT_USER}")

    def on_channels(self, data):
        # Reçoit la liste des salons + le statut admin, et (re)construit les cases à cocher
        global CURRENT_CHANNELS
        self.is_admin = bool(data.get("is_admin"))
        server_list = data.get("list", ["general"])
        CURRENT_CHANNELS = [c for c in CURRENT_CHANNELS if c in server_list] or ["general"]
        update_config(channels=CURRENT_CHANNELS)

        while self.channels_layout.count():
            item = self.channels_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for ch in server_list:
            row = QWidget()
            row.setStyleSheet("background: transparent; border: none;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            cb = QCheckBox(ch)
            cb.setChecked(ch in CURRENT_CHANNELS)
            cb.setStyleSheet("QCheckBox { color: white; font-size: 13px; } QCheckBox::indicator { width:16px; height:16px; }")
            cb.stateChanged.connect(self._channels_changed)
            rl.addWidget(cb)
            rl.addStretch()
            if self.is_admin and ch != "general":
                delb = QPushButton("×")
                delb.setFixedSize(20, 20)
                delb.setCursor(Qt.CursorShape.PointingHandCursor)
                delb.setStyleSheet("QPushButton { background:#f38ba8; color:#11111b; border-radius:10px; font-weight:bold; } QPushButton:hover { background:#eba0ac; }")
                delb.clicked.connect(lambda _, name=ch: send_admin_channel("del", name))
                rl.addWidget(delb)
            self.channels_layout.addWidget(row)

        self.admin_row.setVisible(self.is_admin)
        self._refresh_height()

    def _channels_changed(self):
        global CURRENT_CHANNELS
        chosen = []
        for i in range(self.channels_layout.count()):
            w = self.channels_layout.itemAt(i).widget()
            if w:
                cb = w.findChild(QCheckBox)
                if cb and cb.isChecked():
                    chosen.append(cb.text())
        CURRENT_CHANNELS = chosen or ["general"]
        update_config(channels=CURRENT_CHANNELS)
        send_subscribe(CURRENT_CHANNELS)

    def _admin_create_channel(self):
        name = "".join(c for c in self.new_channel_edit.text().strip().lower() if c.isalnum() or c in "-_")[:24]
        if name:
            send_admin_channel("add", name)
            self.new_channel_edit.clear()

    def set_roster(self, names):
        # Remplit le menu déroulant "Je suis :" avec la liste envoyée par le serveur
        self.pseudo_combo.blockSignals(True)
        self.pseudo_combo.clear()
        items = list(names)
        # On garde le choix sauvegardé même s'il n'est pas (encore) dans la liste
        if CURRENT_USER and CURRENT_USER not in items:
            items.insert(0, CURRENT_USER)
        self.pseudo_combo.addItems(items)
        idx = self.pseudo_combo.findText(CURRENT_USER)
        if idx >= 0:
            self.pseudo_combo.setCurrentIndex(idx)
        self.pseudo_combo.blockSignals(False)

    def update_presence(self, users):
        # Reçoit la liste des connectés ({name, avatar} ou simple pseudo) et met à jour l'affichage
        self.users_list.clear()
        for u in users:
            if isinstance(u, dict):
                name = u.get("name", "Anonyme")
                avatar = u.get("avatar")
            else:
                name = u
                avatar = None
            suffix = "  (moi)" if name == CURRENT_USER else ""
            item = QListWidgetItem(f"{name}{suffix}")
            icon = self._avatar_icon(avatar)
            if icon is not None:
                item.setIcon(icon)
            else:
                item.setText(f"🟢  {name}{suffix}")  # Pastille verte si pas d'avatar
            self.users_list.addItem(item)
        self.label_connected.setText(f"Connectés ({len(users)})")

    def _avatar_icon(self, url):
        # Télécharge (et met en cache) l'avatar Discord pour l'afficher dans la liste
        if not url:
            return None
        if url in self._avatar_cache:
            return self._avatar_cache[url]
        icon = None
        try:
            data = requests.get(url, timeout=3).content
            pix = QPixmap()
            pix.loadFromData(data)
            icon = QIcon(pix.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except Exception:
            icon = None
        self._avatar_cache[url] = icon
        return icon

    def update_volume(self, value):
        # PyQt6 utilise une échelle de 0.0 à 1.0 pour le volume
        float_volume = value / 100.0
        self.overlay.audio.setVolume(float_volume)
        self.label_volume.setText(f"Volume: {value}%")
        update_config(volume=value)

    def show_near_tray(self):
        # Rafraîchit la liste si un écran a été branché/débranché entre-temps
        if len(QApplication.screens()) != self.screen_combo.count():
            self.populate_screens()

        # Positionne la fenêtre juste au dessus de la barre des tâches
        tray_geo = tray_icon.geometry()
        self.move(tray_geo.x() - self.width() + 30, tray_geo.y() - self.height() - 10)
        self.show()
        self.activateWindow()

    def changeEvent(self, event): # type: ignore
        # On détecte quand la fenêtre perd l'état "actif" (clic ailleurs)
        if event.type() == event.Type.ActivationChange:
            # Ne pas masquer si un menu déroulant (ex: choix de l'écran) est ouvert
            popup_open = QApplication.activePopupWidget() is not None
            if not self.isActiveWindow() and not popup_open:
                self.hide()
        super().changeEvent(event)

if __name__ == "__main__":
    # Journalise toute erreur non gérée -> pour diagnostiquer une "erreur Python" au lancement
    def _log_excepthook(exc_type, exc, tb):
        import traceback
        log("💥 ERREUR NON GÉRÉE:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = _log_excepthook
    log(f"===== LiveChat v{APP_VERSION} démarre (pid {os.getpid()}) =====")

    _cleanup_old_update()  # supprime l'ancien exe laissé par une MAJ précédente
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay()
    control_win = ControlWindow(overlay) # Création de la mini fenêtre
    comm.signal.connect(overlay.play_alert)
    comm.presence.connect(control_win.update_presence)
    comm.roster.connect(control_win.set_roster)
    comm.identity.connect(control_win.on_identity)
    comm.channels.connect(control_win.on_channels)
    comm.vote_counts.connect(overlay.on_vote_counts)
    comm.vote_event.connect(overlay.on_vote_event)
    comm.pp_ready.connect(overlay._on_pp_ready)
    comm.update_available.connect(control_win.on_update_available)

    tray_icon = QSystemTrayIcon(app)
    
    # Icône

    def resource_path(relative_path):
        try:
            # PyInstaller crée un dossier temporaire et stocke le chemin dans _MEIPASS
            base_path = sys._MEIPASS # type: ignore
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)


    # Utilise la fonction resource_path pour trouver icon.png
    icon_path = resource_path("icon.png")

    if os.path.exists(icon_path):
        tray_icon.setIcon(QIcon(icon_path))
    else:
        # C'est ce qui s'affiche actuellement (l'icône par défaut)
        tray_icon.setIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)) # type: ignore

    # Au clic sur l'icône, on affiche la fenêtre de contrôle
    def on_tray_click(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger: # Clic gauche
            if control_win.isVisible():
                control_win.hide()
            else:
                control_win.show_near_tray()
                # On force Windows à donner le focus à la petite fenêtre
                control_win.raise_()
                control_win.activateWindow()

    tray_icon.activated.connect(on_tray_click)
    
    # On garde quand même un menu clic droit rapide
    menu = QMenu()
    quit_action = QAction("Quitter", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray_icon.setContextMenu(menu)

    tray_icon.show()

    threading.Thread(target=check_update_startup, daemon=True).start()  # MAJ dispo au lancement -> bouton
    threading.Thread(target=update_watcher, daemon=True).start()        # vérifie en continu -> bouton
    threading.Thread(target=lambda: asyncio.run(websocket_listener()), daemon=True).start()
    sys.exit(app.exec())