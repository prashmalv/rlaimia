"""
Message Worker — matches each person to an appropriate template text.
Designed for 5-lakh scale: all lookups are O(1) via pre-built dict.
"""

import os
import sys
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

# ─── Campaign Phases ─────────────────────────────────────────────────────────
PHASE_T_DAY      = "T_DAY"          # Birthday today
PHASE_T_MINUS_10 = "T_MINUS_10"     # 10 days before birthday
PHASE_ALL        = "ALL"            # No date filter (bulk/manual run)

SHEET_MAP = {
    PHASE_T_MINUS_10: "BirthdayT-10",
    PHASE_T_DAY: "BirthdayT0",
}

# ─── Hardcoded default templates (fallback when no file provided / file fails) ─
# Keys are pre-normalized: lowercase, no parenthetical suffixes.
_DEFAULT_REGISTRY: dict[str, dict[str, list[str]]] = {
    PHASE_T_MINUS_10: {
        "gen z starter": [
            "Message:\n Hey <first_name>,\n Your birthday\u2019s around the corner, and that\u2019s reason enough to pause for a second.\n"
            "Whether you feel like keeping it simple or choosing something that feels a little more you, we\u2019ve put together styles that feel right for where you are right now.\n"
            "Take a look whenever you feel like it. No rush.\n <short_link>"
        ],
        "young achiever": [
            "Hi <first_name>,\nWith your birthday coming up, this is a good moment to pause and think about what feels right for you this year.\n\n"
            "Whether it\u2019s something you\u2019ve been meaning to buy for yourself or a piece that simply fits your style today, our birthday edits bring together contemporary design and everyday elegance.\n\n"
            "You can explore online, preview how styles look on you with Virtual Try-On, or visit a Mia store whenever it suits you.\n\n"
            "Discover styles curated for you\n <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia"
        ],
        "life builder": [
            "Dear <first_name>,\nWith your birthday approaching, this is a quiet moment to reflect on what feels meaningful to you this year.\n\n"
            "Many choose this time to mark the occasion with a piece that feels personal today and continues to hold relevance over time. Our birthday selection is curated with that sensibility in mind.\n\n"
            "You may explore the collection online, preview how styles look on you with Virtual Try-On, or visit a Mia store at your convenience.\n\n"
            "Explore pieces chosen for moments that matter\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq"
        ],
        "family & value seeker": [
            "Dear <first_name>,\nWith your birthday approaching, this is a moment many choose to celebrate with intention - selecting something that reflects their taste and the confidence they\u2019ve built over time.\n\n"
            "Our birthday edit brings together refined design and craftsmanship meant to be worn, enjoyed, and returned to - not just saved for special occasions.\n\n"
            "You may explore the collection online, preview how pieces look on you with Virtual Try-On, or visit a Mia store whenever it suits you.\n\n"
            "Explore jewellery chosen with confidence <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Team Mia"
        ],
        "mature optimiser": [
            "Dear <first_name>,\nAs your birthday approaches, this is a moment many choose to mark with something truly considered - a piece that reflects discernment, individuality, and a life well lived.\n\n"
            "Our birthday edition has been curated for those who value refined design and lasting presence - jewellery chosen not to follow trends, but to stand apart from them.\n\n"
            "You may explore the collection online, preview how pieces look on you with Virtual Try-On, or visit a Mia store at a time that suits you best.\n\n"
            "Discover pieces chosen for those who know their style\n <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq"
        ],
    },
    PHASE_T_DAY: {
        "gen z starter": [
            "Hi <first_name>,\nHappy Birthday \u2728\n\n"
            "Today doesn\u2019t need plans or overthinking - it\u2019s just a good day to do something for yourself.\n\n"
            "If you feel like treating yourself, we\u2019ve got styles that fit into your everyday and still feel special when you wear them.\n\n"
            "Explore online, see how they look on you with Virtual Try-On, or drop by a Mia store whenever it suits you.\n\n"
            "Pick something you\u2019ll love wearing\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you: <link>\n\n\u2014 Always with you, Mia"
        ],
        "young achiever": [
            "Hi <first_name>,\nHappy Birthday\u2728\n\n"
            "Some milestones don\u2019t need a big plan - just a choice that feels considered. If you\u2019re thinking of marking today with something special, our collections are designed to fit seamlessly into your everyday while still standing out.\n\n"
            "Enjoy up to 10% off on select jewellery during your birthday period.\n\n"
            "Choose a piece that feels right for you\n<short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n- Mia"
        ],
        "life builder": [
            "Dear <first_name>,\nWarm birthday wishes from Mia\u2728\n\n"
            "Every year brings moments worth acknowledging in a way that feels thoughtful and lasting.\n\n"
            "If you\u2019re considering marking today with something special, our collections are designed to balance contemporary style with timeless appeal.\n\n"
            "A birthday benefit is available on select jewellery during this period.\n\n"
            "Choose a piece that holds meaning beyond today\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq"
        ],
        "family & value seeker": [
            "Dear <first_name>,\nWishing you a very Happy Birthday\u2728\n\n"
            "Some celebrations call for a choice that feels assured - a piece that complements your style today and continues to feel right beyond the occasion. Our collections are designed with this sensibility at heart.\n\n"
            "A birthday privilege is available on select jewellery during this period.\n\n"
            "Choose a piece that reflects your sense of style\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Team Mia"
        ],
        "mature optimiser": [
            "Dear <first_name>,\nWarm and thoughtful birthday wishes\u2728\n\n"
            "Some birthdays are best marked with intention - celebrating taste, confidence, and a clear sense of what feels right.\n\n"
            "Our birthday selection is curated for individuals who choose with intention - pieces that don\u2019t ask for attention, yet always hold it.\n\n"
            "A birthday privilege is available on select jewellery should you wish to mark the day.\n\n"
            "Choose a piece that reflects who you are <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq"
        ],
    },
}

