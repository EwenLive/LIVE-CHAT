FROM python:3.10-slim
WORKDIR /app
# Ajout de python-dotenv ici
RUN echo "discord.py\nfastapi\nuvicorn\nwebsockets\npython-dotenv" > requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
# Si tu as un fichier .env local, Docker a besoin de le voir ou qu'il soit passé par compose
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]