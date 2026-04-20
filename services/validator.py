import re
from datetime import datetime


# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

# Champs obligatoires par type de document
CHAMPS_OBLIGATOIRES = {
    "Facture Achat":  ["vendorName",   "invoiceDate",    "totalAmountIncludingTax"],
    "Facture Vente":  ["customerName", "invoiceDate",    "totalAmountIncludingTax"],
    "Avoir Achat":    ["vendorName",   "creditMemoDate", "totalAmountIncludingTax"],
    "Commande Achat": ["vendorName",   "orderDate",      "totalAmountIncludingTax"],
    "Commande Vente": ["customerName", "orderDate",      "totalAmountIncludingTax"],
    "Devis":          ["customerName", "documentDate",   "totalAmountIncludingTax"],
    "Contrat":        [],
    "Autre":          [],
}

# Champs des lignes obligatoires
LINE_REQUIRED = ["description", "quantity", "unitPrice", "lineAmountIncludingTax"]

# Clés dates par type
DATE_KEYS = {
    "Facture Achat":  ["invoiceDate", "dueDate"],
    "Facture Vente":  ["invoiceDate", "dueDate"],
    "Avoir Achat":    ["creditMemoDate"],
    "Commande Achat": ["orderDate", "requestedReceiptDate"],
    "Commande Vente": ["orderDate", "requestedDeliveryDate"],
    "Devis":          ["documentDate", "validUntilDate"],
}

# Champs optionnels — null acceptable, pas de pénalité
CHAMPS_OPTIONNELS = {
    "customerNumber", "vendorNumber", "externalDocumentNumber",
    "documentNumber", "vendorInvoiceNumber", "vendorCreditMemoNumber",
    "invoiceNumber", "discountAmount", "dueDate", "requestedReceiptDate",
    "requestedDeliveryDate", "validUntilDate", "salesperson",
    "shipToAddressLine1", "shipToCity", "shipToCountry",
    "buyFromAddressLine1", "buyFromCity", "buyFromCountry",
    "shipToName", "sellToAddressLine1", "sellToCity", "sellToCountry",
}


# ─────────────────────────────────────────────
# NORMALISATION
# ─────────────────────────────────────────────

def to_float_safe(value):
    try:
        if isinstance(value, str):
            value = value.replace(" ", "").replace(",", ".").strip()
        return float(value)
    except:
        return None


def is_valid_date(value):
    if not value or not isinstance(value, str):
        return False
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ─────────────────────────────────────────────
# NIVEAU 1 — CHAMPS OBLIGATOIRES
# ─────────────────────────────────────────────

def score_champs_obligatoires(type_document, bc_fields, bc_lines):
    champs = CHAMPS_OBLIGATOIRES.get(type_document, [])
    missing_header = []
    missing_lines  = []

    # Pas de champs obligatoires pour Contrat / Autre
    if not champs:
        return {
            "score_required": 1.0,
            "header_score":   1.0,
            "lines_score":    1.0,
            "missing_header": [],
            "missing_lines":  [],
        }

    # ── Header score ──
    found_header = 0
    for field in champs:
        val = bc_fields.get(field)
        # null ou "" → manquant | 0 ou 0.0 → valide (TVA = 0 hors taxe)
        if val is not None and str(val).strip() not in ("", "null"):
            found_header += 1
        else:
            missing_header.append(field)

    header_score = found_header / len(champs)

    # ── Lines score ──
    if not bc_lines:
        lines_score = 0.0
        missing_lines.append("no_lines_extracted")
    else:
        line_scores = []
        for i, line in enumerate(bc_lines):
            found_line = 0
            for field in LINE_REQUIRED:
                val = line.get(field)
                if val is not None and str(val).strip() not in ("", "null"):
                    found_line += 1
                else:
                    missing_lines.append(f"ligne{i+1}.{field}")
            line_scores.append(found_line / len(LINE_REQUIRED))
        lines_score = sum(line_scores) / len(line_scores)

    # Header plus important que lignes (60 / 40)
    score_required = round(0.60 * header_score + 0.40 * lines_score, 4)

    return {
        "score_required": score_required,
        "header_score":   round(header_score, 4),
        "lines_score":    round(lines_score, 4),
        "missing_header": missing_header,
        "missing_lines":  list(set(missing_lines)),
    }


# ─────────────────────────────────────────────
# NIVEAU 2 — VALIDATION DES DATES
# ─────────────────────────────────────────────

def validate_dates(type_document, bc_fields):
    cles  = DATE_KEYS.get(type_document, [])
    dates = [bc_fields[k] for k in cles if bc_fields.get(k) is not None]

    if not dates:
        return 0.5, ["no_dates_found"]

    valides = sum(1 for d in dates if is_valid_date(d))
    score   = valides / len(dates)
    flags   = [f"date_invalide:{k}={bc_fields[k]}"
               for k in cles
               if bc_fields.get(k) and not is_valid_date(bc_fields[k])]

    return round(score, 4), flags


# ─────────────────────────────────────────────
# NIVEAU 3 — COHÉRENCE HT + TVA = TTC
# ─────────────────────────────────────────────

def validate_amounts(bc_fields):
    ht  = to_float_safe(bc_fields.get("totalAmountExcludingTax"))
    tva = to_float_safe(bc_fields.get("totalTaxAmount"))
    ttc = to_float_safe(bc_fields.get("totalAmountIncludingTax"))

    if ht is None or ttc is None:
        return 0.5, ["montants_insuffisants"]

    if ttc == 0:
        return 0.0, ["ttc_zero"]

    # TVA null → document hors taxe, on suppose 0
    tva_val = tva if tva is not None else 0.0
    ecart   = abs((ht + tva_val) - ttc) / ttc

    if ecart < 0.01:   return 1.0, []
    elif ecart < 0.05: return 0.7, ["ecart_montants_faible"]
    elif ecart < 0.15: return 0.4, ["ecart_montants_moyen"]
    else:              return 0.1, ["incoherence_montants"]


