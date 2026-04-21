from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import tempfile, shutil, os

from services.ocr_service import extract_text, get_model
from services.llm_service import classify_document
from services.classifier import classify
from services.validator import compute_score


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> Chargement docTR...")
    get_model()
    print(">>> docTR prêt")
    yield

app = FastAPI(
    title="GED OCR Microservice",
    description="OCR intelligent + Classification LLM + Scoring GED V4",
    version="4.0",
    lifespan=lifespan
)

ALLOWED_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.jfif']


# ─────────────────────────────────────────────
# TEMP FILE
# ─────────────────────────────────────────────
def _save_temp(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        return tmp.name


# ─────────────────────────────────────────────
# VALIDATION STATUS
# ─────────────────────────────────────────────
def _get_validation_status(score_global, ocr_score, validator_result):

    # OCR trop faible → rejet immédiat
    if ocr_score < 0.5:
        return {
            "statut":  "REJETE",
            "action":  "CORRECTION_MANUELLE",
            "couleur": "rouge",
            "message": "OCR trop faible — document non fiable"
        }

    # Lignes manquantes → rejet
    if "no_lines_extracted" in validator_result["flags"]:
        return {
            "statut":  "REJETE",
            "action":  "CORRECTION_MANUELLE",
            "couleur": "rouge",
            "message": "Aucune ligne extraite — correction obligatoire"
        }

    # Champs obligatoires incomplets → vérification manuelle
    champs_manquants = [f for f in validator_result["flags"] if f.startswith("manquant:")]
    if champs_manquants:
        return {
            "statut":  "EN_ATTENTE",
            "action":  "VERIFICATION_REQUISE",
            "couleur": "orange",
            "message": "Champs manquants détectés — vérification requise"
        }

    # Score normal
    if score_global >= 0.90:
        return {
            "statut":  "VALIDE",
            "action":  "VALIDATION_AUTO",
            "couleur": "vert",
            "message": "Document validé automatiquement"
        }
    elif score_global >= 0.70:
        return {
            "statut":  "EN_ATTENTE",
            "action":  "VERIFICATION_REQUISE",
            "couleur": "orange",
            "message": "Vérification manuelle recommandée"
        }
    else:
        return {
            "statut":  "REJETE",
            "action":  "CORRECTION_MANUELLE",
            "couleur": "rouge",
            "message": "Score insuffisant — correction obligatoire"
        }


# ─────────────────────────────────────────────
# SMART PENALTY SYSTEM
# ─────────────────────────────────────────────

# Champs critiques — pénalité forte si manquants
CHAMPS_CRITIQUES = {
    "customerName",
    "vendorName",
    "invoiceDate",
    "orderDate",
    "creditMemoDate",
    "documentDate",
    "totalAmountIncludingTax",
}

# Champs optionnels — pénalité légère si manquants
CHAMPS_OPTIONNELS_PENALITE = {
    "dueDate",
    "currencyCode",
    "discountAmount",
}


def _apply_penalty(score_global, validator_result):
    penalty = 0.0

    manquants = [
        f.replace("manquant:", "")
        for f in validator_result["flags"]
        if f.startswith("manquant:")
    ]

    missing_critiques = 0

    for champ in manquants:
        if champ in CHAMPS_CRITIQUES:
            penalty           += 0.10
            missing_critiques += 1
        elif champ in CHAMPS_OPTIONNELS_PENALITE:
            penalty += 0.03

    # Hard stop — 2 champs critiques manquants → score forcé à 0.40
    if missing_critiques >= 2:
        return 0.40

    # Lignes très faibles → score forcé à 0.35
    if validator_result["breakdown"]["lines_score"] < 0.5:
        return 0.35

    return round(max(0.0, min(1.0, score_global - penalty)), 4)


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
@app.post("/upload", summary="Pipeline complet OCR + LLM + Scoring")
async def upload_file(file: UploadFile = File(...)):

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=400, content={
            "succes": False,
            "erreur": f"Format non accepté : {ext}"
        })

    tmp_path = _save_temp(file)

    try:
        # ── OCR ──
        ocr = extract_text(tmp_path)
        if not ocr["is_readable"]:
            return JSONResponse(status_code=422, content={
                "succes":    False,
                "erreur":    "Document illisible ou vide",
                "score_ocr": ocr["score_ocr"]
            })

        # ── LLM ──
        llm = classify_document(ocr["clean_text"])
        if not llm.get("succes"):
            return JSONResponse(status_code=500, content={
                "succes": False,
                "erreur": llm.get("erreur")
            })

        classification       = llm["classification"]
        metadata             = classification["metadata"]
        bc_fields            = classification.get("bc_fields", {})
        bc_lines             = classification.get("bc_lines", [])
        type_document        = metadata.get("type_document", "Autre")
        score_classification = float(metadata.get("score_classification", 0.5))

        # ── SCORING ──
        validator_result = compute_score(
            type_document        = type_document,
            bc_fields            = bc_fields,
            bc_lines             = bc_lines,
            raw_text             = ocr["clean_text"],
            score_ocr            = ocr["score_ocr"],
            score_classification = score_classification,
        )

        # ── PENALTY ──
        score_global = _apply_penalty(
            validator_result["score_global"],
            validator_result
        )

        # ── VALIDATION STATUS ──
        validation = _get_validation_status(
            score_global,
            ocr["score_ocr"],
            validator_result
        )

        # ── WARNINGS ──
        warnings = []
        if validator_result["breakdown"]["lines_score"] < 1.0:
            warnings.append("Certaines lignes sont incomplètes")
        if validator_result["breakdown"]["coherence_montants"] < 0.7:
            warnings.append("Incohérence détectée dans les montants")
        if any("montant_absent_ocr" in f for f in validator_result["flags"]):
            warnings.append("Montant extrait absent du texte OCR — possible hallucination")

        # ── RÉPONSE ──
        return JSONResponse(content={
            "succes":  True,
            "fichier": file.filename,

            "metadata": {
                "type_document": metadata.get("type_document"),
                "bc_entity":     metadata.get("bc_entity"),
                "categorie":     metadata.get("categorie"),
                "langue":        metadata.get("langue"),
                "pays":          metadata.get("pays"),
                "resume":        metadata.get("resume"),
            },

            "business_central": {
                "bc_fields": bc_fields,
                "bc_lines":  bc_lines,
            },

            "ocr": {
                "texte_extrait": ocr["clean_text"],
                "methode":       ocr["method"],
                "score_ocr":     ocr["score_ocr"],
                "nb_mots":       ocr["nb_words"],
            },

            "scores": {
                "score_ocr":            ocr["score_ocr"],
                "score_classification": round(score_classification, 4),
                "score_extraction":     round(metadata.get("score_extraction", 0.5), 4),
                "score_global":         score_global,
                "score_pourcent":       f"{round(score_global * 100, 2)}%",
                "breakdown":            validator_result["breakdown"],
            },

            "validation": validation,
            "flags":      validator_result["flags"],
            "warnings":   warnings,
        })

    finally:
        os.remove(tmp_path)


