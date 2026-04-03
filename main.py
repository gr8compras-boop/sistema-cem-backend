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
                'I': float(row.get('Inercia_I', 0)),
                'width': float(row.get('Ancho_mm', 0)),
                'height': float(row.get('Alto_mm', 0)),
                't': float(row.get('Espesor_t', 0)),
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
        if score > max_score:
            max_score, match = score, key
    return CATALOG[match]

def project_geometry(p, L, angle=0, blade_thickness=3):
    W, H, t = p['width'], p['height'], p['t']
    
    rad = math.radians(angle)
    discount = H * math.tan(rad)
    discount = min(discount, L)
    
    manufacturing_measure = L + blade_thickness
    
    elevation = np.array([
        [0, 0], 
        [L, 0], 
        [L - discount, H], 
        [0, H]
    ])
    
    offset_x = L + 40 
    ext = np.array([[0, 0], [W, 0], [W, H], [0, H]]) + [offset_x, 0]
    int_ptr = np.array([[t, t], [t, H-t], [W-t, H-t], [W-t, t]]) + [offset_x, 0]
    
    return {
        'elevation': elevation, 
        'ext': ext, 
        'int': int_ptr, 
        'total_w': offset_x + W,
        'long_tip': manufacturing_measure, 
        'short_tip': round(manufacturing_measure - discount, 1)
    }

def extract_cut_list(voice_input):
    """
    Translates complex voice commands into a mathematical list of cuts.
    Note: Regex and mapping stay in Spanish to process user input correctly.
    """
    voice_norm = voice_input.lower()

    number_map = {'un': '1', 'una': '1', 'uno': '1', 'dos': '2', 'tres': '3', 
                  'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 
                  'ocho': '8', 'nueve': '9', 'diez': '10'}
    
    for word, digit in number_map.items():
        voice_norm = re.sub(rf'\b{word}\b', digit, voice_norm)

    pattern = r'(\d+)\s*(?:cortes?|piezas?|tramos?|de)*\s*(\d+)\s*(metros?|mts?|cm|centimetros?|mm|milimetros?)?'
    matches = re.findall(pattern, voice_norm)
    
    cut_list = []
    
    if matches:
        for qty_str, measure_str, unit in matches:
            qty = int(qty_str)
            measure = int(measure_str)
            
            if unit.startswith('m') and 'mili' not in unit and unit not in ['mm']: 
                measure *= 1000
            elif unit.startswith('c'):
                measure *= 10
                
            cut_list.extend([measure] * qty)
    else:
        nums = re.findall(r'\d+', voice_norm)
        length = int(nums[0]) if nums else 1000
        if any(m in voice_norm for m in ["metro", "mts"]): length *= 1000
        elif any(c in voice_norm for c in ["cm", "centimetro"]): length *= 10
        cut_list.append(length)
        
    return cut_list

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

    cut_list = extract_cut_list(voice_input)
    ref_length = max(cut_list)

    match_angle = re.search(r'(\d+)\s*grados', voice_input)
    angle = int(match_angle.group(1)) if match_angle else 0

    material = search_material(voice_input)
    l_cm = ref_length / 10
    
    try:
        pcr_calc = (math.pi**2 * STEEL_E * material['I']) / (l_cm**2)
        pcr_round = round(pcr_calc, 2)
    except Exception:
        pcr_round = 0
        
    diag_text, diag_rgb, diag_hex = evaluate_safety(pcr_round)
    
    geo = project_geometry(material, ref_length, angle, blade_thickness=3)
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
            # DB Keys remain in Spanish to match your Supabase SQL Schema
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
            print("💾 Record saved to Supabase successfully.")
        except Exception as e:
            print(f"⚠️ Error saving to Supabase: {e}")

    return {
        "status": "success",
        "material": material['name'],
        "pcr_kg": pcr_round,
        "pieces_requested": len(cut_list),
        "financial_efficiency": f"{optimization['efficiency_percent']}%",
        "bars_to_buy": optimization['bars_to_buy'],
        "svg_code": svg_final,
        "pdf_base64": pdf_base64
    }
