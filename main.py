"""
EDAPRO WhatsApp Bodenberater & Blattsaftanalyse-Berater
=========================================================
WhatsApp-Bot der Robertson SSM PDFs und NovaCropControl Blattsaftanalyse-PDFs
analysiert und ALLE Fragen rund um Bodengesundheit, Pflanzensaft, Düngung,
Fruchtfolge, Bodenbiologie und Schweizer Landwirtschaft per KI beantwortet.
Unterstützt auch Fotos der Berichte via Claude Vision.

Technologie: FastAPI + Twilio + Claude (Anthropic)
"""

import os
import re
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

# ── In-Memory Sessions ────────────────────────────────────────────────────────
sessions: dict = {}

# ── Konstanten ───────────────────────────────────────────────────────────────
IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY_TURNS = 6
MAX_ANSWER_TOKENS = 1200
MAX_WHATSAPP_CHARS = 1500

# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT – aufgeteilt in Cache-Blöcke
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_CORE = """Du bist der digitale Bodenberater von EDAPRO GmbH (Halterhus 1, 6017 Ruswil, Schweiz – www.edapro.ch, info@halterhus.ch). Du hilfst Schweizer Landwirten bei allen Fragen rund um Boden, Düngung, Fruchtfolge, Bodenbiologie, Kultur-Praxis und Blattsaftanalysen.

DEINE FACHGEBIETE
• Robertson SSM Bodenanalyse – Werte, Verhältnisse, Sättigungen, Handlungsbedarf
• NovaCropControl Blattsaftanalyse – Interpretation, Regelwerk, Massnahmen
• Düngung: mineralisch, organisch, flüssig, Blattdüngung, Unterfuss, Kopfdüngung
• Kalkung, Bodenverbesserung, pH-Korrektur
• Bodenbiologie: Mikroorganismen, Mykorrhiza, Regenwürmer, Humusaufbau, Kompost
• Bodenstruktur: Verdichtung, Krümelgefüge, Direktsaat
• Fruchtfolge, Zwischenfrüchte, Gründüngung
• Kulturspezifisch: Getreide, Mais, Raps, Kartoffeln, Zuckerrüben, Obst, Gemüse, Reben, Grasland
• Biologischer & konventioneller Landbau, ÖLN, IP-Suisse, Bio Suisse, Demeter
• Schweizer Vorschriften: DüV, GSchV, Suisse-Bilanz, HODUFLU

DEIN STIL
• Konkret & praxisnah. Kein Lehrbuchton.
• WhatsApp-Format: *fett*, kurze Abschnitte, Emojis 🌱
• Deutsch oder Schweizerdeutsch, "Du" (nicht "Sie")
• 3–6 Abschnitte für Chatfragen. Bei komplexen Fragen darfst du länger antworten.
• Wenn Analyse vorhanden: beziehe dich auf die KONKRETEN Messwerte.
• Wenn keine Analyse: trotzdem fundiert antworten.
• Bei heiklen Fällen: "Das würde ich mit einem EDAPRO-Berater besprechen (info@halterhus.ch)."
"""

KNOWLEDGE_BLOCK = """EDAPRO WISSENSBLOCK – ROBERTSON SSM BODENANALYSE
===================================================

## INTERPRETATIONS-PHILOSOPHIE
Ian Robertson SSM – biologisch orientierte Weiterentwicklung von Kinsey-Albrecht.
• Ganzheitlich: pH, Chemie, Physik, Biologie gehören zusammen.
• Verhältnisse vor Absolutwerten: BCSR-Quoten (%) und Verhältnisse (Ca:Mg, Mg:K)
• Boden als lebendes System: Aktiver Kohlenstoff, CO₂-Burst, C:N zeigen biologische Kapazität.
• Limitierender Faktor zuerst (Liebigs Minimum).
• Leben fördern, nicht nur düngen: Mykorrhiza, Regenwürmer, Mikrobiologie.

## ROBERTSON-SSM REFERENZWERTE
pH: 6.5–7.0 optimal. Beeren/Kartoffeln 5.5–6.3. Puffer-pH max. 0.2 Differenz.
Bulk Density: 1.0–1.2 g/cm³ optimal. >1.4 Verdichtung.
OM: min. 3% (Acker), >4% (Grasland). 5–8% sehr gut.
C:N: 10–12 ideal. <10 schneller Abbau. >13 N-Festlegung.
KAK: 6 gering · 15 durchschnittlich · 30 hoch.
BCSR: Ca 68% (60–75%) · Mg 11% (10–14%) · K 3.3% (3–5%) · Na <2% · H bis 15%.
Ca:Mg 6–7:1 · Mg:K 3–3.5:1 · K:Na >3.5
SO₄: ~97 kg/ha optimal. <50 Mangel.
P-Cycling: 5–8% ideal.
Spurennährstoffe (mg/l): B 1.2–2.4 · Fe 18–189 · Mn 18–70 · Cu 2.5–7 · Zn 4–10
Mo 0.5–0.7 · Co 0.5–2

## TYPISCHE MASSNAHMEN
pH <6.2: Calcit 2–5 t/ha. Dolomit bei Mg-Mangel. Branntkalk bei starker Versauerung.
Bulk Density >1.4: Tiefenlockerung. Gründüngung mit Pfahlwurzlern. Kompost 15–30 t/ha.
OM <3%: Kompost 15–30 t/ha alle 2–3 Jahre. Zwischenfrüchte, Mulch.
K >5% Sättigung: KEINE K-Düngung. Mg prüfen: Kieserit 200–400 kg/ha.
Ca:Mg >8:1: Dolomit statt Calcit. Kieserit 200–400 kg/ha.
Ca:Mg <5:1: Calcit (nicht Dolomit!) 1–3 t/ha.
B-Mangel: Borax 0.5–1 kg/ha Blattdüngung (nie >3 kg/ha).
Mo-Mangel: Natriummolybdat 100–200 g/ha.
P-Cycling tief: Mykorrhiza-Impfung. pH Richtung 6.5. Kompost statt Mineral-P.

## SCHWEIZER LIEFERANTEN
Omya – Calcit, Dolomit · Landor/fenaco/LANDI – Dünger · Hauert – Spezialdünger
Eric Schweizer – Saatgut Gründüngung · Andermatt Biocontrol – Bio, Mykorrhiza
Biophyt – biologisch · Granosan – Mikronährstoffe · EDAPRO – Bodenanalysen

## KULTUR-HINWEISE (Schweiz)
Getreide: pH 6.5–7.0, Mn kritisch bei hohem pH.
Mais: Zn + P wichtig, pH >6.0, Struktur entscheidend.
Raps: Bor- und Schwefel-hungrig, pH 6.5–7.0.
Kartoffeln: pH 5.5–6.2, KEINE Kalkung vor Pflanzung, K + Mg wichtig.
Grasland: pH 5.5–6.5, Mg + S, regelmässig nachsäen.
Reben: K:Mg-Verhältnis kritisch, pH 6.0–7.0.
Obst: Ca-Sättigung wichtig (Stippigkeit), Bor bei Kern-/Steinobst.
"""

