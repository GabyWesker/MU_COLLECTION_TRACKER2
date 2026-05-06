import os
from pathlib import Path

import bcrypt
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()
NEON_CONN = os.environ.get("NEON_CONN")
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

BASE_MASTER_SETS = [
    "Leather",
    "Pad",
    "Scale",
    "Sphinx",
    "Plate",
    "Spirit",
    "Legendary",
    "Adamantine",
    "Black Dragon",
    "Bone",
]

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

def get_all_sets():
    conn = get_connection()
    if not conn:
        return get_master_sets()
    try:
        df = pd.read_sql("SELECT DISTINCT nombre_set FROM sets ORDER BY nombre_set", conn)
        conn.close()
        return df["nombre_set"].dropna().tolist()
    except Exception as e:
        print(f"Error al cargar sets: {e}")
        conn.close()
        return get_master_sets()

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
        sets_sin_helm = {"Storm Crow", "Thunder Hawk", "Sacred Fire", "Storm Zahard"}
        sets_sin_guantes = set()
        if nombre_set in sets_sin_helm:
            piezas = ("Armor", "Pants", "Gloves", "Boots")
        elif nombre_set in sets_sin_guantes:
            piezas = ("Helm", "Armor", "Pants", "Boots")
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

