import sys
import json
import asyncio
import threading
import websockets
import os
import requests
import tempfile
import yt_dlp
import typing # Ajouté pour le typage
import uuid
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QGraphicsDropShadowEffect, QPushButton
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QColor, QPixmap, QImage, QMovie
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# --- CONFIGURATION ---
SERVER_WS_URL = str(os.getenv("Ngrok_Token"))  
IMAGE_DURATION = 5000 

class Communicate(QObject):
    signal = pyqtSignal(str, str)

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(1280, 720)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setGeometry(0, 0, 1280, 720)
        self.video_widget.hide()

        self.image_label = QLabel(self)
        self.image_label.setGeometry(0, 0, 1280, 720)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.hide()

        self.text_window = QWidget() 
        self.text_window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.text_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.text_window.setFixedSize(1280, 720)
        
        self.label = QLabel("", self.text_window)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True) 
        self.label.setStyleSheet("font-family: 'Impact'; font-size: 65px; color: yellow; background: transparent;")
        self.label.setGeometry(50, 430, 1180, 250) 

        self.close_btn = QPushButton("✕", self.text_window)
        self.close_btn.setFixedSize(45, 45)
        self.close_btn.setStyleSheet("background-color: rgba(255, 0, 0, 180); color: white; border-radius: 22px; border: 2px solid white;")
        self.close_btn.clicked.connect(self.hide_overlay)
        self.close_btn.hide()

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
        self.image_timer.timeout.connect(self.hide_overlay)

        self.movie = None
        self.temp_gif = None
        self.hide()
        self.text_window.hide()


    def get_direct_youtube_url(self, url):
        import sys
        # 1. On définit le chemin de ffmpeg
        if hasattr(sys, '_MEIPASS'):
            # Mode .EXE (PyInstaller)
            ffmpeg_path = os.path.join(sys._MEIPASS, "ffmpeg.exe") # type: ignore
        else:
            # Mode SCRIPT (.py) : On cherche le ffmpeg.exe dans le dossier actuel
            # On récupère le chemin du dossier où se trouve client.py
            current_dir = os.path.dirname(os.path.abspath(__file__))
            ffmpeg_path = os.path.join(current_dir, "ffmpeg.exe")

            # Petite vérification de sécurité pour toi
            if not os.path.exists(ffmpeg_path):
                print(f"⚠️ Alerte : ffmpeg.exe non trouvé dans {current_dir}")
                print("On essaie d'utiliser le PATH Windows par défaut...")
                ffmpeg_path = "ffmpeg"

        temp_dir = tempfile.gettempdir() 
        file_name = f"yt_alert_{uuid.uuid4().hex}.mp4"
        temp_path = os.path.join(temp_dir, file_name)

        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': temp_path,
            'noplaylist': True,
            'ffmpeg_location': ffmpeg_path,
            'quiet': True,
            'no_warnings': True,
            # --- OPTIONS ANTI-BLOCAGE ---
            'nocheckcertificate': True,
            'rm_cachedir': True,  # Vide le cache des anciens jetons expirés
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl: # type: ignore
                ydl.download([url])
                # Vérification que le fichier existe bien avant de continuer
                if os.path.exists(temp_path):
                    print(f"✅ Vidéo téléchargée : {temp_path}")
                    return temp_path
                else:
                    print("❌ Fichier non trouvé après téléchargement.")
                    return None
        except Exception as e:
            print(f"❌ Erreur yt-dlp : {e}")
            return None

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.show_all()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.hide_overlay()

    def show_all(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            pos_x = geo.left() + (geo.width() - 1280) // 2
            pos_y = geo.top() + (geo.height() - 720) // 2
            self.move(pos_x, pos_y)
            self.text_window.move(pos_x, pos_y)

        self.show()
        self.raise_()
        self.activateWindow()
        self.text_window.show()
        self.text_window.raise_()
        self.close_btn.show()
        print("🖥️ Overlay affiché.")

    def play_alert(self, url, text):
        print(f"📩 Alerte : {url}")
        self.hide_overlay() 
        self.label.setText(text.upper())
        
        url_lower = url.lower().split('?')[0]
        
        # 1. TEST YOUTUBE
        if "youtube.com" in url or "youtu.be" in url:
            direct_url = self.get_direct_youtube_url(url)
            if direct_url:
                self.display_video(direct_url)
            else:
                print("❌ Impossible de lire cette vidéo YouTube.")
        
        # 2. TEST GIF
        elif url_lower.endswith('.gif'):
            self.display_gif(url)
            
        # 3. TEST IMAGE
        elif any(url_lower.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
            self.display_image(url)
            
        # 4. AUTRE (MP4 DIRECT)
        else:
            self.display_video(url)

    def display_gif(self, url):
        try:
            response = requests.get(url, timeout=10)
            self.temp_gif = tempfile.NamedTemporaryFile(delete=False, suffix=".gif")
            self.temp_gif.write(response.content)
            self.temp_gif.close()
            self.movie = QMovie(self.temp_gif.name)
            self.movie.jumpToFrame(0)
            img_size = self.movie.currentImage().size()
            scaled = img_size.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio)
            self.movie.setScaledSize(scaled)
            self.close_btn.move((1280 - scaled.width()) // 2 + scaled.width() - 50, (720 - scaled.height()) // 2 + scaled.height() - 50)
            self.image_label.setMovie(self.movie)
            self.video_widget.hide()
            self.image_label.show()
            self.movie.start()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except Exception as e:
            print(f"Erreur GIF : {e}")

    def display_image(self, url):
        try:
            response = requests.get(url, timeout=10)
            image = QImage.fromData(response.content)
            pixmap = QPixmap.fromImage(image).scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
            self.close_btn.move((1280 - pixmap.width()) // 2 + pixmap.width() - 50, (720 - pixmap.height()) // 2 + pixmap.height() - 50)
            self.video_widget.hide()
            self.image_label.show()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except Exception as e:
            print(f"Erreur Image : {e}")

    def display_video(self, path):
        self.image_label.hide()
        self.video_widget.show()
        self.close_btn.move(1210, 650) 
        
        # Correction ici : conversion explicite du chemin local
        file_url = QUrl.fromLocalFile(path)
        print(f"🎬 Lecture de l'URL : {file_url.toString()}")
        
        self.player.setSource(file_url)
        self.player.play()

    def hide_overlay(self):
        # 1. Gestion de la vidéo (Nettoyage propre)
        if self.player.source().isLocalFile():
            local_path = self.player.source().toLocalFile()
            self.player.stop()
            self.player.setSource(QUrl("")) # Désengage le fichier immédiatement
            
            # On attend 500ms que Windows libère le verrou sur le fichier
            QTimer.singleShot(500, lambda: self._delete_file(local_path)) 
        else:
            self.player.stop()

        # 2. Gestion du GIF
        if self.movie:
            self.movie.stop()
            self.movie = None
        if self.temp_gif:
            try:
                gif_path = self.temp_gif.name
                if os.path.exists(gif_path):
                    os.remove(gif_path)
            except: pass
            self.temp_gif = None
            
        # 3. Cache de l'interface
        self.image_timer.stop()
        self.image_label.clear()
        self.image_label.hide()
        self.video_widget.hide()
        self.close_btn.hide()
        self.text_window.hide()
        self.hide()

    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"🗑️ Fichier temporaire supprimé : {path}")
        except Exception as e:
            print(f"⚠️ Impossible de supprimer le fichier : {e}")

# --- RÉSEAU ---
comm = Communicate()
async def websocket_listener():
    headers = {"ngrok-skip-browser-warning": "true"}
    while True:
        try:
            async with websockets.connect(SERVER_WS_URL, additional_headers=headers) as ws:
                print("✅ Connecté au serveur")
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    comm.signal.emit(data['url'], data['text'])
        except Exception as e:
            print(f"Connection lost: {e}. Retrying...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = Overlay()
    comm.signal.connect(overlay.play_alert)
    threading.Thread(target=lambda: asyncio.run(websocket_listener()), daemon=True).start()
    sys.exit(app.exec())