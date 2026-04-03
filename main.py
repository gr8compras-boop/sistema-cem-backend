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
from supabase import create_client, Client

# --- SUPABASE CONNECTION (WORKSHOP MEMORY) ---
# Make sure to set SUPABASE_URL and SUPABASE_KEY in Render's Environment Variables
sb_url: str = os.environ.get("SUPABASE_URL")
sb_key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(sb_url, sb_key) if sb_url and sb_key else None

app = FastAPI(title="CEM Parametric CAD Engine v5.1 - Lightweight CSV Cloud")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- TECHNICAL CONSTANTS ---
MM_TO_PX = 3.779527559 
STEEL_E = 2100000 # kg/cm²
STANDARD_LENGTH = 6000

# --- DATA CORE: GOOGLE SHEETS (Via Public CSV) ---
CSV_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRK9OF20weoXx_tx_JEMiHtcEYYdH5Jg1Nxc_kAOtrgJT2sg30_pKHJWzaAl41VB6na4aRLI6w0KVIQ/pub?gid=0&single=true&output=csv"

def load_catalog_from_web():
    """Downloads the catalog from the public Google Sheets CSV link."""
    print("Connecting to cloud CSV database...")
    try:
        response = requests.get(CSV_SHEET_URL)
        response.raise_for_status() 
        
        f = StringIO(response.text)
        csv_reader = csv.DictReader(f)
        
        new_catalog = {}
        for row in csv_reader:
            profile_id = str(row.get('ID_Perfil', '')).strip()
            if not profile_id:
                continue 
                
            new_catalog[profile_id] = {
                'name': str(row.get('Nombre', '')),
                'I': float(row.get('Inercia_I') or 0),
                'width': float(row.get('Ancho_mm') or 0),
                'height': float(row.get('Alto_mm') or 0),
                't': float(row.get('Espesor_t') or 0),
                'tags': [tag.strip().lower() for tag in str(row.get('Tags', '')).split(",")]
            }
        print(f"✅ Success: {len(new_catalog)} profiles loaded into RAM.")
        return new_catalog
        
    except Exception as e:
        print(f"❌ Error loading cloud data: {e}")
        # Extreme fallback if Render network fails
        return {'P2214': {'name': 'PTR 2x2" Cal 14 (Respaldo)', 'I': 14.50, 'width': 50.8, 'height': 50.8, 't': 1.9, 'tags': ['ptr']}}

# Executes on Render server startup
CATALOG = load_catalog_from_web()

class CadRequest(BaseModel):
    full_voice: str

# --- LOGIC & MANUFACTURING FUNCTIONS ---

def search_material(text: str):
    text = text.lower()
    match = list(CATALOG.keys())[0] 
    max_score = 0
    for key, data in CATALOG.items():
        score = sum(1 for tag in data['tags'] if tag in text)
        
        # Bono de precisión: buscar medidas exactas (ej. "2x2") dentro del nombre del material
        dims = re.findall(r'\d+x\d+', data['name'].lower())
        for d in dims:
            if d in text:
                score += 5  # Prioridad alta para evitar que el 1x1 le gane al 2x2
                
        if score > max_score:
            max_score, match = score, key
    return CATALOG[match]

def calculate_structural_weight(p, total_length_mm):
    """
    Calculates the total weight of the requested steel pieces.
    Formula: Cross-sectional Area * Total Length * Steel Density
    """
    W, H, t = p['width'], p['height'], p['t']
    
    # Área transversal en mm^2
    outer_area = W * H
    inner_area = (W - 2*t) * (H - 2*t)
    cross_area = outer_area - inner_area
    
    # Densidad del acero al carbono (kg/mm^3)
    steel_density = 0.00000785
    
    total_weight_kg = cross_area * total_length_mm * steel_density
    return round(total_weight_kg, 2)

