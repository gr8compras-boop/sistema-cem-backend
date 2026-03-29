import math
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Cerebro Sistema CEM v2.0")

# PERMITIR CONEXIÓN CON STITCH (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CATÁLOGO DE MATERIALES (Módulo 2)
CATALOGO = {
    "P2211": {"nombre": "PTR 2x2\" Cal 11", "inercia": 22.10, "peso": 4.41},
    "P2214": {"nombre": "PTR 2x2\" Cal 14", "inercia": 14.50, "peso": 2.93},
    "A118":  {"nombre": "Ángulo 1\" x 1/8\"", "inercia": 0.46, "peso": 1.19},
}

class StitchRequest(BaseModel):
    voz_input_dimension: str
    voz_input_carga: str
    perfil_seleccionado: str

@app.post("/procesar-diseno")
async def api_cem(req: StitchRequest):
    # Lógica de normalización (Módulo 8)
    def clean(t): 
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", t)
        return float(nums[0]) if nums else 0.0
    
    longitud = clean(req.voz_input_dimension)
    if "metro" in req.voz_input_dimension.lower(): longitud *= 1000
    
    carga = clean(req.voz_input_carga)
    perfil = CATALOGO.get(req.perfil_seleccionado, CATALOGO["P2211"])
    
    # Cálculo de Ingeniería (Módulo 3)
    E, FS, K = 2039000, 3.0, 0.5
    L_cm = longitud / 10
    # Fórmula de Euler simplificada
    carga_segura = (math.pi**2 * E * perfil['inercia']) / ((K * L_cm)**2) / FS
    
    es_seguro = carga_segura >= carga
    
    return {
        "status_seguridad": "VERDE" if es_seguro else "ROJO",
        "datos_tecnicos": {"capacidad_max_kg": round(carga_segura, 2)},
        "finanzas": {"inversion_mxn": round((longitud/6000)*850, 2)}
    }
