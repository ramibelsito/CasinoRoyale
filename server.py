from fastapi import FastAPI, WebSocket, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from datetime import datetime
import csv
import asyncio
import json
import secrets
from models import User, Table, users, tables, next_user_id, next_table_id




app = FastAPI()

CSV_FILE = "eventos.csv"
USERS_FILE = "users.json"
TABLES_FILE = "tables.json"
csv_lock = asyncio.Lock()

# Sesiones de crupier activas: {token: timestamp}
crupier_sessions = {}

app.mount("/static", StaticFiles(directory="static"), name="static")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()

def save_data():
    with open(USERS_FILE, 'w') as f:
        json.dump({dni: user.dict() for dni, user in users.items()}, f)
    with open(TABLES_FILE, 'w') as f:
        json.dump({tid: table.dict() for tid, table in tables.items()}, f)

def load_data():
    global next_user_id
    try:
        with open(USERS_FILE, 'r') as f:
            data = json.load(f)
            for dni, udict in data.items():
                users[dni] = User(**udict)
                if users[dni].id >= next_user_id:
                    next_user_id = users[dni].id + 1
    except FileNotFoundError:
        pass
    try:
        with open(TABLES_FILE, 'r') as f:
            data = json.load(f)
            for tid, tdict in data.items():
                tables[int(tid)] = Table(**tdict)
    except FileNotFoundError:
        pass

async def registrar_evento_ws(endpoint, tipo_evento, detalle, cliente_ip):
    evento = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "endpoint": endpoint,
        "tipo_evento": tipo_evento,
        "detalle": detalle,
        "cliente_ip": cliente_ip
    }

    # Guardar en CSV (sync)
    async with csv_lock:
        with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(evento.values())

    # Broadcast en tiempo real
    await manager.broadcast(evento)

@app.get("/")
async def landing():
    return FileResponse("static/index.html")

@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")

@app.get("/usuario")
async def usuario_page():
    return FileResponse("static/usuario.html")

@app.get("/crupier/login")
async def crupier_login_page():
    return FileResponse("static/crupier_login.html")

@app.get("/crupier")
async def crupier_page(crupier_token: Optional[str] = Cookie(None)):
    if not crupier_token or crupier_token not in crupier_sessions:
        return RedirectResponse(url="/crupier/login", status_code=303)
    return FileResponse("static/crupier.html")

@app.post("/crupier/login")
async def crupier_login(body: dict):
    if body.get('password') == 'Crupier007':
        token = secrets.token_urlsafe(32)
        crupier_sessions[token] = datetime.now().isoformat()
        response = HTMLResponse(content="""
        <html>
        <head><title>Redirecting...</title></head>
        <body>
        <script>
            window.location.href = '/crupier';
        </script>
        </body>
        </html>
        """)
        response.set_cookie(key="crupier_token", value=token, max_age=3600)
        return response
    raise HTTPException(status_code=401, detail="Contraseña incorrecta")

@app.post("/registro")
async def registro_submit(request: Request, body: dict):
    dni = body['dni']
    if dni in users:
        raise HTTPException(status_code=400, detail="DNI ya registrado")
    
    global next_user_id
    user = User(id=next_user_id, nombre=body['nombre'], apellido=body['apellido'], dni=dni)
    users[dni] = user
    next_user_id += 1
    save_data()
    
    await registrar_evento_ws(
        "/registro",
        "registro",
        f"Usuario {user.nombre} {user.apellido} registrado con ID {user.id}",
        request.client.host
    )
    return {"ok": True}

@app.post("/login")
async def login_submit(request: Request, body: dict):
    dni = body['dni']
    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    await registrar_evento_ws(
        "/login",
        "login",
        f"Usuario {users[dni].nombre} {users[dni].apellido} logueado",
        request.client.host
    )
    return {"ok": True}

@app.get("/usuario/data")
async def usuario_data(dni: str):
    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user = users[dni]
    leaderboard = sorted(users.values(), key=lambda u: (u.caja - u.deuda), reverse=True)
    return {
        "caja": user.caja,
        "deuda": user.deuda,
        "neto": user.caja - user.deuda,
        "leaderboard": [
            {
                "nombre": u.nombre,
                "apellido": u.apellido,
                "caja": u.caja,
                "deuda": u.deuda,
                "neto": u.caja - u.deuda,
                "mesa": tables[u.mesa_id].nombre if u.en_mesa and u.mesa_id in tables else "Inactivo"
            } for u in leaderboard[:10]
        ]
    }