def project_geometry(p, L, angle=0, blade_thickness=3, double_miter=False):
    W, H, t = p['width'], p['height'], p['t']
    
    rad = math.radians(angle)
    discount = H * math.tan(rad)
    
    # Evitar que el descuento trigonométrico sea mayor que la pieza
    if double_miter:
        discount = min(discount, L / 2)
    else:
        discount = min(discount, L)
    
    manufacturing_measure = L + blade_thickness
    
    # Generar los puntos para el dibujo SVG dependiendo del tipo de corte
    if double_miter:
        elevation = np.array([
            [0, 0], 
            [L, 0], 
            [L - discount, H], 
            [discount, H]  # Lado izquierdo también en ángulo (Trapecio)
        ])
        short_tip = round(manufacturing_measure - (2 * discount), 1)
    else:
        elevation = np.array([
            [0, 0], 
            [L, 0], 
            [L - discount, H], 
            [0, H]         # Lado izquierdo plano (Polígono irregular)
        ])
        short_tip = round(manufacturing_measure - discount, 1)
        
    offset_x = L + 40 
    ext = np.array([[0, 0], [W, 0], [W, H], [0, H]]) + [offset_x, 0]
    int_ptr = np.array([[t, t], [t, H-t], [W-t, H-t], [W-t, t]]) + [offset_x, 0]
    
    return {
        'elevation': elevation, 
        'ext': ext, 
        'int': int_ptr, 
        'total_w': offset_x + W,
        'long_tip': manufacturing_measure, 
        'short_tip': short_tip,
        'is_double_miter': double_miter
    }

def extract_cut_list(voice_input):
    """
    Translates complex voice commands into a mathematical list of cuts.
    Includes an NLP filter and strict word boundaries (\b) to prevent digit splitting.
    """
    voice_norm = voice_input.lower()

    # Mapping Spanish spoken numbers to digits (Kept in Spanish for voice recognition accuracy)
    number_map = {'un': '1', 'una': '1', 'uno': '1', 'dos': '2', 'tres': '3', 
                  'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 
                  'ocho': '8', 'nueve': '9', 'diez': '10'}
    
    for word, digit in number_map.items():
        voice_norm = re.sub(rf'\b{word}\b', digit, voice_norm)

    # 🛑 NLP FILTER: Remove technical data to avoid confusing the length extraction
    # Removing gauge (calibre) info
    voice_norm = re.sub(r'calibre\s*\d+', '', voice_norm) 
    # Removing angles (grados)
    voice_norm = re.sub(r'\d+\s*grados', '', voice_norm)  
    # Removing cross-section dimensions (e.g., 4x4 or 4 por 4)
    voice_norm = re.sub(r'\d+\s*(?:por|x)\s*\d+', '', voice_norm) 

    # 🛡️ BULLETPROOF PATTERN: We use \b to prevent splitting numbers in half
    # Searching for: [Quantity] cuts/pieces of [Measure] meters/cm/mm
    pattern = r'\b(\d+)\b\s*(?:(?:cortes?|piezas?|tramos?)\s*)?(?:de\s*)?\b(\d+)\b\s*(metros?|mts?|cm|centimetros?|mm|milimetros?)'
    matches = re.findall(pattern, voice_norm)
    
    cut_list = []
    
    if matches:
        for qty_str, measure_str, unit in matches:
            qty = int(qty_str)
            measure = int(measure_str)
            
            # Convert everything to millimeters
            if unit.startswith('m') and 'mili' not in unit and unit not in ['mm']: 
                measure *= 1000
            elif unit.startswith('c'):
                measure *= 10
                
            cut_list.extend([measure] * qty)
    else:
        # Strict safety fallback: if the exact pattern fails, try to catch at least one measurement
        nums = re.findall(r'\b(\d+)\b\s*(metros?|mts?|cm|centimetros?|mm|milimetros?)', voice_norm)
        if nums:
            val = int(nums[0][0])
            u = nums[0][1]
            if u.startswith('m') and 'mili' not in u and u not in ['mm']: val *= 1000
            elif u.startswith('c'): val *= 10
            cut_list.append(val)
        else:
            # Default safety value
            cut_list.append(1000) 
            
    return cut_list

def extract_dimensions_3d(text):
    """
    Busca patrones de dimensiones (L x W x H) para activar el modo Arquitectónico.
    Ejemplo: '120x80x90' o '200 por 100'
    """
    pattern = r'\b(\d+)\b\s*(?:x|por|y|\*)\s*\b(\d+)\b(?:\s*(?:x|por|y|\*)\s*\b(\d+)\b)?'
    match = re.search(pattern, text)

    if match:
        vals = [int(v) for v in match.groups() if v is not None]

        # Filtro de seguridad: Ignorar dimensiones microscópicas que provienen del nombre del perfil (ej. PTR 2x2)
        if vals[0] <= 10 and vals[1] <= 10:
            return None

        if len(vals) == 2: vals.append(0) 
        return vals
    return None