# ─────────────────────────────────────────────
# NIVEAU 4 — SOMME LIGNES vs TOTAL
# ─────────────────────────────────────────────

def check_lignes_vs_total(bc_fields, bc_lines):
    ttc = to_float_safe(bc_fields.get("totalAmountIncludingTax"))

    if not bc_lines or ttc is None or ttc == 0:
        return 0.5, ["pas_de_lignes"]

    somme = sum(to_float_safe(l.get("lineAmountIncludingTax")) or 0 for l in bc_lines)
    ecart = abs(somme - ttc) / ttc

    if ecart < 0.02:   return 1.0, []
    elif ecart < 0.10: return 0.6, ["ecart_lignes_faible"]
    else:              return 0.2, ["incoherence_lignes_total"]


# ─────────────────────────────────────────────
# NIVEAU 5 — CROSS-VALIDATION OCR
# ─────────────────────────────────────────────

def cross_validate(raw_text, bc_fields):
    if not raw_text:
        return 0.5, ["texte_ocr_vide"]

    score    = 1.0
    flags    = []
    raw_norm = re.sub(r"[\s,.]", "", raw_text)

    for key in ["totalAmountIncludingTax", "totalAmountExcludingTax"]:
        val = to_float_safe(bc_fields.get(key))
        if val is None or val == 0:
            continue

        variants = [
            str(int(val)),
            f"{val:.2f}",
            f"{val:.2f}".replace(".", ","),
            f"{val:.0f}",
        ]
        trouve = any(re.sub(r"[\s,.]", "", v) in raw_norm for v in variants)

        if not trouve:
            score -= 0.2
            flags.append(f"montant_absent_ocr:{key}={val}")

    return round(max(score, 0.0), 4), flags


# ─────────────────────────────────────────────
# NIVEAU 6 — TAUX REMPLISSAGE
# ─────────────────────────────────────────────

def check_taux_remplissage(bc_fields):
    if not bc_fields:
        return 0.0, ["bc_fields_vide"]

    # Ne compter que les champs non optionnels
    champs = {k: v for k, v in bc_fields.items() if k not in CHAMPS_OPTIONNELS}

    if not champs:
        return 0.5, []

    total   = len(champs)
    remplis = sum(1 for v in champs.values() if v is not None and v != "")
    score   = remplis / total
    flags   = [] if score > 0.5 else ["taux_remplissage_faible"]

    return round(score, 4), flags


# ─────────────────────────────────────────────
# SCORE FINAL
# ─────────────────────────────────────────────

def compute_score(type_document, bc_fields, bc_lines, raw_text, score_ocr, score_classification):
    flags = []

    # Niveau 1 — champs obligatoires (dominant)
    required = score_champs_obligatoires(type_document, bc_fields, bc_lines)
    flags   += [f"manquant:{f}" for f in required["missing_header"]]
    if "no_lines_extracted" in required["missing_lines"]:
        flags.append("no_lines_extracted")

    # Niveau 2 — dates valides
    s_dates, f2 = validate_dates(type_document, bc_fields)
    flags += f2

    # Niveau 3 — cohérence HT + TVA = TTC
    s_montants, f3 = validate_amounts(bc_fields)
    flags += f3

    # Niveau 4 — somme lignes vs total
    s_lignes, f4 = check_lignes_vs_total(bc_fields, bc_lines)
    flags += f4

    # Niveau 5 — cross-validation OCR
    s_cross, f5 = cross_validate(raw_text, bc_fields)
    flags += f5

    # Niveau 6 — taux remplissage
    s_remplissage, f6 = check_taux_remplissage(bc_fields)
    flags += f6

    # ── Score global pondéré ──
    score_global = round(
        0.30 * required["score_required"] +   # champs obligatoires — dominant
        0.20 * score_ocr                  +   # qualité OCR
        0.15 * score_classification       +   # confiance Groq
        0.15 * s_montants                 +   # cohérence HT+TVA=TTC
        0.10 * s_cross                    +   # montants dans texte OCR
        0.05 * s_dates                    +   # dates valides
        0.05 * s_remplissage,                 # taux remplissage
        4
    )
    score_global = max(0.0, min(1.0, score_global))

    # ── Décision ──
    if score_global >= 0.90:
        decision = "VALIDE"
        action   = "VALIDATION_AUTO"
    elif score_global >= 0.70:
        decision = "EN_ATTENTE"
        action   = "VERIFICATION_REQUISE"
    else:
        decision = "REJETE"
        action   = "CORRECTION_MANUELLE"

    return {
        "score_global":   score_global,
        "score_pourcent": f"{round(score_global * 100, 2)}%",
        "decision":       decision,
        "action":         action,
        "flags":          list(set(flags)),
        "breakdown": {
            "champs_obligatoires":  required["score_required"],
            "header_score":         required["header_score"],
            "lines_score":          required["lines_score"],
            "score_ocr":            round(score_ocr, 4),
            "score_classification": round(score_classification, 4),
            "coherence_montants":   s_montants,
            "validite_dates":       s_dates,
            "cross_validation_ocr": s_cross,
            "taux_remplissage":     s_remplissage,
            "coherence_lignes":     s_lignes,
        }
    }