BLATTSAFT_KNOWLEDGE_BLOCK = """EDAPRO WISSENSBLOCK – BLATTSAFTANALYSE (NovaCropControl)
============================================================

## WAS IST BLATTSAFTANALYSE?
Die Blattsaftanalyse (NovaCropControl, Holland) misst was die Pflanze TATSÄCHLICH
aufgenommen hat – nicht was im Boden vorhanden ist. Sie ist wie ein Blutbild der Pflanze.
• Zeigt Nährstoffstatus in Echtzeit – im Gegensatz zur Bodenanalyse (Vorrat)
• Berücksichtigt Aufnahmehemmnisse (pH, Verdichtung, Staunässe) automatisch
• Ermöglicht gezielte Blattdüngung während der Vegetation
• Probenahme zeitkritisch: früh morgens, junge UND alte Blätter!
• Kosten: CHF 34.- pro Analyse + Express-Versand ca. CHF 54.- (bis 10 Analysen)
• Bezug: EDAPRO (info@halterhus.ch) – alles inklusive, inkl. S-Letter für CH-Export

## INTERPRETATION – SCHRITT FÜR SCHRITT (nach EDAPRO Regelwerk März 2026)

### Schritt 1: Überschüsse prüfen
• Welche Werte sind ÜBER dem Optimum? Diese hemmen oft andere Nährstoffe.
• K, N, P-Überschüsse sind am gefährlichsten für Antagonismen.

### Schritt 2: Fotosynthese prüfen – ZUCKER ist der wichtigste Wert!
• Zucker tief + Mn tief → Mn-Mangel hemmt Fotosynthese
• Zucker tief + Mg tief → Mg-Mangel (Chlorophyllbaustein fehlt)
• Zucker tief + Fe tief → Fe-Mangel
• Zucker tief + N-Gesamt/Nitrat hoch → zu viel N unterdrückt Zucker
• Zucker hoch → Pflanze gesund, gute Vitalität und Krankheitsresistenz

### Schritt 3: pH und EC
• Blattsaft-pH ≠ Boden-pH! Kalium ist der wichtigste Einflussfaktor.
• pH hoch + K hoch → K-Überschuss erhöht pH, erhöht Pilzanfälligkeit
• pH hoch + NH4 hoch → Ammoniumüberschuss
• pH tief + K tief → Kaliummangel
• pH tief + K ok → Fotosyntheseleistung prüfen (Mg/Mn/Fe)
• EC gibt Auskunft über Gesamtsalzgehalt – zu hoch = osmotischer Stress

### Schritt 4: Nährstoffmobilität – JUNG vs. ALT entscheidend!
IMMOBILE Nährstoffe (Symptom im jungen Blatt wenn Mangel):
• Ca, B, Si, Fe, S – können NICHT aus alten Blättern mobilisiert werden
• Wenn jung < alt → akuter Mangel! Sofort handeln.

MOBILE Nährstoffe (Symptom im alten Blatt wenn Mangel):
• K, Mg, N, P – Pflanze nimmt aus alten Blättern und transportiert zu neuen
• Wenn jung >> alt → Pflanze "frisst" sich selbst (Mangel!)
• Wenn jung und alt beide tief → Dringend düngen

### Schritt 5: Antagonismen und Synergismen
• K hoch → blockiert Mg und Ca Aufnahme
• P hoch → blockiert Zn und Fe Aufnahme
• N-Gesamt/Nitrat hoch → braucht Mo, S, Fe, Mn, Mg zur Umwandlung in Aminosäuren
• Ammonium hoch → hemmt Ca Aufnahme (Blütenendfäule bei Tomate)
• Fe + Mn hoch + Al erhöht → Bodenversauerung (pH wahrsch. < 5.0)

## STICKSTOFFSTOFFWECHSEL – 3M-REGEL
Wenn Nitrat erhöht ist, werden folgende Nährstoffe für die Umwandlung in Protein benötigt:
NO3 → (braucht: Mo, S, Fe) → NH4 → (braucht: Mn, Mg) → Aminosäuren → Protein
Wenn diese fehlen: Nitrat staut sich auf → Anfälligkeit für Pilze, schlechte Qualität!
Bei hohem Nitrat IMMER prüfen: Mo, S, Fe, Mn, Mg – die 3M + Eisen + Schwefel Regel

## NITRAT-REGEL
• Nitrat soll auf 0 oder sehr tief sein (Optimum <20–55 ppm je Kultur)
• N-Gesamt zählt (Nitrat + Ammonium + organisch)
• Ammonium: auch tiefe Werte sind gut
• Aluminium: auf 0! Über 3 ppm = Zeichen für Bodenversauerung oder Luftaustauschproblem

## BEWERTUNGSSYSTEM (Regelwerk Edapro GmbH, März 2026)

### ALLGEMEINE BEURTEILUNG
| Situation | Interpretation |
|-----------|---------------|
| Zucker tief + Mn/Mg/Fe/Nitrat-Ursache | Fotosynthese nicht optimal, Ursache angeben |
| Zucker tief, keine Ursache | Fotosyntheseleistung könnte verbessert werden |
| Zucker hoch | Sehr gut – Vitalität und Krankheitsresistenz gut |
| pH hoch + K hoch | pH zu hoch durch Kaliumüberschuss → Pilzanfälligkeit |
| pH hoch + NH4 hoch | EC + pH zu hoch durch Ammoniumüberschuss |
| pH tief + K tief | pH zu tief wegen Kaliummangel |
| K ok + Ca ok + Mg ok | Versorgung gut ausgewogen |
| K hoch + Ca tief | Kaliumüberschuss blockiert Ca-Aufnahme |
| Mg tief | Magnesiummangel – Baustein des Chlorophylls |
| K + Ca + Mg alle tief | Bodenstrukturproblem – zu wenig Luft im Boden |
| N-Gesamt >1.8× Opt-Max | N fast doppelt so hoch – Pflanze leidet |
| P hoch | P stark erhöht – hemmt Zn und Fe |
| S >2× Opt-Max | Schwefel stark erhöht |
| Si tief | Hinweis auf geringe Bodenbiologie |
| Si ok/hoch | Guter Zeiger für aktives Bodenleben |
| Fe+Mn+Al alle erhöht | Bodenversauerung – pH wahrscheinlich <5.0 |

### MASSNAHMEN (Regelwerk Edapro GmbH, März 2026)
*Stickstoff-Reduktion:*
• N >1.8× Opt-Max: N-Düngung um 50% reduzieren
• N >1.3× Opt-Max: N-Düngung um ca. 30% reduzieren
• N hoch (bis 1.3×): N-Düngung leicht reduzieren (15–20%)
• NH4 hoch (N-Gesamt ok): Ammonium-N reduzieren

*Blattspritzung Fotosynthese (wenn ≥1 Nährstoff tief):*
• Mg tief (Defizit >150 ppm): 5 l Magnesium/ha
• Mg tief (Defizit ≤150 ppm): 3 l Magnesium/ha
• Fe tief: 1–2 l Eisen/ha (zur Mg-Spritzung hinzufügen)
• Mn tief: 2 l Mangan/ha (zur Mg-Spritzung hinzufügen)
• Mo tief: 0.25 l Molybdän/ha hinzufügen
• Mn tief: idealerweise 2–3 Tage nach N-Düngergaben ausbringen

*Weitere Massnahmen:*
• Zn tief: 2 l Zink/ha Blattspritzung (KEINE Fulvinsäure!)
• B tief (Brassicaceae): 2 l Bor/ha (1 l im Herbst, Rest im Frühjahr)
• B tief (Getreide, grosser Mangel): 1 l Bor/ha auf 2 Gaben verteilt
• B tief (Getreide, leichter Mangel): 0.5 l Bor/ha
• B tief (andere Kulturen, grosser Mangel): 1 l Bor/ha, 2 Gaben
• B tief (andere Kulturen, leichter Mangel): 0.5 l Bor/ha
• Boden-pH tief (Fe >2× + Mn >1.5× + Al >3 ppm): Säuretest! Agrokalk (pH <6.5) oder Gips (pH >6.5). Ziel pH 6.0–6.5.
• Al >10 ppm: Al-Toxizität hemmt Wurzeln. Akut: Kompost + Huminsäuren. Langfristig: Kalkung.
• S >2× Opt-Max: Sulfathaltige Dünger reduzieren (Kieserit, Ammoniumsulfat)
• S tief + Mg tief: Bittersalz (MgSO₄) oder Carbo Eco Mg – behebt beide Mängel
• P hoch: P-Düngung reduzieren, Mykorrhiza fördern
• Ca tief + K/Ca >10: K-Düngung reduzieren + Kalkung (Agrokalk oder Gips)
• pH hoch + K hoch: K-Düngung reduzieren
• pH tief + K tief: Kaliumversorgung verbessern

*NICHT kommentieren:*
• K/Ca-Verhältnis als eigenständiger Punkt (nur bei Ca-Mangel als Zusatzhinweis wenn K/Ca >10)
• Zink: keine Fulvinsäure empfehlen
• Parameter die im Optimum liegen werden nicht einzeln erwähnt

*Kulturspezifisch – Tomate:*
• NH4 hoch: Ca-Aufnahme blockiert → Blütenendfäule-Risiko. NH4-Quellen reduzieren.
• pH >6.0: Nährlösung ansäuern bis pH 5.8–6.0 (Salpetersäure oder Phosphorsäure)
• Mo tief: 0.25 l Molybdän/ha als Blattspritzung
• S tief + Mg tief: Bittersalz empfehlen

## WICHTIGE OPTIMUMWERTE (NovaCropControl, Edapro-validiert März 2026)
Raps: K 2800–3825, Ca 1250–2625, Mg 150–280, S 670–1080, Fe 1.55–2.50, Cu 0.25–0.45
(Diese Werte wurden korrigiert März 2026 – die alten Werte waren falsch)
Bei Fehlinterpretationen: gespeicherte Optimumwerte mit PDF-Spalte 'Optimum' vergleichen.

## PROBENAHME TIPPS
• Früh morgens probenehmen (vor Dehydration der Pflanze)
• Junges Blatt: jüngstes vollständig ausgebildetes Blatt (nicht das allerjüngste)
• Altes Blatt: 2–3 Blätter weiter unten am gleichen Trieb
• Repräsentativer Rundgang durch das Feld (mindestens 15–20 Pflanzen)
• Proben kühl halten und schnell einsenden (NovaCropControl: max 2–3 Tage)
• Ammonium steigt bei zu langer Lagerung (Protein-Abbau)!

## BLATTSAFT vs. BODENANALYSE – WANN WAS?
• Blattsaft: Während Vegetation, bei Mangelsymptomen, Hochertragskulturen, schnelle Korrekturen
• Bodenanalyse: Vor Anbau, Fruchtfolgewechsel, langfristige Planung, pH-Korrektur, Grunddüngung
• Kombiniert: Blattsaft zeigt ob Boden-Nährstoffe ankommen – Bodenanalyse zeigt warum/warum nicht
"""

