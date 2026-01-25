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
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction

from PyQt6.QtWidgets import (QStyle, 
    QVBoxLayout, QHBoxLayout, QCheckBox
)

from PyQt6.QtWidgets import QSlider 


# --- CONFIGURATION ---
SERVER_WS_URL = str(os.getenv("Ngrok_Token"))
IMAGE_DURATION = 5000 

class Communicate(QObject):
    # Le signal reçoit maintenant : URL, Texte, et Pseudo
    signal = pyqtSignal(str, str, str)

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.quality = "720"
        
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

    def update_resolution(self, width, height):
        # Redimensionne les fenêtres principales
        self.setFixedSize(width, height)
        self.text_window.setFixedSize(width, height)
        self.video_widget.setGeometry(0, 0, width, height)
        self.image_label.setGeometry(0, 0, width, height)
        
        # Repositionne le message (en bas)
        self.label.setGeometry(50, height - 250, width - 100, 200)
        
        # --- POSITIONNEMENT DYNAMIQUE DE LA CROIX ---
        # On place la croix à 60 pixels du bord droit et 60 pixels du bas
        self.close_btn.move(width - 60, height - 60)
        
        print(f"📏 Interface et bouton redimensionnés en : {width}x{height}")

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
            'format': f'bestvideo[height<={self.quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
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
            # On utilise les dimensions actuelles de la fenêtre (self.width() et self.height())
            pos_x = geo.left() + (geo.width() - self.width()) // 2
            pos_y = geo.top() + (geo.height() - self.height()) // 2
            self.move(pos_x, pos_y)
            self.text_window.move(pos_x, pos_y)

        self.show()
        self.raise_()
        self.activateWindow()
        self.text_window.show()
        self.text_window.raise_()
        self.close_btn.show()

    def play_alert(self, url, text, user):
        if not self.isEnabled():
            print(f"🔇 Bot en pause : Alerte de {user} ignorée.")
            return


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
            # Utilise self.width() et self.height() au lieu de valeurs fixes
            pixmap = QPixmap.fromImage(image).scaled(self.width(), self.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
            
            img_width = pixmap.width()
            img_height = pixmap.height()
            start_x = (self.width() - img_width) // 2
            start_y = (self.height() - img_height) // 2
            
            # Positionne la croix au coin de l'image redimensionnée
            self.close_btn.move(start_x + img_width - 55, start_y + img_height - 55)

            self.video_widget.hide()
            self.image_label.show()
            self.show_all()
            self.image_timer.start(IMAGE_DURATION)
        except: pass

    def display_video(self, path):
        self.image_label.hide()
        self.video_widget.show()
        
        # Place le bouton dynamiquement selon la résolution actuelle
        self.close_btn.move(self.width() - 70, self.height() - 70) 
        
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

class ControlWindow(QWidget):
    def __init__(self, overlay):
        super().__init__()
        self.overlay = overlay
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(250, 220)

        # Container principal avec bord arrondi et ombre
        self.container = QWidget(self)
        self.container.setGeometry(5, 5, 240, 210)
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
        
        quality_layout.addWidget(self.label_quality)
        quality_layout.addStretch()
        quality_layout.addWidget(self.quality_switch)
        layout.addLayout(quality_layout) # On l'ajoute au layout principal
        
        switch_layout.addWidget(self.label_status)
        switch_layout.addStretch()
        switch_layout.addWidget(self.switch)
        layout.addLayout(switch_layout)

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
        
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)  # Volume par défaut
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
        
        volume_layout.addWidget(self.label_volume)
        volume_layout.addStretch()
        volume_layout.addWidget(self.volume_slider)
        layout.addLayout(volume_layout)


        self.quit_btn.clicked.connect(app.quit)
        layout.addWidget(self.quit_btn)

    def toggle_bot(self, state):
        is_active = (state == 2) # 2 = Checked
        self.overlay.setEnabled(is_active)
        self.label_status.setText("Bot Actif" if is_active else "Bot en Pause")
        self.label_status.setStyleSheet("color: #a6e3a1;" if is_active else "color: #f38ba8;")

    def toggle_quality(self, state):
        is_hd = (state == 2) # Checked = 1080p
        if is_hd:
            self.overlay.quality = "1080"
            self.overlay.update_resolution(1920, 1080)
        else:
            self.overlay.quality = "720"
            self.overlay.update_resolution(1280, 720)
            
        self.label_quality.setText(f"Qualité: {self.overlay.quality}p")

    def update_volume(self, value):
        # PyQt6 utilise une échelle de 0.0 à 1.0 pour le volume
        float_volume = value / 100.0
        self.overlay.audio.setVolume(float_volume)
        self.label_volume.setText(f"Volume: {value}%")

    def show_near_tray(self):
        # Positionne la fenêtre juste au dessus de la barre des tâches
        tray_geo = tray_icon.geometry()
        self.move(tray_geo.x() - self.width() + 30, tray_geo.y() - self.height() - 10)
        self.show()
        self.activateWindow()

    def changeEvent(self, event): # type: ignore
        # On détecte quand la fenêtre perd l'état "actif" (clic ailleurs)
        if event.type() == event.Type.ActivationChange:
            if not self.isActiveWindow():
                self.hide()
        super().changeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay()
    control_win = ControlWindow(overlay) # Création de la mini fenêtre
    comm.signal.connect(overlay.play_alert)

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

    threading.Thread(target=lambda: asyncio.run(websocket_listener()), daemon=True).start()
    sys.exit(app.exec())