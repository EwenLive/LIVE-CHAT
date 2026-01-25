import sys
import json
import asyncio
import threading
import websockets
import os
import requests
import tempfile
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QGraphicsDropShadowEffect, QPushButton
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QColor, QPixmap, QImage, QMovie
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# --- CONFIGURATION ---
SERVER_WS_URL = "wss://decomposable-untawdry-shaquana.ngrok-free.dev/ws"
IMAGE_DURATION = 5000 

os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.multimedia.ffmpeg=false"

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

        self.movie = None
        self.temp_gif = None

        self.text_window = QWidget() 
        self.text_window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        self.text_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.text_window.setFixedSize(1280, 720)
        
        # --- MODIFICATION DU TEXTE ---
        self.label = QLabel("", self.text_window)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Permet le retour à la ligne automatique
        self.label.setWordWrap(True) 
        # Taille 65px au lieu de 75px pour que 2 lignes rentrent mieux
        self.label.setStyleSheet("font-family: 'Impact'; font-size: 65px; color: yellow; background: transparent;")
        # On augmente la hauteur de 180 à 250 pour laisser de la place à la 2ème ligne
        self.label.setGeometry(50, 430, 1180, 250) 

        self.close_btn = QPushButton("✕", self.text_window)
        self.close_btn.setFixedSize(45, 45)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 0, 0, 180);
                color: white;
                font-size: 22px;
                font-weight: bold;
                border-radius: 22px;
                border: 2px solid white;
            }
            QPushButton:hover { background-color: rgb(255, 0, 0); }
        """)
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

        self.hide()
        self.text_window.hide()

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.show_all()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.hide_overlay()

    def show_all(self):
        self.show()
        self.text_window.show()
        self.close_btn.show()
        self.text_window.raise_()

    def play_alert(self, url, text):
        self.hide_overlay() 
        self.label.setText(text.upper())
        
        url_lower = url.lower().split('?')[0]
        is_gif = url_lower.endswith('.gif')
        is_image = any(url_lower.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp'])

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.left() + (geo.width() - 1280) // 2, geo.top() + (geo.height() - 720) // 2)
            self.text_window.move(self.pos())

        if is_gif:
            self.display_gif(url)
        elif is_image:
            self.display_image(url)
        else:
            self.display_video(url)

    def display_gif(self, url):
        try:
            response = requests.get(url)
            self.temp_gif = tempfile.NamedTemporaryFile(delete=False, suffix=".gif")
            self.temp_gif.write(response.content)
            self.temp_gif.close()

            self.movie = QMovie(self.temp_gif.name)
            self.movie.jumpToFrame(0)
            orig_size = self.movie.currentImage().size()
            scaled_size = orig_size.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio)
            self.movie.setScaledSize(scaled_size)
            
            w, h = scaled_size.width(), scaled_size.height()
            self.close_btn.move((1280 - w) // 2 + w - 50, (720 - h) // 2 + h - 50)

            self.image_label.setMovie(self.movie)
            self.video_widget.hide()
            self.image_label.show()
            self.movie.start()
            
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except Exception as e:
            print(f"Erreur GIF: {e}")

    def display_image(self, url):
        try:
            response = requests.get(url)
            image = QImage.fromData(response.content)
            pixmap = QPixmap.fromImage(image)
            scaled = pixmap.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(scaled)
            
            w, h = scaled.width(), scaled.height()
            self.close_btn.move((1280 - w) // 2 + w - 50, (720 - h) // 2 + h - 50)

            self.video_widget.hide()
            self.image_label.show()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except Exception as e: print(f"Erreur image: {e}")  # noqa: E701

    def display_video(self, url):
        self.image_label.hide()
        self.video_widget.show()
        self.close_btn.move(1210, 650) 
        self.player.setSource(QUrl(url))
        self.player.play()

    def hide_overlay(self):
        if self.movie:
            self.movie.stop()
            self.movie = None
        if self.temp_gif:
            try: os.unlink(self.temp_gif.name)  # noqa: E701
            except: pass  # noqa: E701, E722
            self.temp_gif = None
            
        self.player.stop()
        self.image_timer.stop()
        self.image_label.clear()
        self.image_label.hide()
        self.video_widget.hide()
        self.close_btn.hide()
        self.text_window.hide()
        self.hide()

# --- RÉSEAU ---
comm = Communicate()
async def websocket_listener():
    headers = {"ngrok-skip-browser-warning": "true"}
    while True:
        try:
            async with websockets.connect(SERVER_WS_URL, additional_headers=headers) as ws:
                print("✅ Connecté")
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    comm.signal.emit(data['url'], data['text'])
        except Exception:
            await asyncio.sleep(5)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = Overlay()
    comm.signal.connect(overlay.play_alert)
    threading.Thread(target=lambda: asyncio.run(websocket_listener()), daemon=True).start()
    sys.exit(app.exec())