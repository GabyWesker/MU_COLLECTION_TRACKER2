import psycopg2
import os
import bcrypt
from dotenv import load_dotenv

load_dotenv()
NEON_CONN = os.environ.get("NEON_CONN")

def get_connection():
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

def get_user_sets_distinct(user_id):
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
        return []

def get_master_sets():
    # Lista Maestra para la Galería (Agregá acá todos los sets)
    return ["Leather", "Pad", "Scale", "Sphinx", "Plate", "Spirit", "Legendary", "Adamantine", "Black Dragon", "Bone"]

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
        return None