def extract_components(text):
    """
    Extrae la cantidad de elementos internos (entrepaños, niveles, repisas) para ensambles 3D.
    Incluye mapeo NLP para transcripciones de voz.
    """
    text_norm = text.lower()
    
    # Mapeo de números hablados a dígitos numéricos
    number_map = {'un': '1', 'una': '1', 'uno': '1', 'dos': '2', 'tres': '3', 
                  'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 
                  'ocho': '8', 'nueve': '9', 'diez': '10'}
    
    for word, digit in number_map.items():
        text_norm = re.sub(rf'\b{word}\b', digit, text_norm)

    # Regex blindada con vocabulario extendido
    pattern = r'\b(\d+)\b\s*(?:entrepaños?|niveles?|divisiones?|repisas?|estantes?)'
    match = re.search(pattern, text_norm)
    
    return int(match.group(1)) if match else 0

def optimize_1d_cuts(cut_list, standard_length=6000, kerf=3):
    """
    First Fit Decreasing (FFD) algorithm to optimize 1D cutting stock.
    """
    sorted_cuts = sorted(cut_list, reverse=True)
    used_bars = [] 
    
    for original_cut in sorted_cuts:
        real_cut = original_cut + kerf
        placed = False
        
        for bar in used_bars:
            occupied_space = sum(bar)
            if (occupied_space + real_cut) <= standard_length:
                bar.append(real_cut)
                placed = True
                break
                
        if not placed:
            used_bars.append([real_cut])
            
    bar_reports = []
    global_scrap = 0
    
    for i, bar in enumerate(used_bars):
        occupied = sum(bar)
        leftover = standard_length - occupied
        global_scrap += leftover
        bar_reports.append({
            "bar_number": i + 1,
            "assigned_cuts": bar,
            "leftover_mm": leftover
        })
        
    total_material_bought = len(used_bars) * standard_length
    net_cuts_sum = sum(cut_list)
    efficiency = round((net_cuts_sum / total_material_bought) * 100, 1) if total_material_bought > 0 else 0

    return {
        "bars_to_buy": len(used_bars),
        "bar_details": bar_reports,
        "global_scrap_mm": global_scrap,
        "efficiency_percent": efficiency
    }

def evaluate_safety(pcr):
    if pcr >= 150:
        return "ESTRUCTURAL (Seguro para carga pesada)", (0, 120, 0), "#007800" 
    elif pcr >= 50:
        return "LIGERO (Solo carga secundaria/vista)", (200, 100, 0), "#c86400" 
    else:
        return "PELIGRO DE PANDEO (Riesgo inminente)", (200, 0, 0), "#c80000" 

# --- RENDERING ENGINES (SVG / PDF) ---

