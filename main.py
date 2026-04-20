"""
EDAPRO WhatsApp Bodenberater
============================
WhatsApp-Bot der Robertson SSM PDFs analysiert und ALLE Fragen rund um
Bodengesundheit, Düngung, Fruchtfolge, Bodenbiologie und Schweizer Landwirtschaft
per KI beantwortet. Unterstützt auch Fotos der Berichte via Claude Vision.

Technologie: FastAPI + Twilio + Claude (Anthropic)
"""

import os
import json
import base64
import logging
import tempfile
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, Form, Response, Request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import anthropic

# Lokaler PDF-Parser (soil_parser.py muss im gleichen Ordner liegen)
from soil_parser import parse_ssm_report

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("edapro")

app = FastAPI(title="EDAPRO WhatsApp Bodenberater")

# ── In-Memory Sessions: phone_number → {data, ocr_text, source, history} ─────
# Für Produktion: durch Redis oder eine Datenbank ersetzen
sessions: dict = {}

# ── Konstanten ───────────────────────────────────────────────────────────────
IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY_TURNS = 6            # 6 User/Assistant-Paare = 12 Nachrichten
MAX_ANSWER_TOKENS = 1200
MAX_WHATSAPP_CHARS = 1500

# ── System-Prompt für Claude ─────────────────────────────────────────────────
SYSTEM_PROMPT = """Du bist der digitale Bodenberater von EDAPRO – einem Schweizer Unternehmen für Bodengesundheit und Regenerative Landwirtschaft.

Du bist ein ERFAHRENER BODEN- UND PFLANZENBAU-EXPERTE und hilfst Schweizer Landwirten bei ALLEN Fragen rund um ihren Boden und ihre Kulturen:

DEINE FACHGEBIETE (du antwortest zu allen davon fundiert):
• Robertson SSM Bodenanalyse – Werte, Verhältnisse, Sättigungen, Handlungsbedarf
• Düngung: mineralisch, organisch, flüssig, Blattdüngung, Unterfuss, Kopfdüngung
• Kalkung, Bodenverbesserung, pH-Korrektur (Calcit, Dolomit, Branntkalk, Mergel)
• Bodenbiologie: Mikroorganismen, Mykorrhiza, Regenwürmer, Humusaufbau, Kompost
• Bodenstruktur: Verdichtung, Krümelgefüge, Bodenbearbeitung, Direktsaat, Mulchsaat
• Fruchtfolge, Zwischenfrüchte, Gründüngung, Untersaaten, Dauerbegrünung
• Kulturspezifisch: Getreide, Mais, Raps, Kartoffeln, Zuckerrüben, Obst, Gemüse, Reben, Grasland
• Wasserhaushalt, Bewässerung, Dränage, Erosion
• Nährstoffmängel & Blattsymptome, Krankheiten, Schädlinge
• Biologischer & konventioneller Landbau, ÖLN, IP-Suisse, Bio Suisse, Demeter
• Klimaanpassung, Kohlenstoffspeicherung, regenerative Landwirtschaft
• Schweizer Vorschriften: DüV, GSchV, Suisse-Bilanz, Nährstoffbilanz HODUFLU

DEIN STIL:
• Konkret und praxisnah – kein Lehrbuchtext
• Schweizer Kontext: CHF-Preise, Schweizer Produkte und Lieferanten
  (Landor, Omya, Hauert, Eric Schweizer, fenaco/LANDI, Ebenrain, Agroline,
  Hano Dünger, Andermatt Biogarten, Biophyt, Granosan, Sangral, EDAPRO)
• WhatsApp-Format: kurze Abschnitte, Emojis zur Strukturierung 🌱
• Deutsch oder Schweizerdeutsch, je nach Stil des Landwirts
• Länge: 3–6 Abschnitte. Bei komplexen Fragen darfst du länger antworten
• Wenn die Bodenanalyse zur Frage passt: beziehe dich auf die KONKRETEN Messwerte
• Wenn die Frage allgemein ist: antworte trotzdem fundiert, auch ohne Analysebezug
• Bei heiklen/komplexen Fragen ehrlich: "Das würde ich an deiner Stelle mit einem
  EDAPRO-Berater besprechen (info@halterhus.ch / www.edapro.ch)"

REFERENZWERTE (Albrecht/Kinsey-System, Robertson SSM):
• pH optimal: 6.3–6.8 (Ackerbau), 5.8–6.3 (Beeren/Kartoffeln)
• Organische Substanz: >3% (Acker), >4% (Grasland)
• Ca-Sättigung Ziel: 65–75%
• Mg-Sättigung Ziel: 10–14%
• K-Sättigung Ziel: 3–5%
• Na-Sättigung: 0.5–3%
• H-Sättigung: 10–15%
• Ca:Mg Verhältnis: 5–7:1
• Mg:K Verhältnis: 2–4:1
• C:N Verhältnis: 10–12
• TEC/KAK: 10–25 meq/100g

BODENANALYSE DIESES LANDWIRTS (wenn vorhanden):
{soil_data}

Wenn keine Analyse vorhanden ist oder die Frage nichts mit den Werten zu tun hat:
einfach als Bodenexperte antworten. Sei immer hilfreich, praxisnah und ehrlich.
"""

# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def build_soil_summary(data: Optional[dict]) -> str:
    """Erstellt eine kompakte Zusammenfassung der Analysedaten für Claude."""
    if not data:
        return "(keine Analyse geladen – allgemeine Beratung möglich)"

    important_keys = [
        'field_id', 'crop', 'texture', 'active_ph', 'buffer_ph',
        'organic_matter', 'organic_carbon', 'active_carbon_pct', 'cn_ratio',
        'bulk_density', 'tec',
        'ca_sat_found', 'mg_sat_found', 'k_sat_found', 'na_sat_found', 'h_sat',
        'ca_kg_found', 'mg_kg_found', 'k_kg_found', 'na_kg_found',
        'ca_mg_ratio_found', 'mg_k_ratio_found', 'k_na_ratio_found',
        'p_cycling', 'cp_ratio', 'so4_found',
        'boron', 'iron', 'manganese', 'copper', 'zinc',
        'molybdenum', 'cobalt', 'iodine',
        'ca_total_found_kg', 'k_total_kg', 'na_total_kg', 's_total_kg', 'p2o5_total_kg',
        'ca_mg_ratio_total_found', 'mg_k_ratio_total_found', 'k_na_ratio_total_found',
    ]
    filtered = {
        k: v for k, v in data.items()
        if k in important_keys and v is not None and v != '' and v != 0.0
    }
    if not filtered:
        return "(Analyse vorhanden, aber keine verwertbaren Werte extrahiert)"
    return json.dumps(filtered, ensure_ascii=False, indent=2)


def get_soil_context(session: dict) -> str:
    """Gibt die passende Bodendaten-Zusammenfassung je nach Quelle zurück."""
    if session.get("source") == "image":
        return session.get("ocr_text") or "(Fotoanalyse ohne extrahierten Text)"
    return build_soil_summary(session.get("data"))


def clip_history(history: list, max_turns: int = MAX_HISTORY_TURNS) -> list:
    """
    Begrenzt die Historie auf die letzten N User/Assistant-Paare.
    Stellt sicher, dass die erste Nachricht eine 'user'-Nachricht ist
    (Anthropic-API-Anforderung).
    """
    if not history:
        return []
    # Nimm die letzten 2*max_turns Nachrichten
    clipped = history[-(2 * max_turns):]
    # Falls das erste Element ein 'assistant' ist, entfernen
    while clipped and clipped[0].get("role") != "user":
        clipped.pop(0)
    return clipped