@app.post("/crupier/ingresar")
async def crupier_ingresar(request: Request, body: dict):
    dni = body['dni']
    monto = body['monto']
    mesa_id = body['mesa_id']
    
    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user = users[dni]
    if user.en_mesa:
        raise HTTPException(status_code=400, detail="Usuario ya en mesa")
    
    if user.caja < monto:
        raise HTTPException(status_code=400, detail="Monto insuficiente")
    
    if mesa_id not in tables:
        tables[mesa_id] = Table(id=mesa_id)
    
    table = tables[mesa_id]
    table.jugadores[user.id] = {"monto": monto, "ingreso": datetime.now().isoformat()}
    user.caja -= monto
    user.en_mesa = True
    user.mesa_id = mesa_id
    save_data()
    
    await registrar_evento_ws(
        "/crupier/ingresar",
        "ingreso_mesa",
        f"Usuario {user.nombre} {user.apellido} ingreso a mesa {mesa_id} con {monto}",
        request.client.host
    )
    return {"ok": True}

@app.post("/crupier/retirar")
async def crupier_retirar(request: Request, body: dict):
    dni = body['dni']
    monto = body['monto']
    
    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user = users[dni]
    if not user.en_mesa:
        raise HTTPException(status_code=400, detail="Usuario no en mesa")
    
    table = tables[user.mesa_id]
    current_monto = table.jugadores[user.id]["monto"]
    if current_monto + monto < 0:
        raise HTTPException(status_code=400, detail="Monto negativo no permitido")
    
    table.jugadores[user.id]["monto"] += monto
    user.caja += monto
    del table.jugadores[user.id]
    user.en_mesa = False
    user.mesa_id = None
    save_data()
    
    await registrar_evento_ws(
        "/crupier/retirar",
        "retiro_mesa",
        f"Usuario {user.nombre} {user.apellido} retiro de mesa con {monto}",
        request.client.host
    )
    return {"ok": True}

@app.post("/crupier/logout")
async def crupier_logout(crupier_token: Optional[str] = Cookie(None)):
    if crupier_token and crupier_token in crupier_sessions:
        del crupier_sessions[crupier_token]
    response = RedirectResponse(url="/crupier/login", status_code=303)
    response.delete_cookie("crupier_token")
    return response

@app.get("/crupier/mesas")
async def crupier_mesas():
    mesas_detalle = []
    for table in tables.values():
        jugadores_detalle = []
        for user_id, data in table.jugadores.items():
            # Encontrar el usuario por id
            user = next((u for u in users.values() if u.id == user_id), None)
            if user:
                ingreso = datetime.fromisoformat(data["ingreso"])
                tiempo = datetime.now() - ingreso
                minutos = int(tiempo.total_seconds() // 60)
                segundos = int(tiempo.total_seconds() % 60)
                tiempo_str = f"{minutos}m {segundos}s"
                jugadores_detalle.append({
                    "id": user.id,
                    "nombre": f"{user.nombre} {user.apellido}",
                    "monto": data["monto"],
                    "tiempo": tiempo_str
                })
        mesas_detalle.append({
            "id": table.id,
            "nombre": table.nombre,
            "jugadores": jugadores_detalle
        })
    return mesas_detalle

@app.post("/crupier/transferir")
async def crupier_transferir(request: Request, body: dict):
    dni = body['dni']
    monto = body['monto']
    
    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user = users[dni]
    user.caja += monto
    save_data()
    
    await registrar_evento_ws(
        "/crupier/transferir",
        "transferencia",
        f"Transferencia de {monto} a usuario {user.nombre} {user.apellido}",
        request.client.host
    )
    return {"ok": True}

@app.post("/crupier/deuda")
async def crupier_deuda(request: Request, body: dict):
    dni = body['dni']

    if dni not in users:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user = users[dni]
    user.caja += 2500.0
    user.deuda += 2500.0
    save_data()

    await registrar_evento_ws(
        "/crupier/deuda",
        "deuda",
        f"Usuario {user.nombre} {user.apellido} recibe 2500, deuda total {user.deuda}",
        request.client.host
    )
    return {"ok": True}

@app.on_event("startup")
async def startup_event():
    load_data()
    # Asegurar mesas existen
    if 1 not in tables:
        tables[1] = Table(id=1, nombre="Poker")
    if 2 not in tables:
        tables[2] = Table(id=2, nombre="Blackjack")
    if 3 not in tables:
        tables[3] = Table(id=3, nombre="Ruleta")
    if 4 not in tables:
        tables[4] = Table(id=4, nombre="Slot Machine")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # No se usa para enviar, solo recibir
    except:
        manager.disconnect(websocket)