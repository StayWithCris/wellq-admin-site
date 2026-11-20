# Guia de inicio rapido

Bienvenido al proyecto. Sigue este paso a paso para instalar todo lo necesario y levantar la aplicacion correctamente. El proyecto se divide en dos partes:

- Backend: API en FastAPI.
- Frontend: interfaz visual en React/Vite.

Para una instalacion local desde cero, revisar tambien:

```txt
INSTALL.md
```

Ese archivo explica que `.env` se necesitan y como se entregan las credenciales privadas.

## 1. Configuracion del backend

Abre una terminal en la raiz del proyecto.

```bash
# 1. Entramos a la carpeta del backend
cd backend

# 2. Creamos el entorno virtual de Python
python -m venv venv

# 3. Activamos el entorno virtual
.\venv\Scripts\activate

# 4. Actualizamos pip
python -m pip install --upgrade pip

# 5. Instalamos dependencias del backend
pip install -r requirements.txt

# 6. Cargamos datos iniciales a la base de datos
python seed.py

# 7. Eliminamos duplicados historicos si corresponde
python cleanup_duplicates.py

# 8. Encendemos el backend local
uvicorn app.main:app --reload --port 8000
```

Antes de ejecutar el backend local, debe existir:

```txt
backend/.env
```

Ese archivo no se sube a Git. Se entrega por Drive privado, WhatsApp empresarial autorizado o canal interno definido por la empresa.

## 2. Configuracion del frontend

Abre una segunda terminal en la raiz del proyecto.

```bash
# 1. Entramos a la carpeta del frontend
cd frontend

# 2. Instalamos dependencias del frontend
npm install

# 3. Encendemos la interfaz
npm run dev
```

Antes de ejecutar el frontend, debe existir:

```txt
frontend/.env
```

Para backend local:

```env
VITE_API_URL=http://localhost:8000
```

Para backend desplegado en Google Cloud Run:

```env
VITE_API_URL=https://URL-DEL-BACKEND-EN-GOOGLE-CLOUD-RUN
```

## 3. Despliegue en Google Cloud Run (Backend)

Para desplegar el backend en Google Cloud Run con la topologia oficial de costo minimo y coherencia de estado en RAM:

```bash
cd backend
# En Linux/macOS:
chmod +x deploy_cloud_run.sh
./deploy_cloud_run.sh

# O en Windows PowerShell:
.\deploy_cloud_run.ps1
```

O ejecutando directamente con el CLI de Google Cloud:

```bash
gcloud run deploy wellq-admin-backend \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --max-instances 1 \
  --min-instances 0 \
  --cpu-throttling \
  --timeout 300
```

> **Parametros arquitectonicos clave:**
> - `--max-instances 1`: Mantiene una unica instancia activa para garantizar la coherencia del estado en RAM (buffer de telemetria, histograma y SSE Hub) sin requerir broker externo como Redis.
> - `--min-instances 0`: Permite escalar a cero cuando no hay peticiones activas, eliminando costos fijos 24/7.
> - `--cpu-throttling`: Asigna CPU exclusivamente durante la atencion de peticiones HTTP, complementandose con la pausa por inactividad (`document.visibilityState`) del frontend para ahorrar costos de infraestructura.

## 4. Configuracion de GitHub

Usar estos comandos cuando quieras subir cambios al repositorio.

Importante: no subir archivos privados como `.env`.

```bash
# 1. Revisa que archivos cambiaron
git status

# 2. Empaqueta los archivos modificados o creados que si deben subirse
git add .

# 3. Crea el commit
git commit -m "Explica brevemente que cambiaste"

# 4. Sube los cambios al repositorio
git push
```