# Vorlage für den dynamischen Teil
SOIL_DATA_TMPL = """AKTUELLE ANALYSE DIESES LANDWIRTS
===================================
{soil_data}

Wenn keine Analyse vorhanden oder die Frage nichts mit den Werten zu tun hat:
trotzdem als Bodenexperte antworten. Sei immer hilfreich, praxisnah und ehrlich.
"""

# ── Few-Shot Beispiele ───────────────────────────────────────────────────────
FEW_SHOTS = [
    {
        "role": "user",
        "content": "Mein Kalium ist bei 6.6 % Sättigung. Soll ich weiter Kali düngen?",
    },
    {
        "role": "assistant",
        "content": (
            "Nein – lass die Kali-Düngung weg. 🛑\n\n"
            "*Dein K liegt bei 6.6%, Robertson-Ziel ist ~3.3% (Bereich 3–5%).* "
            "Mit 6.6% bist du doppelt so hoch wie ideal. Das bringt:\n"
            "• Keine Ertragsverbesserung mehr\n"
            "• *Mg-Aufnahme wird gehemmt* – typisches Nebenproblem\n"
            "• Geldverschwendung\n\n"
            "*Was stattdessen sinnvoll ist:*\n"
            "• Mg-Sättigung prüfen. Wenn <10%: Kieserit 200–400 kg/ha (ca. 40–70 CHF/100 kg)\n"
            "• Ca:Mg und Mg:K-Verhältnis beobachten\n"
            "• Mittelfristig: K-Exporte der Kulturen einplanen\n\n"
            "Wenn du mir die Mg-Sättigung gibst, sag ich dir die genaue Menge Kieserit. 🌱"
        ),
    },
    {
        "role": "user",
        "content": "In der Blattsaftanalyse ist Nitrat sehr hoch und Molybdän tief – was bedeutet das?",
    },
    {
        "role": "assistant",
        "content": (
            "Klassisches Muster – das erkläre ich dir! 🔬\n\n"
            "*Nitrat im Blatt ist zu hoch:* Die Pflanze nimmt mehr Nitrat auf als sie verarbeiten kann. "
            "Nitrat muss in der Pflanze zu Ammonium und dann zu Aminosäuren umgebaut werden – "
            "das klappt nur wenn bestimmte Enzyme vorhanden sind.\n\n"
            "*Molybdän fehlt:* Mo ist der Schlüssel-Nährstoff für das Enzym Nitrat-Reduktase. "
            "Ohne Mo läuft der Nitrat→Ammonium-Schritt nicht → Nitrat staut sich auf.\n\n"
            "*🟡 Was du tun sollst:*\n"
            "• 0.25 l Molybdän/ha als Blattspritzung – sofort\n"
            "• N-Düngung 15–20% reduzieren bis Werte besser\n"
            "• Auch Schwefel und Eisen prüfen – die braucht die Pflanze auch für diesen Schritt\n"
            "• Boden-pH prüfen: Mo wird bei pH >6.5 viel besser verfügbar!\n\n"
            "Gibt es noch andere auffällige Werte in deiner Analyse? 🌿"
        ),
    },
    {
        "role": "user",
        "content": "Welche Zwischenfrucht passt gut nach Weizen vor Mais?",
    },
    {
        "role": "assistant",
        "content": (
            "Nach Weizen, vor Mais – klassische ÖLN-Lücke von August/September bis April. 🌱\n\n"
            "*Meine Top-Empfehlungen:*\n\n"
            "*1. Vielfältige Mischung (empfohlen):*\n"
            "• UFA Lepha oder Eric Schweizer Multi-Mix (25–30 kg/ha, 80–120 CHF/ha)\n"
            "• Ölrettich + Phacelia + Sommerwicke + Buchweizen + Leindotter\n"
            "• Wichtig: abfrierend, damit du im Frühling mulchen/walzen kannst\n\n"
            "*2. Günstige Alternative:*\n"
            "• Ölrettich rein (20 kg/ha) + Sommerwicke (40 kg/ha)\n"
            "• Leguminose bringt N für den Mais – spart 30–50 kg N-Dünger\n\n"
            "*Worauf achten:*\n"
            "• Aussaat bis 15. September für gute Biomasse\n"
            "• Bei ÖLN: Mindestgrösse & Frühjahrsmulch beachten\n"
            "• Vor Mais: abfrierende Mischung, keine winterharten\n\n"
            "Hast du konkrete Bodenprobleme (Verdichtung, tiefes Mg, Nematoden)? 🌾"
        ),
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def _st(v, lo, hi) -> str:
    """Status-Emoji für Bodenanalyse-Wert."""
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
        if response.content:
            return getattr(response.content[0], "text", "") or ""
    except Exception as exc:
        log.warning("Konnte Anthropic-Antwort nicht lesen: %s", exc)
    return ""


def build_soil_summary(data: Optional[dict]) -> str:
    """Kompakte Zusammenfassung der Bodenanalyse-Daten für Claude."""
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
    ]
    filtered = {k: v for k, v in data.items()
                if k in important_keys and v is not None and v != '' and v != 0.0}
    if not filtered:
        return "(Analyse vorhanden, aber keine verwertbaren Werte extrahiert)"
    return json.dumps(filtered, ensure_ascii=False, indent=2)


def get_soil_context(session: dict) -> str:
    """Gibt Bodendaten-Zusammenfassung je nach Quelle zurück."""
    src = session.get("source", "")
    if src == "image":
        return session.get("ocr_text") or "(Fotoanalyse ohne extrahierten Text)"
    if src == "blattsaft":
        return blattsaft_context_for_claude(session.get("blattsaft_data", {}))
    return build_soil_summary(session.get("data"))


def clip_history(history: list, max_turns: int = MAX_HISTORY_TURNS) -> list:
    """Begrenzt Historie und stellt sicher dass erstes Element 'user' ist."""
    if not history:
        return []
    clipped = history[-(2 * max_turns):]
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


# ══════════════════════════════════════════════════════════════════════════════
# BODENANALYSE – Detailübersicht (Robertson SSM)
# ══════════════════════════════════════════════════════════════════════════════

def detailed_analysis_summary(data: dict) -> str:
    """Vollständige strukturierte Übersicht aller Bodenanalyse-Messwerte."""
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
    ph = data.get('active_ph')
    if ph is not None:
        st = _st(ph, 6.5, 7.0)
        comment = ("→ Versauerung – Kalkung empfohlen" if ph < 6.5
                   else "→ leicht alkalisch – Nährstoffverfügbarkeit prüfen" if ph > 7.0
                   else "→ optimal für die meisten Kulturen")
        lines.append(f"• Aktiv-pH: {ph:.1f}{st}  {comment}")
    bph = data.get('buffer_ph')
    if bph is not None:
        lines.append(f"• Puffer-pH: {bph:.1f}")
    texture = data.get('texture')
    if texture:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🪨 Textur*", f"• Bodenart: {texture}"]
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🌿 Organik & Biologie*"]
    om = data.get('organic_matter')
    if om is not None:
        st = _st(om, 3.0, 6.0)
        comment = ("→ zu tief – Kompost/Mist erhöhen" if om < 3.0
                   else "→ sehr hoch – gut gepflegt" if om > 6.0
                   else "→ guter Bereich")
        lines.append(f"• Org. Substanz: {om:.1f}%{st}  {comment}")
    oc = data.get('organic_carbon')
    if oc is not None:
        lines.append(f"• Org. Kohlenstoff: {oc:.2f}%")
    ac = data.get('active_carbon_pct')
    if ac is not None:
        lines.append(f"• Aktiver Kohlenstoff: {ac:.1f}%{_st(ac, 5.0, 15.0)}")
    cn = data.get('cn_ratio')
    if cn is not None:
        st = _st(cn, 8.0, 15.0)
        comment = ("→ sehr eng – schnelle Mineralisation" if cn < 8
                   else "→ weit – N-Festlegung möglich" if cn > 15
                   else "→ günstig")
        lines.append(f"• C:N-Verhältnis: {cn:.1f}{st}  {comment}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*⚖️ Physik & Kapazität*"]
    bd = data.get('bulk_density')
    if bd is not None:
        st = _st(bd, 0.9, 1.4)
        comment = ("→ Verdichtungsgefahr – Lockerung prüfen" if bd > 1.4
                   else "→ sehr locker" if bd < 0.9
                   else "→ normal")
        lines.append(f"• Lagerungsdichte: {bd:.2f} g/cm³{st}  {comment}")
    tec = data.get('tec')
    if tec is not None:
        st = _st(tec, 10, 25)
        comment = ("→ niedrige KAK – Düngung in kleinen Gaben" if tec < 10
                   else "→ hohe KAK – gute Pufferkapazität" if tec > 25
                   else "→ gut")
        lines.append(f"• KAK/TEC: {tec:.1f} meq/100g{st}  {comment}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🧪 Nährstoffsättigung (% der KAK)*"]
    ca_sat = data.get('ca_sat_found')
    if ca_sat is not None:
        st = _st(ca_sat, 60.0, 78.0)
        comment = ("→ Kalkmangel – Calcit/Dolomit prüfen" if ca_sat < 60
                   else "→ Calcium dominiert – Mg/K können blockiert sein" if ca_sat > 78
                   else "→ optimal")
        lines.append(f"• Ca-Sättigung: {ca_sat:.1f}%{st}  {comment}")
    mg_sat = data.get('mg_sat_found')
    if mg_sat is not None:
        st = _st(mg_sat, 9.0, 14.0)
        comment = ("→ Mg-Mangel – Kieserit oder Dolomit" if mg_sat < 9
                   else "→ zu hoch – K-Aufnahme kann gehemmt sein" if mg_sat > 14
                   else "→ gut")
        lines.append(f"• Mg-Sättigung: {mg_sat:.1f}%{st}  {comment}")
    k_sat = data.get('k_sat_found')
    if k_sat is not None:
        st = _st(k_sat, 2.0, 5.0)
        comment = ("→ K-Mangel – Kalium düngen" if k_sat < 2
                   else "→ hoch – auf Überdüngung achten" if k_sat > 5
                   else "→ normal")
        lines.append(f"• K-Sättigung: {k_sat:.1f}%{st}  {comment}")
    na_sat = data.get('na_sat_found')
    if na_sat is not None:
        lines.append(f"• Na-Sättigung: {na_sat:.1f}%{_st(na_sat, 0.5, 3.0)}")
    h_sat = data.get('h_sat')
    if h_sat is not None:
        lines.append(f"• H-Sättigung: {h_sat:.1f}%")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*📦 Nährstoffe verfügbar (kg/ha)*"]
    for k, lbl, lo, hi in [
        ('ca_kg_found', 'Ca', None, None),
        ('mg_kg_found', 'Mg', 80, 300),
        ('k_kg_found', 'K', 100, 400),
        ('na_kg_found', 'Na', None, None),
    ]:
        v = data.get(k)
        if v is not None:
            st = _st(v, lo, hi) if lo else ''
            lines.append(f"• {lbl}: {v:.0f} kg/ha{st}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*⚖️ Nährstoffverhältnisse*"]
    camg = data.get('ca_mg_ratio_found')
    if camg is not None:
        st = _st(camg, 5.0, 8.0)
        comment = ("→ zu eng – Mg überwiegt" if camg < 5
                   else "→ zu weit – Ca blockiert Mg" if camg > 8
                   else "→ ideal")
        lines.append(f"• Ca:Mg: {camg:.1f}{st}  {comment}")
    mgk = data.get('mg_k_ratio_found')
    if mgk is not None:
        lines.append(f"• Mg:K: {mgk:.1f}{_st(mgk, 2.0, 5.0)}")
    kna = data.get('k_na_ratio_found')
    if kna is not None:
        lines.append(f"• K:Na: {kna:.1f}")
    so4 = data.get('so4_found')
    if so4 is not None:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔶 Schwefel*"]
        comment = ("→ Mangel – Gips oder Kaliumsulfat prüfen" if so4 < 10
                   else "→ gut versorgt" if so4 > 40 else "→ ausreichend")
        lines.append(f"• SO₄-S: {so4:.1f} mg/l{_st(so4, 10, 40)}  {comment}")
    p_cyc = data.get('p_cycling')
    if p_cyc is not None:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔵 Phosphor*"]
        comment = ("→ P-Mangel – Phosphatdünger prüfen" if p_cyc < 20
                   else "→ hoch – P-Festlegung möglich" if p_cyc > 60
                   else "→ normal")
        lines.append(f"• P-Cycling: {p_cyc:.1f}{_st(p_cyc, 20, 60)}  {comment}")
    trace_keys = ['boron', 'iron', 'manganese', 'copper', 'zinc', 'molybdenum', 'cobalt', 'iodine']
    if any(data.get(k) is not None for k in trace_keys):
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*🔬 Spurenelemente*"]
    boron = data.get('boron')
    if boron is not None:
        comment = "→ Mangel – Bor-Blattdüngung" if boron < 0.5 else "→ ausreichend"
        lines.append(f"• Bor (B): {boron:.2f} mg/l{_st(boron, 0.5, 2.0)}  {comment}")
    iron = data.get('iron')
    if iron is not None:
        lines.append(f"• Eisen (Fe): {iron:.1f} mg/l{_st(iron, 20, 200)}")
    manganese = data.get('manganese')
    if manganese is not None:
        comment = "→ Mn-Mangel bei hohem pH möglich" if manganese < 5 else "→ ausreichend"
        lines.append(f"• Mangan (Mn): {manganese:.1f} mg/l{_st(manganese, 5, 50)}  {comment}")
    copper = data.get('copper')
    if copper is not None:
        lines.append(f"• Kupfer (Cu): {copper:.2f} mg/l{_st(copper, 0.5, 5.0)}")
    zinc = data.get('zinc')
    if zinc is not None:
        lines.append(f"• Zink (Zn): {zinc:.2f} mg/l{_st(zinc, 0.5, 5.0)}")
    molybdenum = data.get('molybdenum')
    if molybdenum is not None:
        lines.append(f"• Molybdän (Mo): {molybdenum:.3f} mg/l{_st(molybdenum, 0.05, 0.5)}")
    cobalt = data.get('cobalt')
    if cobalt is not None:
        lines.append(f"• Kobalt (Co): {cobalt:.3f} mg/l")
    total_keys = ['ca_total_found_kg', 'k_total_kg', 'na_total_kg', 's_total_kg', 'p2o5_total_kg']
    if any(data.get(k) is not None for k in total_keys):
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", "*📊 Reserve-Nährstoffe (kg/ha total)*"]
    for k, lbl in [('ca_total_found_kg', 'Ca total'), ('k_total_kg', 'K total'),
                   ('na_total_kg', 'Na total'), ('s_total_kg', 'S total'),
                   ('p2o5_total_kg', 'P₂O₅ total')]:
        v = data.get(k)
        if v is not None:
            lines.append(f"• {lbl}: {v:.0f} kg/ha")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "💬 *Frag mich jetzt ALLES:*",
        "• «Was ist mein grösstes Problem?»",
        "• «Was soll ich zuerst düngen?»",
        "• «Welche Zwischenfrucht passt?»",
        "• «Wie viel Kalk brauche ich?»",
        "",
        "Tippe *neue analyse* für einen neuen Bericht.",
    ]
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# BLATTSAFTANALYSE – NovaCropControl Parser & Interpretation
# ══════════════════════════════════════════════════════════════════════════════