def generate_davinci_blueprint(L, W, H, name="ESTRUCTURA_PARAMETRICA"):
    vertices = [(0,0,0), (L,0,0), (L,W,0), (0,W,0), (0,0,H), (L,0,H), (L,W,H), (0,W,H)]
    edges = [(0,1), (1,2), (2,3), (3,0), (4,5), (5,6), (6,7), (7,4), (0,4), (1,5), (2,6), (3,7)]

    max_dim = max(L, W, H)
    if max_dim == 0: max_dim = 1
    scale = 600.0 / max_dim 

    def proj_front(v): return (v[0]*scale, -v[2]*scale)
    def proj_side(v):  return (v[1]*scale, -v[2]*scale)
    def proj_top(v):   return (v[0]*scale, -v[1]*scale)
    def proj_iso(v):
        rad30 = math.radians(30)
        x, y, z = v[0]*scale, v[1]*scale, v[2]*scale
        x_iso = (x - y) * math.cos(rad30)
        y_iso = -(z + (x + y) * math.sin(rad30))
        return (x_iso, y_iso)

    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 3200" style="background-color:#f4f1ea; font-family:monospace;">'
    
    svg += f'<text x="50" y="60" fill="#1e293b" font-size="28" font-weight="bold">SISTEMA CEM // PLANO TÉCNICO</text>'
    svg += f'<text x="50" y="90" fill="#64748b" font-size="18">PROYECTO: {name} | DIMENSIONES GLOBALES: {L}x{W}x{H} mm</text>'
    svg += '<line x1="50" y1="110" x2="950" y2="110" stroke="#1e293b" stroke-width="4"/>'

    def draw_view(proj_func, center_x, center_y, title):
        pts = [proj_func(v) for v in vertices]
        min_x = min(p[0] for p in pts)
        max_x = max(p[0] for p in pts)
        min_y = min(p[1] for p in pts)
        max_y = max(p[1] for p in pts)
        
        w, h = max_x - min_x, max_y - min_y
        dx = center_x - (min_x + w/2)
        dy = center_y - (min_y + h/2)
        
        g = f'<g transform="translate({dx}, {dy})">'
        g += f'<line x1="{min_x - 50}" y1="{max_y + 10}" x2="{max_x + 50}" y2="{max_y + 10}" stroke="#cbd5e1" stroke-width="3" stroke-dasharray="5,5"/>'
        
        for start_idx, end_idx in edges:
            g += f'<line x1="{pts[start_idx][0]}" y1="{pts[start_idx][1]}" x2="{pts[end_idx][0]}" y2="{pts[end_idx][1]}" stroke="#1e293b" stroke-width="4" stroke-linecap="round"/>'
        
        g += f'<text x="{min_x}" y="{max_y + 50}" fill="#1e293b" font-size="24" font-weight="bold">{title}</text>'
        g += '</g>'
        return g

    svg += draw_view(proj_front, 500, 450, "1. VISTA FRONTAL (ALZADO)")
    svg += draw_view(proj_side,  500, 1250, "2. VISTA LATERAL (PERFIL)")
    svg += draw_view(proj_top,   500, 2050, "3. VISTA SUPERIOR (PLANTA)")
    svg += draw_view(proj_iso,   500, 2850, "4. PROYECCIÓN ISOMÉTRICA (3D)")
    svg += '</svg>'
    return svg
    
# ==========================================
# 2. MOTOR PDF ARQUITECTÓNICO (PARA DESCARGA)
# ==========================================

