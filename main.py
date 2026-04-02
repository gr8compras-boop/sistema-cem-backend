import numpy as np
import math
import re
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Motor CAD Paramétrico CEM v4.1.2 - Industrial")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONSTANTES TÉCNICAS ---
MM_TO_PX = 3.779527559 
E_ACERO = 2100000 # kg/cm²

CATALOGO = {
    # --- PTR / HSS ---
    'P1114': {'nombre': 'PTR 1x1" Cal 14', 'inercia': 1.15, 'ancho': 25.4, 'alto': 25.4, 'espesor': 1.9, 'peso': 1.27, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '1', 'una', 'calibre', '14', 'catorce', 'cuadrado']},
    'P151514': {'nombre': 'PTR 1.5x1.5" Cal 14', 'inercia': 4.12, 'ancho': 38.1, 'alto': 38.1, 'espesor': 1.9, 'peso': 2.16, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '1.5', 'pulgada y media', 'calibre', '14', 'catorce', 'cuadrado']},
    'P2214': {'nombre': 'PTR 2x2" Cal 14', 'inercia': 14.50, 'ancho': 50.8, 'alto': 50.8, 'espesor': 1.9, 'peso': 2.93, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '2', 'dos', 'calibre', '14', 'catorce', 'cuadrado']},
    'P2211': {'nombre': 'PTR 2x2" Cal 11', 'inercia': 22.10, 'ancho': 50.8, 'alto': 50.8, 'espesor': 3.0, 'peso': 4.41, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '2', 'dos', 'calibre', '11', 'once', 'cuadrado']},
    'P3311': {'nombre': 'PTR 3x3" Cal 11', 'inercia': 72.40, 'ancho': 76.2, 'alto': 76.2, 'espesor': 3.0, 'peso': 6.84, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '3', 'tres', 'calibre', '11', 'once', 'cuadrado']},
    'P3310': {'nombre': 'PTR 3x3" Cal 10', 'inercia': 88.00, 'ancho': 76.2, 'alto': 76.2, 'espesor': 3.4, 'peso': 7.68, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '3', 'tres', 'calibre', '10', 'diez', 'cuadrado']},
    'P4411': {'nombre': 'PTR 4x4" Cal 11', 'inercia': 180.0, 'ancho': 101.6, 'alto': 101.6, 'espesor': 3.0, 'peso': 9.25, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '4', 'cuatro', 'calibre', '11', 'once', 'cuadrado']},
    'P44316': {'nombre': 'PTR 4x4" Cal 3/16', 'inercia': 250.0, 'ancho': 101.6, 'alto': 101.6, 'espesor': 4.8, 'peso': 14.20, 'tipo': 'rect', 'tags': ['ptr', 'perfil', 'tubular', '4', 'cuatro', '3/16', 'tres dieciseisavos', 'cuadrado']},
    
    # --- ÁNGULOS DE LADOS IGUALES (LI) ---
    'A075125': {'nombre': 'Ángulo 3/4 x 1/8"', 'inercia': 0.37, 'ancho': 19.1, 'alto': 19.1, 'espesor': 3.2, 'peso': 0.88, 'tipo': 'rect', 'tags': ['angulo', '3/4', 'tres cuartos', '1/8', 'un octavo', 'l', 'escuadra']},
    'A100125': {'nombre': 'Ángulo 1 x 1/8"', 'inercia': 0.92, 'ancho': 25.4, 'alto': 25.4, 'espesor': 3.2, 'peso': 1.19, 'tipo': 'rect', 'tags': ['angulo', '1', 'una', '1/8', 'un octavo', 'l', 'escuadra']},
    'A150188': {'nombre': 'Ángulo 1.5 x 3/16"', 'inercia': 4.58, 'ancho': 38.1, 'alto': 38.1, 'espesor': 4.8, 'peso': 2.68, 'tipo': 'rect', 'tags': ['angulo', '1.5', 'pulgada y media', '3/16', 'tres dieciseisavos', 'l', 'escuadra']},
    'A200250': {'nombre': 'Ángulo 2 x 1/4"', 'inercia': 14.57, 'ancho': 50.8, 'alto': 50.8, 'espesor': 6.4, 'peso': 4.75, 'tipo': 'rect', 'tags': ['angulo', '2', 'dos', '1/4', 'un cuarto', 'l', 'escuadra']},
    'A300375': {'nombre': 'Ángulo 3 x 3/8"', 'inercia': 73.30, 'ancho': 76.2, 'alto': 76.2, 'espesor': 9.5, 'peso': 10.72, 'tipo': 'rect', 'tags': ['angulo', '3', 'tres', '3/8', 'tres octavos', 'l', 'escuadra']},
    'A400500': {'nombre': 'Ángulo 4 x 1/2"', 'inercia': 231.40, 'ancho': 101.6, 'alto': 101.6, 'espesor': 12.7, 'peso': 19.05, 'tipo': 'rect', 'tags': ['angulo', '4', 'cuatro', '1/2', 'media', 'l', 'escuadra']},
    
    # --- CANALES (Polín Montén C / CPS) ---
    'C314': {'nombre': 'Polín C 3x1.5" Cal 14', 'inercia': 23.00, 'ancho': 38.1, 'alto': 76.2, 'espesor': 1.9, 'peso': 2.50, 'tipo': 'rect', 'tags': ['polin', 'canal', 'monten', 'c', '3', 'tres', 'calibre', '14', 'catorce']},
    'C414': {'nombre': 'Polín C 4x2" Cal 14', 'inercia': 56.00, 'ancho': 50.8, 'alto': 101.6, 'espesor': 1.9, 'peso': 3.35, 'tipo': 'rect', 'tags': ['polin', 'canal', 'monten', 'c', '4', 'cuatro', 'calibre', '14', 'catorce']},
    'C612': {'nombre': 'Polín C 6x2" Cal 12', 'inercia': 246.2, 'ancho': 50.8, 'alto': 152.4, 'espesor': 2.6, 'peso': 5.74, 'tipo': 'rect', 'tags': ['polin', 'canal', 'monten', 'c', '6', 'seis', 'calibre', '12', 'doce']},
    'CE306': {'nombre': 'Canal Estándar 3" x 6.1kg', 'inercia': 66.60, 'ancho': 35.8, 'alto': 76.2, 'espesor': 4.3, 'peso': 6.10, 'tipo': 'rect', 'tags': ['canal', 'estandar', 'cps', 'monten', '3', 'tres']},
    
    # --- VIGAS IPR (IR) e IPS (IE) ---
    'IPR413': {'nombre': 'Viga IPR 4x13 lb/ft', 'inercia': 470.0, 'ancho': 103.0, 'alto': 106.0, 'espesor': 7.1, 'peso': 19.4, 'tipo': 'rect', 'tags': ['viga', 'ipr', '4', 'cuatro', '13', 'trece', 'libras']},
    'IPR609': {'nombre': 'Viga IPR 6x9 lb/ft', 'inercia': 683.0, 'ancho': 100.0, 'alto': 150.0, 'espesor': 4.3, 'peso': 13.6, 'tipo': 'rect', 'tags': ['viga', 'ipr', '6', 'seis', '9', 'nueve', 'libras']},
    'IPS357': {'nombre': 'Viga IPS 3x5.7 lb/ft', 'inercia': 105.0, 'ancho': 59.2, 'alto': 76.2, 'espesor': 4.3, 'peso': 8.5, 'tipo': 'rect', 'tags': ['viga', 'ips', '3', 'tres', '5.7']},
    'IPS477': {'nombre': 'Viga IPS 4x7.7 lb/ft', 'inercia': 253.0, 'ancho': 67.6, 'alto': 102.0, 'espesor': 4.9, 'peso': 11.5, 'tipo': 'rect', 'tags': ['viga', 'ips', '4', 'cuatro', '7.7']},
    
    # --- LÁMINAS ACANALADAS Y LOSACERO ---
    'L101-24': {'nombre': 'Lámina TR-101 Cal 24', 'inercia': 12.76, 'ancho': 1008.0, 'alto': 25.0, 'espesor': 0.53, 'peso': 5.37, 'tipo': 'lamina', 'tags': ['lamina', 'tr101', '101', 'acanalada', 'calibre', '24', 'veinticuatro']},
    'L101-26': {'nombre': 'Lámina TR-101 Cal 26', 'inercia': 10.57, 'ancho': 1008.0, 'alto': 25.0, 'espesor': 0.45, 'peso': 4.64, 'tipo': 'lamina', 'tags': ['lamina', 'tr101', '101', 'acanalada', 'calibre', '26', 'veintiseis']},
    'L101-28': {'nombre': 'Lámina TR-101 Cal 28', 'inercia': 8.21, 'ancho': 1008.0, 'alto': 25.0, 'espesor': 0.38, 'peso': 3.92, 'tipo': 'lamina', 'tags': ['lamina', 'tr101', '101', 'acanalada', 'calibre', '28', 'veintiocho']},
    'L72-26':  {'nombre': 'Lámina TR-72 Cal 26', 'inercia': 10.57, 'ancho': 720.2, 'alto': 25.0, 'espesor': 0.45, 'peso': 4.89, 'tipo': 'lamina', 'tags': ['lamina', 'tr72', '72', 'acanalada', 'calibre', '26', 'veintiseis']},
    'L90-24':  {'nombre': 'Lámina TR-90 Cal 24 (Estructural)', 'inercia': 45.17, 'ancho': 900.0, 'alto': 119.7, 'espesor': 0.53, 'peso': 6.02, 'tipo': 'lamina', 'tags': ['lamina', 'tr90', '90', 'acanalada', 'estructural', 'calibre', '24', 'veinticuatro']},
    'L100-26': {'nombre': 'Lámina RN-100/35 Cal 26', 'inercia': 10.57, 'ancho': 1000.0, 'alto': 35.0, 'espesor': 0.45, 'peso': 4.69, 'tipo': 'lamina', 'tags': ['lamina', 'rn100', '100', 'acanalada', 'calibre', '26', 'veintiseis']},
    'LSA-25-22': {'nombre': 'Losacero 25 Cal 22', 'inercia': 21.54, 'ancho': 914.4, 'alto': 63.5, 'espesor': 0.76, 'peso': 8.32, 'tipo': 'lamina', 'tags': ['lamina', 'losacero', '25', 'calibre', '22', 'veintidos']},
    'LSA-25-20': {'nombre': 'Losacero 25 Cal 20', 'inercia': 27.67, 'ancho': 914.4, 'alto': 63.5, 'espesor': 0.91, 'peso': 9.91, 'tipo': 'lamina', 'tags': ['lamina', 'losacero', '25', 'calibre', '20', 'veinte']},
    'LSA-15-22': {'nombre': 'Losacero 15 Cal 22', 'inercia': 18.53, 'ancho': 914.4, 'alto': 38.1, 'espesor': 0.76, 'peso': 8.32, 'tipo': 'lamina', 'tags': ['lamina', 'losacero', '15', 'calibre', '22', 'veintidos']},
    'L-GLK-24': {'nombre': 'Galvalok II Cal 24', 'inercia': 30.62, 'ancho': 609.6, 'alto': 76.5, 'espesor': 0.53, 'peso': 5.67, 'tipo': 'lamina', 'tags': ['lamina', 'galvalok', 'calibre', '24', 'veinticuatro']},
    
    # --- LÁMINA LISA ---
    'L-LISA-10': {'nombre': 'Lámina Lisa Cal 10', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 3.42, 'peso': 27.05, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '10', 'diez']},
    'L-LISA-12': {'nombre': 'Lámina Lisa Cal 12', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 2.67, 'peso': 21.01, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '12', 'doce']},
    'L-LISA-14': {'nombre': 'Lámina Lisa Cal 14', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 1.91, 'peso': 15.00, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '14', 'catorce']},
    'L-LISA-16': {'nombre': 'Lámina Lisa Cal 16', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 1.52, 'peso': 11.96, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '16', 'dieciseis']},
    'L-LISA-20': {'nombre': 'Lámina Lisa Cal 20', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 0.91, 'peso': 7.14, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '20', 'veinte']},
    'L-LISA-24': {'nombre': 'Lámina Lisa Cal 24', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 0.53, 'peso': 4.16, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '24', 'veinticuatro']},
    'L-LISA-28': {'nombre': 'Lámina Lisa Cal 28', 'inercia': 0.01, 'ancho': 1219.0, 'alto': 1.0, 'espesor': 0.32, 'peso': 2.51, 'tipo': 'lamina', 'tags': ['lamina', 'lisa', 'calibre', '28', 'veintiocho']},
    
    # --- TUBERÍA DE CONDUCCIÓN (Cédula 30 y 40) ---
    'T40-050': {'nombre': 'Tubo Cédula 40 1/2"', 'inercia': 0.71, 'ancho': 21.3, 'alto': 21.3, 'espesor': 2.8, 'peso': 1.28, 'tipo': 'tubo', 'tags': ['tubo', '1/2', 'media', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-075': {'nombre': 'Tubo Cédula 40 3/4"', 'inercia': 1.55, 'ancho': 26.7, 'alto': 26.7, 'espesor': 2.9, 'peso': 1.69, 'tipo': 'tubo', 'tags': ['tubo', '3/4', 'tres cuartos', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-100': {'nombre': 'Tubo Cédula 40 1"', 'inercia': 3.64, 'ancho': 33.4, 'alto': 33.4, 'espesor': 3.4, 'peso': 2.50, 'tipo': 'tubo', 'tags': ['tubo', '1', 'una', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-150': {'nombre': 'Tubo Cédula 40 1.5"', 'inercia': 13.32, 'ancho': 48.3, 'alto': 48.3, 'espesor': 3.7, 'peso': 4.05, 'tipo': 'tubo', 'tags': ['tubo', '1.5', 'pulgada y media', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-200': {'nombre': 'Tubo Cédula 40 2"', 'inercia': 28.20, 'ancho': 60.3, 'alto': 60.3, 'espesor': 3.9, 'peso': 5.44, 'tipo': 'tubo', 'tags': ['tubo', '2', 'dos', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-300': {'nombre': 'Tubo Cédula 40 3"', 'inercia': 110.00, 'ancho': 88.9, 'alto': 88.9, 'espesor': 5.5, 'peso': 11.29, 'tipo': 'tubo', 'tags': ['tubo', '3', 'tres', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-400': {'nombre': 'Tubo Cédula 40 4"', 'inercia': 310.00, 'ancho': 114.3, 'alto': 114.3, 'espesor': 6.0, 'peso': 16.07, 'tipo': 'tubo', 'tags': ['tubo', '4', 'cuatro', 'cedula', '40', 'cuarenta', 'redondo']},
    'T40-600': {'nombre': 'Tubo Cédula 40 6"', 'inercia': 1171.0, 'ancho': 168.3, 'alto': 168.3, 'espesor': 7.1, 'peso': 28.27, 'tipo': 'tubo', 'tags': ['tubo', '6', 'seis', 'cedula', '40', 'cuarenta', 'redondo']},
    'T30-100': {'nombre': 'Tubo Cédula 30 1"', 'inercia': 2.48, 'ancho': 33.4, 'alto': 33.4, 'espesor': 2.6, 'peso': 1.95, 'tipo': 'tubo', 'tags': ['tubo', '1', 'una', 'cedula', '30', 'treinta', 'redondo']},
    'T30-200': {'nombre': 'Tubo Cédula 30 2"', 'inercia': 21.40, 'ancho': 60.3, 'alto': 60.3, 'espesor': 3.1, 'peso': 4.35, 'tipo': 'tubo', 'tags': ['tubo', '2', 'dos', 'cedula', '30', 'treinta', 'redondo']},
    
    # --- SÓLIDOS (Redondos y Cuadrados) ---
    'S-RED-3/8': {'nombre': 'Redondo Sólido 3/8"', 'inercia': 0.04, 'ancho': 9.5, 'alto': 9.5, 'espesor': 9.5, 'peso': 0.56, 'tipo': 'solido', 'tags': ['solido', 'redondo', 'macizo', '3/8', 'tres octavos']},
    'S-RED-1/2': {'nombre': 'Redondo Sólido 1/2"', 'inercia': 0.13, 'ancho': 12.7, 'alto': 12.7, 'espesor': 12.7, 'peso': 0.99, 'tipo': 'solido', 'tags': ['solido', 'redondo', 'macizo', '1/2', 'media']},
    'S-RED-5/8': {'nombre': 'Redondo Sólido 5/8"', 'inercia': 0.31, 'ancho': 15.9, 'alto': 15.9, 'espesor': 15.9, 'peso': 1.55, 'tipo': 'solido', 'tags': ['solido', 'redondo', 'macizo', '5/8', 'cinco octavos']},
    'S-RED-3/4': {'nombre': 'Redondo Sólido 3/4"', 'inercia': 0.65, 'ancho': 19.1, 'alto': 19.1, 'espesor': 19.1, 'peso': 2.24, 'tipo': 'solido', 'tags': ['solido', 'redondo', 'macizo', '3/4', 'tres cuartos']},
    'S-CUA-1/2': {'nombre': 'Cuadrado Sólido 1/2"', 'inercia': 0.22, 'ancho': 12.7, 'alto': 12.7, 'espesor': 12.7, 'peso': 1.27, 'tipo': 'solido', 'tags': ['solido', 'cuadrado', 'macizo', '1/2', 'media']},
    'S-CUA-1.0': {'nombre': 'Cuadrado Sólido 1"', 'inercia': 3.52, 'ancho': 25.4, 'alto': 25.4, 'espesor': 25.4, 'peso': 5.06, 'tipo': 'solido', 'tags': ['solido', 'cuadrado', 'macizo', '1', 'una']},
    
    # --- PLACA ESTRUCTURAL (A36) ---
    'PL-3/16': {'nombre': 'Placa A36 3/16"', 'inercia': 0.01, 'ancho': 1000.0, 'alto': 1000.0, 'espesor': 4.76, 'peso': 37.37, 'tipo': 'lamina', 'tags': ['placa', 'acero', 'a36', '3/16', 'tres dieciseisavos', 'lisa', 'comercial', 'negra', 'lamina negra']},
    'PL-1/4':  {'nombre': 'Placa A36 1/4"',  'inercia': 0.01, 'ancho': 1000.0, 'alto': 1000.0, 'espesor': 6.35, 'peso': 49.85, 'tipo': 'lamina', 'tags': ['placa', 'acero', 'a36', '1/4', 'un cuarto', 'lisa', 'comercial', 'negra', 'lamina negra']},
    'PL-3/8':  {'nombre': 'Placa A36 3/8"',  'inercia': 0.01, 'ancho': 1000.0, 'alto': 1000.0, 'espesor': 9.53, 'peso': 74.81, 'tipo': 'lamina', 'tags': ['placa', 'acero', 'a36', '3/8', 'tres octavos', 'lisa', 'comercial', 'negra', 'lamina negra']},
    'PL-1/2':  {'nombre': 'Placa A36 1/2"',  'inercia': 0.01, 'ancho': 1000.0, 'alto': 1000.0, 'espesor': 12.70, 'peso': 99.70, 'tipo': 'lamina', 'tags': ['placa', 'acero', 'a36', '1/2', 'media', 'lisa', 'comercial', 'negra', 'lamina negra']},
}

class CadRequest(BaseModel):
    voz_completa: str

def buscar_material(texto: str):
    texto = texto.lower()
    match, max_score = 'P2214', 0
    for key, data in CATALOGO.items():
        score = sum(1 for tag in data['tags'] if tag in texto)
        if score > max_score:
            max_score, match = score, key
    return CATALOGO[match]

# --- MOTOR GEOMÉTRICO (NUMPY) ---
import math
import numpy as np

def proyectar_geometria(p, L, angulo=0, grosor_disco=3):
    """
    Motor geométrico con compensación de corte (Kerf).
    grosor_disco: Tolerancia en milímetros que consume la herramienta de corte.
    """
    W, H, t = p['ancho'], p['alto'], p['t']
    
    rad = math.radians(angulo)
    descuento = H * math.tan(rad)
    descuento = min(descuento, L)
    
    # --- CONOCIMIENTO EXPERTO: Compensación Kerf ---
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
    """
    Calcula cuántos tramos enteros y qué cortes se necesitan para alcanzar
    una longitud total, considerando el desperdicio del disco de corte (kerf).
    """
    # 1. ¿Cuántos tramos completos de 6m necesitamos?
    tramos_enteros = longitud_total // tramo_estandar
    
    # 2. ¿Cuánto falta para completar la medida?
    resto = longitud_total % tramo_estandar
    
    instrucciones = []
    tramos_a_comprar = tramos_enteros
    retazo_util = 0
    
    # Si hay tramos completos, los anotamos sin aplicar descuento de corte
    if tramos_enteros > 0:
        instrucciones.append(f"{tramos_enteros} tramo(s) entero(s) de {tramo_estandar} mm (De fábrica)")
        
    # Si sobra un pedazo, a ese SÍ le aplicamos el Kerf (el disco)
    punta_larga_resto = 0
    if resto > 0:
        punta_larga_resto = resto + kerf
        instrucciones.append(f"1 corte de {punta_larga_resto} mm (Incluye {kerf} mm por el disco)")
        tramos_a_comprar += 1
        
        # Calculamos cuánto nos sobró de ese último tubo que compramos
        retazo_util = tramo_estandar - punta_larga_resto
        
    return {
        "tramos_enteros": tramos_enteros,
        "medida_corte_final": punta_larga_resto,
        "tramos_comprar": tramos_a_comprar,
        "retazo": retazo_util,
        "lista_instrucciones": instrucciones
    }

def evaluar_seguridad(Pcr):
    """
    Evalúa la Carga Crítica de Euler y retorna un texto de diagnóstico, 
    un color RGB (para el PDF) y un color Hexadecimal (para el SVG).
    """
    if Pcr >= 150:
        return "ESTRUCTURAL (Seguro para carga pesada)", (0, 120, 0), "#007800" # Verde
    elif Pcr >= 50:
        return "LIGERO (Solo carga secundaria/vista)", (200, 100, 0), "#c86400" # Naranja
    else:
        return "PELIGRO DE PANDEO (Riesgo inminente)", (200, 0, 0), "#c80000" # Rojo

def renderizar_svg(geo, p, L, Pcr, angulo, diag_texto, diag_hex):
    s = MM_TO_PX
    H = p['alto']
    
    carril_superior = 50 * s  
    carril_inferior = 65 * s  # Ampliamos el carril para la 3ra línea
    
    W_view = (geo['total_w'] + 50) * s
    H_view = (H * s) + carril_superior + carril_inferior
    
    def fmt(pts): return " ".join([f"{pt[0]*s},{pt[1]*s + carril_superior}" for pt in pts])

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {W_view} {H_view}">',
        '<style>.line {stroke:#1a1a1a; fill:none; stroke-width:3;} .cota {stroke:red; stroke-width:1.5;} .txt {font-family:monospace; font-size:18px; font-weight:bold;}</style>',
        
        # Carril Central
        f'<polygon points="{fmt(geo["alzado"])}" class="line"/>',
        f'<path d="M {fmt(geo["ext"])} Z M {fmt(geo["int"])} Z" fill="#d0d0d0" stroke="black" fill-rule="evenodd"/>',
        
        # Carril Superior (Cotas)
        f'<line x1="0" y1="{30*s}" x2="{L*s}" y2="{30*s}" class="cota"/>',
        f'<text x="{(L*s)/2}" y="{24*s}" class="txt" text-anchor="middle" fill="red">{L} mm</text>',
        
        # Carril Inferior (Textos)
        f'<text x="10" y="{carril_superior + (H*s) + (20*s)}" class="txt" fill="#333">PIEZA: {p["nombre"]} | CORTE: {angulo}°</text>',
        f'<text x="10" y="{carril_superior + (H*s) + (35*s)}" class="txt" fill="#000">PUNTA LARGA: {geo["punta_larga"]} mm | PUNTA CORTA: {geo["punta_corta"]} mm</text>',
        
        # --- NUEVO: El Semáforo de Seguridad en pantalla ---
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

    # --- MEMBRETE TÉCNICO ---
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "SISTEMA CEM - PLANO DE FABRICACIÓN", align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, f"Material: {p['nombre']} | Pcr: {Pcr} kg", new_x="LMARGIN", new_y="NEXT")
    
    # --- NUEVO: LISTA DE CORTE Y MATERIALES ---
    # Ponemos un fondo gris claro para separar esta sección
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, "INSTRUCCIONES DE CORTE (Tramos de 6m):", new_x="LMARGIN", new_y="NEXT", fill=True)
    
    pdf.set_font("helvetica", "", 9)
    pdf.cell(0, 5, f"Material a comprar: {despiece['tramos_comprar']} tramo(s) estándar.", new_x="LMARGIN", new_y="NEXT")
    
    # Imprimimos cada instrucción generada por el algoritmo
    for inst in despiece['lista_instrucciones']:
        pdf.cell(0, 5, f"> {inst}", new_x="LMARGIN", new_y="NEXT")
        
    # Destacamos el retazo en verde para control de inventario
    pdf.set_text_color(0, 120, 0) 
    pdf.cell(0, 5, f"Retazo útil sobrante: {despiece['retazo']} mm", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    
    # --- ALERTA DE SEGURIDAD ---
    pdf.set_text_color(*diag_rgb) 
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 6, f"Estatus Estructural: {diag_texto}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0) 
    
    # Línea divisoria dinámica
    altura_linea = pdf.get_y() + 2
    pdf.line(10, altura_linea, 270, altura_linea) 
    
    # --- DIBUJO GEOMÉTRICO ---
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
    
    # NÚCLEO DE MANUFACTURA
    geo = proyectar_geometria(material, longitud, angulo, grosor_disco=3)
    
    # NUEVO: Calculamos el despiece antes de generar el PDF
    despiece_info = calcular_despiece(longitud, tramo_estandar=6000, kerf=3)
    
    svg_final = renderizar_svg(geo, material, longitud, pcr_redondo, angulo, diag_texto, diag_hex)
    
    # Pasamos 'despiece_info' al PDF
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
