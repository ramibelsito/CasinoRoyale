# CasinoRoyale

Aplicación web para registrar acciones, ganancias y pérdidas de usuarios en una noche de casino.

## Estructura del Proyecto

- `server.py`: Backend con FastAPI
- `models.py`: Modelos de datos (User, Table, etc.)
- `static/`: Archivos HTML estáticos
  - `index.html`: Página de registro
  - `login.html`: Página de login
  - `usuario.html`: Dashboard del usuario (ver caja, leaderboard)
  - `crupier.html`: Panel del crupier (ingresar/retirar jugadores)
- `eventos.csv`: Log de todos los eventos

## Funcionalidades

### Usuarios
- Registrarse con nombre, apellido, DNI (caja inicial 1000)
- Login con DNI
- Ver su caja actual
- Ver leaderboard

### Crupiers
- Ingresar jugador a mesa con monto (controla que tenga suficiente y no esté en otra mesa). El monto se descuenta de la caja del usuario.
- Retirar jugador de mesa con monto (no permite negativo). El monto especificado se suma a la caja del usuario (puede ser 0 si pierde todo, o más si gana).
- Ver estado de mesas (Poker, Blackjack, Ruleta, Slot Machine)

### Backend
- Gestión de cajas de usuarios (inicial 2500)
- Estado de mesas: Poker, Blackjack, Ruleta, Slot Machine (jugadores y montos)
- Historial en CSV
- Estadísticas (leaderboard)

## Instalación y Ejecución

1. Activar entorno virtual:
   ```bash
   source venv/bin/activate
   ```

2. Instalar dependencias:
   ```bash
   pip install fastapi uvicorn
   ```

3. Ejecutar servidor:
   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8000 --reload
   ```

## Persistencia

El servidor guarda automáticamente el estado de usuarios y mesas en archivos JSON (`users.json`, `tables.json`) para persistencia entre reinicios. El historial de eventos se mantiene en `eventos.csv`.

- **Usuarios**: Registrarse en la página principal, luego login.
- **Crupiers**: Acceder directamente a http://localhost:8000/crupier (enlaces disponibles en las páginas de registro y login).