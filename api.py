import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from database_logic import (
    register_user,
    get_users_for_auth,
    load_user_data_bundle,
    get_user_items_for_set,
    get_all_sets,
    add_full,
    delete_item,
    delete_set_complete,
    toggle_obtenido,
    update_item_field,
    export_data,
    verify_password,
    create_set_complete,
    get_user_id_by_username,
    get_master_sets,
    get_user_sets,
    search_market,
    fetch_market_upstream_items,
    market_badges_from_item,
)
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="MU Collection Tracker API")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SET_BONUSES = {
    "Pad": {"hp": 1000},
    "Leather": {"hp": 1000},
    "Vine": {"hp": 1000},
    "Bronze": {"atk_speed": 15},
    "Silk": {"atk_speed": 20},
    "Bone": {"increase_dmg": 2},
    "Scale": {"increase_dmg": 4},
    "Wind": {"increase_def": 2},
    "Violent Wind": {"ignore": 1},
    "Sphinx": {"hp": 1500},
    "Brass": {"sd": 2},
    "Spirit": {"increase_dmg": 3},
    "Plate": {"dd": 1},
    "Legendary": {"increase_def": 3},
    "Red Winged": {"hp": 1500},
    "Guardian": {"atk_speed": 25},
    "Dragon": {"hp": 1500},
    "Light Plate": {"exce_dmg": 2},
    "Sacred Fire": {"increase_def": 5},
    "Ancient": {"dd": 2},
    "Adamantine": {"hp": 1500},
    "Storm Crow": {"exce_dmg": 4},
    "Storm Zahard": {"hp": 2000},
    "Black Dragon": {"atk_speed": 30},
    "Demonic": {"reflect": 2},
    "Grand Soul": {"increase_def": 5},
    "Holy Spirit": {"dd": 2},
    "Dark Steel": {"hp": 2000},
    "Dark Phoenix": {"sd": 6},
}

BONUS_LABELS = {
    "hp": "HP",
    "atk_speed": "Atk Speed",
    "increase_dmg": "Increase Dmg",
    "increase_def": "Increase Def",
    "ignore": "Ignore",
    "sd": "SD",
    "dd": "DD",
    "exce_dmg": "Exce Dmg",
    "reflect": "Reflect",
}

class UserLogin(BaseModel):
    username: str
    password: str

class UserRegister(BaseModel):
    email: str
    username: str
    password: str
    personaje: Optional[str] = ""

class CreateSetRequest(BaseModel):
    nombre_set: str
    kundun: int

class AddItemRequest(BaseModel):
    user_id: int
    nombre_set: str
    pieza: str
    kundun: int
    luck: int
    obtiene: int
    enchant: int
    life: int
    sd: int
    dd: int
    dsr: int
    ref: int
    hp: int
    zen: int

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )

@app.post("/api/login")
async def login(credentials: UserLogin):
    users = get_users_for_auth()
    if credentials.username in users:
        user_data = users[credentials.username]
        if verify_password(credentials.password, user_data['password']):
            user_id = get_user_id_by_username(credentials.username)
            return {"success": True, "username": credentials.username, "user_id": user_id}
    raise HTTPException(status_code=401, detail="Credenciales inválidas")

@app.post("/api/register")
async def register(user: UserRegister):
    result = register_user(user.email, user.username, user.password, user.personaje)
    if result is True:
        return {"success": True, "message": "Cuenta creada"}
    raise HTTPException(status_code=400, detail=str(result))

@app.get("/api/sets")
async def get_sets():
    return {"sets": get_all_sets()}

@app.get("/api/sets/master")
async def get_master_sets_endpoint():
    return {"sets": get_master_sets()}

@app.get("/api/bonus-types")
async def get_bonus_types():
    keys = sorted({bonus_type for bonuses in SET_BONUSES.values() for bonus_type in bonuses.keys()})
    return {
        "bonus_types": [
            {"value": key, "label": BONUS_LABELS.get(key, key)}
            for key in keys
        ]
    }

@app.get("/api/user/{user_id}/sets")
async def get_user_sets_endpoint(user_id: int):
    return {"sets": get_user_sets(user_id)}

@app.get("/api/user/{user_id}/data")
async def get_user_data(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    estado: Optional[str] = None,
    tier: Optional[str] = None,
    set_name: Optional[str] = Query(None, alias="set"),
):
    bundle = load_user_data_bundle(
        user_id,
        page=page,
        page_size=page_size,
        search=search,
        estado=estado,
        tier=tier,
        set_name=set_name,
    )
    return bundle


@app.get("/api/user/{user_id}/items/in-set")
async def get_items_in_set(user_id: int, nombre_set: str = Query(..., min_length=1)):
    return {"items": get_user_items_for_set(user_id, nombre_set)}

@app.get("/search-items")
async def search_items(query: str, luck: Optional[bool] = None, opts: Optional[str] = None):
    excellent_options = [opt.strip().lower() for opt in opts.split(",") if opt.strip()] if opts else []
    try:
        return search_market(query, luck=luck, excellent_options=excellent_options)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/market-payload")
async def debug_market_payload(
    query: str = Query(..., min_length=1),
    opts: Optional[str] = None,
):
    """
    Con MARKET_DEBUG=1: primer ítem crudo del mercado + badges parseados.
    Sirve para ver la forma real del JSON y ajustar database_logic.
    """
    if os.environ.get("MARKET_DEBUG", "").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not found")
    excellent_options = [opt.strip().lower() for opt in opts.split(",") if opt.strip()] if opts else []
    try:
        items = fetch_market_upstream_items(query, excellent_options=excellent_options)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    first = items[0] if items else None
    preview = ""
    if isinstance(first, dict):
        try:
            preview = json.dumps(first, ensure_ascii=False, default=str)[:12000]
        except (TypeError, ValueError):
            preview = ""
    return {
        "upstream_count": len(items),
        "first_item": first,
        "badges_parsed": market_badges_from_item(first) if isinstance(first, dict) else [],
        "first_item_json_preview": preview,
    }


@app.post("/api/user/{user_id}/create_set")
async def create_set(user_id: int, req: CreateSetRequest):
    if create_set_complete(user_id, req.nombre_set, req.kundun):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al crear set")

@app.post("/api/user/{user_id}/add_item")
async def add_item(user_id: int, req: AddItemRequest):
    if add_full(req.user_id, req.nombre_set, req.pieza, req.kundun, req.luck, req.obtiene,
                req.enchant, req.life, req.sd, req.dd, req.dsr, req.ref, req.hp, req.zen):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al añadir item")

@app.delete("/api/user/{user_id}/item/{item_id}")
async def delete_item_endpoint(user_id: int, item_id: int):
    if delete_item(item_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al eliminar")

@app.delete("/api/user/{user_id}/set")
async def delete_set_endpoint(user_id: int, nombre_set: str):
    if delete_set_complete(user_id, nombre_set):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al eliminar set")

@app.put("/api/item/{item_id}/toggle")
async def toggle_item(item_id: int, obtenido: bool):
    if toggle_obtenido(item_id, obtenido):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al actualizar")

@app.put("/api/item/{item_id}/field")
async def update_field(item_id: int, field: str, value: str):
    if update_item_field(item_id, field, value):
        return {"success": True}
    raise HTTPException(status_code=500, detail="Error al actualizar")

@app.get("/api/user/{user_id}/export")
async def export_user_data(user_id: int):
    csv_data = export_data(user_id)
    if csv_data:
        return Response(content=csv_data, media_type="text/csv")
    raise HTTPException(status_code=500, detail="Error al exportar")