def generate_davinci_pdf(L, W, H, material_name):
    pdf = FPDF(orientation='L', unit='mm', format='Letter')
    
    vertices = [(0,0,0), (L,0,0), (L,W,0), (0,W,0), (0,0,H), (L,0,H), (L,W,H), (0,W,H)]
    edges = [(0,1), (1,2), (2,3), (3,0), (4,5), (5,6), (6,7), (7,4), (0,4), (1,5), (2,6), (3,7)]

    max_dim = max(L, W, H)
    if max_dim == 0: max_dim = 1
    # Escala ajustada para llenar media hoja (aprox 110mm de ancho)
    scale = 110.0 / max_dim 

    def proj_front(v): return (v[0]*scale, -v[2]*scale)
    def proj_side(v):  return (v[1]*scale, -v[2]*scale)
    def proj_top(v):   return (v[0]*scale, -v[1]*scale)
    def proj_iso(v):
        rad30 = math.radians(30)
        x, y, z = v[0]*scale, v[1]*scale, v[2]*scale
        x_iso = (x - y) * math.cos(rad30)
        y_iso = -(z + (x + y) * math.sin(rad30))
        return (x_iso, y_iso)

    def build_iso_page(view1_title, view2_title, func1, func2, page_num):
        pdf.add_page()
        
        # Margen Norma ISO/UNE (Izq 25mm, Superior/Inferior/Derecha 10mm)
        # Formato Letter apaisado: Ancho 279.4, Alto 215.9
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.5)
        pdf.rect(25, 10, 244.4, 195.9)
        
        # Cajetín (Esquina inferior derecha de la zona útil)
        cajetin_w = 110
        cajetin_h = 30
        cajetin_x = 269.4 - cajetin_w
        cajetin_y = 205.9 - cajetin_h
        
        pdf.rect(cajetin_x, cajetin_y, cajetin_w, cajetin_h)
        pdf.line(cajetin_x, cajetin_y + 10, cajetin_x + cajetin_w, cajetin_y + 10)
        pdf.line(cajetin_x, cajetin_y + 20, cajetin_x + cajetin_w, cajetin_y + 20)
        
        pdf.set_font("helvetica", "B", 10)
        pdf.set_xy(cajetin_x, cajetin_y + 2)
        pdf.cell(cajetin_w, 6, "SISTEMA CEM - PLANO TÉCNICO DE ENSAMBLE", align="C")
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_xy(cajetin_x, cajetin_y + 12)
        pdf.cell(cajetin_w, 6, f"MAT: {material_name} | DIM GLOBALES: {L}x{W}x{H} mm", align="C")
        
        pdf.set_xy(cajetin_x, cajetin_y + 22)
        pdf.cell(cajetin_w, 6, f"NORMA ISO | HOJA: {page_num}/2", align="C")

        # Función interna para centrar el dibujo en su respectiva media hoja
        def draw_view(proj_func, center_x, center_y, title):
            pts = [proj_func(v) for v in vertices]
            min_x = min(p[0] for p in pts)
            max_x = max(p[0] for p in pts)
            min_y = min(p[1] for p in pts)
            max_y = max(p[1] for p in pts)
            
            w, h = max_x - min_x, max_y - min_y
            dx = center_x - (min_x + w/2)
            dy = center_y - (min_y + h/2)
            
            pdf.set_draw_color(160, 160, 160)
            pdf.set_line_width(0.2)
            pdf.line(dx + min_x - 10, dy + max_y + 5, dx + max_x + 10, dy + max_y + 5)
            
            pdf.set_draw_color(0, 0, 0)
            pdf.set_line_width(0.6)
            for start_idx, end_idx in edges:
                p1, p2 = pts[start_idx], pts[end_idx]
                pdf.line(dx + p1[0], dy + p1[1], dx + p2[0], dy + p2[1])
                
            pdf.set_font("helvetica", "B", 10)
            pdf.set_xy(center_x - 40, dy + max_y + 10)
            pdf.cell(80, 5, title, align="C")

        # Dibujamos las dos vistas de esta página (Izquierda y Derecha)
        draw_view(func1, 85, 95, view1_title)
        draw_view(func2, 205, 80, view2_title) # La vista derecha sube ligeramente para no cruzar el cajetín

    # Compilación de las dos hojas
    build_iso_page("1. VISTA FRONTAL (ALZADO)", "2. VISTA LATERAL (PERFIL)", proj_front, proj_side, 1)
    build_iso_page("3. VISTA SUPERIOR (PLANTA)", "4. PROYECCIÓN ISOMÉTRICA", proj_top, proj_iso, 2)
    
    pdf_out = pdf.output(dest='S')
    if isinstance(pdf_out, str):
        pdf_out = pdf_out.encode('latin1')
    return base64.b64encode(pdf_out).decode('utf-8')
    
def render_svg(geo, p, L, pcr, angle, diag_text, diag_hex):
    s = MM_TO_PX
    H = p['height']
    
    top_lane = 50 * s  
    bottom_lane = 65 * s  
    
    w_view = (geo['total_w'] + 50) * s
    h_view = (H * s) + top_lane + bottom_lane
    
    def fmt(pts): return " ".join([f"{pt[0]*s},{pt[1]*s + top_lane}" for pt in pts])

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {w_view} {h_view}">',
        '<style>.line {stroke:#1a1a1a; fill:none; stroke-width:3;} .cota {stroke:red; stroke-width:1.5;} .txt {font-family:monospace; font-size:18px; font-weight:bold;}</style>',
        f'<polygon points="{fmt(geo["elevation"])}" class="line"/>',
        f'<path d="M {fmt(geo["ext"])} Z M {fmt(geo["int"])} Z" fill="#d0d0d0" stroke="black" fill-rule="evenodd"/>',
        f'<line x1="0" y1="{30*s}" x2="{L*s}" y2="{30*s}" class="cota"/>',
        f'<text x="{(L*s)/2}" y="{24*s}" class="txt" text-anchor="middle" fill="red">{L} mm</text>',
        f'<text x="10" y="{top_lane + (H*s) + (20*s)}" class="txt" fill="#333">PIEZA: {p["name"]} | CORTE: {angle}°</text>',
        f'<text x="10" y="{top_lane + (H*s) + (35*s)}" class="txt" fill="#000">PUNTA LARGA: {geo["long_tip"]} mm | PUNTA CORTA: {geo["short_tip"]} mm</text>',
        f'<text x="10" y="{top_lane + (H*s) + (50*s)}" class="txt" fill="{diag_hex}">ESTADO: {diag_text} (Soporta: {pcr} kg)</text>',
        '</svg>'
    ]
    return "".join(svg)
    