# ─── Template Registry (singleton, loaded once per process) ──────────────────
_template_registry: dict[str, dict[str, list[str]]] = {}   # phase → persona → [texts]


def load_templates(template_file: str = None) -> None:
    """
    Load and index all templates from XLSX into memory.
    Falls back to hardcoded _DEFAULT_REGISTRY if file is None, missing, or corrupted.
    """
    global _template_registry
    _template_registry = {}

    if not template_file:
        _template_registry = _DEFAULT_REGISTRY
        logger.info("Using hardcoded default templates (no file provided)")
        return

    try:
        xl = pd.ExcelFile(template_file, engine="openpyxl")
        for phase, sheet_name in SHEET_MAP.items():
            if sheet_name not in xl.sheet_names:
                logger.warning(f"Sheet '{sheet_name}' not found in {template_file}")
                continue
            df = pd.read_excel(template_file, sheet_name=sheet_name, engine="openpyxl")
            df.columns = df.columns.str.strip()
            text_col = next((c for c in df.columns if c.lower().startswith("text")), None)
            persona_col = next((c for c in df.columns if "persona" in c.lower() or "age" in c.lower()), None)

            if not text_col or not persona_col:
                logger.error(f"Could not detect text/persona columns in sheet {sheet_name}. Cols: {df.columns.tolist()}")
                continue

            phase_dict: dict[str, list[str]] = {}
            for _, row in df.iterrows():
                raw_persona = row.get(persona_col, "")
                texts_val   = row.get(text_col, "")
                if pd.isna(raw_persona) or pd.isna(texts_val):
                    continue
                persona_key = _normalize_persona(str(raw_persona))
                if persona_key not in phase_dict:
                    phase_dict[persona_key] = []
                phase_dict[persona_key].append(str(texts_val).strip())

            _template_registry[phase] = phase_dict
            logger.info(f"Loaded {sum(len(v) for v in phase_dict.values())} templates for phase {phase}")

    except Exception as e:
        logger.warning(f"Failed to load templates from '{template_file}': {e}. Using hardcoded defaults.")
        _template_registry = _DEFAULT_REGISTRY


