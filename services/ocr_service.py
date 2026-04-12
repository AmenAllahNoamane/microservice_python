import os
import re
import fitz
import pdfplumber
from doctr.io import DocumentFile
from doctr.models import ocr_predictor
from utils.text_cleaner import clean_text, fix_dates


_model = None
 
def get_model():
    global _model
    if _model is None:
        _model = ocr_predictor(pretrained=True)
    return _model


# Détection PDF natif 
def is_pdf_native(file_path):

    doc = fitz.open(file_path)
    text = ""
    has_images = False

    for page in doc:
        text += page.get_text("text")

        if len(page.get_images()) > 0:
            has_images = True

    nb_words = len(text.split())

    if nb_words >= 20:
        return True
    elif has_images:
        return False
    else:
        return False


# pdfplumber (PDF natif)
def extract_text_pdf(file_path):
    full_text = ""

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or ""
            full_text += page_text + "\n"

    nb_words = len(full_text.split())
    score_ocr = 1.0 if nb_words > 0 else 0.0

    return full_text.strip(), score_ocr, nb_words

#  docTR (PDF scanné / image) 
def extract_text_doctr(file_path):

   
    ext = os.path.splitext(file_path)[1].lower()
    model = get_model()
    if ext == ".pdf":
        doc = DocumentFile.from_pdf(file_path)
    else:
        doc = DocumentFile.from_images(file_path)

    result = model(doc)

    full_text = ""
    all_scores = []

    for page in result.pages:
        for block in page.blocks:
            for line in block.lines:
                line_words = []
                for word in line.words:
                    line_words.append(word.value)
                    all_scores.append(word.confidence)
                full_text += " ".join(line_words) + "\n"

    score_ocr = sum(all_scores) / len(all_scores) if all_scores else 0.0
    nb_words = len(full_text.split())

    return full_text.strip(), round(score_ocr, 4), nb_words

# Pipeline principal 


def extract_text(file_path):
    """
    Pipeline d'extraction intelligent :
    - Images → OCR docTR
    - PDF natif → pdfplumber
    - PDF scanné → OCR docTR
    """
    ext = os.path.splitext(file_path)[1].lower()

    # IMAGE → OCR
    if ext in [".jpg", ".jpeg", ".png", ".jfif"]:
        print(" Image détectée => OCR docTR")
        method = "doctr"
        raw, score_ocr, nb_words = extract_text_doctr(file_path)
        

    # PDF
    if ext == ".pdf":
        if is_pdf_native(file_path):
            print(" PDF natif détecté => pdfplumber")
            method = "pdfplumber"
            raw, score_ocr, nb_words = extract_text_pdf(file_path)
        else:
            print(" PDF scanné détecté => OCR docTR")
            method = "doctr"
            raw, score_ocr, nb_words = extract_text_doctr(file_path)
    else:
        raise ValueError(f"Format non supporté : {ext}")
    

    #  Nettoyage
    clean = clean_text(raw)
    clean = fix_dates(clean)

    return {
        "raw_text": raw,
        "clean_text": clean,
        "score_ocr": score_ocr,
        "nb_words": nb_words,
        "method": method,
        "is_readable": len(clean) >= 10
    }