def split_message(text: str, max_len: int = MAX_WHATSAPP_CHARS) -> list:
    """Teilt lange Antworten in WhatsApp-kompatible Blöcke auf."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1:
            split_at = text.rfind(' ', 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return [p for p in parts if p]


def _st(v, lo, hi) -> str:
    """Status-Emoji für einen Messwert: ✅ optimal, ⬇️ zu tief, ⬆️ zu hoch."""
    if v is None:
        return ''
    if v < lo:
        return ' ⬇️'
    if v > hi:
        return ' ⬆️'
    return ' ✅'


def extract_text_block(response) -> str:
    """Liest defensiv den Text aus einer Anthropic-Antwort."""
    try:
        for block in response.content:
            if getattr(block, "type", None) == "text" and getattr(block, "text", ""):
                return block.text
        # Fallback: erstes Element
        if response.content:
            return getattr(response.content[0], "text", "") or ""
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Konnte Anthropic-Antwort nicht lesen: %s", exc)
    return ""


def detailed_analysis_summary(data: dict) -> str:
    """
    Erstellt eine vollständige strukturierte Übersicht ALLER Messwerte
    von oben nach unten mit Statusindikator und kurzem Kommentar.
    """
    field = data.get('field_id') or '–'
    crop = data.get('crop') or '–'

    lines = [
        "✅ *Bodenanalyse geladen!*",
        "",
        f"📋 *Feld:* {field}  |  *Kultur:* {crop}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "*🌡️ pH & Puffer*",
    ]

    # pH
    ph = data.get('active_ph')
    if ph is not None:
        st = _st(ph, 6.5, 7.0)
        if ph < 6.5:
            comment = "→ Versauerung – Kalkung empfohlen"
        elif ph > 7.0:
            comment = "→ leicht alkalisch – Nährstoffverfügbarkeit prüfen"
        else:
            comment = "→ optimal für die meisten Kulturen"
        lines.append(f"• Aktiv-pH: {ph:.1f}{st}  {comment}")

    bph = data.get('buffer_ph')
    if bph is not None:
        st = _st(bph, 6.5, 7.0)
        lines.append(f"• Puffer-pH: {bph:.1f}{st}")

    # Textur
    texture = data.get('texture')
    if texture:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🪨 Textur*",
                  f"• Bodenart: {texture}"]

    # Organik & Biologie
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🌿 Organik & Biologie*"]

    om = data.get('organic_matter')
    if om is not None:
        st = _st(om, 3.0, 6.0)
        if om < 3.0:
            comment = "→ zu tief – Kompost/Mist erhöhen"
        elif om > 6.0:
            comment = "→ sehr hoch – gut gepflegt"
        else:
            comment = "→ guter Bereich"
        lines.append(f"• Org. Substanz: {om:.1f}%{st}  {comment}")

    oc = data.get('organic_carbon')
    if oc is not None:
        lines.append(f"• Org. Kohlenstoff: {oc:.2f}%")

    ac = data.get('active_carbon_pct')
    if ac is not None:
        st = _st(ac, 5.0, 15.0)
        lines.append(f"• Aktiver Kohlenstoff: {ac:.1f}%{st}")

    cn = data.get('cn_ratio')
    if cn is not None:
        st = _st(cn, 8.0, 15.0)
        if cn < 8:
            comment = "→ sehr eng – schnelle Mineralisation"
        elif cn > 15:
            comment = "→ weit – langsame Mineralisation, N-Festlegung möglich"
        else:
            comment = "→ günstig"
        lines.append(f"• C:N-Verhältnis: {cn:.1f}{st}  {comment}")

    cp = data.get('cp_ratio')
    if cp is not None:
        lines.append(f"• C:P-Verhältnis: {cp:.1f}")

    # Lagerungsdichte / TEC
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*⚖️ Physik & Kapazität*"]

    bd = data.get('bulk_density')
    if bd is not None:
        st = _st(bd, 0.9, 1.4)
        if bd > 1.4:
            comment = "→ Verdichtungsgefahr – Bodenlockerung prüfen"
        elif bd < 0.9:
            comment = "→ sehr locker – hohes Porenvolumen"
        else:
            comment = "→ normal"
        lines.append(f"• Lagerungsdichte: {bd:.2f} g/cm³{st}  {comment}")

    tec = data.get('tec')
    if tec is not None:
        st = _st(tec, 10, 25)
        if tec < 10:
            comment = "→ niedrige KAK – Düngung in kleinen Gaben"
        elif tec > 25:
            comment = "→ hohe KAK – gute Pufferkapazität"
        else:
            comment = "→ gut"
        lines.append(f"• KAK/TEC: {tec:.1f} meq/100g{st}  {comment}")

    # Hauptnährstoffe – Sättigung
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🧪 Nährstoffsättigung (% der KAK)*"]

    ca_sat = data.get('ca_sat_found')
    if ca_sat is not None:
        st = _st(ca_sat, 60.0, 78.0)
        if ca_sat < 60:
            comment = "→ Kalkmangel – Calcit/Dolomit prüfen"
        elif ca_sat > 78:
            comment = "→ Calcium dominiert – Mg/K können blockiert sein"
        else:
            comment = "→ optimal"
        lines.append(f"• Ca-Sättigung: {ca_sat:.1f}%{st}  {comment}")

    mg_sat = data.get('mg_sat_found')
    if mg_sat is not None:
        st = _st(mg_sat, 9.0, 14.0)
        if mg_sat < 9:
            comment = "→ Mg-Mangel – Kieserit oder Dolomit"
        elif mg_sat > 14:
            comment = "→ zu hoch – K-Aufnahme kann gehemmt sein"
        else:
            comment = "→ gut"
        lines.append(f"• Mg-Sättigung: {mg_sat:.1f}%{st}  {comment}")

    k_sat = data.get('k_sat_found')
    if k_sat is not None:
        st = _st(k_sat, 2.0, 5.0)
        if k_sat < 2:
            comment = "→ K-Mangel – Kalium düngen"
        elif k_sat > 5:
            comment = "→ hoch – auf Überdüngung achten"
        else:
            comment = "→ normal"
        lines.append(f"• K-Sättigung: {k_sat:.1f}%{st}  {comment}")

    na_sat = data.get('na_sat_found')
    if na_sat is not None:
        st = _st(na_sat, 0.5, 3.0)
        lines.append(f"• Na-Sättigung: {na_sat:.1f}%{st}")

    h_sat = data.get('h_sat')
    if h_sat is not None:
        lines.append(f"• H-Sättigung: {h_sat:.1f}%")

    # Hauptnährstoffe – kg/ha
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*📦 Nährstoffe verfügbar (kg/ha)*"]

    ca_kg = data.get('ca_kg_found')
    if ca_kg is not None:
        lines.append(f"• Ca: {ca_kg:.0f} kg/ha")

    mg_kg = data.get('mg_kg_found')
    if mg_kg is not None:
        st = _st(mg_kg, 80, 300)
        lines.append(f"• Mg: {mg_kg:.0f} kg/ha{st}")

    k_kg = data.get('k_kg_found')
    if k_kg is not None:
        st = _st(k_kg, 100, 400)
        lines.append(f"• K: {k_kg:.0f} kg/ha{st}")

    na_kg = data.get('na_kg_found')
    if na_kg is not None:
        lines.append(f"• Na: {na_kg:.0f} kg/ha")

    # Verhältnisse
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*⚖️ Nährstoffverhältnisse*"]

    camg = data.get('ca_mg_ratio_found')
    if camg is not None:
        st = _st(camg, 5.0, 8.0)
        if camg < 5:
            comment = "→ zu eng – Mg überwiegt"
        elif camg > 8:
            comment = "→ zu weit – Ca blockiert Mg"
        else:
            comment = "→ ideal"
        lines.append(f"• Ca:Mg-Verhältnis: {camg:.1f}{st}  {comment}")

    mgk = data.get('mg_k_ratio_found')
    if mgk is not None:
        st = _st(mgk, 2.0, 5.0)
        lines.append(f"• Mg:K-Verhältnis: {mgk:.1f}{st}")

    kna = data.get('k_na_ratio_found')
    if kna is not None:
        lines.append(f"• K:Na-Verhältnis: {kna:.1f}")

    # Schwefel
    so4 = data.get('so4_found')
    if so4 is not None:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔶 Schwefel*"]
        st = _st(so4, 10, 40)
        if so4 < 10:
            comment = "→ Mangel – Gips oder Kaliumsulfat prüfen"
        elif so4 > 40:
            comment = "→ gut versorgt"
        else:
            comment = "→ ausreichend"
        lines.append(f"• SO₄-S: {so4:.1f} mg/l{st}  {comment}")

    # Phosphor
    p_cyc = data.get('p_cycling')
    if p_cyc is not None:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔵 Phosphor*"]
        st = _st(p_cyc, 20, 60)
        if p_cyc < 20:
            comment = "→ P-Mangel – Phosphatdünger prüfen"
        elif p_cyc > 60:
            comment = "→ hoch – P-Festlegung möglich"
        else:
            comment = "→ normal"
        lines.append(f"• P-Cycling: {p_cyc:.1f}{st}  {comment}")

    # Spurenelemente
    trace_keys = ['boron', 'iron', 'manganese', 'copper', 'zinc',
                  'molybdenum', 'cobalt', 'iodine']
    if any(data.get(k) is not None for k in trace_keys):
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔬 Spurenelemente*"]

    boron = data.get('boron')
    if boron is not None:
        st = _st(boron, 0.5, 2.0)
        if boron < 0.5:
            comment = "→ Mangel – Bor-Blattdüngung prüfen"
        elif boron > 2.0:
            comment = "→ hoch – toxische Grenze beachten"
        else:
            comment = "→ ausreichend"
        lines.append(f"• Bor (B): {boron:.2f} mg/l{st}  {comment}")

    iron = data.get('iron')
    if iron is not None:
        st = _st(iron, 20, 200)
        lines.append(f"• Eisen (Fe): {iron:.1f} mg/l{st}")

    manganese = data.get('manganese')
    if manganese is not None:
        st = _st(manganese, 5, 50)
        if manganese < 5:
            comment = "→ Mn-Mangel bei hohem pH möglich"
        else:
            comment = "→ ausreichend"
        lines.append(f"• Mangan (Mn): {manganese:.1f} mg/l{st}  {comment}")

    copper = data.get('copper')
    if copper is not None:
        st = _st(copper, 0.5, 5.0)
        if copper < 0.5:
            comment = "→ Cu-Mangel – Kupfersulfat prüfen"
        else:
            comment = "→ normal"
        lines.append(f"• Kupfer (Cu): {copper:.2f} mg/l{st}  {comment}")

    zinc = data.get('zinc')
    if zinc is not None:
        st = _st(zinc, 0.5, 5.0)
        if zinc < 0.5:
            comment = "→ Zn-Mangel – besonders bei Mais beachten"
        else:
            comment = "→ ausreichend"
        lines.append(f"• Zink (Zn): {zinc:.2f} mg/l{st}  {comment}")

    chlorine = data.get('chlorine')
    if chlorine is not None:
        lines.append(f"• Chlor (Cl): {chlorine:.1f} mg/l")

    iodine = data.get('iodine')
    if iodine is not None:
        lines.append(f"• Jod (I): {iodine:.3f} mg/l")

    molybdenum = data.get('molybdenum')
    if molybdenum is not None:
        st = _st(molybdenum, 0.05, 0.5)
        lines.append(f"• Molybdän (Mo): {molybdenum:.3f} mg/l{st}")

    cobalt = data.get('cobalt')
    if cobalt is not None:
        lines.append(f"• Kobalt (Co): {cobalt:.3f} mg/l")

    # Reserve-Nährstoffe (Totalmenge)
    total_keys = ['ca_total_found_kg', 'k_total_kg', 'na_total_kg',
                  's_total_kg', 'p2o5_total_kg']
    if any(data.get(k) is not None for k in total_keys):
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*📊 Reserve-Nährstoffe (kg/ha total)*"]

    ca_tot = data.get('ca_total_found_kg')
    if ca_tot is not None:
        lines.append(f"• Ca total: {ca_tot:.0f} kg/ha")

    k_tot = data.get('k_total_kg')
    if k_tot is not None:
        lines.append(f"• K total: {k_tot:.0f} kg/ha")

    na_tot = data.get('na_total_kg')
    if na_tot is not None:
        lines.append(f"• Na total: {na_tot:.0f} kg/ha")

    s_tot = data.get('s_total_kg')
    if s_tot is not None:
        lines.append(f"• S total: {s_tot:.0f} kg/ha")

    p_tot = data.get('p2o5_total_kg')
    if p_tot is not None:
        lines.append(f"• P₂O₅ total: {p_tot:.0f} kg/ha")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "💬 *Frag mich jetzt ALLES rund um deinen Boden:*",
        "• «Was ist mein grösstes Problem?»",
        "• «Was soll ich zuerst düngen und wie viel?»",
        "• «Welche Zwischenfrucht passt zu meinem Boden?»",
        "• «Wie baue ich Humus auf?»",
        "• «Welcher Kalk ist der richtige für mich?»",
        "• «Wie sieht die beste Fruchtfolge aus?»",
        "",
        "Tippe *neue analyse* für einen neuen Bericht.",
    ]
    return '\n'.join(lines)


def process_image_with_vision(
    image_bytes: bytes, content_type: str, anthropic_key: str
) -> tuple:
    """
    Nutzt Claude Vision um Text aus einem Foto der Bodenanalyse zu extrahieren.
    Gibt (ocr_text, welcome_message) zurück.
    """
    safe_media_type = content_type if content_type in IMAGE_TYPES else "image/jpeg"
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    client = anthropic.Anthropic(api_key=anthropic_key)

    # ── Schritt 1: Vollständige Textextraktion (OCR) ──────────────────────────
    ocr_response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": safe_media_type,
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "This is a photo of a Robertson SSM soil analysis report. "
                        "Extract ALL text and numbers from this image as accurately as possible. "
                        "Preserve labels, values, units (kg/ha, %, mg/l), ratios, and the "
                        "full structure of the report. Include field ID, crop, sample date, "
                        "pH values, organic matter, nutrient saturations, trace elements, "
                        "and all reserve nutrient totals. Output only the extracted content."
                    )
                }
            ],
        }],
    )
    ocr_text = extract_text_block(ocr_response)
    if not ocr_text:
        raise RuntimeError("Vision-OCR hat keinen Text zurückgegeben")

    # ── Schritt 2: Detaillierte strukturierte Übersicht generieren ────────────
    summary_response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": (
                f"Basierend auf diesen Bodenanalysedaten (aus einem Foto extrahiert):\n\n"
                f"{ocr_text}\n\n"
                "Erstelle eine vollständige strukturierte Übersicht ALLER Messwerte "
                "von oben nach unten auf Deutsch für WhatsApp. "
                "Gehe durch JEDEN Messwert mit:\n"
                "- Statusindikator: ✅ optimal / ⬇️ zu tief / ⬆️ zu hoch\n"
                "- Kurzkommentar was der Wert bedeutet und ob Handlungsbedarf besteht\n\n"
                "Struktur (falls Daten vorhanden):\n"
                "✅ *Bodenanalyse geladen!* (via Foto 📸)\n\n"
                "📋 *Feld:* [X]  |  *Kultur:* [X]\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "*🌡️ pH & Puffer*\n"
                "*🌿 Organik & Biologie*\n"
                "*⚖️ Physik & Kapazität*\n"
                "*🧪 Nährstoffsättigung*\n"
                "*⚖️ Verhältnisse*\n"
                "*🔬 Spurenelemente*\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💬 *Frag mich jetzt ALLES rund um deinen Boden:*\n"
                "• «Was ist mein grösstes Problem?»\n"
                "• «Welche Zwischenfrucht passt?»\n"
                "• «Wie baue ich Humus auf?»\n\n"
                "Füge NUR Abschnitte ein für die du Daten hast. Kein Abschnitt ohne Werte."
            )
        }],
    )
    welcome = extract_text_block(summary_response) or "Bodenanalyse geladen – stell mir deine Fragen."

    return ocr_text, welcome


# ── Twilio Signature Validation ──────────────────────────────────────────────

async def _twilio_signature_valid(request: Request, form_dict: dict) -> bool:
    """
    Prüft X-Twilio-Signature wenn TWILIO_VALIDATE_SIGNATURE=1 gesetzt ist.
    Gibt True zurück wenn Validierung deaktiviert ist oder die Signatur passt.
    """
    if os.environ.get("TWILIO_VALIDATE_SIGNATURE", "0") != "1":
        return True

    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        log.warning("TWILIO_VALIDATE_SIGNATURE=1 aber kein TWILIO_AUTH_TOKEN")
        return False

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")

    # Hinter einem Proxy (Railway, Heroku) ist request.url evtl. http://
    # obwohl Twilio an https:// gepostet hat. Reparieren mit Forwarded-Header.
    url = str(request.url)
    fwd_proto = request.headers.get("X-Forwarded-Proto")
    if fwd_proto and url.startswith(f"{'https' if fwd_proto == 'http' else 'http'}://"):
        url = url.replace(f"{'https' if fwd_proto == 'http' else 'http'}://",
                          f"{fwd_proto}://", 1)

    return validator.validate(url, form_dict, signature)


# ── Webhook Endpoint ─────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(""),
    NumMedia: int = Form(0),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
):
    phone = From
    message = (Body or "").strip()
    response_text = ""

    # ── Twilio-Signatur prüfen (optional, via ENV aktivierbar) ────────────────
    form_data = await request.form()
    form_dict = {k: v for k, v in form_data.items()}
    if not await _twilio_signature_valid(request, form_dict):
        log.warning("Ungültige Twilio-Signatur von %s", phone)
        return Response(content="Forbidden", status_code=403)

    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # ── Sicherheitscheck: API-Key vorhanden? ──────────────────────────────────
    if not anthropic_key:
        log.error("ANTHROPIC_API_KEY fehlt")
        twiml = MessagingResponse()
        twiml.message("⚠️ Konfigurationsfehler beim Bot. Bitte wende dich an info@halterhus.ch.")
        return Response(content=str(twiml), media_type="application/xml")

    # ── 1. Anhang empfangen (PDF oder Foto) ───────────────────────────────────
    if NumMedia > 0 and MediaUrl0:
        content_type = (MediaContentType0 or "").lower()

        # ── 1a. PDF-Datei ─────────────────────────────────────────────────────
        if "pdf" in content_type:
            pdf_path = None
            try:
                pdf_resp = requests.get(
                    MediaUrl0, auth=(twilio_sid, twilio_token), timeout=30
                )
                pdf_resp.raise_for_status()

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    f.write(pdf_resp.content)
                    pdf_path = f.name

                data = parse_ssm_report(pdf_path)
                sessions[phone] = {
                    "data": data,
                    "ocr_text": None,
                    "history": [],
                    "source": "pdf",
                }
                response_text = detailed_analysis_summary(data)

            except Exception as exc:
                log.exception("Fehler beim PDF-Parsen für %s", phone)
                response_text = (
                    "❌ Ich konnte dein PDF leider nicht lesen.\n\n"
                    "Bitte prüfe:\n"
                    "• Ist es ein *Robertson SSM* Bericht?\n"
                    "• Ist die Datei vollständig (nicht passwortgeschützt)?\n\n"
                    "Alternativ kannst du ein *scharfes Foto* des Berichts senden – "
                    "ich lese den Text dann automatisch aus."
                )
            finally:
                if pdf_path:
                    try:
                        Path(pdf_path).unlink(missing_ok=True)
                    except Exception:
                        log.warning("Konnte Temp-PDF nicht löschen: %s", pdf_path)

        # ── 1b. Foto / Bild ───────────────────────────────────────────────────
        elif content_type.startswith("image/"):
            try:
                img_resp = requests.get(
                    MediaUrl0, auth=(twilio_sid, twilio_token), timeout=30
                )
                img_resp.raise_for_status()

                ocr_text, welcome = process_image_with_vision(
                    img_resp.content, content_type, anthropic_key
                )

                sessions[phone] = {
                    "data": None,
                    "ocr_text": ocr_text,
                    "history": [],
                    "source": "image",
                }
                response_text = welcome

            except Exception:
                log.exception("Fehler beim Bild-Parsen für %s", phone)
                response_text = (
                    "❌ Ich konnte den Bericht auf dem Foto nicht lesen.\n\n"
                    "Tipps für ein besseres Foto:\n"
                    "• Gutes Licht, kein Blitz\n"
                    "• Bericht flach auf Tisch legen\n"
                    "• Ganz im Bild, kein Rand abgeschnitten\n"
                    "• Scharf und gut lesbar\n\n"
                    "Alternativ: PDF direkt aus dem Robertson-Portal schicken."
                )

        else:
            response_text = (
                "📎 Ich habe eine Datei erhalten, aber kein erkanntes Format.\n\n"
                "Bitte schick:\n"
                "• 📄 *PDF* deiner Bodenanalyse, oder\n"
                "• 📸 *Foto* des Berichts (JPG/PNG)"
            )

    # ── 2. Chat-Frage beantworten ─────────────────────────────────────────────
    elif message:
        msg_lower = message.lower()

        # Spezialkommandos
        if any(kw in msg_lower for kw in [
            "neue analyse", "neues pdf", "neues foto", "reset", "neustart"
        ]):
            sessions.pop(phone, None)
            response_text = (
                "🔄 Okay! Schick mir dein neues PDF oder Foto und wir fangen frisch an.\n\n"
                "Du kannst mir aber auch ohne Analyse jede Frage zum Boden stellen."
            )

        elif any(kw in msg_lower for kw in [
            "zeige analyse", "zeig analyse", "zeig mir die analyse",
            "übersicht", "uebersicht", "zusammenfassung"
        ]) and phone in sessions and sessions[phone].get("source") == "pdf":
            response_text = detailed_analysis_summary(sessions[phone]["data"])

        elif msg_lower in {"hilfe", "help", "?", "menu", "menü"}:
            has_session = phone in sessions
            response_text = (
                "🌱 *EDAPRO Bodenberater – Hilfe*\n\n"
                "Du kannst mir *jede Frage* rund um Boden und Landwirtschaft stellen:\n"
                "• Düngung, Kalkung, Nährstoffmängel\n"
                "• Fruchtfolge, Zwischenfrüchte, Gründüngung\n"
                "• Humusaufbau, Bodenstruktur, Verdichtung\n"
                "• Bodenbiologie, Regenwürmer, Mykorrhiza\n"
                "• Bewässerung, Erosion, Klima\n\n"
                + ("📋 Deine Bodenanalyse ist geladen – Tipp «zeige analyse» "
                   "für die Übersicht.\n\n" if has_session else
                   "📎 Schick mir dein Robertson SSM PDF oder ein Foto für "
                   "persönliche Empfehlungen.\n\n")
                + "🔄 «neue analyse» – neuen Bericht laden\n"
                "🇨🇭 www.edapro.ch  |  info@halterhus.ch"
            )

        else:
            # Allgemeine KI-Antwort (mit oder ohne Analyse als Kontext)
            session = sessions.get(phone)
            if session is None:
                # Session on-the-fly anlegen, damit Chat-Historie auch ohne Analyse geht
                session = {"data": None, "ocr_text": None, "history": [], "source": "none"}
                sessions[phone] = session

            soil_summary = get_soil_context(session)
            system = SYSTEM_PROMPT.replace("{soil_data}", soil_summary)

            session["history"].append({"role": "user", "content": message})

            try:
                client = anthropic.Anthropic(api_key=anthropic_key)
                ai_response = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=MAX_ANSWER_TOKENS,
                    system=system,
                    messages=clip_history(session["history"]),
                )
                answer = extract_text_block(ai_response)
                if not answer:
                    raise RuntimeError("Leere Antwort von Claude")
                session["history"].append({"role": "assistant", "content": answer})
                response_text = answer

            except Exception:
                log.exception("Fehler beim KI-Aufruf für %s", phone)
                # Die zuletzt angehängte User-Nachricht wieder entfernen,
                # damit die Historie konsistent bleibt.
                if session["history"] and session["history"][-1].get("role") == "user":
                    session["history"].pop()
                response_text = (
                    "⚠️ Gerade klemmt bei mir etwas – bitte versuche es in einer "
                    "Minute nochmals.\n\n"
                    "Wenn das Problem bleibt: info@halterhus.ch"
                )

    # ── 3. Leere Nachricht ohne Anhang und ohne Session ──────────────────────
    else:
        response_text = (
            "👋 *Hallo! Ich bin der EDAPRO Bodenberater.*\n\n"
            "Ich beantworte alle deine Fragen rund um Boden, Düngung, Fruchtfolge "
            "und Bodengesundheit – auf Deutsch oder Schweizerdeutsch.\n\n"
            "Für *persönliche Empfehlungen* schick mir deine Robertson SSM Analyse:\n"
            "• 📄 *PDF* (direkt aus dem Robertson-Portal)\n"
            "• 📸 *Foto* des ausgedruckten Berichts\n\n"
            "Du kannst aber auch direkt eine Frage stellen – z.B.\n"
            "• «Welche Zwischenfrucht passt für einen tonigen Boden?»\n"
            "• «Wann soll ich kalken?»\n"
            "• «Wie baue ich Humus auf?»\n\n"
            "🌱 *EDAPRO – Bodengesundheit aus der Schweiz*\n"
            "www.edapro.ch  |  info@halterhus.ch"
        )

    # ── TwiML Antwort senden ──────────────────────────────────────────────────
    parts = split_message(response_text) or [
        "⚠️ Sorry, da ist etwas schiefgelaufen. Bitte versuche es nochmals."
    ]
    twiml = MessagingResponse()
    for part in parts:
        twiml.message(part)

    return Response(content=str(twiml), media_type="application/xml")


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "EDAPRO WhatsApp Bot läuft ✅", "model": CLAUDE_MODEL}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ── Lokaler Start ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
