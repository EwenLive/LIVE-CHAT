import sys
import json
import asyncio
import threading
import websockets
import os
import requests
import tempfile
import yt_dlp
import uuid
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QGraphicsDropShadowEffect, QPushButton
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QColor, QPixmap, QImage, QMovie
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from yt_dlp.utils import match_filter_func

# --- CONFIGURATION ---
SERVER_WS_URL = "wss://decomposable-untawdry-shaquana.ngrok-free.dev/ws" 
IMAGE_DURATION = 5000 

class Communicate(QObject):
    # Le signal reçoit maintenant : URL, Texte, et Pseudo
    signal = pyqtSignal(str, str, str)

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
        
        # --- LOGIQUE PSEUDO (HAUT GAUCHE) ---
        self.user_label = QLabel("", self.text_window)
        self.user_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.user_label.setStyleSheet("font-family: 'Impact'; font-size: 35px; color: white; background: transparent;")
        self.user_label.setGeometry(30, 20, 1000, 60) 

        self.user_shadow = QGraphicsDropShadowEffect()
        self.user_shadow.setBlurRadius(1)
        self.user_shadow.setXOffset(3)
        self.user_shadow.setYOffset(3)
        self.user_shadow.setColor(QColor(0, 0, 0))
        self.user_label.setGraphicsEffect(self.user_shadow)

        # --- LABEL MESSAGE (BAS) ---
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
        self.temp_gif_path = None
        self.hide()
        self.text_window.hide()


    def get_direct_youtube_url(self, url):
        import sys
        if hasattr(sys, '_MEIPASS'):
            ffmpeg_path = os.path.join(sys._MEIPASS, "ffmpeg.exe") # type: ignore
        else:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            ffmpeg_path = os.path.join(current_dir, "ffmpeg.exe")
            if not os.path.exists(ffmpeg_path):
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
            'match_filter': match_filter_func("duration <= 5400"), 
            'force_keyframes_at_cuts': True,
            'nocheckcertificate': True,
            'rm_cachedir': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl: # type: ignore
                info = ydl.extract_info(url, download=False)
                if info is None:
                    return None
                ydl.download([url])
                if os.path.exists(temp_path):
                    return temp_path
        except Exception as e:
            print(f"❌ Erreur Vidéo : {e}")
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

    def play_alert(self, url, text, user):
        print(f"📩 Alerte de {user} : {url}")
        self.hide_overlay() 
        self.label.setText(text.upper())
        self.user_label.setText(user.upper())
        
        # On nettoie l'URL pour tester l'extension (ex: retire les paramètres après le ?)
        url_clean = url.lower().split('?')[0]
        
        # --- LOGIQUE DE PRIORITÉ ---
        
        # 1. FICHIERS VIDÉO DIRECTS (Priorité Haute pour Discord)
        # Si ça finit par .mp4, .mov ou .avi, on joue direct sans passer par yt-dlp
        if any(url_clean.endswith(ext) for ext in ['.mp4', '.mov', '.avi']):
            print("🎬 Fichier vidéo direct détecté (Discord/Lien direct).")
            self.display_video(url)

        # 2. LIENS DE PLATEFORMES (YouTube, TikTok, Twitter, Instagram)
        elif any(site in url_clean for site in ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "twitter.com", "x.com"]):
            print("🔗 Lien de plateforme détecté, extraction via yt-dlp...")
            direct_url = self.get_direct_youtube_url(url)
            if direct_url:
                self.display_video(direct_url)
            else:
                # Si yt-dlp échoue sur Twitter/X, on tente de récupérer l'image
                if "twitter.com" in url_clean or "x.com" in url_clean:
                    try:
                        api_url = url.replace("twitter.com", "api.fxtwitter.com").replace("x.com", "api.fxtwitter.com")
                        res = requests.get(api_url, timeout=5).json()
                        photos = res.get('tweet', {}).get('media', {}).get('photos', [])
                        if photos:
                            self.display_image(photos[0].get('url'))
                    except: 
                        print("❌ Échec de la récupération d'image Twitter.")

        # 3. GIFS
        elif url_clean.endswith('.gif'):
            self.display_gif(url)
            
        # 4. IMAGES CLASSIQUES
        elif any(url_clean.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
            self.display_image(url)
            
        # 5. PAR DÉFAUT
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
        except: pass

    def display_image(self, url):
        try:
            response = requests.get(url, timeout=10)
            image = QImage.fromData(response.content)
            pixmap = QPixmap.fromImage(image).scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
            
            # --- CORRECTION ICI : BAS À DROITE ---
            # On calcule la position pour que le bouton soit au bord de l'image affichée
            img_width = pixmap.width()
            img_height = pixmap.height()
            start_x = (1280 - img_width) // 2
            start_y = (720 - img_height) // 2
            
            self.close_btn.move(start_x + img_width - 55, start_y + img_height - 55)
            # ------------------------------------

            self.video_widget.hide()
            self.image_label.show()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except: pass

    def display_video(self, path):
        self.image_label.hide()
        self.video_widget.show()
        
        # Place le bouton en bas à droite de l'écran 1280x720
        self.close_btn.move(1210, 650) 
        
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
        self.image_label.clear()
        self.user_label.setText("") # Reset le pseudo
        self.image_label.hide()
        self.video_widget.hide()
        self.text_window.hide()
        self.hide()

    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except: pass

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
                    # Envoie maintenant 3 arguments au signal
                    comm.signal.emit(data['url'], data['text'], data.get('user', 'ANONYME'))
        except Exception as e:
            await asyncio.sleep(5)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = Overlay()
    comm.signal.connect(overlay.play_alert)
    threading.Thread(target=lambda: asyncio.run(websocket_listener()), daemon=True).start()
    sys.exit(app.exec())