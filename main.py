from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import tempfile, shutil, os

from services.ocr_service import extract_text, get_model
from services.llm_service import classify_document
from services.classifier import classify

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> Chargement docTR...")
    get_model()
    print(">>> docTR prêt")
    yield

app = FastAPI(
    title="GED OCR Microservice",
    description="OCR intelligent + Classification LLM (Groq + Gemini) pour Business Central",
    version="3.0",
    lifespan=lifespan
)

ALLOWED_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.jfif']


def _save_temp(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        return tmp.name


def _get_validation_status(score_global: float) -> dict:
    if score_global >= 0.90:
        return {"statut": "VALIDE",      "action": "VALIDATION_AUTO",    "couleur": "vert",   "message": "Document validé automatiquement"}
    elif score_global >= 0.70:
        return {"statut": "EN_ATTENTE",  "action": "VERIFICATION_REQUISE","couleur": "orange", "message": "Vérification manuelle recommandée"}
    else:
        return {"statut": "EN_ATTENTE",  "action": "CORRECTION_MANUELLE", "couleur": "rouge",  "message": "Correction manuelle obligatoire"}


# ── POST /upload — pipeline complet ──────────────────────

@app.post("/upload", summary="Pipeline complet : OCR + Classification + Extraction BC")
async def upload_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=400, content={"succes": False, "erreur": f"Format non accepté : {ext}"})

    tmp_path = _save_temp(file)
    try:
        # OCR
        ocr = extract_text(tmp_path)
        if not ocr["is_readable"]:
            return JSONResponse(status_code=422, content={"succes": False, "erreur": "Document illisible ou vide", "score_ocr": ocr["score_ocr"]})

        # LLM
        llm = classify_document(ocr["clean_text"])
        print(llm)
        if not llm.get("succes"):
            return JSONResponse(status_code=500, content={"succes": False, "erreur": llm.get("erreur")})

        classification   = llm["classification"]
        metadata         = classification["metadata"]
        score_global_llm = metadata["score_global"]

        # Score global final (OCR inclus)
        score_global = round(
            (0.5 * ocr["score_ocr"]) +
            (0.3 * metadata["score_extraction"]) +
            (0.2 * metadata["score_classification"]),
            4
        )
        validation = _get_validation_status(score_global)

        return JSONResponse(content={
            "succes": True,
            "fichier": file.filename,
            "metadata": {
                "type_document":    metadata["type_document"],
                "bc_entity":        metadata["bc_entity"],
                "categorie":        metadata["categorie"],
                "langue":           metadata["langue"],
                "pays":             metadata["pays"],
                "resume":           metadata["resume"],
            },
            "business_central": {
                "bc_fields": classification["bc_fields"],
                "bc_lines":  classification["bc_lines"],
            },
            "ocr": {
                "texte_extrait": ocr["clean_text"],
                "methode":       ocr["method"],
                "score_ocr":     ocr["score_ocr"],
                "nb_mots":       ocr["nb_words"],
            },
            "scores": {
                "score_ocr":            ocr["score_ocr"],
                "score_extraction":     metadata["score_extraction"],
                "score_classification": metadata["score_classification"],
                "score_global":         score_global,
                "score_global_pourcent": f"{round(score_global * 100, 2)}%"
            },
            "validation": validation
        })
    finally:
        os.remove(tmp_path)


# ── POST /test-classify — OCR + classification seule ─────

@app.post("/test-classify", summary="[TEST] OCR + Classification Groq uniquement")
async def test_classify(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=400, content={"succes": False, "erreur": f"Format non accepté : {ext}"})

    tmp_path = _save_temp(file)
    try:
        ocr = extract_text(tmp_path)
        if not ocr["is_readable"]:
            return JSONResponse(status_code=422, content={"succes": False, "erreur": "Document illisible", "score_ocr": ocr["score_ocr"]})

        classif = classify(ocr["clean_text"])
        if not classif.get("succes"):
            return JSONResponse(status_code=500, content={"succes": False, "erreur": classif.get("erreur")})

        return JSONResponse(content={
            "succes": True,
            "fichier": file.filename,
            "ocr": {
                "texte_extrait": ocr["clean_text"],
                "methode":       ocr["method"],
                "score_ocr":     ocr["score_ocr"],
                "nb_mots":       ocr["nb_words"],
            },
            "classification": classif["data"]
        })
    finally:
        os.remove(tmp_path)


# ── GET /health ───────────────────────────────────────────

@app.get("/health", summary="Health check")
def health():
    return {
        "status": "ok",
        "version": "3.0",
        "llm_classification": "Groq llama-3.3-70b-versatile",
        "llm_extraction":     "Gemini 2.5 Flash",
        "ocr":                "docTR + pdfplumber"
    }


# ── GET / ─────────────────────────────────────────────────

@app.get("/", summary="Informations service")
def root():
    return {
        "service": "GED OCR Microservice",
        "version": "3.0",
        "endpoints": {
            "POST /upload":        "Pipeline complet OCR + LLM",
            "POST /test-classify": "OCR + Classification uniquement",
            "GET  /health":        "Health check",
            "GET  /docs":          "Swagger UI"
        },
        "formats_supportes": ALLOWED_EXTENSIONS
    }