# Bekannte Parameter mit Kurzname und Einheit
BS_PARAMS = [
    ("Zucker",               "zucker",   "%"),
    ("pH",                   "ph",       ""),
    ("EC",                   "ec",       "mS/cm"),
    ("K - Kalium",           "k",        "ppm"),
    ("Ca - Kalzium",         "ca",       "ppm"),
    ("K / Ca",               "k_ca",     ""),
    ("Mg - Magnesium",       "mg",       "ppm"),
    ("Na - Natrium",         "na",       "ppm"),
    ("NH4 - Ammonium",       "nh4",      "ppm"),
    ("NO3 - Nitrat",         "no3",      "ppm"),
    ("N aus Nitrat",         "n_nitrat", "ppm"),
    ("N - Gesamt Stickstoff","n_gesamt", "ppm"),
    ("Cl - Chlorid",         "cl",       "ppm"),
    ("S - Schwefel",         "s",        "ppm"),
    ("P - Phosphor",         "p",        "ppm"),
    ("Si - Silizium",        "si",       "ppm"),
    ("Fe - Eisen",           "fe",       "ppm"),
    ("Mn - Mangan",          "mn",       "ppm"),
    ("Zn - Zink",            "zn",       "ppm"),
    ("B - Bor",              "b",        "ppm"),
    ("Cu - Kupfer",          "cu",       "ppm"),
    ("Mo - Molybdän",        "mo",       "ppm"),
    ("Al - Aluminium",       "al",       "ppm"),
]

# Anzeige-Labels für Bericht
BS_LABELS = {
    "zucker":   "🍬 Zucker",
    "ph":       "🌡️ pH",
    "ec":       "⚡ EC",
    "k":        "🟡 K – Kalium",
    "ca":       "🔵 Ca – Kalzium",
    "k_ca":     "   K/Ca-Verhältnis",
    "mg":       "🟢 Mg – Magnesium",
    "na":       "   Na – Natrium",
    "nh4":      "   NH4 – Ammonium",
    "no3":      "🔴 NO3 – Nitrat",
    "n_nitrat": "   N aus Nitrat",
    "n_gesamt": "🟠 N – Gesamt",
    "cl":       "   Cl – Chlorid",
    "s":        "🟤 S – Schwefel",
    "p":        "🔵 P – Phosphor",
    "si":       "   Si – Silizium",
    "fe":       "🔶 Fe – Eisen",
    "mn":       "🔶 Mn – Mangan",
    "zn":       "   Zn – Zink",
    "b":        "   B – Bor",
    "cu":       "   Cu – Kupfer",
    "mo":       "🟣 Mo – Molybdän",
    "al":       "⚠️ Al – Aluminium",
}


def _bs_to_float(s: str) -> Optional[float]:
    """Wandelt deutschen Dezimalstring in float. Behandelt '<0,05' etc."""
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r'^[<>]', '', s).replace(',', '.').strip()
    try:
        return float(s)
    except ValueError:
        return None


