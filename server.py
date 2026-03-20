from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import HTMLResponse
from typing import List
from datetime import datetime
import csv
import asyncio

app = FastAPI()

CSV_FILE = "eventos.csv"
csv_lock = asyncio.Lock()

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

async def registrar_evento_ws(endpoint, tipo_evento, detalle, cliente_ip):
    evento = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "endpoint": endpoint,
        "tipo_evento": tipo_evento,
        "detalle": detalle,
        "cliente_ip": cliente_ip
    }

    # Guardar en CSV (sync)
    with csv_lock:
        with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(evento.values())

    # Broadcast en tiempo real
    await manager.broadcast(evento)

@app.get("/")
async def landing():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Registro</title>
        <style>
            body { font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f0f0f0; }
            .form-container { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); width: 300px; }
            h1 { text-align: center; color: #333; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            button { width: 100%; padding: 10px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
        </style>
    </head>
    <body>
        <div class="form-container">
            <h1>Registro</h1>
            <form id="registroForm">
                <input type="text" id="nombre" placeholder="Nombre" required>
                <input type="text" id="apellido" placeholder="Apellido" required>
                <input type="text" id="dni" placeholder="DNI" required>
                <button type="submit">Registrar</button>
            </form>
        </div>
        <script>
            document.getElementById('registroForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const nombre = document.getElementById('nombre').value;
                const apellido = document.getElementById('apellido').value;
                const dni = document.getElementById('dni').value;
                
                await fetch('/registro', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({nombre, apellido, dni})
                });
                
                window.location.href = '/usuario';
            });
        </script>
    </body>
    </html>
    """)

@app.post("/registro")
async def registro_submit(request: Request, body: dict):
    await registrar_evento_ws(
        "/registro",
        "submit",
        f"nombre={body['nombre']}, apellido={body['apellido']}, dni={body['dni']}",
        request.client.host
    )
    return {"ok": True}

@app.get("/usuario")
async def usuario_page():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Eventos en Tiempo Real</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #f0f0f0; }
            h1 { text-align: center; }
            table { width: 100%; border-collapse: collapse; background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #007bff; color: white; }
            tr:hover { background: #f5f5f5; }
        </style>
    </head>
    <body>
        <h1>Eventos Registrados</h1>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Endpoint</th>
                    <th>Tipo</th>
                    <th>Detalle</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody id="tabla"></tbody>
        </table>
        <script>
            const ws = new WebSocket("ws://" + location.host + "/ws");

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const table = document.getElementById("tabla");
                const row = document.createElement("tr");
                
                row.innerHTML = `
                    <td>${data.timestamp}</td>
                    <td>${data.endpoint}</td>
                    <td>${data.tipo_evento}</td>
                    <td>${data.detalle}</td>
                    <td>${data.cliente_ip}</td>
                `;
                
                table.prepend(row);
            };
        </script>
    </body>
    </html>
    """)