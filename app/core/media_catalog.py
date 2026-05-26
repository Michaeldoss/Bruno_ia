import re

# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de mídias Doss Group (Cloudinary)
# Vídeos com compressão automática: q_auto,vc_h264,br_800k
# ─────────────────────────────────────────────────────────────────────────────

_C = "q_auto,vc_h264,br_800k"

_PRODUCTS = {
    "1801": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776688349/V%C3%ADdeo_1801_d1fhdm.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776436318/1801_x8qpbr.jpg",
    },
    "1802": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686175/1802_kbalek.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776460806/1802iE_png_sappgj.png",
    },
    "1902": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686438/1902_2_dvxaqp.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776685644/1902i_-_SOMBRA_bscduh.png",
    },
    "1904": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461517/DG1904i_V2_mrt95y.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776460820/1904_-_Frontal_pmphwg.png",
    },
    "1908": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461533/Video_Michael_Maquina_1_ieduwg.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776434758/hf_20260417_140052_9ff876c2-a456-462c-ae27-6325593af312_e1splr.png",
    },
    "3202": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461540/Em_busca_de_aumentar_a_produ%C3%A7%C3%A3o_de_forma_eficiente_e_r%C3%A1pida_sem_comprometer_a_qualidade_Plot_dlsllu.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776460897/3202_M%C3%A1quina_so4yij.png",
    },
    "3204": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686633/3204_g4yf2k.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776461359/DG-3204_owdjhu.png",
    },
    "3003": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C},f_mp4/v1776685842/IMG_1993_lz5ohr.mov",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776685626/IMG_1922_of9aiz.jpg",
    },
    "dtf30": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776689977/3002_1_mwm4ph.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776689827/IMG_6740_mn6uvi.jpg",
    },
    "dtf60": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461555/NOVIDADE_NA_%C3%81REA_Apresentamos_a_AJ-6002_a_impressora_DTF_que_vai_revolucionar_suas_estampas_vns0hn.mp4",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776460912/DTF60_Forno_gp76pl.png",
    },
    "jinka1351": {
        "video": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C},f_mp4/v1776685835/Jinka_ABJ_1351_y6dj9z.mov",
        "image": "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776685661/m%C3%A1quina_de_recorte_plotter_-_4_i9bkom.png",
    },
}

