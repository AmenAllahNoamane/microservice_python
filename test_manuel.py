from services.validator import compute_score

# ── Simuler ce que Gemini extrait de ce document ──
bc_fields = {
    "externalDocumentNumber":  "103032",
    "customerNumber":          None,
    "customerName":            "",
    "invoiceDate":             "2028-01-27",
    "dueDate":                 "2028-01-31",
    "currencyCode":            "USD",
    "totalAmountExcludingTax": 13280.85,
    "totalTaxAmount":          0,        # hors taxe
    "totalAmountIncludingTax": 132,
    "discountAmount":          None,
}

bc_lines = [
    {
        "sequence":               1,
        "itemId":                 "1000",
        "description":            "Bicycle",
        "quantity":               2,
        "unitPrice":              6165.00,
        "discountPercent":        0,
        "taxPercent":             0,
        "lineAmountExcludingTax": 330.00,
        "lineAmountIncludingTax": 330.00,
    },
    {
        "sequence":               2,
        "itemId":                 "1896-S",
        "description":            "ATHENS Desk",
        "quantity":               1,
        "unitPrice":              1000.888,
        "discountPercent":        -5,
        "taxPercent":             0,
        "lineAmountExcludingTax": 950.85,
        "lineAmountIncludingTax": 950.85,
    },
]

# ── Texte OCR brut ──
raw_text = """
Invoice 103032
CRONUS International Ltd. New Concepts Furniture
Ms. Tammy L. McDonald
705 West Peachtree Street Atlanta GA US
Document Date: January 27, 2028   Due Date: January 31, 2028
1000   Bicycle       2  Piece  6,165.00   0  12,330.00
1896-S ATHENS Desk   1  Piece  1,000.888 -5%  0  950.85
Total $ 13,280.85
"""

# ── Score OCR et classification simulés ──
score_ocr            = 0.95   # OCR lisible
score_classification = 0.90   # Groq très confiant

# ── Lancer le scoring ──
result = compute_score(
    type_document        = "Facture Vente",
    bc_fields            = bc_fields,
    bc_lines             = bc_lines,
    raw_text             = raw_text,
    score_ocr            = score_ocr,
    score_classification = score_classification,
)
# ── Simuler _apply_penalty() de main.py ──
CHAMPS_CRITIQUES = {
    "customerName", "vendorName", "invoiceDate",
    "orderDate", "creditMemoDate", "documentDate",
    "totalAmountIncludingTax",
}
CHAMPS_OPTIONNELS_PENALITE = {"dueDate", "currencyCode", "discountAmount"}

# ── Simuler _apply_penalty() de main.py ──
CHAMPS_CRITIQUES = {
    "customerName", "vendorName", "invoiceDate",
    "orderDate", "creditMemoDate", "documentDate",
    "totalAmountIncludingTax",
}
CHAMPS_OPTIONNELS_PENALITE = {"dueDate", "currencyCode", "discountAmount"}

def apply_penalty(score_global, validator_result):
    penalty = 0.0
    manquants = [
        f.replace("manquant:", "")
        for f in validator_result["flags"]
        if f.startswith("manquant:")
    ]
    missing_critiques = 0
    for champ in manquants:
        if champ in CHAMPS_CRITIQUES:
            penalty += 0.10
            missing_critiques += 1
        elif champ in CHAMPS_OPTIONNELS_PENALITE:
            penalty += 0.03
    if missing_critiques >= 2:
        return 0.40
    if validator_result["breakdown"]["lines_score"] < 0.5:
        return 0.35
    return round(max(0.0, min(1.0, score_global - penalty)), 4)

# Appliquer après compute_score()
score_final = apply_penalty(result["score_global"], result)

print(f"\n{'='*50}")
print(f"  Score validator : {result['score_pourcent']}")
print(f"  Score final     : {round(score_final * 100, 2)}%")
print(f"  Décision        : {'VALIDE' if score_final >= 0.90 else 'EN_ATTENTE' if score_final >= 0.70 else 'REJETE'}")
print(f"  Flags           : {result['flags'] or 'aucun ✓'}")
print(f"\n  Détail :")
for k, v in result["breakdown"].items():
    barre = "█" * int(v * 20) + "░" * (20 - int(v * 20))
    print(f"    {k:<26} {barre}  {round(v*100)}%")
print(f"{'='*50}\n")