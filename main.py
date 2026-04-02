import os
import json
import gspread
from google.oauth2.service_account import Credentials
import numpy as np
import math
import re
import base64
from fpdf import FPDF
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Motor CAD Paramétrico CEM v5.0 - Industrial Cloud")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONSTANTES TÉCNICAS ---
MM_TO_PX = 3.779527559 
E_ACERO = 2100000 # kg/cm²
TRAMO_ESTANDAR = 6000

# --- NÚCLEO DE DATOS: GOOGLE SHEETS (Hard Cutover) ---
creds_json = os.getenv("GOOGLE_CREDS")

if not creds_json:
    raise ValueError("ERROR CRÍTICO: Variable GOOGLE_CREDS no detectada. El servidor no arrancará sin conexión a la base de datos.")

# Autenticación estricta con Google Cloud
info = json.loads(creds_json)
creds = Credentials.from_service_account_info(
    info, 
    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(creds)
sheet = client.open("CEM_Database").sheet1

def cargar_catalogo_dinamico():
    """Descarga la tabla de Google Sheets y construye el motor en RAM."""
    print("Conectando a Google Sheets para descargar inventario...")
    datos = sheet.get_all_records()
    nuevo_catalogo = {}
    for fila in datos:
        id_perfil = str(fila['ID_Perfil'])
        nuevo_catalogo[id_perfil] = {
            'nombre': str(fila['Nombre']),
            'I': float(fila['Inercia_I']),
            'ancho': float(fila['Ancho_mm']),
            'alto': float(fila['Alto_mm']),
            't': float(fila['Espesor_t']),
            'tags': [tag.strip().lower() for tag in str(fila['Tags']).split(",")]
        }
    return nuevo_catalogo

# El sistema colapsará aquí si no logra descargar la hoja de cálculo
CATALOGO = cargar_catalogo_dinamico()

class CadRequest(BaseModel):
    voz_completa: str

# --- FUNCIONES DE LÓGICA Y MANUFACTURA ---

def buscar_material(texto: str):
    texto = texto.lower()
    # Asignamos un valor por defecto seguro (usando el primer elemento descargado si falla)
    match = list(CATALOGO.keys())[0] 
    max_score = 0
    for key, data in CATALOGO.items():
        score = sum(1 for tag in data['tags'] if tag in texto)
        if score > max_score:
            max_score, match = score, key
    return CATALOGO[match]

def proyectar_geometria(p, L, angulo=0, grosor_disco=3):
    W, H, t = p['ancho'], p['alto'], p['t']
    
    rad = math.radians(angulo)
    descuento = H * math.tan(rad)
    descuento = min(descuento, L)
    
    medida_fabricacion = L + grosor_disco
    
    alzado = np.array([
        [0, 0], 
        [L, 0], 
        [L - descuento, H], 
        [0, H]
    ])
    
    offset_x = L + 40 
    ext = np.array([[0, 0], [W, 0], [W, H], [0, H]]) + [offset_x, 0]
    int_ptr = np.array([[t, t], [t, H-t], [W-t, H-t], [W-t, t]]) + [offset_x, 0]
    
    return {
        'alzado': alzado, 
        'ext': ext, 
        'int': int_ptr, 
        'total_w': offset_x + W,
        'punta_larga': medida_fabricacion, 
        'punta_corta': round(medida_fabricacion - descuento, 1)
    }

def calcular_despiece(longitud_total, tramo_estandar=6000, kerf=3):
    tramos_enteros = longitud_total // tramo_estandar
    resto = longitud_total % tramo_estandar
    
    instrucciones = []
    tramos_a_comprar = tramos_enteros
    retazo_util = 0
    
    if tramos_enteros > 0:
        instrucciones.append(f"{tramos_enteros} tramo(s) entero(s) de {tramo_estandar} mm (De fábrica)")
        
    punta_larga_resto = 0
    if resto > 0:
        punta_larga_resto = resto + kerf
        instrucciones.append(f"1 corte de {punta_larga_resto} mm (Incluye {kerf} mm por el disco)")
        tramos_a_comprar += 1
        retazo_util = tramo_estandar - punta_larga_resto
        
    return {
        "tramos_enteros": tramos_enteros,
        "medida_corte_final": punta_larga_resto,
        "tramos_comprar": tramos_a_comprar,
        "retazo": retazo_util,
        "lista_instrucciones": instrucciones
    }

def evaluar_seguridad(Pcr):
    if Pcr >= 150:
        return "ESTRUCTURAL (Seguro para carga pesada)", (0, 120, 0), "#007800" 
    elif Pcr >= 50:
        return "LIGERO (Solo carga secundaria/vista)", (200, 100, 0), "#c86400" 
    else:
        return "PELIGRO DE PANDEO (Riesgo inminente)", (200, 0, 0), "#c80000" 

# --- MOTORES DE RENDERIZADO (SVG / PDF) ---

def renderizar_svg(geo, p, L, Pcr, angulo, diag_texto, diag_hex):
    s = MM_TO_PX
    H = p['alto']
    
    carril_superior = 50 * s  
    carril_inferior = 65 * s  
    
    W_view = (geo['total_w'] + 50) * s
    H_view = (H * s) + carril_superior + carril_inferior
    
    def fmt(pts): return " ".join([f"{pt[0]*s},{pt[1]*s + carril_superior}" for pt in pts])

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {W_view} {H_view}">',
        '<style>.line {stroke:#1a1a1a; fill:none; stroke-width:3;} .cota {stroke:red; stroke-width:1.5;} .txt {font-family:monospace; font-size:18px; font-weight:bold;}</style>',
        f'<polygon points="{fmt(geo["alzado"])}" class="line"/>',
        f'<path d="M {fmt(geo["ext"])} Z M {fmt(geo["int"])} Z" fill="#d0d0d0" stroke="black" fill-rule="evenodd"/>',
        f'<line x1="0" y1="{30*s}" x2="{L*s}" y2="{30*s}" class="cota"/>',
        f'<text x="{(L*s)/2}" y="{24*s}" class="txt" text-anchor="middle" fill="red">{L} mm</text>',
        f'<text x="10" y="{carril_superior + (H*s) + (20*s)}" class="txt" fill="#333">PIEZA: {p["nombre"]} | CORTE: {angulo}°</text>',
        f'<text x="10" y="{carril_superior + (H*s) + (35*s)}" class="txt" fill="#000">PUNTA LARGA: {geo["punta_larga"]} mm | PUNTA CORTA: {geo["punta_corta"]} mm</text>',
        f'<text x="10" y="{carril_superior + (H*s) + (50*s)}" class="txt" fill="{diag_hex}">ESTADO: {diag_texto} (Soporta: {Pcr} kg)</text>',
        '</svg>'
    ]
    return "".join(svg)
    
