from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from database_logic import (
    register_user,
    get_users_for_auth,
    load_data,
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
    search_market
)
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="MU Collection Tracker API")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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

ITEM_DEFAULTS = {
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

def dataframe_records(df, defaults=None):
    if df.empty:
        return []
    safe_df = df.copy()
    for column, default in (defaults or {}).items():
        if column in safe_df.columns:
            safe_df[column] = safe_df[column].fillna(default)
    return safe_df.to_dict(orient="records")

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

@app.get("/api/user/{user_id}/sets")
async def get_user_sets_endpoint(user_id: int):
    return {"sets": get_user_sets(user_id)}

@app.get("/api/user/{user_id}/data")
async def get_user_data(user_id: int):
    df, df_premios = load_data(user_id)
    items = dataframe_records(df, ITEM_DEFAULTS)
    premios = dataframe_records(df_premios)
    return {"items": items, "premios": premios}

@app.get("/search-items")
async def search_items(query: str, luck: Optional[bool] = None, opts: Optional[str] = None):
    excellent_options = [opt.strip().lower() for opt in opts.split(",") if opt.strip()] if opts else []
    return search_market(query, luck=luck, excellent_options=excellent_options)

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
