import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import bcrypt
import pandas as pd
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
NEON_CONN = os.environ.get("NEON_CONN")
DEFAULT_MU_API_URL = "https://mudream-api.crusoft.dev/api/game/market/items"
MU_API_URL = os.environ.get("MU_API_URL", DEFAULT_MU_API_URL)
MU_API_TOKEN = os.environ.get("MU_API_TOKEN")
BASE_DIR = Path(__file__).resolve().parent
SETS_ASSETS_DIR = BASE_DIR / "static" / "assets" / "Sets"

ALLOWED_UPDATE_FIELDS = {
    "kundun",
    "obtenido",
    "luck",
    "nivel_bs",
    "add_lif",
    "opt_sd",
    "opt_dd",
    "opt_dsr",
    "opt_ref",
    "opt_hp",
    "opt_zen",
}

# Catálogo oficial de sets (orden de referencia). Nombres exactos en BD / UI.
CANONICAL_SET_NAMES = (
    "Pad",
    "Leather",
    "Vine",
    "Bronze",
    "Silk",
    "Bone",
    "Scale",
    "Wind",
    "Violent Wind",
    "Sphinx",
    "Brass",
    "Spirit",
    "Plate",
    "Legendary",
    "Red Winged",
    "Guardian",
    "Dragon",
    "Light Plate",
    "Sacred Fire",
    "Ancient",
    "Adamantine",
    "Storm Crow",
    "Storm Zahard",
    "Black Dragon",
    "Demonic",
    "Grand Soul",
    "Holy Spirit",
    "Dark Steel",
    "Dark Phoenix",
    "Great Dragon",
    "Dark Soul",
    "Hurricane",
    "Red Spirit",
    "Dark Master",
    "Thunder Hawk",
    "Storm Blitz",
    "Piercing Grove",
)

BASE_MASTER_SETS = list(CANONICAL_SET_NAMES)

# Clave en minúsculas → nombre canónico exacto (typos que no resuelve el match por casing).
SET_NAME_ALIASES = {
    "sphinix": "Sphinx",
    "shpinx": "Sphinx",
    "thuner hawk": "Thunder Hawk",
    "thunder hawk": "Thunder Hawk",
}

_CANONICAL_BY_LOWER = {name.lower(): name for name in CANONICAL_SET_NAMES}

EXCELLENT_OPTION_CODES = {
    "sd": {"sd", "imsd"},
    "dd": {"dd"},
    "dsr": {"dsr"},
    "ref": {"ref", "rd"},
    "hp": {"hp", "iml"},
    "zen": {"zen", "izdr"},
    "mana": {"mana", "imm"},
}

MARKET_OPTION_CODES = {
    "sd": "imsd",
    "dd": "dd",
    "dsr": "dsr",
    "ref": "rd",
    "hp": "iml",
    "zen": "izdr",
    "mana": "imm",
}

MARKET_OPTION_LEVELS = range(5)

MARKET_OPTION_LABELS = {
    "sd": "SD",
    "dd": "DD",
    "dsr": "DSR",
    "ref": "REF",
    "hp": "HP",
    "zen": "ZEN",
    "mana": "MANA",
}

# Dónde buscar opciones excelentes en la respuesta del mercado (lista, dict o string JSON/texto).
MARKET_ITEM_OPTION_KEYS = (
    "options",
    "excellentOptions",
    "excellent_options",
    "opts",
    "excellent",
    "excellent_option_list",
    "excellentOptionList",
    "excellentDetails",
    "details",
    "itemDetails",
    "modifiers",
    "attributes",
    "bonusOptions",
    "bonuses",
)

