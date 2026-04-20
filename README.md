# EDAPRO WhatsApp Bodenberater

KI-gestützter WhatsApp-Bot für Robertson SSM Bodenanalysen.

## Wie es funktioniert

```
Landwirt → WhatsApp → Twilio → dieser Server → Claude KI → Twilio → Landwirt
```

1. Landwirt schickt Robertson SSM PDF per WhatsApp
2. Bot analysiert PDF automatisch
3. Landwirt stellt Fragen auf Deutsch
4. Claude antwortet personalisiert mit den Analysewerten als Kontext

## Setup (lokal testen)

```bash
pip install -r requirements.txt
cp .env.example .env
# .env ausfüllen mit deinen Keys
uvicorn main:app --reload --port 8000
```

## Deployment auf Railway

Siehe Schritt 4 der Anleitung.

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `main.py` | FastAPI Server + Twilio Webhook |
| `soil_parser.py` | Robertson SSM PDF-Parser |
| `requirements.txt` | Python-Abhängigkeiten |
| `Procfile` | Railway/Heroku Start-Befehl |
| `.env.example` | Vorlage für Umgebungsvariablen |