def _normalize_persona(text: str) -> str:
    """Normalize persona string for consistent matching."""
    if not text:
        return ""
    text = text.lower().strip()
    if "(" in text:
        text = text.split("(")[0].strip()
    return text


def get_campaign_phase(dob: str, reference_date: datetime = None) -> Optional[str]:
    """
    Determine campaign phase based on DOB relative to today.
    Returns: PHASE_T_DAY | PHASE_T_MINUS_10 | None
    """
    today = (reference_date or datetime.today()).date()
    try:
        dob_date = pd.to_datetime(dob).date()
        dob_this_year = dob_date.replace(year=today.year)

        if dob_this_year == today:
            return PHASE_T_DAY
        if dob_this_year - timedelta(days=10) == today:
            return PHASE_T_MINUS_10
        return None
    except Exception as e:
        logger.debug(f"Could not parse DOB '{dob}': {e}")
        return None


def generate_message(
    first_name: str,
    persona: str,
    phase: str,
    short_link: str = "<short_link>",
    store_link: str = "<link>",
) -> Optional[str]:
    """
    Pick a random template for the given persona+phase and personalise it.
    Returns None if no template found.
    """
    if phase == PHASE_ALL:
        # Try T_DAY first, fallback to T_MINUS_10
        for p in [PHASE_T_DAY, PHASE_T_MINUS_10]:
            msg = generate_message(first_name, persona, p, short_link, store_link)
            if msg:
                return msg
        return None

    phase_dict = _template_registry.get(phase, {})
    persona_key = _normalize_persona(persona)

    texts = phase_dict.get(persona_key)
    if not texts:
        # Fuzzy fallback: try partial match
        for key, val in phase_dict.items():
            if persona_key and persona_key in key:
                texts = val
                break

    if not texts:
        logger.warning(f"No template for persona='{persona}' phase='{phase}'")
        return None

    text = random.choice(texts)
    text = text.replace("<first_name>", first_name)
    text = text.replace("<short_link>", short_link)
    text = text.replace("<link>", store_link)
    return text.strip()


def extract_message_lines(full_message: str) -> dict:
    """
    Extract structured lines from the message for image/video overlay.
    Returns dict with: heading, subheading, body, cta
    """
    lines = [l.strip() for l in full_message.split("\n") if l.strip()]

    heading    = ""
    subheading = ""
    body_parts = []
    cta        = ""

    for i, line in enumerate(lines):
        if i == 0 and ("happy birthday" in line.lower() or "hi " in line.lower()):
            heading = line
        elif i == 1 and heading:
            subheading = line
        elif line.startswith("http") or "<short_link>" in line or "<link>" in line:
            cta = line
        else:
            body_parts.append(line)

    return {
        "heading":    heading or (lines[0] if lines else "Happy Birthday"),
        "subheading": subheading or "",
        "body":       "\n".join(body_parts[:3]),     # max 3 body lines on image
        "cta":        cta or "",
        "full":       full_message,
    }


def build_person_record(
    row: dict,
    name_col: str,
    template_file: str,
    phase_override: str = None,
) -> Optional[dict]:
    """
    Build a complete job record for one person.
    Returns None if phase doesn't apply today.
    """
    if not _template_registry:
        load_templates(template_file)

    full_name  = str(row.get(name_col, "")).strip()
    first_name = full_name.split()[0] if full_name else "Friend"
    persona    = str(row.get("Persona", row.get("persona", ""))).strip()
    age        = row.get("Age", row.get("age", 0))
    dob        = str(row.get("DOB", row.get("dob", "")))

    phase = phase_override or get_campaign_phase(dob)
    if not phase:
        return None  # Not their birthday window today

    message = generate_message(first_name, persona, phase)
    if not message:
        return None

    lines = extract_message_lines(message)

    return {
        "person_name":  full_name,
        "first_name":   first_name,
        "age":          int(age) if age else 0,
        "persona":      persona,
        "dob":          dob,
        "phase":        phase,
        "message_text": message,
        "lines":        lines,
    }