# Ej. "[Normal] Damage Decrease", "[Rare] Defense Success Rate" (API MU Dream).
_BRACKET_TIER_LINE_RE = re.compile(
    r"^\s*\[(normal|common|uncommon|rare|epic|legendary)\]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _market_option_values(options):
    values = set()

    def add_value(value):
        if value is None:
            return
        if isinstance(value, str):
            m = _BRACKET_TIER_LINE_RE.match(value.strip())
            if m:
                rest = m.group(2).strip().lower()
                c = _canonical_from_description(rest)
                if c:
                    values.add(c)
                    oc = MARKET_OPTION_CODES.get(c)
                    if oc:
                        values.add(oc)
        text = str(value).lower()
        values.add(text)
        for token in text.replace(",", " ").replace(";", " ").replace("|", " ").split():
            values.add(token.strip("[](){}:"))
        if text in {"rd", "ref"}:
            values.update({"ref", "rd"})
        if text in {"iml", "hp"}:
            values.update({"hp", "iml"})
        if text in {"imm", "mana"}:
            values.update({"mana", "imm"})
        if text in {"imsd", "sd"}:
            values.update({"sd", "imsd"})
        if text in {"izdr", "zen"}:
            values.update({"zen", "izdr"})
        for option, code in MARKET_OPTION_CODES.items():
            if text.startswith(code) and text[len(code):].isdigit():
                values.update({option, code})
        if "damage decrease" in text:
            values.add("dd")
        if "reflect damage" in text:
            values.update({"ref", "rd"})
        if "defense success rate" in text:
            values.add("dsr")
        if "zen drop" in text:
            values.update({"zen", "izdr"})
        if "increase maximum life" in text or "increase max life" in text or "max hp" in text:
            values.update({"hp", "iml"})
        if "increase maximum mana" in text or "increase max mana" in text or "max mana" in text:
            values.update({"mana", "imm"})
        if "increase shield" in text or "increase sd" in text:
            values.update({"sd", "imsd"})

    if options is None:
        iterable_options = []
    elif isinstance(options, dict):
        iterable_options = [options]
    elif isinstance(options, (list, tuple, set)):
        iterable_options = options
    else:
        iterable_options = [options]

    for option in iterable_options:
        if isinstance(option, dict):
            for key, value in option.items():
                add_value(key)
                add_value(value)
        elif isinstance(option, (list, tuple, set)):
            values.update(_market_option_values(option))
        else:
            add_value(option)
    return values

def _item_market_option_values(item):
    values = set()
    for key in MARKET_ITEM_OPTION_KEYS + ("socketOptions",):
        if key in item:
            values.update(_market_option_values(item.get(key)))
    return values

def _match_reasons_for_item(item, excellent_options, luck=False):
    reasons = []
    if luck:
        reasons.append("Luck")
    item_options = _item_market_option_values(item)
    for option in excellent_options:
        allowed_codes = EXCELLENT_OPTION_CODES.get(option, {option})
        if not item_options or item_options.intersection(allowed_codes):
            reasons.append(MARKET_OPTION_LABELS.get(option, option.upper()))
    return reasons

def _item_has_luck(item):
    if "hasLuck" in item:
        return bool(item.get("hasLuck"))
    if "luck" in item:
        return bool(item.get("luck"))
    item_options = _item_market_option_values(item)
    if "luck" in item_options:
        return True
    # If the API does not expose luck in a parseable field, avoid false negatives.
    return True

def _get_market_api_url():
    parsed = urlparse(MU_API_URL)
    if parsed.path.rstrip("/").endswith("/api/game/market/items"):
        return MU_API_URL

    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else MU_API_URL.rstrip("/")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/game/market"):
        return origin + path + "/items"
    if path.endswith("/api/game"):
        return origin + path + "/market/items"
    if path.endswith("/api"):
        return origin + path + "/game/market/items"
    return origin + "/api/game/market/items"

def _market_items_from_response(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "data", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _market_items_from_response(value)
            if nested:
                return nested
    return []


def api_numeric_to_ui_rarity(api_level):
    """Nivel numérico API / dígitos imsd3 → índice de color 0–4 (igual que tier en juego)."""
    try:
        L = int(float(api_level))
    except (TypeError, ValueError):
        return 0
    return max(0, min(4, L))


def api_option_level_to_ui_rarity(api_level):
    """Alias: niveles numéricos de la API (regex, campos level, pares JSON)."""
    return api_numeric_to_ui_rarity(api_level)


def _extract_api_option_level_from_dict(d):
    """Obtiene nivel 0–4 según la API en cada opción."""
    if not isinstance(d, dict):
        return None
    for key in (
        "level",
        "optionLevel",
        "option_level",
        "lvl",
        "rank",
        "enhancementLevel",
        "enhancement",
        "excellentLevel",
        "optionTier",
        "upgradeLevel",
    ):
        if key in d and d[key] is not None:
            try:
                v = int(float(d[key]))
                return max(0, min(4, v))
            except (TypeError, ValueError):
                continue
    for key in ("rarity", "grade", "tier", "quality", "optionRarity"):
        if key in d and d[key] is not None:
            try:
                v = int(float(d[key]))
                return max(0, min(4, v))
            except (TypeError, ValueError):
                continue
    return None


_WORD_TIER_TO_LEVEL = {
    "normal": 0,
    "norm": 0,
    "common": 0,
    "grey": 0,
    "gray": 0,
    "uncommon": 1,
    "green": 1,
    "rare": 2,
    "pink": 2,
    "magenta": 2,
    "epic": 3,
    "orange": 3,
    "legendary": 4,
    "legend": 4,
    "red": 4,
}


def _tier_word_level_from_dict(d):
    """Nivel 0–4 a partir de strings tipo Epic / Legendary en la opción."""
    if not isinstance(d, dict):
        return 0
    best = 0
    for key in (
        "tier",
        "tierName",
        "rarityName",
        "qualityName",
        "rankName",
        "optionRarityName",
        "gradeName",
        "levelName",
        "grade",
        "rarity",
        "quality",
    ):
        v = d.get(key)
        if isinstance(v, str):
            t = v.strip().lower()
            if t in _WORD_TIER_TO_LEVEL:
                best = max(best, _WORD_TIER_TO_LEVEL[t])
    return best


def _rarity_from_option_dict(d):
    lvl = _extract_api_option_level_from_dict(d)
    base = api_numeric_to_ui_rarity(lvl) if lvl is not None else 0
    tw = _tier_word_level_from_dict(d)
    return max(base, tw)


_CODE_ALIASES = {
    "imsd": "sd",
    "izdr": "zen",
    "iml": "hp",
    "imm": "mana",
    "rd": "ref",
}


def _canonical_from_description(text):
    """Nombres largos que envía la API en strings / labels."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    if not s:
        return ""
    if s.strip() in ("sd", "imsd"):
        return "sd"
    if "damage decrease" in s or s.strip() == "dd":
        return "dd"
    if "defense success rate" in s or "def success" in s:
        return "dsr"
    if s.strip() in ("dsr", "dr"):
        return "dsr"
    if "reflect damage" in s:
        return "ref"
    if "zen drop" in s or "zen obtain" in s:
        return "zen"
    if "increase shield" in s or "increase sd" in s or s == "shield":
        return "sd"
    if re.search(r"(?:^|\s)sd(?:\s|$)", s) and "damage decrease" not in s:
        return "sd"
    if "increase maximum life" in s or "increase max life" in s or "max hp" in s:
        return "hp"
    if "increase maximum mana" in s or "max mana" in s:
        return "mana"
    return ""


def _badge_from_bracket_option_line(line):
    """Una línea tipo '[Rare] Damage Decrease' → code + rareza 0–4."""
    if not isinstance(line, str) or not line.strip():
        return None
    m = _BRACKET_TIER_LINE_RE.match(line.strip())
    if not m:
        return None
    tier_key = m.group(1).lower()
    rest = m.group(2).strip()
    # Tier del texto [Epic]/[Legendary] ya es semántico (3=naranja, 4=rojo); no aplicar swap API.
    lvl = _WORD_TIER_TO_LEVEL.get(tier_key, 0)
    canon = _canonical_from_description(rest)
    if not canon:
        return None
    return {
        "code": canon,
        "label": MARKET_OPTION_LABELS.get(canon, canon.upper()),
        "rarity": lvl,
    }


def _badges_from_bracket_labeled_strings(item):
    badges = []
    for key in MARKET_ITEM_OPTION_KEYS:
        v = item.get(key)
        if not isinstance(v, list):
            continue
        for line in v:
            b = _badge_from_bracket_option_line(line)
            if b:
                badges.append(b)
    return badges


def _canonical_excellent_code(raw_code):
    if raw_code is None:
        return ""
    by_desc = _canonical_from_description(raw_code)
    if by_desc:
        return by_desc
    s = str(raw_code).strip().lower()
    s = re.sub(r"\d+$", "", s)
    s = _CODE_ALIASES.get(s, s)
    if s in MARKET_OPTION_LABELS:
        return s
    return ""


def _unwrap_option_dict(opt):
    if isinstance(opt, dict):
        inner = opt.get("excellentOption") or opt.get("excellent_option") or opt.get("opt")
        if isinstance(inner, dict):
            merged = {**opt, **inner}
            merged.pop("excellentOption", None)
            merged.pop("excellent_option", None)
            merged.pop("opt", None)
            return merged
    return opt


def _resolve_canon_from_option_dict(opt):
    """
    Resuelve sd/dd/hp… desde name/label antes que code numérico sin sentido.
    """
    if not isinstance(opt, dict):
        return ""
    priority = (
        "name",
        "label",
        "description",
        "displayName",
        "title",
        "text",
        "code",
        "type",
        "option",
        "key",
        "optionType",
        "optionCode",
        "excellentType",
    )
    for fk in priority:
        v = opt.get(fk)
        if v is None:
            continue
        c = _canonical_excellent_code(v)
        if c:
            return c
    for v in opt.values():
        if isinstance(v, str) and len(v) > 2:
            c = _canonical_from_description(v)
            if c:
                return c
    return ""


def _parse_market_option_badge(opt):
    """Una opción excelente → dict code / label / rarity UI (sin Luck)."""
    if opt is None:
        return None
    if isinstance(opt, dict):
        opt = _unwrap_option_dict(opt)
        try:
            sub_blob = json.dumps(opt, default=str).lower()
        except (TypeError, ValueError):
            sub_blob = ""
        blob_boost = 0
        if sub_blob:
            rb = (
                _badges_from_regex_codes_blob(sub_blob)
                + _badges_from_json_keyed_levels_blob(sub_blob)
            )
            if rb:
                blob_boost = max(b["rarity"] for b in rb)

        if len(opt) == 1:
            raw_key = next(iter(opt.keys()))
            code_raw = re.sub(r"\d+$", "", str(raw_key))
            val0 = next(iter(opt.values()))
            api_lvl = None
            try:
                if val0 is not None and str(val0).strip() != "":
                    api_lvl = int(float(val0))
            except (TypeError, ValueError):
                api_lvl = None
            rarity = (
                api_option_level_to_ui_rarity(api_lvl)
                if api_lvl is not None
                else _rarity_from_option_dict(opt)
            )
            rarity = max(rarity, blob_boost)
            canon = (
                _canonical_excellent_code(code_raw)
                or _canonical_excellent_code(str(raw_key))
                or (isinstance(val0, str) and _canonical_from_description(val0))
                or ""
            )
        else:
            rarity = max(_rarity_from_option_dict(opt), blob_boost)
            canon = _resolve_canon_from_option_dict(opt)
        if not canon:
            return None
        return {
            "code": canon,
            "label": MARKET_OPTION_LABELS.get(canon, canon.upper()),
            "rarity": rarity,
        }
    if isinstance(opt, str):
        text = opt.strip()
        m = re.match(
            r"^(imsd|izdr|iml|imm|sd|dd|dsr|ref|rd|hp|zen|mana)(\d)?$",
            text.lower(),
        )
        if m:
            base = m.group(1)
            digit = m.group(2)
            canon = _canonical_excellent_code(base)
            if not canon:
                return None
            api_lvl = int(digit) if digit else 0
            rarity = api_option_level_to_ui_rarity(api_lvl)
            return {
                "code": canon,
                "label": MARKET_OPTION_LABELS.get(canon, canon.upper()),
                "rarity": rarity,
            }
    return None


def _merge_badges_max_rarity(badges):
    """Un badge por código; conserva la rareza máxima."""
    best = {}
    order = []
    for b in badges:
        if not b:
            continue
        c = b["code"]
        if c not in best:
            order.append(c)
            best[c] = dict(b)
        elif b["rarity"] > best[c]["rarity"]:
            best[c] = dict(b)
    return [best[c] for c in order]


def _item_json_blob_lower(item):
    try:
        return json.dumps(item, default=str).lower()
    except (TypeError, ValueError):
        return ""


def _badges_from_regex_codes_blob(blob_lower):
    """Códigos cortos + dígito (p. ej. izdr3, dd2) en un JSON ya serializado."""
    if not blob_lower:
        return []
    badges = []
    pat = re.compile(
        r"(?:^|[^a-z0-9])(imsd|izdr|iml|imm|sd|dd|dsr|ref|rd|hp|zen|mana)(\d)(?=[^0-9]|$)"
    )
    for m in pat.finditer(blob_lower):
        canon = _canonical_excellent_code(m.group(1))
        if not canon:
            continue
        badges.append({
            "code": canon,
            "label": MARKET_OPTION_LABELS[canon],
            "rarity": api_option_level_to_ui_rarity(int(m.group(2))),
        })
    return badges


def _badges_from_regex_codes_in_blob(item):
    """Códigos cortos + dígito (p. ej. izdr3, dd2) en cualquier parte del JSON."""
    return _badges_from_regex_codes_blob(_item_json_blob_lower(item))


def _badges_from_json_keyed_levels_blob(blob_lower):
    """Pares \"dd\": 3, \"izdr\": 4 en JSON serializado (fragmento o ítem completo)."""
    if not blob_lower:
        return []
    badges = []
    for canon, label in MARKET_OPTION_LABELS.items():
        for alt in filter(None, (canon, MARKET_OPTION_CODES.get(canon))):
            # Número con o sin comillas: "imsd": 3 o "imsd": "3"
            m = re.search(rf'"{re.escape(alt)}"\s*:\s*"?(\d+)"?', blob_lower)
            if m:
                try:
                    lvl = int(m.group(1))
                except ValueError:
                    lvl = 0
                badges.append({
                    "code": canon,
                    "label": label,
                    "rarity": api_option_level_to_ui_rarity(lvl),
                })
                break
    return badges


def _badges_from_json_keyed_levels(item):
    """Pares \"dd\": 3, \"izdr\": 4 en JSON serializado (respuesta típica de API)."""
    return _badges_from_json_keyed_levels_blob(_item_json_blob_lower(item))


def _badges_from_option_tokens(item):
    """Mismo criterio que el matcher de ítems + nivel por regex en el JSON."""
    blob = _item_json_blob_lower(item)
    tokens = _item_market_option_values(item)
    badges = []
    for canon, label in MARKET_OPTION_LABELS.items():
        aliases = {canon, MARKET_OPTION_CODES.get(canon, "")}
        aliases.discard("")
        if not (tokens & aliases):
            continue
        api_lvl = 0
        for alt in aliases:
            mat = re.search(
                rf"(?:^|[^a-z0-9])({re.escape(alt)})(\d)(?=[^0-9]|$)",
                blob,
            )
            if mat:
                api_lvl = max(api_lvl, int(mat.group(2)))
        badges.append({
            "code": canon,
            "label": label,
            "rarity": api_option_level_to_ui_rarity(api_lvl),
        })
    return badges


def market_badges_from_item(item):
    """Une todas las fuentes de opciones que envía la API."""
    structured = []
    for key in MARKET_ITEM_OPTION_KEYS:
        v = item.get(key)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and len(v) == 0:
            continue

        string_blobs = []
        lst = []
        if isinstance(v, str):
            string_blobs.append(v.strip().lower())
            try:
                parsed = json.loads(v)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                lst = [parsed]
            elif isinstance(parsed, list):
                lst = parsed
        elif isinstance(v, dict):
            lst = [v]
        elif isinstance(v, list):
            lst = v
        else:
            continue

        for blob in string_blobs:
            structured.extend(_badges_from_regex_codes_blob(blob))
            structured.extend(_badges_from_json_keyed_levels_blob(blob))
        for opt in lst:
            b = _parse_market_option_badge(opt)
            if b:
                structured.append(b)
            if isinstance(opt, dict):
                ob = json.dumps(opt, default=str).lower()
                structured.extend(_badges_from_json_keyed_levels_blob(ob))
                structured.extend(_badges_from_regex_codes_blob(ob))

    kv_badges = _badges_from_json_keyed_levels(item)
    regex_badges = _badges_from_regex_codes_in_blob(item)
    token_badges = _badges_from_option_tokens(item)
    bracket_badges = _badges_from_bracket_labeled_strings(item)

    combined = structured + kv_badges + regex_badges + token_badges + bracket_badges
    if not combined:
        return []
    return _merge_badges_max_rarity(combined)


def market_dc_price_from_item(item):
    prices = item.get("prices")
    if prices is None:
        return None, "DC"
    if isinstance(prices, dict):
        prices = [prices]
    if not isinstance(prices, list) or not prices:
        return None, "DC"
    for p in prices:
        if not isinstance(p, dict):
            continue
        cur = str(p.get("currency") or p.get("type") or p.get("symbol") or "").strip().upper()
        if cur == "DC" or "DREAM" in cur:
            amt = p.get("amount")
            if amt is None:
                amt = p.get("value")
            if amt is not None:
                return amt, "DC"
    p0 = prices[0]
    if isinstance(p0, dict):
        amt = p0.get("amount")
        if amt is None:
            amt = p0.get("value")
        cur = str(p0.get("currency") or "").strip().upper()
        if amt is not None and (cur == "DC" or "DREAM" in cur):
            return amt, "DC"
    return None, "DC"

def get_connection():
    if not NEON_CONN:
        print("Error de conexión: NEON_CONN no está configurada")
        return None
    try:
        return psycopg2.connect(NEON_CONN)
    except Exception as e:
        print(f"Error de conexión: {e}")
        return None

# --- FUNCIÓN QUE FALTABA (Autenticación) ---
def get_users_for_auth():
    conn = get_connection()
    if not conn: return {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT email, username, password_hash FROM usuarios")
        users = cur.fetchall()
        cur.close()
        conn.close()
        result = {}
        for email, username, pwd_hash in users:
            result[username] = {
                'name': username,
                'email': email,
                'password': pwd_hash
            }
        return result
    except Exception as e:
        print(f"Error en auth: {e}")
        return {}

def register_user(email, username, password, personaje=None):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        # Hasheamos la password antes de guardar.
        hashed_pwd = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if personaje is None:
            cur.execute(
                "INSERT INTO usuarios (email, username, password_hash) VALUES (%s, %s, %s)",
                (email, username, hashed_pwd),
            )
        else:
            cur.execute(
                """
                INSERT INTO usuarios (email, username, password_hash, personaje)
                VALUES (%s, %s, %s, %s)
                """,
                (email, username, hashed_pwd, personaje),
            )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al registrar: {e}")
        conn.rollback()
        conn.close()
        return False

def get_user_info(user_id):
    conn = get_connection()
    if not conn: return (None, None)
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, personaje FROM usuarios WHERE id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result if result else (None, None)
    except Exception as e:
        print(f"Error al cargar usuario: {e}")
        conn.close()
        return (None, None)

def update_personaje(user_id, personaje):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET personaje = %s WHERE id = %s", (personaje, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al actualizar personaje: {e}")
        conn.rollback()
        conn.close()
        return False

PIEZA_ORDER_SQL = (
    "CASE pieza WHEN 'Helm' THEN 1 WHEN 'Armor' THEN 2 WHEN 'Pants' THEN 3 "
    "WHEN 'Gloves' THEN 4 WHEN 'Boots' THEN 5 ELSE 99 END"
)

_ITEM_ROW_DEFAULTS = {
    "nombre_set": "",
    "pieza": "",
    "kundun": 0,
    "obtenido": False,
    "luck": False,
    "nivel_bs": 0,
    "add_lif": 0,
    "opt_sd": False,
    "opt_dd": False,
    "opt_dsr": False,
    "opt_ref": False,
    "opt_hp": False,
    "opt_zen": False,
}


def _df_item_records(df):
    if df.empty:
        return []
    safe_df = df.copy()
    for column, default in _ITEM_ROW_DEFAULTS.items():
        if column in safe_df.columns:
            safe_df[column] = safe_df[column].fillna(default)
    return safe_df.to_dict(orient="records")


def load_data(user_id):
    conn = get_connection()
    if not conn:
        return pd.DataFrame(), pd.DataFrame()
    try:
        df = pd.read_sql("SELECT * FROM sets WHERE user_id = %s", conn, params=(user_id,))
        df_premios = pd.read_sql("SELECT * FROM premios_sets", conn)
        conn.close()
        return df, df_premios
    except Exception as e:
        print(f"Error al cargar datos: {e}")
        conn.close()
        return pd.DataFrame(), pd.DataFrame()


def get_user_items_for_set(user_id, nombre_set):
    """Todas las piezas de un set (p. ej. búsqueda en mercado); máx. ~5 filas."""
    conn = get_connection()
    if not conn:
        return []
    try:
        order_by = f"ORDER BY {PIEZA_ORDER_SQL}"
        df = pd.read_sql(
            f"SELECT * FROM sets WHERE user_id = %s AND nombre_set = %s {order_by}",
            conn,
            params=(user_id, nombre_set),
        )
        conn.close()
        return _df_item_records(df)
    except Exception as e:
        print(f"Error get_user_items_for_set: {e}")
        conn.close()
        return []


def load_user_data_bundle(
    user_id,
    page=1,
    page_size=20,
    search=None,
    estado=None,
    tier=None,
    set_name=None,
):
    """
    Items paginados (filas completas) + listado ligero de todas las piezas del usuario
    para bonus / galería sin enviar todos los campos en cada fila.
    """
    empty = {
        "items": [],
        "items_light": [],
        "premios": [],
        "total": 0,
        "obtained_count": 0,
        "filtered_total": 0,
        "page": page,
        "page_size": page_size,
        "total_pages": 1,
    }

    conn = get_connection()
    if not conn:
        return empty

    page_size = max(1, min(int(page_size), 100))
    page = max(1, int(page))
    offset = (page - 1) * page_size

    conditions = ["user_id = %s"]
    params = [user_id]

    if search and str(search).strip():
        term = f"%{search.strip()}%"
        conditions.append("(nombre_set ILIKE %s OR pieza ILIKE %s)")
        params.extend([term, term])

    if estado == "Pendientes":
        conditions.append("(obtenido = FALSE OR obtenido IS NULL)")
    elif estado == "Completados":
        conditions.append("obtenido = TRUE")

    if tier and tier != "Todos":
        conditions.append("kundun = %s")
        params.append(int(tier))

    if set_name and set_name != "Todos":
        conditions.append("nombre_set = %s")
        params.append(set_name)

    where_sql = " AND ".join(conditions)
    order_by = f"ORDER BY nombre_set ASC, {PIEZA_ORDER_SQL}"

    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sets WHERE user_id = %s", (user_id,))
        total_all = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM sets WHERE user_id = %s AND obtenido IS TRUE",
            (user_id,),
        )
        obtained_all = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM sets WHERE {where_sql}", params)
        filtered_total = cur.fetchone()[0]
        cur.close()

        page_df = pd.read_sql(
            f"SELECT * FROM sets WHERE {where_sql} {order_by} LIMIT %s OFFSET %s",
            conn,
            params=params + [page_size, offset],
        )

        light_df = pd.read_sql(
            f"SELECT id, nombre_set, pieza, obtenido FROM sets WHERE {where_sql} {order_by}",
            conn,
            params=params,
        )
        df_premios = pd.read_sql("SELECT * FROM premios_sets", conn)

        conn.close()

        items = _df_item_records(page_df)
        light_records = _df_item_records(light_df)
        for row in light_records:
            if "obtenido" in row:
                row["obtenido"] = bool(row["obtenido"])

        premios_records = []
        if not df_premios.empty:
            premios_records = df_premios.fillna("").to_dict(orient="records")

        total_pages = (
            max(1, (filtered_total + page_size - 1) // page_size)
            if filtered_total
            else 1
        )

        return {
            "items": items,
            "items_light": light_records,
            "premios": premios_records,
            "total": int(total_all),
            "obtained_count": int(obtained_all),
            "filtered_total": int(filtered_total),
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    except Exception as e:
        print(f"Error load_user_data_bundle: {e}")
        conn.close()
        return empty

def resolve_canonical_set_name(raw):
    """Devuelve el nombre canónico o None si no pertenece al catálogo."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in SET_NAME_ALIASES:
        return SET_NAME_ALIASES[lowered]
    return _CANONICAL_BY_LOWER.get(lowered)


def get_all_sets():
    """Lista para catálogos globales: solo maestro, sin mezclar filas de usuarios."""
    return get_master_sets()


def reconcile_user_sets(username):
    """
    Normaliza nombre_set al catálogo, borra sets no canónicos y fusiona duplicados
    (mismo user, set y pieza). Devuelve un dict con contadores.
    """
    user_id = get_user_id_by_username(username)
    if not user_id:
        return {"error": "usuario no encontrado", "username": username}

    conn = get_connection()
    if not conn:
        return {"error": "sin conexión"}

    stats = {
        "username": username,
        "user_id": user_id,
        "deleted_non_canonical": 0,
        "updated_names": 0,
        "merged_duplicate_rows": 0,
    }

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nombre_set, pieza, obtenido FROM sets WHERE user_id = %s",
            (user_id,),
        )
        rows = cur.fetchall()

        ids_delete = []
        id_updates = []

        for row_id, nombre_set, pieza, obtenido in rows:
            canon = resolve_canonical_set_name(nombre_set)
            if canon is None:
                ids_delete.append(row_id)
            elif canon != nombre_set:
                id_updates.append((canon, row_id))

        for row_id in ids_delete:
            cur.execute("DELETE FROM sets WHERE id = %s", (row_id,))
        stats["deleted_non_canonical"] = len(ids_delete)

        for canon, row_id in id_updates:
            cur.execute("UPDATE sets SET nombre_set = %s WHERE id = %s", (canon, row_id))
        stats["updated_names"] = len(id_updates)

        cur.execute(
            """
            SELECT nombre_set, pieza, array_agg(id ORDER BY obtenido DESC, id DESC) AS ids
            FROM sets
            WHERE user_id = %s
            GROUP BY nombre_set, pieza
            HAVING COUNT(*) > 1
            """,
            (user_id,),
        )
        merged = 0
        for _nombre, _pieza, ids in cur.fetchall():
            if not ids:
                continue
            keep_id = ids[0]
            for dup_id in ids[1:]:
                cur.execute("DELETE FROM sets WHERE id = %s", (dup_id,))
                merged += 1
        stats["merged_duplicate_rows"] = merged

        conn.commit()
        cur.close()
        conn.close()
        return stats
    except Exception as e:
        print(f"reconcile_user_sets: {e}")
        conn.rollback()
        conn.close()
        return {"error": str(e)}

def save_data(edited_df, user_id):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        for _, row in edited_df.iterrows():
            cur.execute(
                """
                UPDATE sets SET kundun=%s, obtenido=%s, luck=%s, nivel_bs=%s, add_lif=%s,
                opt_sd=%s, opt_dd=%s, opt_dsr=%s, opt_ref=%s, opt_hp=%s, opt_zen=%s
                WHERE id=%s AND user_id=%s
                """,
                (
                    int(row["kundun"]),
                    bool(row["obtenido"]),
                    bool(row["luck"]),
                    int(row["nivel_bs"]),
                    int(row["add_lif"]),
                    bool(row["opt_sd"]),
                    bool(row["opt_dd"]),
                    bool(row["opt_dsr"]),
                    bool(row["opt_ref"]),
                    bool(row["opt_hp"]),
                    bool(row["opt_zen"]),
                    int(row["id"]),
                    user_id,
                ),
            )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al guardar: {e}")
        conn.rollback()
        conn.close()
        return False

def add_full(user_id, nombre_set, pieza, kundun, luck, obtiene, enchant, life, sd, dd, dsr, ref, hp, zen):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sets (
                user_id, nombre_set, pieza, kundun, obtenido, luck, nivel_bs, add_lif,
                opt_sd, opt_dd, opt_dsr, opt_ref, opt_hp, opt_zen
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                nombre_set,
                pieza,
                kundun,
                bool(obtiene),
                bool(luck),
                enchant,
                life,
                bool(sd),
                bool(dd),
                bool(dsr),
                bool(ref),
                bool(hp),
                bool(zen),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[DEBUG] ERROR: {e}")
        conn.rollback()
        conn.close()
        return False

def delete_item(item_id, user_id):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sets WHERE id=%s AND user_id=%s", (item_id, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al eliminar: {e}")
        conn.rollback()
        conn.close()
        return False

def delete_set_complete(user_id, nombre_set):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sets WHERE user_id=%s AND nombre_set=%s", (user_id, nombre_set))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al eliminar: {e}")
        conn.rollback()
        conn.close()
        return False

def toggle_obtenido(item_id, new_value):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("UPDATE sets SET obtenido=%s WHERE id=%s", (bool(new_value), item_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al actualizar: {e}")
        conn.rollback()
        conn.close()
        return False

def update_item_field(item_id, field, value):
    if field not in ALLOWED_UPDATE_FIELDS:
        print(f"Campo no permitido: {field}")
        return False
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE sets SET {field}=%s WHERE id=%s", (value, item_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error al actualizar: {e}")
        conn.rollback()
        conn.close()
        return False

def export_data(user_id):
    conn = get_connection()
    if not conn: return None
    try:
        df = pd.read_sql("SELECT * FROM sets WHERE user_id = %s", conn, params=(user_id,))
        conn.close()
        return df.to_csv(index=False).encode("utf-8")
    except Exception as e:
        print(f"Error al exportar: {e}")
        conn.close()
        return None

def get_user_sets(user_id):
    conn = get_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT nombre_set FROM sets WHERE user_id = %s ORDER BY nombre_set", (user_id,))
        rows = cur.fetchall()
        nombres = [row[0] for row in rows]
        cur.close()
        conn.close()
        return nombres
    except Exception as e:
        print(f"Error en sets: {e}")
        conn.close()
        return []

def create_set_complete(user_id, nombre_set, kundun):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        sets_sin_guantes = {"Sacred Fire", "Storm Zahard", "Piercing Grove"}
        sets_sin_helm = {"Storm Crow", "Thunder Hawk", "Hurricane"}
        if nombre_set in sets_sin_guantes:
            piezas = ("Helm", "Armor", "Pants", "Boots")
        elif nombre_set in sets_sin_helm:
            piezas = ("Armor", "Pants", "Gloves", "Boots")
        else:
            piezas = ("Helm", "Armor", "Pants", "Gloves", "Boots")
        for p in piezas:
            cur.execute(
                """
                INSERT INTO sets (user_id, nombre_set, pieza, kundun, obtenido)
                VALUES (%s, %s, %s, %s, FALSE)
                """,
                (user_id, nombre_set, p, kundun),
            )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        conn.close()
        return False

def get_master_sets():
    asset_sets = []
    if SETS_ASSETS_DIR.exists():
        asset_sets = [
            path.stem for path in SETS_ASSETS_DIR.glob("*.png")
            if path.stem.lower() != "default"
        ]
    return sorted(set(BASE_MASTER_SETS + asset_sets))

def verify_password(plain_pwd, hashed_pwd):
    return bcrypt.checkpw(plain_pwd.encode(), hashed_pwd.encode())

def get_user_id_by_username(username):
    conn = get_connection()
    if not conn: return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE username = %s", (username,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        print(f"Error: {e}")
        conn.close()
        return None

def _market_debug_enabled():
    return os.environ.get("MARKET_DEBUG", "").strip().lower() in ("1", "true", "yes")


def fetch_market_upstream_items(item_name, excellent_options=None):
    """
    Lista cruda de ítems devuelta por la API de mercado (sin filtrar luck ni enriquecer).
    """
    excellent_options = [opt for opt in (excellent_options or []) if opt in EXCELLENT_OPTION_CODES]
    if not MU_API_TOKEN:
        raise RuntimeError("MU_API_TOKEN no está configurado")

    headers = {"Authorization": f"Bearer {MU_API_TOKEN}"}

    option_filters = []
    for option in excellent_options:
        code = MARKET_OPTION_CODES[option]
        option_filters.extend(f"{code}{level}" for level in MARKET_OPTION_LEVELS)

    params = {
        "query": item_name,
        "limit": 25,
    }
    if option_filters:
        params["options"] = ",".join(option_filters)

    market_url = _get_market_api_url()
    response = requests.get(market_url, headers=headers, params=params, timeout=10)
    parsed_url = urlparse(market_url)
    print(
        "[market] "
        f"GET {parsed_url.netloc}{parsed_url.path} "
        f"query={params.get('query')} "
        f"options={params.get('options', '-')} "
        f"status={response.status_code}"
    )
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise RuntimeError(f"Error de API de mercado: {response.status_code}")

    items = _market_items_from_response(response.json())
    print(f"[market] upstream_items={len(items)} query={params.get('query')}")

    if _market_debug_enabled() and items:
        try:
            dump = json.dumps(items[0], indent=2, ensure_ascii=False, default=str)
            max_len = 26000
            if len(dump) > max_len:
                dump = dump[:max_len] + "\n… [truncado]"
            print("[market-debug] primer ítem upstream (JSON):\n" + dump)
        except Exception as exc:
            print(f"[market-debug] no se pudo serializar primer ítem: {exc}")

    return items


def search_market(item_name, luck=None, excellent_options=None, ancient=False):
    excellent_options = [opt for opt in (excellent_options or []) if opt in EXCELLENT_OPTION_CODES]

    try:
        items = fetch_market_upstream_items(item_name, excellent_options=excellent_options)
        results = []
        for item in items:
            if luck is True and not _item_has_luck(item):
                continue
            item_opts = item.get("options", [])
            match_reasons = _match_reasons_for_item(item, excellent_options, luck=bool(luck))
            dc_amt, dc_cur = market_dc_price_from_item(item)
            badge_list = market_badges_from_item(item)
            results.append({
                "name": item.get("name", ""),
                "level": item.get("level", 0),
                "isExcellent": item.get("isExcellent", False),
                "isAncient": item.get("isAncient", False),
                "hasLuck": item.get("hasLuck", False),
                "hasSkill": item.get("hasSkill", False),
                "gearScore": item.get("gearScore", 0),
                "options": item_opts,
                "prices": item.get("prices", []),
                "jewels": item.get("jewels") or item.get("jewelCosts") or item.get("jewel_costs"),
                "imageUrl": item.get("imageUrl", ""),
                "match_score": 1,
                "match_reasons": match_reasons,
                "marketBadges": badge_list,
                "dcPrice": dc_amt,
                "dcCurrency": dc_cur,
            })
        print(f"[market] returned_results={len(results)} query={item_name}")
        return results
    except Exception as e:
        print(f"Error consultando mercado: {e}")
        raise


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "reconcile":
        print(json.dumps(reconcile_user_sets(sys.argv[2]), indent=2, ensure_ascii=False))
    else:
        print("Uso: python database_logic.py reconcile <usuario>")