def generate_1to1_pdf(geo, p, L, pcr, angle, diag_text, diag_rgb, cut_plan):
    pdf = FPDF(orientation="landscape", unit="mm", format="letter")
    pdf.add_page()
    
    max_paper_width = 239.0 
    drawing_width = geo['total_w']
    H = p['height']
    
    if drawing_width > max_paper_width:
        scale = max_paper_width / drawing_width
        scale_text = f"Escala Visual: 1 : {round(1/scale, 1)} (Ajustado a Carta)"
    else:
        scale = 1.0
        scale_text = "Escala Visual: 1:1 (Medidas Reales)"

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "SISTEMA CEM - PLANO DE FABRICACIÓN", align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, f"Material: {p['name']} | Pcr: {pcr} kg", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, "INSTRUCCIONES DE CORTE (Tramos de 6m):", new_x="LMARGIN", new_y="NEXT", fill=True)
    
    pdf.set_font("helvetica", "", 9)
    pdf.cell(0, 5, f"Material a comprar: {cut_plan['bars_to_buy']} tramo(s) estándar.", new_x="LMARGIN", new_y="NEXT")
    
    for inst in cut_plan['instructions_list']:
        pdf.cell(0, 5, f"> {inst}", new_x="LMARGIN", new_y="NEXT")
        
    pdf.set_text_color(0, 120, 0) 
    pdf.cell(0, 5, f"Retazo útil sobrante: {cut_plan['global_scrap']} mm", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    
    pdf.set_text_color(*diag_rgb) 
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, f"Estatus Estructural: {diag_text}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0) 
    
    line_height = pdf.get_y() + 2
    pdf.line(10, line_height, 270, line_height) 
    
    origin_x = 20
    origin_y = line_height + 10
    
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    
    elevation_pts = [((pt[0]*scale) + origin_x, (pt[1]*scale) + origin_y) for pt in geo['elevation']]
    pdf.polygon(elevation_pts, style="D")
    
    ext_pts = [((pt[0]*scale) + origin_x, (pt[1]*scale) + origin_y) for pt in geo['ext']]
    int_pts = [((pt[0]*scale) + origin_x, (pt[1]*scale) + origin_y) for pt in geo['int']]
    
    pdf.polygon(ext_pts, style="D")
    pdf.polygon(int_pts, style="D")
    
    y_cota = origin_y + (H * scale) + 10
    pdf.set_draw_color(200, 0, 0) 
    pdf.line(origin_x, y_cota, origin_x + (L * scale), y_cota)
    pdf.set_text_color(200, 0, 0)
    pdf.set_font("helvetica", "B", 9)
    pdf.text(origin_x + ((L * scale) / 2) - 5, y_cota - 2, f"{L} mm")
    
    pdf_bytes = pdf.output()
    return base64.b64encode(pdf_bytes).decode('utf-8')
    
# --- MAIN ENDPOINT ---
@app.post("/procesar-diseno")
async def process_design(req: CadRequest):
    voice_input = req.full_voice.lower()
    material = search_material(voice_input)
    
    # --- 🔍 DETECCIÓN DE MODO: ¿ARQUITECTURA O DESPIECE? ---
    dims_3d = extract_dimensions_3d(voice_input)

    # ==========================================
    # MODO A: ENSAMBLE ARQUITECTÓNICO (DA VINCI)
    # ==========================================
   if dims_3d:
        L, W, H = dims_3d
        tipo_obra = "MARCO_ESTRUCTURAL" if H == 0 else "ENSAMBLE_3D"
        
        # 1. Extraemos los entrepaños usando tu nueva función
        num_entrepanos = extract_components(voice_input)
        
        blueprint_svg = generate_davinci_blueprint(L, W, H, name=f"{tipo_obra}_{material['name']}")
        
        # 2. Matemáticas de Cotización: Esqueleto base + Entrepaños
        if H > 0:
            # Cubo/Prisma completo: 4 aristas de largo, 4 de ancho, 4 de altura.
            total_mm_estimado = (4*L + 4*W + 4*H)
            
            # Sumamos el acero necesario para los marcos de cada nivel interno
            if num_entrepanos > 0:
                total_mm_estimado += num_entrepanos * ((2*L) + (2*W))
        else:
            # Marco plano 2D
            total_mm_estimado = (2*L + 2*W)
            
        peso_total_kg = calculate_structural_weight(material, total_mm_estimado)
        
        pdf_arq_b64 = generate_davinci_pdf(L, W, H, material['name'])

        return {
            "status": "success",
            "material": material["name"],
            "is_assembly": True,
            "peso_total_kg": peso_total_kg,
            "pcr_kg": "Análisis de conjunto",
            "financial_efficiency": f"Incluye {num_entrepanos} entrepaños", # Mostramos el dato aquí
            "bars_to_buy": "Ver despiece",
            "svg_code": blueprint_svg,
            "pdf_base64": pdf_arq_b64
        }

    # ==========================================
    # MODO B: PIEZAS INDIVIDUALES (TU MODELO ACTUAL)
    # ==========================================
    cut_list = extract_cut_list(voice_input)
    ref_length = max(cut_list)
    total_length_mm = sum(cut_list)

    match_angle = re.search(r'(\d+)\s*grados', voice_input)
    angle = int(match_angle.group(1)) if match_angle else 0

    l_cm = ref_length / 10
    
    # --- 🤖 MOTOR DE INFERENCIA DE DOBLE INGLETE ---
    contexto_cerrado = ["mesa", "marco", "ventana", "puerta", "cuadro", "bastidor"]
    peticion_manual = ["doble inglete", "ambas puntas", "dos puntas", "trapecio"]
    
    es_estructura_cerrada = any(palabra in voice_input for palabra in contexto_cerrado)
    es_peticion_manual = any(palabra in voice_input for palabra in peticion_manual)
    
    aplicar_doble_inglete = False
    if angle > 0 and (es_estructura_cerrada or es_peticion_manual):
        aplicar_doble_inglete = True
    # -----------------------------------------------

    try:
        pcr_calc = (math.pi**2 * STEEL_E * material['I']) / (l_cm**2)
        pcr_round = round(pcr_calc, 2)
    except Exception:
        pcr_round = 0
        
    diag_text, diag_rgb, diag_hex = evaluate_safety(pcr_round)
    
    # Cálculos Avanzados de Peso y Geometría
    peso_total_kg = calculate_structural_weight(material, total_length_mm)
    geo = project_geometry(material, ref_length, angle, blade_thickness=3, double_miter=aplicar_doble_inglete)
    
    optimization = optimize_1d_cuts(cut_list, standard_length=STANDARD_LENGTH, kerf=3)
    svg_final = render_svg(geo, material, ref_length, pcr_round, angle, diag_text, diag_hex)
    
    # --- PDF ADAPTER ---
    pdf_instructions = []
    for bar in optimization['bar_details']:
        cuts_str = " + ".join([f"{c}mm" for c in bar['assigned_cuts']])
        pdf_instructions.append(f"Tramo {bar['bar_number']}: [{cuts_str}] | Sobra: {bar['leftover_mm']}mm")
        
    adapted_cut_plan = {
        "bars_to_buy": optimization['bars_to_buy'],
        "global_scrap": optimization['global_scrap_mm'],
        "instructions_list": pdf_instructions
    }
    
    pdf_base64 = generate_1to1_pdf(geo, material, ref_length, pcr_round, angle, diag_text, diag_rgb, adapted_cut_plan)

    # --- SUPABASE PROFESSIONAL SAVE ---
    if supabase:
        try:
            db_record = {
                "material_nombre": material['name'],
                "longitud_mm": ref_length,
                "angulo_grados": angle,
                "pcr_kg": pcr_round,
                "estatus_seguridad": diag_text,
                "piezas_totales": len(cut_list),
                "eficiencia_porcentaje": optimization['efficiency_percent']
            }
            supabase.table("historial_planos").insert(db_record).execute()
        except Exception as e:
            print(f"⚠️ Error Supabase: {e}")

    return {
        "status": "success",
        "material": material['name'],
        "is_assembly": False,
        "pcr_kg": pcr_round,
        "peso_total_kg": peso_total_kg,
        "pieces_requested": len(cut_list),
        "financial_efficiency": f"{optimization['efficiency_percent']}%",
        "bars_to_buy": optimization['bars_to_buy'],
        "svg_code": svg_final,
        "pdf_base64": pdf_base64
    }