_ALIASES = {
    # ── 1801 ──────────────────────────────────────────────────────────────────
    "1801":                    "1801",
    "1801i":                   "1801",
    "dg 1801":                 "1801",
    "dg 1801i":                "1801",
    "dg1801":                  "1801",
    "dg1801i":                 "1801",
    "hs 1801":                 "1801",
    "hs 1801i":                "1801",
    "hs1801":                  "1801",
    "hs1801i":                 "1801",
    "plotter 1801":            "1801",
    "plotter 1801i":           "1801",

    # ── 1802 ──────────────────────────────────────────────────────────────────
    "1802":                    "1802",
    "1802i":                   "1802",
    "1801/2":                  "1802",
    "1801 2":                  "1802",
    "dg 1802":                 "1802",
    "dg 1802i":                "1802",
    "dg1802":                  "1802",
    "dg1802i":                 "1802",
    "plotter 1802":            "1802",
    "plotter 1802i":           "1802",
    "duas cabeças":"1802",
    "2 cabecas":"1802",

    # ── 1902 ──────────────────────────────────────────────────────────────────
    "1902":                    "1902",
    "1902i":                   "1902",
    "dg 1902":                 "1902",
    "dg 1902i":                "1902",
    "dg1902":                  "1902",
    "dg1902i":                 "1902",
    "hs 1902":                 "1902",
    "hs 1902i":                "1902",
    "hs1902":                  "1902",
    "hs1902i":                 "1902",
    "plotter 1902":            "1902",
    "plotter 1902i":           "1902",

    # ── 1904 ──────────────────────────────────────────────────────────────────
    "1904":                    "1904",
    "1904i":                   "1904",
    "dg 1904":                 "1904",
    "dg 1904i":                "1904",
    "dg1904":                  "1904",
    "dg1904i":                 "1904",
    "plotter 1904":            "1904",
    "plotter 1904i":           "1904",
    "quatro cabeças":          "1904",
    "4 cabecas":               "1904",

    # ── 1908 ──────────────────────────────────────────────────────────────────
    "1908":                    "1908",
    "1908i":                   "1908",
    "dg 1908":                 "1908",
    "dg 1908i":                "1908",
    "dg1908":                  "1908",
    "dg1908i":                 "1908",
    "plotter 1908":            "1908",
    "plotter 1908i":           "1908",
    "oito cabeças":            "1908",
    "8 cabecas":               "1908",

    # ── 3202 ──────────────────────────────────────────────────────────────────
    "3202":                    "3202",
    "3202i":                   "3202",
    "dg 3202":                 "3202",
    "dg 3202i":                "3202",
    "dg3202":                  "3202",
    "dg3202i":                 "3202",
    "plotter 3202":            "3202",
    "plotter 3202i":           "3202",

    # ── 3204 ──────────────────────────────────────────────────────────────────
    "3204":                    "3204",
    "3204i":                   "3204",
    "dg 3204":                 "3204",
    "dg 3204i":                "3204",
    "dg3204":                  "3204",
    "dg3204i":                 "3204",
    "plotter 3204":            "3204",
    "plotter 3204i":           "3204",

    # ── 3003 / DTF UV ─────────────────────────────────────────────────────────
    "3003":                    "3003",
    "3003i":                   "3003",
    "dg 3003":                 "3003",
    "dg 3003i":                "3003",
    "dg3003":                  "3003",
    "dg3003i":                 "3003",
    "dtf uv":                  "3003",
    "dtf uv 30":               "3003",
    "dtf uv 3003":             "3003",
    "uv 3003":                 "3003",

    # ── DTF 30 / 3002 ─────────────────────────────────────────────────────────
    "dtf 30":                  "dtf30",
    "dtf30":                   "dtf30",
    "dtf 3002":                "dtf30",
    "dtf3002":                 "dtf30",
    "3002":                    "dtf30",
    "3002i":                   "dtf30",
    "dg 3002":                 "dtf30",
    "dg 3002i":                "dtf30",
    "dg3002":                  "dtf30",
    "dg3002i":                 "dtf30",
    "3002 dtf":                "dtf30",
    "impressora dtf 30":       "dtf30",
    "dtf têxtil 30":           "dtf30",
    "dtf textil 30":           "dtf30",
    "dtf têxtil 3002":         "dtf30",
    "dtf textil 3002":         "dtf30",
    "dtf 30cm":                "dtf30",

    # ── DTF 60 / 6002 ─────────────────────────────────────────────────────────
    "dtf 60":                  "dtf60",
    "dtf60":                   "dtf60",
    "dtf 6002":                "dtf60",
    "dtf6002":                 "dtf60",
    "6002":                    "dtf60",
    "6002i":                   "dtf60",
    "dg 6002":                 "dtf60",
    "dg 6002i":                "dtf60",
    "dg6002":                  "dtf60",
    "dg6002i":                 "dtf60",
    "6002 dtf":                "dtf60",
    "impressora dtf 60":       "dtf60",
    "dtf têxtil 60":           "dtf60",
    "dtf textil 60":           "dtf60",
    "dtf têxtil 6002":         "dtf60",
    "dtf textil 6002":         "dtf60",
    "aj 6002":                 "dtf60",
    "aj6002":                  "dtf60",
    "dtf 60cm":                "dtf60",

    # ── Jinka 1351 ────────────────────────────────────────────────────────────
    "jinka":                   "jinka1351",
    "jinka 1351":              "jinka1351",
    "jinka1351":               "jinka1351",
    "jinka abj":               "jinka1351",
    "abj 1351":                "jinka1351",
    "plotter corte":           "jinka1351",
    "plotter de corte":        "jinka1351",
    "recorte":                 "jinka1351",
    "plotter recorte":         "jinka1351",
    "dg 1351":                 "jinka1351",
    "dg1351":                  "jinka1351",
}

# Catálogo final
MEDIA_CATALOG = {alias: _PRODUCTS[product] for alias, product in _ALIASES.items()}


def find_media_for_message(text: str) -> dict | None:
    if not text:
        return None
    text_lower = text.lower()
    sorted_keys = sorted(MEDIA_CATALOG.keys(), key=len, reverse=True)
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, text_lower):
            return MEDIA_CATALOG[key]
    return None


def find_media_key_for_message(text: str):
    if not text:
        return None
    text_lower = text.lower()
    sorted_keys = sorted(MEDIA_CATALOG.keys(), key=len, reverse=True)
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, text_lower):
            return key, MEDIA_CATALOG[key]
    return None
