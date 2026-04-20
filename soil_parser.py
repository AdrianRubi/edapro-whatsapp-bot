"""
soil_parser.py – Schlanker Robertson SSM PDF-Parser für den WhatsApp Bot.
Extrahiert nur die Daten, keine Docx/Grafik-Abhängigkeiten.
"""

import logging
import re
from typing import Optional

import fitz  # PyMuPDF

log = logging.getLogger("edapro.parser")


def extract_number(text: str, pattern: str, group: int = 1) -> Optional[float]:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(group).replace(',', '.'))
    except (ValueError, IndexError):
        return None


def _first_group(text: str, pattern: str, default: str = '') -> str:
    """Gibt die erste Gruppe eines Regex zurück oder den Default-Wert."""
    m = re.search(pattern, text)
    return m.group(1).strip() if m else default


def parse_ssm_report(pdf_path: str) -> dict:
    """
    Liest ein Robertson SSM PDF und gibt ein Dict mit allen Messwerten zurück.
    """
    data: dict = {}

    # Context-Manager schliesst das PDF auch bei Exceptions sauber
    with fitz.open(pdf_path) as doc:
        text = "".join(page.get_text() for page in doc)

        # ── Felder ────────────────────────────────────────────────────────────
        data['field_id']    = _first_group(text, r'Field ID[:\s]*\n([^\n]+)')
        data['sample_date'] = _first_group(text, r'Sample DATE[:\s]+([^\n]+)')
        data['report_date'] = _first_group(text, r'Report DATE[:\s]+([^\n]+)')
        data['crop']        = _first_group(text, r'CROP SOWN[:\s]+([^\n]+)')

        # ── pH ────────────────────────────────────────────────────────────────
        data['active_ph'] = extract_number(text, r'Active pH\s*\n?([\d.]+)')
        data['buffer_ph'] = extract_number(text, r'Buffer pH\s*\n?([\d.]+)')

        # ── Textur ────────────────────────────────────────────────────────────
        m = re.search(
            r'Sand\s*%\s*\nSilt\s*%\s*\nClay\s*%\s*\n(\d+)\s*\n(\d+)\s*\n(\d+)',
            text,
        )
        if m:
            data['sand'] = float(m.group(1))
            data['silt'] = float(m.group(2))
            data['clay'] = float(m.group(3))
        m = re.search(
            r'(Sandy Clay Loam|Silty Clay Loam|Sandy Clay|Silty Clay|'
            r'Clay Loam|Sandy Loam|Silty Loam|Loam|Clay)\b',
            text, re.IGNORECASE,
        )
        data['texture'] = m.group(1) if m else ''

        # ── Organische Substanz & Kohlenstoff ─────────────────────────────────
        m = re.search(r'Organic Matter[^\n]*\nMin[^\n]*\n([\d.]+)', text)
        data['organic_matter'] = (
            float(m.group(1)) if m
            else extract_number(text, r'Organic Matter[^\n]*\n\s*([\d.]+)')
        )
        data['organic_carbon'] = extract_number(
            text, r'Organic Carbon\(LOI\)[^\n]*\n?\s*([\d.]+)'
        )
        m = re.search(r'Active Carbon[^\n]*\n?([\d.]+)\s*([\d.]+)%', text)
        if m:
            data['active_carbon_mg']  = float(m.group(1))
            data['active_carbon_pct'] = float(m.group(2))
        else:
            data['active_carbon_pct'] = extract_number(
                text, r'Active Carbon[^\n]*?([\d.]+)%'
            )
        data['co2_burst'] = extract_number(text, r'Co2 Burst\s*\n?([\d.]+)')
        data['cn_ratio']  = extract_number(text, r'C:N ratio\s*\n?([\d.]+)')
        m = re.search(r'Clay:SOC\s*\n?\s*[\w\s]*?\n?\s*([\d.]+)', text)
        data['clay_soc']  = float(m.group(1)) if m else None

        # ── Lagerungsdichte & KAK ─────────────────────────────────────────────
        data['bulk_density'] = extract_number(
            text, r'Field Bulk density[^\n]*\n([\d.]+)'
        )
        m = re.search(r'15 viewed as average\s*\n([\d.]+)', text)
        data['tec'] = float(m.group(1)) if m else None

        # ── Hauptnährstoffe (kg/ha + % Sättigung) ────────────────────────────
        for elem, pat in [
            ('ca', r'Calcium\s*\+\+\s*([\d.]+)\s*([\d.]+)\s*(-?[\d.]+)\s*([\d.]+)\s*([\d.]+)'),
            ('mg', r'Magnesium\s*\+\+\s*([\d.]+)\s*([\d.]+)\s*(-?[\d.]+)\s*([\d.]+)\s*([\d.]+)'),
            ('k',  r'Potassium\s*\+\s*([\d.]+)\s*([\d.]+)\s*(-?[\d.]+)\s*([\d.]+)\s*([\d.]+)'),
            ('na', r'Sodium\s*\+\s*([\d.]+)\s*([\d.]+)\s*(-?[\d.]+)\s*([\d.]+)\s*([\d.]+)'),
        ]:
            m = re.search(pat, text)
            if m:
                data[f'{elem}_kg_desired']  = float(m.group(1))
                data[f'{elem}_kg_found']    = float(m.group(2))
                data[f'{elem}_sat_desired'] = float(m.group(4))
                data[f'{elem}_sat_found']   = float(m.group(5))

        m = re.search(r'Hydrogen\s*\n?(\d+)%\s*\n?([\d.]+)\s*([\d.]+)', text)
        if m:
            data['h_sat'] = float(m.group(3))

        # ── Schwefel ──────────────────────────────────────────────────────────
        m = re.search(r'Sulphate\s*\(S0?3?\)\s*([\d.]+)\s*([\d.]+)', text)
        if m:
            data['so4_desired'] = float(m.group(1))
            data['so4_found']   = float(m.group(2))

        # ── Verhältnisse ──────────────────────────────────────────────────────
        ca_sat = data.get('ca_sat_found')
        mg_sat = data.get('mg_sat_found')
        if ca_sat is not None and mg_sat is not None and mg_sat > 0:
            data['ca_mg_ratio_found'] = round(ca_sat / mg_sat, 2)

        m = re.search(r'Mg\s*:\s*K\s*\n?([\d.]+)\s*\n?([\d.]+)', text)
        if m:
            data['mg_k_ratio_target'] = float(m.group(1))
            data['mg_k_ratio_found']  = float(m.group(2))
        m = re.search(r'K\s*:\s*Na\s*\n?([\d.]+)\s*\n?([\d.]+)', text)
        if m:
            data['k_na_ratio_target'] = float(m.group(1))
            data['k_na_ratio_found']  = float(m.group(2))

        # ── Phosphor ──────────────────────────────────────────────────────────
        data['p_cycling'] = extract_number(text, r'Phosphorus\s*\n?([\d.]+)\s*%\s*5-8')
        data['cp_ratio']  = extract_number(text, r'C:P ratio\s*\n?([\d.]+)')

        # ── Spurenelemente ────────────────────────────────────────────────────
        for name, pat in [
            ('boron',      r'Boron\s*B\s*mg/l\s*([\d.]+)'),
            ('iron',       r'Iron\s*Fe\s*mg/l\s*([\d.]+)'),
            ('manganese',  r'Manganese\s*Mn\s*mg/l\s*([\d.]+)'),
            ('copper',     r'Copper\s*Cu\s*mg/l\s*([\d.]+)'),
            ('zinc',       r'Zinc\s*Zn\s*mg/l\s*([\d.]+)'),
            ('chlorine',   r'Chlorine\s*Cl\s*mg/l\s*([\d.]+)'),
            ('iodine',     r'Iodine\s*I\s*mg/l\s*([\d.]+)'),
            ('molybdenum', r'Molybdenum\s*Mo\s*mg/l\s*([\d.]+)'),
            ('cobalt',     r'Cobalt\s*Co\s*mg/l\s*([\d.]+)'),
        ]:
            data[name] = extract_number(text, pat)

        # ── Reservenährstoffe (Block-basiert) ─────────────────────────────────
        try:
            blks = doc[0].get_text('blocks')
            res_col = sorted(
                [b for b in blks if 255 <= b[0] <= 335 and (b[3] - b[1]) < 30],
                key=lambda b: b[1],
            )
            for b in res_col:
                y0, txt = b[1], b[4]
                nums = [float(n) for n in re.findall(r'\b(\d{2,5})\b', txt)
                        if float(n) > 0]
                if not nums:
                    continue
                if 330 <= y0 <= 360 and len(nums) >= 2:
                    data.setdefault('ca_total_desired_kg', nums[0])
                    data.setdefault('ca_total_found_kg',  nums[1])
                elif 361 <= y0 <= 373:
                    data.setdefault('k_total_kg',  nums[0])
                elif 374 <= y0 <= 392:
                    data.setdefault('na_total_kg', nums[0])
                elif 393 <= y0 <= 430:
                    data.setdefault('s_total_kg',  nums[0])
        except Exception as exc:
            log.warning("Reservenährstoff-Block-Parsing fehlgeschlagen: %s", exc)

        # ── P2O5 total ────────────────────────────────────────────────────────
        try:
            for b in doc[0].get_text('blocks'):
                btxt = b[4]
                if 'P2O5' in btxt or 'P205' in btxt or 'Phosphate (' in btxt:
                    pnums = re.findall(r'(-?\d+(?:\.\d+)?)', btxt)
                    pos_nums = [float(n) for n in pnums if float(n) >= 100]
                    if len(pos_nums) >= 3:
                        data.setdefault('p2o5_total_kg', pos_nums[-1])
                        break
        except Exception as exc:
            log.warning("P2O5-Block-Parsing fehlgeschlagen: %s", exc)

        # ── Gesamtverhältnisse ────────────────────────────────────────────────
        ratio_m = re.search(r'RATIOS\s*:\s*1', text)
        if ratio_m:
            rat = text[ratio_m.start():]
            for pat, tk, fk in [
                (r'Ca\s*:\s*Mg\s+([\d.]+)[\s\n]+([\d.]+)',
                 'ca_mg_ratio_total_target', 'ca_mg_ratio_total_found'),
                (r'Mg\s*:?\s*K\s+([\d.]+)[\s\n]+([\d.]+)',
                 'mg_k_ratio_total_target',  'mg_k_ratio_total_found'),
                (r'K\s*:\s*Na\s+([\d.]+)[\s\n]+([\d.]+)',
                 'k_na_ratio_total_target',  'k_na_ratio_total_found'),
            ]:
                m2 = re.search(pat, rat)
                if m2:
                    try:
                        data[tk] = float(m2.group(1))
                        data[fk] = float(m2.group(2))
                    except (ValueError, IndexError):
                        pass

    return data