# ─────────────────────────────────────────────
# TEST CLASSIFICATION ONLY
# ─────────────────────────────────────────────
@app.post("/test-classify", summary="OCR + Classification uniquement")
async def test_classify(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=400, content={
            "succes": False,
            "erreur": f"Format non accepté : {ext}"
        })

    tmp_path = _save_temp(file)
    try:
        ocr = extract_text(tmp_path)
        if not ocr["is_readable"]:
            return JSONResponse(status_code=422, content={
                "succes":    False,
                "erreur":    "Document illisible",
                "score_ocr": ocr["score_ocr"]
            })

        classif = classify(ocr["clean_text"])
        return JSONResponse(content={
            "succes":         True,
            "fichier":        file.filename,
            "ocr":            ocr,
            "classification": classif["data"]
        })
    finally:
        os.remove(tmp_path)


# ─────────────────────────────────────────────
# HEALTH / ROOT
# ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status":             "ok",
        "version":            "4.0",
        "llm_classification": "Groq llama-3.3-70b-versatile",
        "llm_extraction":     "Gemini 2.5 Flash",
        "ocr":                "docTR + pdfplumber",
        "scoring":            "Validator déterministe + Penalty System V4"
    }


@app.get("/")
def root():
    return {
        "service":  "GED OCR Microservice",
        "version":  "4.0",
        "endpoints": {
            "POST /upload":        "Pipeline complet OCR + LLM + Scoring",
            "POST /test-classify": "OCR + Classification uniquement",
            "GET  /health":        "Health check",
            "GET  /docs":          "Swagger UI"
        },
        "formats_supportes": ALLOWED_EXTENSIONS
    }