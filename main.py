import csv
import requests
from io import StringIO
import numpy as np
import math
import re
import base64
from fpdf import FPDF
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import os
import json
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

app = FastAPI(title="Motor CAD Paramétrico CEM v5.1 - Cloud CSV Ligero")

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

# --- FASE 2: CONEXIÓN A BASE DE DATOS (FIREBASE) ---
firebase_creds = os.getenv("FIREBASE_CREDS")
db = None

if firebase_creds:
    try:
        # Cargamos las credenciales desde la variable de entorno en Render
        cred_dict = json.loads(firebase_creds)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Memoria CEM: Conectado a Firebase Firestore exitosamente.")
    except Exception as e:
        print(f"❌ Error al iniciar Firebase: {e}")
else:
    print("⚠️ Aviso: Llave FIREBASE_CREDS no detectada. El servidor operará sin guardar historial.")

# --- NÚCLEO DE DATOS: GOOGLE SHEETS (Vía CSV Público) ---
URL_HOJA_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRK9OF20weoXx_tx_JEMiHtcEYYdH5Jg1Nxc_kAOtrgJT2sg30_pKHJWzaAl41VB6na4aRLI6w0KVIQ/pub?gid=0&single=true&output=csv"

def cargar_catalogo_desde_web():
    """Descarga el catálogo desde el enlace público de Google Sheets en formato CSV."""
    print("Conectando a la base de datos CSV en la nube...")
    try:
        response = requests.get(URL_HOJA_CSV)
        response.raise_for_status() 
        
        f = StringIO(response.text)
        lector_csv = csv.DictReader(f)
        
        nuevo_catalogo = {}
        for fila in lector_csv:
            id_perfil = str(fila.get('ID_Perfil', '')).strip()
            if not id_perfil:
                continue # Salta filas vacías
                
            nuevo_catalogo[id_perfil] = {
                'nombre': str(fila.get('Nombre', '')),
                'I': float(fila.get('Inercia_I', 0)),
                'ancho': float(fila.get('Ancho_mm', 0)),
                'alto': float(fila.get('Alto_mm', 0)),
                't': float(fila.get('Espesor_t', 0)),
                'tags': [tag.strip().lower() for tag in str(fila.get('Tags', '')).split(",")]
            }
        print(f"✅ Éxito: {len(nuevo_catalogo)} perfiles cargados en memoria RAM.")
        return nuevo_catalogo
        
    except Exception as e:
        print(f"❌ Error al cargar nube: {e}")
        # Salvavidas extremo por si falla la red de Render
        return {'P2214': {'nombre': 'PTR 2x2" Cal 14 (Respaldo)', 'I': 14.50, 'ancho': 50.8, 'alto': 50.8, 't': 1.9, 'tags': ['ptr']}}

# Se ejecuta al encender el servidor en Render
CATALOGO = cargar_catalogo_desde_web()

class CadRequest(BaseModel):
    voz_completa: str

# --- FUNCIONES DE LÓGICA Y MANUFACTURA ---

def buscar_material(texto: str):
    texto = texto.lower()
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

def extraer_lista_cortes(voz):
    """
    Traduce comandos de voz complejos en una lista de cortes matemáticos.
    Soporta lenguaje natural (ej. "dos de un metro", "3 piezas de 500").
    """
    voz_norm = voz.lower()

    # Diccionario para convertir texto a números (clave para comandos de voz)
    mapa_numeros = {'un': '1', 'una': '1', 'uno': '1', 'dos': '2', 'tres': '3', 
                    'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 
                    'ocho': '8', 'nueve': '9', 'diez': '10'}
    
    for palabra, digito in mapa_numeros.items():
        voz_norm = re.sub(rf'\b{palabra}\b', digito, voz_norm)

    # Buscamos el patrón: (Cantidad) + (Palabras intermedias) + (Medida) + (Unidad)
    patron = r'(\d+)\s*(?:cortes?|piezas?|tramos?|de)*\s*(\d+)\s*(metros?|mts?|cm|centimetros?|mm|milimetros?)?'
    coincidencias = re.findall(patron, voz_norm)
    
    lista_cortes = []
    
    if coincidencias:
        for cant_str, med_str, unidad in coincidencias:
            cantidad = int(cant_str)
            medida = int(med_str)
            
            # Conversión a milímetros (nuestra unidad base en el taller)
            if unidad.startswith('m') and 'mili' not in unidad and unidad not in ['mm']: 
                medida *= 1000
            elif unidad.startswith('c'):
                medida *= 10
                
            lista_cortes.extend([medida] * cantidad)
    else:
        # Mecanismo de respaldo: Si el usuario habla simple ("un ptr de 2 metros")
        nums = re.findall(r'\d+', voz_norm)
        longitud = int(nums[0]) if nums else 1000
        if any(m in voz_norm for m in ["metro", "mts"]): longitud *= 1000
        elif any(c in voz_norm for c in ["cm", "centimetro"]): longitud *= 10
        lista_cortes.append(longitud)
        
    return lista_cortes

