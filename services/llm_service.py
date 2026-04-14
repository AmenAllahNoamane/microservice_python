# Pipeline final : Groq (classification) + Gemini (extraction)
from services.classifier import classify
#from services.extractor import extract
#from services.extractor2 import extract
from services.extractor3 import extract
def classify_document(texte: str) -> dict:
    """
    Pipeline 2 LLM :
    1. Groq/Llama  → metadata complet (type, bc_entity, langue, pays, resume, score_classification)
    2. Gemini      → bc_fields + bc_lines + score_extraction
    """

    #ÉTAPE 1 : Classification 
    step1 = classify(texte)
    if not step1.get("succes"):
        return {"succes": False, "erreur": f"Classification échouée : {step1.get('erreur')}"}

    meta = step1["data"]
    type_document        = meta.get("type_document", "Autre")
    bc_entity            = meta.get("bc_entity")
    categorie            = meta.get("categorie", "Autre")
    langue               = meta.get("langue", "fr")
    pays                 = meta.get("pays")
    resume               = meta.get("resume", "")
    score_classification = float(meta.get("score_classification", 0.5))

    # ÉTAPE 2 : Extraction 
    step2 = extract(type_document, texte)
    if not step2.get("succes"):
        return {"succes": False, "erreur": f"Extraction échouée : {step2.get('erreur')}"}

    extraction           = step2["data"]
    analyse              = extraction.get("analyse_preliminaire")
    bc_fields            = extraction.get("bc_fields", {})
    bc_lines             = extraction.get("bc_lines", [])
    score_extraction     = float(extraction.get("score_extraction", 0.5))

    #  Score global (calculé côté Python) 
    champs_non_null = sum(
        1 for v in bc_fields.values()
        if v is not None and v != [] and v != [None, None]
    )
    total_champs      = len(bc_fields) if bc_fields else 1
    score_remplissage = champs_non_null / total_champs

    score_global = round(
        (0.4 * score_extraction) +
        (0.3 * score_classification) +
        (0.3 * score_remplissage),
        4
    )

    # ── Résultat final (structure identique à gimi.py) ──
    return {
        "succes": True,
        "classification": {
            "metadata": {
                "type_document":       type_document,
                "bc_entity":           bc_entity,
                "categorie":           categorie,
                "langue":              langue,
                "pays":                pays,
                "resume":              resume,
                "score_classification": round(score_classification, 4),
                "score_extraction":    round(score_extraction, 4),
                "score_global":        score_global,
                "champs_remplis":      champs_non_null,
                "champs_total":        total_champs,
            },
            "analyse":analyse,
            "bc_fields": bc_fields,
            "bc_lines":  bc_lines,
        }
    }