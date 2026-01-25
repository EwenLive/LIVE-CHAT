FROM python:3.10-slim
WORKDIR /app
# On crée le fichier requirements à la volée s'il n'existe pas
RUN echo "discord.py\nfastapi\nuvicorn\nwebsockets" > requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]