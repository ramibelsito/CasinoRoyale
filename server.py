from fastapi import FastAPI, WebSocket, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from datetime import datetime
import csv
import asyncio
import json
import secrets
import re
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

@app.get("/crupier/estadisticas")
async def crupier_estadisticas():
    def parse_amount(text, tipo=None):
        if not text:
            return 0.0

        if tipo in ['ingreso_mesa', 'retiro_mesa']:
            match = re.search(r"con\s+([0-9]+(?:[\.,][0-9]+)*)", text)
            if not match:
                match = re.search(r"([0-9]+(?:[\.,][0-9]+)*)", text)
        elif tipo == 'transferencia':
            match = re.search(r"Transferencia\s+de\s+(-?[0-9]+(?:[\.,][0-9]+)*)", text, re.IGNORECASE)
            if not match:
                match = re.search(r"([0-9]+(?:[\.,][0-9]+)*)", text)
        else:
            match = re.search(r"([0-9]+(?:[\.,][0-9]+)*)", text)

        if not match:
            return 0.0

        value = match.group(1).replace(',', '.')
        try:
            return float(value)
        except:
            return 0.0

    def parse_usuario_name(text):
        if not text:
            return None
        m = re.search(r"Usuario\s+(.+?)\s+(?:ingreso|retiro|registrado|logueado)", text)
        if m:
            return m.group(1).strip()
        m = re.search(r"a usuario\s+(.+)", text)
        if m:
            return m.group(1).strip()
        return None

    DEFAULT_TABLE_NAMES = {
        1: 'Poker',
        2: 'Blackjack',
        3: 'Ruleta',
        4: 'Slot Machine'
    }

    players = {}
    table_flow = {}
    user_table = {}
    events_by_creator = {}
    player_balance = {}

    first_ts = None
    last_ts = None
    total_ingresos = 0.0
    total_retiros = 0.0
    total_transferencias = 0.0
    total_deudas = 0.0
    total_eventos = 0

    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_eventos += 1
            ts_str = (row.get('timestamp') or '').strip()
            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else None
            except:
                ts = None
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            tipo = (row.get('tipo_evento') or '').strip()
            detalle = (row.get('detalle') or '').strip()
            table_id = None
            if tipo in ['ingreso_mesa', 'retiro_mesa']:
                m = re.search(r"mesa\s*(\d+)", detalle)
                if m:
                    table_id = int(m.group(1))

            usuario = parse_usuario_name(detalle)
            if not usuario:
                continue

            # Normalizar espacios dobles
            usuario = ' '.join(usuario.split())

            # Inicializar saldo del jugador con 2500 de caja inicial
            if usuario not in player_balance:
                player_balance[usuario] = {
                    'starting_caja': 2500.0,
                    'caja_actual': 2500.0,
                    'deuda': 0.0,
                    'ingreso_mesa': 0.0,
                    'retiro_mesa': 0.0,
                    'transferencia': 0.0,
                    'event_count': 0
                }

            # Asignar tabla conocida al jugador para retirar si detalle no lleva mesa
            if tipo == 'ingreso_mesa' and table_id is not None:
                user_table[usuario] = table_id
            if tipo == 'retiro_mesa' and table_id is None and usuario in user_table:
                table_id = user_table.pop(usuario, None)

            players.setdefault(usuario, {
                'in': 0.0,
                'out': 0.0,
                'transfer': 0.0,
                'deuda': 0.0,
                'net_play': 0.0,
                'net_total': 0.0,
                'event_count': 0,
                'curve': [],
                'mesas': {}  # {mesa_id: {'ingreso_ts': datetime, 'retiro_ts': datetime, 'ingreso_amount': float, 'retiro_amount': float}}
            })
            p = players[usuario]
            p['event_count'] += 1
            events_by_creator[usuario] = events_by_creator.get(usuario, 0) + 1

            if tipo == 'ingreso_mesa':
                amount = parse_amount(detalle, tipo='ingreso_mesa')
                p['in'] += amount
                p['net_play'] -= amount
                total_ingresos += amount
                player_balance[usuario]['caja_actual'] -= amount
                player_balance[usuario]['ingreso_mesa'] += amount
                if table_id is not None:
                    tf = table_flow.setdefault(table_id, {'in': 0.0, 'out': 0.0, 'timeline': []})
                    tf['in'] += amount
                    if ts:
                        tf['timeline'].append({'timestamp': ts.isoformat(timespec='seconds'), 'type': 'ingreso', 'amount': amount})
                    # Registrar ingreso a sala
                    if table_id not in p['mesas']:
                        p['mesas'][table_id] = {'ingreso_ts': ts, 'retiro_ts': None, 'ingreso_amount': amount, 'retiro_amount': 0.0}
                    else:
                        p['mesas'][table_id]['ingreso_ts'] = ts
                        p['mesas'][table_id]['ingreso_amount'] += amount
            elif tipo == 'retiro_mesa':
                amount = parse_amount(detalle, tipo='retiro_mesa')
                p['out'] += amount
                p['net_play'] += amount
                total_retiros += amount
                player_balance[usuario]['caja_actual'] += amount
                player_balance[usuario]['retiro_mesa'] += amount
                if table_id is not None:
                    tf = table_flow.setdefault(table_id, {'in': 0.0, 'out': 0.0, 'timeline': []})
                    tf['out'] += amount
                    if ts:
                        tf['timeline'].append({'timestamp': ts.isoformat(timespec='seconds'), 'type': 'retiro', 'amount': amount})
                    # Registrar retiro de sala
                    if table_id in p['mesas']:
                        p['mesas'][table_id]['retiro_ts'] = ts
                        p['mesas'][table_id]['retiro_amount'] += amount
            elif tipo == 'transferencia':
                amount = parse_amount(detalle, tipo='transferencia')
                p['transfer'] += amount
                total_transferencias += amount
                player_balance[usuario]['caja_actual'] += amount
                player_balance[usuario]['transferencia'] += amount
            elif tipo == 'deuda':
                amount = 2500.0
                p['deuda'] += amount
                total_deudas += amount
                player_balance[usuario]['caja_actual'] += amount
                player_balance[usuario]['deuda'] += amount

            p['net_total'] = p['net_play'] + p['transfer'] - p['deuda']

            if ts:
                p['curve'].append({
                    'timestamp': ts.isoformat(timespec='seconds'),
                    'net_play': round(p['net_play'], 2),
                    'net_total': round(p['net_total'], 2)
                })

    if not players:
        return {
            'night_span': None,
            'summary': 'No hay datos de eventos para procesar',
            'players': [],
            'top_winner': None,
            'top_loser': None,
            'average_net_play': 0.0,
            'tables': [],
            'top_creators': []
        }

    player_list = []
    for usuario, p in players.items():
        bal = player_balance.get(usuario, {'starting_caja': 2500.0, 'caja_actual': 2500.0, 'deuda': 0.0, 'ingreso_mesa': 0.0, 'retiro_mesa': 0.0, 'transferencia': 0.0})
        net_by_games = bal['caja_actual'] - bal['starting_caja']
        
        # Procesar información de mesas
        mesas_info = []
        for mesa_id, mesa_data in p['mesas'].items():
            table_name = None
            if mesa_id in tables and getattr(tables[mesa_id], 'nombre', None):
                table_name = tables[mesa_id].nombre
            table_name = table_name or DEFAULT_TABLE_NAMES.get(mesa_id, f'Mesa {mesa_id}')
            
            tiempo_minutos = 0
            if mesa_data['ingreso_ts'] and mesa_data['retiro_ts']:
                tiempo_delta = mesa_data['retiro_ts'] - mesa_data['ingreso_ts']
                tiempo_minutos = int(tiempo_delta.total_seconds() // 60)
            
            net_mesa = mesa_data['retiro_amount'] - mesa_data['ingreso_amount']
            
            mesas_info.append({
                'mesa_id': mesa_id,
                'mesa_nombre': table_name,
                'ingreso': round(mesa_data['ingreso_amount'], 2),
                'retiro': round(mesa_data['retiro_amount'], 2),
                'neto': round(net_mesa, 2),
                'tiempo_minutos': tiempo_minutos
            })
        
        player_list.append({
            'usuario': usuario,
            'starting_caja': round(bal['starting_caja'], 2),
            'caja_actual': round(bal['caja_actual'], 2),
            'net_by_games': round(net_by_games, 2),
            'ingreso_total': round(p['in'], 2),
            'retiro_total': round(p['out'], 2),
            'net_play': round(p['net_play'], 2),
            'net_total': round(p['net_total'], 2),
            'transferencias': round(p['transfer'], 2),
            'deuda': round(p['deuda'], 2),
            'event_count': p['event_count'],
            'mesas': mesas_info
        })

    # Totales de juego según tu definición explicita
    for item in player_list:
        item['game_total'] = round(2500.0 - item['ingreso_total'] + item['retiro_total'], 2)

    # Ordenar por game_total (nada de netas complejas)
    player_list.sort(key=lambda x: x['game_total'], reverse=True)

    overall_net_play = sum(item['game_total'] for item in player_list)
    avg_net_play = overall_net_play / len(player_list) if player_list else 0.0

    top_winner = player_list[0] if player_list else None
    top_loser = player_list[-1] if player_list else None

    # Cotejo con users.json
    def normalize_name(name):
        return ' '.join(name.lower().strip().split())

    user_scores = {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            users_data = json.load(f)
            for user in users_data.values():
                full = normalize_name(f"{user.get('nombre','')} {user.get('apellido','')}")
                user_scores[full] = {
                    'caja': user.get('caja', 0.0),
                    'deuda': user.get('deuda', 0.0)
                }
    except Exception:
        users_data = {}

    concordancia = []
    for item in player_list:
        full = normalize_name(item['usuario'])
        target = user_scores.get(full)
        if target:
            concordancia.append({
                'usuario': item['usuario'],
                'csv_game_total': item['game_total'],
                'users_json_caja': round(target['caja'], 2),
                'users_json_deuda': round(target['deuda'], 2),
                'caja_ok': abs(item['caja_actual'] - target['caja']) < 0.01
            })

    tabla_mesas = []
    for tid, tf in table_flow.items():
        net_casa = round(tf['in'] - tf['out'], 2)
        table_name = None
        if tid in tables and getattr(tables[tid], 'nombre', None):
            table_name = tables[tid].nombre
        table_name = table_name or DEFAULT_TABLE_NAMES.get(tid, f'Mesa {tid}')
        
        # Calcular acumulado a través del timeline
        timeline_accum = []
        ingreso_accum = 0.0
        retiro_accum = 0.0
        for event in tf.get('timeline', []):
            if event['type'] == 'ingreso':
                ingreso_accum += event['amount']
            else:
                retiro_accum += event['amount']
            timeline_accum.append({
                'timestamp': event['timestamp'],
                'ingreso': round(ingreso_accum, 2),
                'retiro': round(retiro_accum, 2),
                'neto': round(ingreso_accum - retiro_accum, 2)
            })
        
        tabla_mesas.append({
            'mesa_id': tid,
            'mesa_nombre': table_name,
            'ingreso': round(tf['in'], 2),
            'retiro': round(tf['out'], 2),
            'net_casa': net_casa,
            'timeline': timeline_accum
        })
    tabla_mesas.sort(key=lambda x: x['mesa_id'])

    top_creators = sorted(events_by_creator.items(), key=lambda x: x[1], reverse=True)[:2]
    creators_curves = []
    for nombre, cnt in top_creators:
        creators_curves.append({
            'usuario': nombre,
            'event_count': cnt,
            'curve': players[nombre]['curve']
        })

    return {
        'night_span': {
            'start': first_ts.isoformat(timespec='seconds') if first_ts else None,
            'end': last_ts.isoformat(timespec='seconds') if last_ts else None,
            'duration_minutes': round(((last_ts - first_ts).total_seconds() / 60.0), 2) if first_ts and last_ts else 0
        },
        'summary': {
            'total_eventos': total_eventos,
            'total_ingreso': round(total_ingresos, 2),
            'total_retiro': round(total_retiros, 2),
            'total_transferencias': round(total_transferencias, 2),
            'total_deudas': round(total_deudas, 2),
            'neto_casa': round(total_ingresos - total_retiros, 2),
            'average_game_total': round(avg_net_play, 2)
        },
        'players': player_list,
        'top_winner': top_winner,
        'top_loser': top_loser,
        'tables': tabla_mesas,
        'top_creators': creators_curves,
        'player_count': len(player_list),
        'concordancia': concordancia
    }


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