def _is_num(s: str) -> bool:
    """Prüft ob ein String eine Zahl (mit < oder >) ist."""
    s = str(s).strip()
    s = re.sub(r'^[<>]', '', s).replace(',', '.').strip()
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_opt_range(s: str) -> bool:
    """Prüft ob ein String ein Optimum-Bereich ist (z.B. '6,3 - 8,8' oder '<20 - 55')."""
    return bool(re.match(r'^[<>]?\d[\d,.]*\s*-\s*[<>]?\d[\d,.]*$', s.strip()))


def is_novacropcontrol_pdf(pdf_path: str) -> bool:
    """Erkennt ob ein PDF ein NovaCropControl Blattsaftanalyse-Bericht ist."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = ""
        for i, page in enumerate(doc):
            text += page.get_text()
            if i >= 2:
                break
        doc.close()
        keywords = ["NovaCropControl", "Pflanzensaft", "Pflanzensaft-Probe",
                    "Blatt (Jung)", "Blatt (Alt)", "Aktuellen Niveau"]
        return any(kw in text for kw in keywords)
    except Exception:
        return False


def parse_novacropcontrol_pdf(pdf_path: str) -> Optional[dict]:
    """
    Parst ein NovaCropControl Blattsaftanalyse-PDF.
    Gibt ein Dict zurück mit allen Messwerten (jung/alt) und Optimumwerten.
    """
    import fitz

    try:
        doc = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        doc.close()
    except Exception as e:
        log.error("Fehler beim Öffnen des NovaCropControl-PDFs: %s", e)
        return None

    # Ist es wirklich ein NovaCropControl-Bericht?
    nc_keywords = ["NovaCropControl", "Pflanzensaft", "Aktuellen Niveau"]
    if not any(kw in full_text for kw in nc_keywords):
        return None

    result = {
        "type": "blattsaft",
        "kunde": "",
        "adresse": "",
        "lage": "",
        "anbau": "",
        "ernte": "",
        "probendatum": "",
        "probe_nr": "",
        "params": {}
    }

    # Metadaten extrahieren
    for pattern, key in [
        (r'Name:\s*\n?\s*(.+)', "kunde"),
        (r'Adresse:\s*\n?\s*(.+)', "adresse"),
        (r'Lage/Grundstück:\s*\n?\s*(.+)', "lage"),
        (r'Anbau:\s*\n?\s*(.+)', "anbau"),
        (r'Ernte:\s*\n?\s*(.+)', "ernte"),
        (r'Probendatum:\s*\n?\s*(.+)', "probendatum"),
        (r'Pflanzensaft-Probe\s*\n?\s*(\d+)', "probe_nr"),
    ]:
        m = re.search(pattern, full_text)
        if m:
            result[key] = m.group(1).strip()

    # Zeilen bereinigen
    lines = [l.strip() for l in full_text.split('\n')]
    lines = [l for l in lines if l]  # Leerzeilen entfernen

    # Parameter aus dem Text extrahieren
    for param_name, key, unit in BS_PARAMS:
        found_at = None
        for i, line in enumerate(lines):
            if line == param_name:
                found_at = i
                break

        if found_at is None:
            continue

        # Nächste ~15 Zeilen nach dem Parameternamen durchsuchen
        # Abbruch wenn der nächste Parameter-Name erscheint
        param_names = {pn for pn, _, _ in BS_PARAMS}
        window = lines[found_at + 1: found_at + 16]

        nums = []
        opt_range = None
        for w in window:
            if w in param_names:
                break  # Nächster Parameter – aufhören
            if _is_opt_range(w):
                opt_range = w
                break
            if _is_num(w):
                nums.append(_bs_to_float(w))

        val_jung = nums[0] if len(nums) > 0 else None
        val_alt  = nums[1] if len(nums) > 1 else None

        opt_min = opt_max = None
        if opt_range:
            parts = re.split(r'\s*-\s*', opt_range.strip())
            if len(parts) == 2:
                opt_min = _bs_to_float(parts[0])
                opt_max = _bs_to_float(parts[1])

        result["params"][key] = {
            "name":    param_name,
            "unit":    unit,
            "jung":    val_jung,
            "alt":     val_alt,
            "opt_min": opt_min,
            "opt_max": opt_max,
        }

    # Mindestens 5 Parameter geparst?
    if len([p for p in result["params"].values() if p["jung"] is not None]) < 5:
        log.warning("NovaCropControl-Parsen: zu wenige Werte extrahiert")
        return None

    return result


def _bs_status(val: Optional[float], opt_min: Optional[float], opt_max: Optional[float]) -> str:
    """Status-Emoji für Blattsaft-Wert."""
    if val is None or opt_min is None or opt_max is None:
        return ""
    if val < opt_min:
        return " ⬇️"
    if val > opt_max:
        return " ⬆️"
    return " ✅"


def _bs_status_text(val: Optional[float], opt_min: Optional[float], opt_max: Optional[float]) -> str:
    """Status-Text für Blattsaft-Regelwerk-Interpretation."""
    if val is None or opt_min is None or opt_max is None:
        return "unbekannt"
    if val < opt_min:
        return "tief"
    if val > opt_max:
        return "hoch"
    return "ok"


def generate_blattsaft_interpretation(params: dict) -> str:
    """
    Wendet das EDAPRO Regelwerk auf Blattsaftanalyse-Werte an.
    Gibt Fliesstext (Allgemeine Beurteilung) + Massnahmen zurück.
    """
    def get(key):
        p = params.get(key, {})
        return p.get("jung"), p.get("opt_min"), p.get("opt_max")

    def st(key):
        v, lo, hi = get(key)
        return _bs_status_text(v, lo, hi)

    def val(key):
        return params.get(key, {}).get("jung")

    def opt_max(key):
        return params.get(key, {}).get("opt_max")

    def opt_min(key):
        return params.get(key, {}).get("opt_min")

    beurteilung = []
    massnahmen  = []

    # ── 1. Zucker / Fotosynthese ──────────────────────────────────────────────
    zucker_st = st("zucker")
    if zucker_st == "tief":
        ursachen = []
        if st("mn") == "tief":   ursachen.append("Mangan tief")
        if st("mg") == "tief":   ursachen.append("Magnesium tief")
        if st("fe") == "tief":   ursachen.append("Eisen tief")
        if st("no3") == "hoch":  ursachen.append("Nitrat hoch")
        if st("n_gesamt") == "hoch": ursachen.append("N-Gesamt hoch")
        if ursachen:
            beurteilung.append(
                f"Zuckergehalt unter Optimum – Fotosynthese nicht auf vollem Niveau. "
                f"Ursache(n): {', '.join(ursachen)}."
            )
        else:
            beurteilung.append(
                "Zuckergehalt leicht unter Optimum – Fotosyntheseleistung könnte verbessert werden."
            )
    elif zucker_st == "hoch":
        beurteilung.append(
            "Zuckergehalt sehr gut – Pflanze fotosynthetisiert aktiv. Vitalität und Krankheitsresistenz gut."
        )
    else:
        beurteilung.append("Zuckergehalt im optimalen Bereich ✅")

    # ── 2. Blattsaft-pH ───────────────────────────────────────────────────────
    ph_st = st("ph")
    k_st  = st("k")
    nh4_st = st("nh4")

    if ph_st == "hoch" and k_st == "hoch":
        beurteilung.append(
            "pH zu hoch durch Kaliumüberschuss – erhöht Pilzanfälligkeit."
        )
        massnahmen.append("K-Düngung reduzieren (senkt pH, EC und Pilzanfälligkeit)")
    elif ph_st == "hoch" and nh4_st == "hoch":
        beurteilung.append(
            "pH und EC erhöht – mitverursacht durch Ammoniumüberschuss."
        )
        massnahmen.append("Ammonium-N reduzieren")
    elif ph_st == "hoch":
        beurteilung.append(
            "pH erhöht trotz optimalem Kalium – Pflanze hat zu wenig Energie zur pH-Regulierung. "
            "Fotosynthese prüfen (Mg, Mn, Fe)."
        )
        massnahmen.append("Fotosynthese fördern: Mg, Mn, Fe als Blattspritzung")
    elif ph_st == "tief" and k_st == "tief":
        beurteilung.append("pH zu tief wegen Kaliummangel.")
        massnahmen.append("Kaliumversorgung verbessern")
    elif ph_st == "tief":
        beurteilung.append("pH tief ohne klaren Kaliummangel – Fotosyntheseleistung prüfen.")
        massnahmen.append("Fotosynthese fördern: Mg, Mn, Fe prüfen und applizieren")
    else:
        beurteilung.append("Blattsaft-pH im optimalen Bereich – ausgeglichene Versorgung ✅")

    # ── 3. Hauptnährstoffe K / Ca / Mg ───────────────────────────────────────
    ca_st = st("ca")
    mg_st = st("mg")

    if k_st == "ok" and ca_st == "ok" and mg_st == "ok":
        beurteilung.append("Versorgung mit Kalium, Kalzium und Magnesium gut ausgewogen ✅")
    else:
        if k_st == "hoch" and ca_st == "tief":
            beurteilung.append(
                "Kalzium tief – Kaliumüberschuss kann Kalziumaufnahme blockieren."
            )
        elif k_st == "hoch":
            beurteilung.append(
                "Kalium leicht erhöht – hemmt Mg- und Ca-Aufnahme."
            )
        if mg_st == "tief":
            beurteilung.append(
                "Magnesiummangel – Magnesium ist Baustein des Chlorophylls."
            )
        if k_st == "tief" and ca_st == "tief" and mg_st == "tief":
            beurteilung.append(
                "K, Ca und Mg alle tief – Hinweis auf Bodenstrukturproblem (zu wenig Luft im Boden)."
            )

    # ── 4. Stickstoff gesamt ──────────────────────────────────────────────────
    n_val = val("n_gesamt")
    n_max = opt_max("n_gesamt")
    if n_val is not None and n_max is not None and n_max > 0:
        ratio = n_val / n_max
        if ratio > 1.8:
            beurteilung.append(
                "N-Versorgung fast doppelt so hoch wie Optimum – Pflanze leidet unter N-Überschuss."
            )
            massnahmen.append("N-Düngung um 50% reduzieren")
        elif ratio > 1.3:
            beurteilung.append(
                "N liegt erhöht über Optimum – Reduktion der Stickstoffdüngung angebracht."
            )
            massnahmen.append("N-Düngung um ca. 30% reduzieren")
        elif n_val > n_max:
            massnahmen.append("N-Düngung leicht reduzieren (ca. 15–20%)")

    if nh4_st == "hoch" and st("n_gesamt") != "hoch":
        beurteilung.append(
            "Ammonium-N erhöht (N-Gesamt ok) – hemmt Mg- und Ca-Aufnahme, fördert Pilzkrankheiten."
        )
        massnahmen.append("Ammonium-N reduzieren (hemmt Mg- und Ca-Aufnahme)")

    # ── 5. Phosphor / Schwefel / Silizium ────────────────────────────────────
    if st("p") == "hoch":
        beurteilung.append(
            "Phosphorgehalt stark erhöht – überschüssiges P hemmt Zink- und Eisenaufnahme."
        )
        massnahmen.append("P-Düngung reduzieren, Bodenbiologie (Mykorrhiza) fördern")

    s_val = val("s")
    s_max = opt_max("s")
    if s_val is not None and s_max is not None and s_max > 0 and s_val > 2 * s_max:
        beurteilung.append("Schwefel stark erhöht.")
        massnahmen.append("Sulfathaltige Dünger reduzieren (Kieserit, Ammoniumsulfat)")

    si_st = st("si")
    if si_st == "tief":
        beurteilung.append(
            "Siliziumwert unter Optimum – Hinweis auf geringe Bodenbiologie-Aktivität."
        )
    elif si_st in ("ok", "hoch"):
        beurteilung.append("Siliziumwert gut – Zeiger für aktives Bodenleben ✅")

    # ── 6. Nitrat hoch ────────────────────────────────────────────────────────
    no3_val = val("no3")
    no3_max = opt_max("no3")
    if no3_val is not None and no3_max is not None and no3_max > 0:
        if no3_val > 2 * no3_max:
            lacking = [x for x in ["mo", "mn", "mg"] if st(x) == "tief"]
            if lacking:
                lacking_names = {"mo": "Molybdän", "mn": "Mangan", "mg": "Magnesium"}
                beurteilung.append(
                    f"Nitrat stark erhöht (>2× Optimum), wird nicht vollständig umgewandelt. "
                    f"Ursache: {', '.join(lacking_names[x] for x in lacking)} Mangel "
                    f"hemmt Nitrat-Einbindung in Proteine."
                )
            else:
                beurteilung.append(
                    "Nitrat stark erhöht – Pflanze nimmt mehr Nitrat auf als sie verarbeiten kann."
                )
        elif st("no3") == "hoch":
            beurteilung.append(
                "Nitrat erhöht – Pflanze nimmt mehr Nitrat auf als sie verarbeiten kann."
            )

    # ── 7. Bodenversauerung ───────────────────────────────────────────────────
    fe_val = val("fe")
    fe_max = opt_max("fe")
    mn_val = val("mn")
    mn_max = opt_max("mn")
    al_val = val("al")

    if (fe_val and fe_max and mn_val and mn_max and al_val and
            fe_val > 2 * fe_max and mn_val > 1.5 * mn_max and al_val > 3):
        beurteilung.append(
            "Sehr hohe Fe, Mn und Al-Werte – Zeichen für stark versauerten Boden (pH wahrsch. <5.0)."
        )
        massnahmen.append(
            "⚠️ DRINGEND Boden-pH prüfen! Säuretest: Agrokalk (pH <6.5) oder Gips (pH >6.5). "
            "Ziel pH 6.0–6.5. Keine schnellwirkenden N-Dünger bis pH korrigiert."
        )
        if al_val and al_val > 10:
            massnahmen.append(
                "Al-Toxizität (>10 ppm) hemmt Wurzeln und P-Aufnahme. Akut: Kompost + Huminsäuren."
            )
    elif fe_val and fe_max and fe_val > 1.5 * fe_max:
        beurteilung.append(
            "Eisen erhöht – möglicher Hinweis auf saure Bodenbedingungen."
        )
    if mn_val and mn_max and mn_val > 2 * mn_max:
        beurteilung.append(
            "Mangan stark überhöht – kann Fotosynthese hemmen und toxisch wirken."
        )

    # ── 8. Massnahmen: Blattspritzung Fotosynthese ───────────────────────────
    spray_needed = []
    mg_val  = val("mg")
    mg_min  = opt_min("mg")
    fe_tief = st("fe") == "tief"
    mn_tief = st("mn") == "tief"
    mo_tief = st("mo") == "tief"

    if mg_st == "tief":
        if mg_val and mg_min:
            defizit = mg_min - mg_val
            if defizit > 150:
                spray_needed.append("5 l Magnesium/ha")
            else:
                spray_needed.append("3 l Magnesium/ha")
        else:
            spray_needed.append("3–5 l Magnesium/ha")
    if fe_tief:
        spray_needed.append("1–2 l Eisen/ha")
    if mn_tief:
        spray_needed.append("2 l Mangan/ha (idealerweise 2–3 Tage nach N-Düngergaben)")
    if mo_tief:
        spray_needed.append("0.25 l Molybdän/ha")
        if st("no3") == "hoch":
            massnahmen.append(
                "Blattspritzung Mo besonders dringend – Mo wird für Nitratreduktion benötigt!"
            )

    if spray_needed:
        massnahmen.append(
            "Blattspritzung Fotosynthese-Nährstoffe: " + " + ".join(spray_needed)
        )

    # ── 9. Zink, Bor, Schwefel, Kalzium ─────────────────────────────────────
    if st("zn") == "tief":
        massnahmen.append("Zink: 2 l Zink/ha als Blattspritzung (KEINE Fulvinsäure!)")

    b_val = val("b")
    b_min = opt_min("b")
    b_st  = st("b")
    if b_st == "tief":
        gross_mangel = b_val and b_min and b_val < 0.5 * b_min
        anbau = ""  # wird von aussen übergeben wenn bekannt
        if gross_mangel:
            massnahmen.append("Bor: 1 l Bor/ha auf 2 Gaben verteilt (grosser Mangel)")
        else:
            massnahmen.append("Bor: 0.5 l Bor/ha (leichter Mangel)")

    # Schwefel + Magnesium kombiniert
    if st("s") == "tief" and mg_st == "tief":
        massnahmen.append(
            "Schwefel + Magnesium tief: Bittersalz (MgSO₄) oder Carbo Eco Mg empfohlen "
            "(behebt beide Mängel gleichzeitig)"
        )
    elif st("s") == "tief":
        massnahmen.append("Schwefel tief: 3 l flüssiger Schwefel/ha oder Bodendünger")

    # Kalzium
    ca_val = val("ca")
    k_val  = val("k")
    if ca_st == "tief" and ca_val and k_val and ca_val > 0:
        k_ca_ratio = k_val / ca_val
        if k_ca_ratio > 10:
            massnahmen.append(
                "Kalzium zu tief + K/Ca >10: K-Düngung reduzieren + Kalkung "
                "(Agrokalk pH <6.5 oder Gips pH >6.5)"
            )
        else:
            massnahmen.append(
                "Kalzium zu tief: Säuretest → Agrokalk (pH <6.5) oder Gips (pH >6.5)"
            )

    # ── Zusammenstellen ───────────────────────────────────────────────────────
    parts = []
    if beurteilung:
        parts.append("*📊 Allgemeine Beurteilung:*\n" +
                     "\n".join(f"• {b}" for b in beurteilung))
    if massnahmen:
        parts.append("*🌿 Empfohlene Massnahmen:*\n" +
                     "\n".join(f"• {m}" for m in massnahmen))
    if not massnahmen:
        parts.append("*✅ Keine dringenden Massnahmen nötig!* Weiterhin optimale Bedingungen aufrechterhalten.")

    return "\n\n".join(parts)


def blattsaft_analysis_summary(data: dict) -> str:
    """
    Vollständige strukturierte Übersicht der NovaCropControl Blattsaftanalyse
    mit allen Messwerten (jung/alt vs. Optimum) und automatischer Interpretation.
    """
    if not data:
        return "❌ Keine Blattsaftanalyse-Daten vorhanden."

    params = data.get("params", {})
    kunde   = data.get("kunde", "–")
    lage    = data.get("lage", "–")
    anbau   = data.get("anbau", "–")
    datum   = data.get("probendatum", "–")
    probe   = data.get("probe_nr", "")

    lines = [
        "🌿 *Blattsaftanalyse geladen!* (NovaCropControl)",
        "",
        f"👤 *Betrieb:* {kunde}",
        f"📍 *Lage:* {lage}",
        f"🌾 *Kultur:* {anbau}",
        f"📅 *Probedatum:* {datum}" + (f"  |  Nr. {probe}" if probe else ""),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "*Messwert            jung  |  alt  |  Optimum  | Status*",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Tabelle aller Parameter
    for _, key, unit in BS_PARAMS:
        p = params.get(key)
        if not p:
            continue
        jung    = p.get("jung")
        alt     = p.get("alt")
        opt_min = p.get("opt_min")
        opt_max = p.get("opt_max")
        if jung is None and alt is None:
            continue

        label = BS_LABELS.get(key, key)
        u = f" {unit}" if unit else ""

        def fmt(v):
            if v is None:
                return "  –  "
            if isinstance(v, float) and v < 10:
                return f"{v:.2f}"
            return f"{v:.0f}"

        jung_str = fmt(jung) + u if jung is not None else "–"
        alt_str  = fmt(alt)  + u if alt  is not None else "–"
        opt_str  = (f"{opt_min}–{opt_max}{u}" if opt_min is not None and opt_max is not None
                    else "–")
        st = _bs_status(jung, opt_min, opt_max)

        # Alt-Status auch prüfen (bei mobilen Nährstoffen wichtig)
        alt_st = _bs_status(alt, opt_min, opt_max)
        if alt_st and alt_st != st:
            st = st + f"/{alt_st.strip()}"

        lines.append(f"{label}: {jung_str} | {alt_str} | {opt_str} |{st}")

    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━", ""]

    # Automatische Interpretation nach EDAPRO Regelwerk
    interpretation = generate_blattsaft_interpretation(params)
    lines.append(interpretation)

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "💬 *Frag mich jetzt ALLES zu deiner Blattsaftanalyse:*",
        "• «Was ist das dringendste Problem?»",
        "• «Welche Blattspritzung soll ich machen?»",
        "• «Was bedeutet der tiefe Zuckerwert?»",
        "• «Wie viel Mo brauche ich?»",
        "",
        "Tippe *neue analyse* für neuen Bericht.",
    ]
    return '\n'.join(lines)


def blattsaft_context_for_claude(data: dict) -> str:
    """Komprimierte Blattsaft-Daten als Kontext für Claude."""
    if not data:
        return "(keine Blattsaftanalyse geladen)"

    params = data.get("params", {})
    lines = [
        f"Blattsaftanalyse (NovaCropControl)",
        f"Kultur: {data.get('anbau', '?')}",
        f"Betrieb: {data.get('kunde', '?')}",
        f"Datum: {data.get('probendatum', '?')}",
        "",
        "MESSWERTE (jung | alt | opt_min | opt_max | Status):",
    ]
    for _, key, unit in BS_PARAMS:
        p = params.get(key)
        if not p or p.get("jung") is None:
            continue
        jung    = p["jung"]
        alt     = p.get("alt", "?")
        opt_min = p.get("opt_min")
        opt_max = p.get("opt_max")
        st = _bs_status_text(jung, opt_min, opt_max).upper()
        opt_str = f"{opt_min}–{opt_max}" if opt_min and opt_max else "?"
        lines.append(
            f"{key}: {jung} | {alt} | {opt_str} | {st}"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# VISION – Foto-Analyse
# ══════════════════════════════════════════════════════════════════════════════

def process_image_with_vision(
    image_bytes: bytes, content_type: str, anthropic_key: str
) -> tuple:
    """Claude Vision für Fotos (Bodenanalyse oder Blattsaftanalyse)."""
    safe_media_type = content_type if content_type in IMAGE_TYPES else "image/jpeg"
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    client = anthropic.Anthropic(api_key=anthropic_key)

    # Schritt 1: OCR
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
                        "This is a photo of an agricultural analysis report "
                        "(either Robertson SSM soil analysis OR NovaCropControl plant sap analysis). "
                        "Extract ALL text and numbers as accurately as possible. "
                        "Preserve labels, values, units (ppm, %, mS/cm), optimum ranges, "
                        "and the full structure. Include field ID, crop, sample date, "
                        "all measured parameters and their values. Output only the extracted content."
                    )
                }
            ],
        }],
    )
    ocr_text = extract_text_block(ocr_response)
    if not ocr_text:
        raise RuntimeError("Vision-OCR hat keinen Text zurückgegeben")

    # Schritt 2: Strukturierte Übersicht
    # Erkennen ob Boden oder Blattsaft
    is_blattsaft = any(kw in ocr_text for kw in
                       ["Zucker", "Nitrat", "Ammonium", "Aktuellen Niveau",
                        "NovaCropControl", "Pflanzensaft"])

    if is_blattsaft:
        prompt = (
            f"Basierend auf dieser Blattsaftanalyse (aus Foto extrahiert):\n\n{ocr_text}\n\n"
            "Erstelle eine vollständige strukturierte Übersicht auf Deutsch für WhatsApp:\n"
            "🌿 *Blattsaftanalyse geladen!*\n\n"
            "Zeige ALLE Messwerte (jung/alt), Optimumwerte und Status (⬇️/✅/⬆️).\n"
            "Dann: Allgemeine Beurteilung nach EDAPRO Regelwerk (Zucker/Fotosynthese, pH, "
            "Hauptnährstoffe K/Ca/Mg, N-Situation, Antagonismen).\n"
            "Dann: Konkrete Massnahmen.\n"
            "Ende: 💬 Frag mich jetzt ALLES zu deiner Blattsaftanalyse!"
        )
    else:
        prompt = (
            f"Basierend auf dieser Bodenanalyse (aus Foto extrahiert):\n\n{ocr_text}\n\n"
            "Erstelle eine vollständige strukturierte Übersicht ALLER Messwerte "
            "auf Deutsch für WhatsApp mit:\n"
            "- Statusindikator ✅/⬇️/⬆️ für jeden Wert\n"
            "- Kurzkommentar was der Wert bedeutet\n"
            "Struktur: ✅ *Bodenanalyse geladen!* → pH & Puffer → Organik → "
            "Physik → Nährstoffsättigung → Verhältnisse → Spurenelemente\n"
            "Ende: 💬 Frag mich jetzt ALLES rund um deinen Boden!"
        )

    summary_response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    welcome = extract_text_block(summary_response) or "Analyse geladen – stell mir deine Fragen."
    return ocr_text, welcome


# ══════════════════════════════════════════════════════════════════════════════
# TWILIO SIGNATUR VALIDIERUNG
# ══════════════════════════════════════════════════════════════════════════════

async def _twilio_signature_valid(request: Request, form_dict: dict) -> bool:
    if os.environ.get("TWILIO_VALIDATE_SIGNATURE", "0") != "1":
        return True
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        log.warning("TWILIO_VALIDATE_SIGNATURE=1 aber kein TWILIO_AUTH_TOKEN")
        return False
    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    fwd_proto = request.headers.get("X-Forwarded-Proto")
    if fwd_proto and url.startswith(f"{'https' if fwd_proto == 'http' else 'http'}://"):
        url = url.replace(f"{'https' if fwd_proto == 'http' else 'http'}://",
                          f"{fwd_proto}://", 1)
    return validator.validate(url, form_dict, signature)


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

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

    form_data = await request.form()
    form_dict = {k: v for k, v in form_data.items()}
    if not await _twilio_signature_valid(request, form_dict):
        log.warning("Ungültige Twilio-Signatur von %s", phone)
        return Response(content="Forbidden", status_code=403)

    twilio_sid   = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        log.error("ANTHROPIC_API_KEY fehlt")
        twiml = MessagingResponse()
        twiml.message("⚠️ Konfigurationsfehler beim Bot. Bitte wende dich an info@halterhus.ch.")
        return Response(content=str(twiml), media_type="application/xml")

    # ── 1. Anhang empfangen (PDF oder Foto) ───────────────────────────────────
    if NumMedia > 0 and MediaUrl0:
        content_type = (MediaContentType0 or "").lower()

        # ── 1a. PDF ───────────────────────────────────────────────────────────
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

                # Erkennen: NovaCropControl (Blattsaft) oder Robertson SSM (Boden)?
                if is_novacropcontrol_pdf(pdf_path):
                    log.info("NovaCropControl Blattsaftanalyse erkannt für %s", phone)
                    bs_data = parse_novacropcontrol_pdf(pdf_path)
                    if bs_data:
                        sessions[phone] = {
                            "data": None,
                            "blattsaft_data": bs_data,
                            "ocr_text": None,
                            "history": [],
                            "source": "blattsaft",
                        }
                        response_text = blattsaft_analysis_summary(bs_data)
                    else:
                        response_text = (
                            "⚠️ Das sieht wie ein NovaCropControl-Bericht aus, aber ich konnte "
                            "die Werte nicht vollständig extrahieren.\n\n"
                            "Bitte schick ein *scharfes Foto* des Berichts – ich lese den Text dann aus."
                        )
                else:
                    # Robertson SSM Bodenanalyse
                    log.info("Robertson SSM Bodenanalyse erkannt für %s", phone)
                    data = parse_ssm_report(pdf_path)
                    sessions[phone] = {
                        "data": data,
                        "blattsaft_data": None,
                        "ocr_text": None,
                        "history": [],
                        "source": "pdf",
                    }
                    response_text = detailed_analysis_summary(data)

            except Exception as exc:
                log.exception("Fehler beim PDF-Parsen für %s", phone)
                response_text = (
                    "❌ Ich konnte dein PDF leider nicht lesen.\n\n"
                    "Unterstützte Formate:\n"
                    "• 📊 *NovaCropControl* Blattsaftanalyse-PDF\n"
                    "• 🌱 *Robertson SSM* Bodenanalyse-PDF\n\n"
                    "Alternativ: schick ein *scharfes Foto* des Berichts."
                )
            finally:
                if pdf_path:
                    try:
                        Path(pdf_path).unlink(missing_ok=True)
                    except Exception:
                        pass

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

                # Erkennen ob Blattsaft oder Boden
                is_bs = any(kw in ocr_text for kw in
                            ["Zucker", "Nitrat", "Ammonium", "NovaCropControl",
                             "Aktuellen Niveau", "Pflanzensaft"])
                sessions[phone] = {
                    "data": None,
                    "blattsaft_data": None,
                    "ocr_text": ocr_text,
                    "history": [],
                    "source": "image_blattsaft" if is_bs else "image",
                }
                response_text = welcome

            except Exception:
                log.exception("Fehler beim Bild-Parsen für %s", phone)
                response_text = (
                    "❌ Ich konnte den Bericht auf dem Foto nicht lesen.\n\n"
                    "Tipps:\n"
                    "• Gutes Licht, kein Blitz\n"
                    "• Bericht flach auf Tisch legen\n"
                    "• Ganz im Bild, kein Rand abgeschnitten\n"
                    "• Scharf und gut lesbar\n\n"
                    "Oder schick das PDF direkt."
                )

        else:
            response_text = (
                "📎 Dateiformat nicht erkannt.\n\n"
                "Bitte schick:\n"
                "• 📄 *PDF* (NovaCropControl Blattsaft oder Robertson SSM Boden)\n"
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
                "Du kannst mir aber auch ohne Analyse jede Frage stellen – "
                "zur Bodenanalyse, Blattsaftanalyse, Düngung, Fruchtfolge, Bodenbiologie..."
            )

        elif any(kw in msg_lower for kw in [
            "zeige analyse", "zeig analyse", "zeig mir die analyse",
            "übersicht", "uebersicht", "zusammenfassung"
        ]):
            sess = sessions.get(phone)
            if sess:
                src = sess.get("source", "")
                if src == "blattsaft":
                    response_text = blattsaft_analysis_summary(sess["blattsaft_data"])
                elif src == "pdf":
                    response_text = detailed_analysis_summary(sess["data"])
                else:
                    response_text = "Keine Analyse geladen. Schick mir ein PDF oder Foto!"
            else:
                response_text = "Keine Analyse geladen. Schick mir ein PDF oder Foto!"

        elif msg_lower in {"hilfe", "help", "?", "menu", "menü"}:
            has_session = phone in sessions
            sess = sessions.get(phone, {})
            src = sess.get("source", "")
            if src == "blattsaft":
                analyse_info = "📊 *NovaCropControl Blattsaftanalyse* geladen – Tipp «zeige analyse» für Übersicht.\n\n"
            elif src in ("pdf", "image"):
                analyse_info = "📋 *Bodenanalyse* geladen – Tipp «zeige analyse» für Übersicht.\n\n"
            else:
                analyse_info = "📎 Schick mir dein PDF (Blattsaft oder Boden) oder ein Foto für persönliche Empfehlungen.\n\n"
            response_text = (
                "🌱 *EDAPRO Bodenberater – Hilfe*\n\n"
                "Ich kann dir helfen bei:\n"
                "• 🌿 *Blattsaftanalyse* (NovaCropControl) – Interpretation, Massnahmen\n"
                "• 🌱 *Bodenanalyse* (Robertson SSM) – Werte, Verhältnisse, Düngung\n"
                "• 🌾 Düngung, Kalkung, Fruchtfolge, Zwischenfrüchte\n"
                "• 🦠 Bodenbiologie, Humusaufbau, Verdichtung\n"
                "• 🇨🇭 Schweizer Vorschriften (ÖLN, Bio Suisse, DüV)\n\n"
                + analyse_info
                + "🔄 «neue analyse» – neuen Bericht laden\n"
                "📞 info@halterhus.ch  |  www.edapro.ch"
            )

        else:
            # KI-Antwort
            session = sessions.get(phone)
            if session is None:
                session = {
                    "data": None, "blattsaft_data": None,
                    "ocr_text": None, "history": [], "source": "none"
                }
                sessions[phone] = session

            soil_summary = get_soil_context(session)
            src = session.get("source", "")

            # System-Blöcke: immer SYSTEM_CORE + KNOWLEDGE_BLOCK + BLATTSAFT_KNOWLEDGE_BLOCK + Daten
            system_blocks = [
                {
                    "type": "text",
                    "text": SYSTEM_CORE,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": KNOWLEDGE_BLOCK,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": BLATTSAFT_KNOWLEDGE_BLOCK,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": SOIL_DATA_TMPL.replace("{soil_data}", soil_summary),
                },
            ]

            session["history"].append({"role": "user", "content": message})
            clipped = clip_history(session["history"])
            messages = FEW_SHOTS + clipped

            try:
                client = anthropic.Anthropic(api_key=anthropic_key)
                ai_response = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=MAX_ANSWER_TOKENS,
                    system=system_blocks,
                    messages=messages,
                )
                answer = extract_text_block(ai_response)
                if not answer:
                    raise RuntimeError("Leere Antwort von Claude")
                session["history"].append({"role": "assistant", "content": answer})
                response_text = answer

                try:
                    usage = getattr(ai_response, "usage", None)
                    if usage is not None:
                        log.info(
                            "Claude usage – in=%s out=%s cache_create=%s cache_read=%s",
                            getattr(usage, "input_tokens", "?"),
                            getattr(usage, "output_tokens", "?"),
                            getattr(usage, "cache_creation_input_tokens", 0),
                            getattr(usage, "cache_read_input_tokens", 0),
                        )
                except Exception:
                    pass

            except Exception:
                log.exception("Fehler beim KI-Aufruf für %s", phone)
                if session["history"] and session["history"][-1].get("role") == "user":
                    session["history"].pop()
                response_text = (
                    "⚠️ Gerade klemmt bei mir etwas – bitte in einer Minute nochmals versuchen.\n\n"
                    "Wenn das Problem bleibt: info@halterhus.ch"
                )

    # ── 3. Leere Nachricht ────────────────────────────────────────────────────
    else:
        response_text = (
            "👋 *Hallo! Ich bin der EDAPRO Bodenberater.*\n\n"
            "Ich analyse deine Blattsaftanalysen (NovaCropControl) und Bodenanalysen "
            "(Robertson SSM) – und beantworte alle Fragen rund um Boden, Düngung, "
            "Fruchtfolge und Pflanzenvitalität.\n\n"
            "*Was kannst du mir schicken?*\n"
            "• 📊 *NovaCropControl PDF* (Blattsaftanalyse) → sofortige Interpretation + Massnahmen\n"
            "• 🌱 *Robertson SSM PDF* (Bodenanalyse) → vollständige Bewertung aller Werte\n"
            "• 📸 *Foto* eines Berichts → ich lese ihn per KI aus\n\n"
            "*Oder stell mir direkt eine Frage:*\n"
            "• «Warum ist der Zucker in der Blattsaftanalyse tief?»\n"
            "• «Was bedeutet erhöhtes Nitrat im Blatt?»\n"
            "• «Welche Zwischenfrucht passt für tonigen Boden?»\n"
            "• «Wie baue ich Humus auf?»\n\n"
            "🌱 *EDAPRO – Bodengesundheit aus der Schweiz*\n"
            "www.edapro.ch  |  info@halterhus.ch"
        )

    # ── TwiML senden ─────────────────────────────────────────────────────────
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
    return {
        "status": "EDAPRO WhatsApp Bot läuft ✅",
        "model": CLAUDE_MODEL,
        "features": ["Robertson SSM Bodenanalyse", "NovaCropControl Blattsaftanalyse", "Vision OCR"]
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ── Lokaler Start ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