def optimizar_cortes_1d(lista_cortes, tramo_estandar=6000, kerf=3):
    """
    Algoritmo First Fit Decreasing (FFD) para optimizar el acomodo de múltiples 
    cortes en tramos estándar, minimizando la merma y maximizando la utilidad.
    """
    # 1. Ordenamos los cortes de mayor a menor longitud
    cortes_ordenados = sorted(lista_cortes, reverse=True)
    tramos_utilizados = [] # Lista de listas. Cada sublista es un tramo de 6m
    
    for corte_original in cortes_ordenados:
        # Sumamos el desgaste del disco a cada pieza individual
        corte_real = corte_original + kerf
        acomodado = False
        
        # 2. Intentamos meter la pieza en los tramos que ya empezamos a cortar
        for tramo in tramos_utilizados:
            espacio_ocupado = sum(tramo)
            if (espacio_ocupado + corte_real) <= tramo_estandar:
                tramo.append(corte_real)
                acomodado = True
                break
                
        # 3. Si la pieza no cabe en los retazos, sacamos un tramo nuevo del inventario
        if not acomodado:
            tramos_utilizados.append([corte_real])
            
    # --- REPORTAJE EJECUTIVO DE MATERIALES ---
    reporte_tramos = []
    retazo_global = 0
    
    for i, tramo in enumerate(tramos_utilizados):
        ocupado = sum(tramo)
        sobrante = tramo_estandar - ocupado
        retazo_global += sobrante
        reporte_tramos.append({
            "numero_tramo": i + 1,
            "cortes_asignados": tramo,
            "sobrante_mm": sobrante
        })
        
    # Calculamos la eficiencia financiera del uso del material
    total_material_comprado = len(tramos_utilizados) * tramo_estandar
    suma_cortes_netos = sum(lista_cortes)
    eficiencia = round((suma_cortes_netos / total_material_comprado) * 100, 1) if total_material_comprado > 0 else 0

    return {
        "tramos_comprar": len(tramos_utilizados),
        "detalle_tramos": reporte_tramos,
        "retazo_global_mm": retazo_global,
        "eficiencia_porcentaje": eficiencia
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

    # 1. Extracción Multilongitud (El nuevo NLP)
    lista_cortes = extraer_lista_cortes(voz)
    
    # Para la física (Euler) y el dibujo (PDF), tomaremos la pieza más larga como referencia
    longitud_referencia = max(lista_cortes)

    match_angulo = re.search(r'(\d+)\s*grados', voz)
    angulo = int(match_angulo.group(1)) if match_angulo else 0

    material = buscar_material(voz)
    L_cm = longitud_referencia / 10
    
    try:
        Pcr = (math.pi**2 * E_ACERO * material['I']) / (L_cm**2)
        pcr_redondo = round(Pcr, 2)
    except Exception:
        pcr_redondo = 0
        
    diag_texto, diag_rgb, diag_hex = evaluar_seguridad(pcr_redondo)
    
    # 2. Geometría Teórica
    geo = proyectar_geometria(material, longitud_referencia, angulo, grosor_disco=3)
    
    # 3. ALGORITMO DE OPTIMIZACIÓN INDUSTRIAL (Acomodo de Material)
    optimizacion = optimizar_cortes_1d(lista_cortes, tramo_estandar=6000, kerf=3)
    
    svg_final = renderizar_svg(geo, material, longitud_referencia, pcr_redondo, angulo, diag_texto, diag_hex)
    
    # --- ADAPTADOR PARA EL PDF ---
    # Traducimos el diccionario del nuevo algoritmo al formato que tu PDF ya sabe imprimir
    instrucciones_pdf = []
    for tramo in optimizacion['detalle_tramos']:
        cortes_str = " + ".join([f"{c}mm" for c in tramo['cortes_asignados']])
        instrucciones_pdf.append(f"Tramo {tramo['numero_tramo']}: [{cortes_str}] | Sobra: {tramo['sobrante_mm']}mm")
        
    despiece_adaptado = {
        "tramos_comprar": optimizacion['tramos_comprar'],
        "retazo": optimizacion['retazo_global_mm'],
        "lista_instrucciones": instrucciones_pdf
    }
    
    pdf_base64 = generar_pdf_1a1(geo, material, longitud_referencia, pcr_redondo, angulo, diag_texto, diag_rgb, despiece_adaptado)

    # --- MEMORIA DEL TALLER: GUARDAR REGISTRO EN FIREBASE ---
    if db is not None:
        try:
            registro_plano = {
                "fecha_creacion": datetime.utcnow().isoformat(),
                "material": material['nombre'],
                "piezas_totales": len(lista_cortes),
                "longitud_maxima_mm": longitud_referencia,
                "angulo_corte": angulo,
                "diagnostico_seguridad": diag_texto,
                "carga_critica_kg": pcr_redondo,
                "tramos_a_comprar": optimizacion['tramos_comprar'],
                "eficiencia_financiera": optimizacion['eficiencia_porcentaje']
            }
            # Guarda el documento en una colección llamada 'historial_planos'
            db.collection("historial_planos").add(registro_plano)
            print("💾 Registro guardado en la nube.")
        except Exception as e:
            print(f"⚠️ Error al guardar el historial en Firebase: {e}")

    return {
        "status": "success",
        "material": material['nombre'],
        "piezas_solicitadas": len(lista_cortes),
        "eficiencia_financiera": f"{optimizacion['eficiencia_porcentaje']}%",
        "tramos_comprar": optimizacion['tramos_comprar'],
        "svg_code": svg_final,
        "pdf_base64": pdf_base64
    }
