from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime

class User(BaseModel):
    id: int
    nombre: str
    apellido: str
    dni: str
    caja: float = 2500.0    # caja inicial
    deuda: float = 0.0      # deuda por cajas posteriores            
    en_mesa: bool = False
    mesa_id: Optional[int] = None

class Table(BaseModel):
    id: int
    nombre: str
    jugadores: Dict[int, Dict[str, Any]] = {}  # user_id: {"monto": float, "ingreso": str}

class Evento(BaseModel):
    timestamp: str
    endpoint: str
    tipo_evento: str
    detalle: str
    cliente_ip: str

# In-memory storage
users: Dict[str, User] = {}  # dni -> User
tables: Dict[int, Table] = {}
next_user_id = 1
next_table_id = 1