def generar_pdf_1a1(geo, p, L, Pcr, angulo, diag_texto, diag_rgb, despiece):
    pdf = FPDF(orientation="landscape", unit="mm", format="letter")
    pdf.add_page()
    
    ancho_maximo_papel = 239.0 
    ancho_dibujo = geo['total_w']
    H = p['alto']
    
    if ancho_dibujo > ancho_maximo_papel:
        escala = ancho_maximo_papel / ancho_dibujo
        texto_escala = f"Escala Visual: 1 : {round(1/escala, 1)} (Ajustado a Carta)"
    else:
        escala = 1.0
        texto_escala = "Escala Visual: 1:1 (Medidas Reales)"

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "SISTEMA CEM - PLANO DE FABRICACIÓN", align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, f"Material: {p['nombre']} | Pcr: {Pcr} kg", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, "INSTRUCCIONES DE CORTE (Tramos de 6m):", new_x="LMARGIN", new_y="NEXT", fill=True)
    
    pdf.set_font("helvetica", "", 9)
    pdf.cell(0, 5, f"Material a comprar: {despiece['tramos_comprar']} tramo(s) estándar.", new_x="LMARGIN", new_y="NEXT")
    
    for inst in despiece['lista_instrucciones']:
        pdf.cell(0, 5, f"> {inst}", new_x="LMARGIN", new_y="NEXT")
        
    pdf.set_text_color(0, 120, 0) 
    pdf.cell(0, 5, f"Retazo útil sobrante: {despiece['retazo']} mm", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    
    pdf.set_text_color(*diag_rgb) 
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, f"Estatus Estructural: {diag_texto}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0) 
    
    altura_linea = pdf.get_y() + 2
    pdf.line(10, altura_linea, 270, altura_linea) 
    
    origen_x = 20
    origen_y = altura_linea + 10
    
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    
    puntos_alzado = [((pt[0]*escala) + origen_x, (pt[1]*escala) + origen_y) for pt in geo['alzado']]
    pdf.polygon(puntos_alzado, style="D")
    
    puntos_ext = [((pt[0]*escala) + origen_x, (pt[1]*escala) + origen_y) for pt in geo['ext']]
    puntos_int = [((pt[0]*escala) + origen_x, (pt[1]*escala) + origen_y) for pt in geo['int']]
    
    pdf.polygon(puntos_ext, style="D")
    pdf.polygon(puntos_int, style="D")
    
    y_cota = origen_y + (H * escala) + 10
    pdf.set_draw_color(200, 0, 0) 
    pdf.line(origen_x, y_cota, origen_x + (L * escala), y_cota)
    pdf.set_text_color(200, 0, 0)
    pdf.set_font("helvetica", "B", 9)
    pdf.text(origen_x + ((L * escala) / 2) - 5, y_cota - 2, f"{L} mm")
    
    pdf_bytes = pdf.output()
    return base64.b64encode(pdf_bytes).decode('utf-8')
    
# --- ENDPOINT PRINCIPAL ---
@app.post("/procesar-diseno")
async def api_cem(req: CadRequest):
    voz = req.voz_completa.lower()

    nums = re.findall(r'\d+', voz)
    longitud = int(nums[0]) if nums else 1000
    if any(m in voz for m in ["metro", " mts"]): 
        longitud *= 1000

    match_angulo = re.search(r'(\d+)\s*grados', voz)
    angulo = int(match_angulo.group(1)) if match_angulo else 0

    material = buscar_material(voz)
    L_cm = longitud / 10
    Pcr = (math.pi**2 * E_ACERO * material['I']) / (L_cm**2)
    pcr_redondo = round(Pcr, 2)
    
    diag_texto, diag_rgb, diag_hex = evaluar_seguridad(pcr_redondo)
    
    geo = proyectar_geometria(material, longitud, angulo, grosor_disco=3)
    
    despiece_info = calcular_despiece(longitud, tramo_estandar=6000, kerf=3)
    
    svg_final = renderizar_svg(geo, material, longitud, pcr_redondo, angulo, diag_texto, diag_hex)
    
    pdf_base64 = generar_pdf_1a1(geo, material, longitud, pcr_redondo, angulo, diag_texto, diag_rgb, despiece_info)
    
    return {
        "status": "success",
        "material": material['nombre'],
        "pcr_kg": pcr_redondo,
        "seguridad": diag_texto,
        "punta_larga_real": geo['punta_larga'], 
        "punta_corta_real": geo['punta_corta'], 
        "angulo": angulo,
        "tramos_comprar": despiece_info['tramos_comprar'],
        "retazo": despiece_info['retazo'],
        "svg_code": svg_final,
        "pdf_base64": pdf_base64
    }
