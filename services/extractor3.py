import re
import json
from google import genai
from google.genai import types
from config.settings import settings
 
client = genai.Client(api_key=settings.GEMINI_API_KEY)

# 1. Ajout d'une consigne pour forcer l'analyse préliminaire
SYSTEM = """Tu es un expert en GED intégrée à Microsoft Dynamics 365 Business Central.
Analyse le texte OCR d'un document commercial et retourne UNIQUEMENT un objet JSON valide.

RÈGLES ABSOLUES :
1. Tu n'inventes RIEN — si un champ n'est pas dans le texte, tu mets null.
2. Les dates doivent être retournées STRICTEMENT au format YYYY-MM-DD..
3. Extrais TOUTES les lignes d'articles présentes.
4. Détecte automatiquement la devise du document en code ISO (EUR, USD, TND...).
5. Un numéro de document (commande, facture...) n'est JAMAIS un vendorNumber ou customerNumber.

RÈGLE CRITIQUE : DIFFÉRENCIER CLIENT ET FOURNISSEUR
- FOURNISSEUR (Vendeur) : L'entreprise qui émet le document (en haut, avec logo, téléphone, SIRET).
- CLIENT (Acheteur) : Le destinataire (souvent sous "Facturé à", "Client", "Bill To").

STRATÉGIE OCR DÉSORDONNÉ :
- Remplis TOUJOURS le champ "analyse_preliminaire" en premier. Explique brièvement qui est le client, qui est le fournisseur, et comment tu as identifié les colonnes du tableau. Cela t'aidera à ne pas te tromper pour la suite.
"""


# ── Templates d'extraction par type ──────────────────────

TEMPLATES = {

    "Facture Achat": {
        "bc_entity": "purchaseInvoice",
        "prompt": """Extrais les champs d'une Facture Achat (purchaseInvoice BC).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "vendorInvoiceNumber": "N° facture EXACT ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur EXACT ou null",
    "invoiceDate": "YYYY-MM-DD ou null",
    "dueDate": "YYYY-MM-DD ou null",
    "currencyCode": "TND|EUR|USD|... ou null",
    "totalAmountExcludingTax":0.0,
    "totalTaxAmount":0.0,
    "totalAmountIncludingTax":0.0,
    "discountAmount": 0.0
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": 0.0,
      "unitPrice": 0.0,
      "discountPercent": 0.0,
      "taxPercent":  0.0,
      "lineAmountExcludingTax": 0.0,
      "lineAmountIncludingTax":  0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Facture Vente": {
        "bc_entity": "salesInvoice",
        "prompt": """Extrais les champs d'une Facture Vente (salesInvoice BC).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "externalDocumentNumber": "N° document externe ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom EXACT du client ou null",
    "invoiceDate": "YYYY-MM-DD ou null",
    "dueDate": "YYYY-MM-DD ou null",
    "currencyCode": "TND|EUR|USD|... ou null",
    "totalAmountExcludingTax": 0.0,
    "totalTaxAmount":  0.0,
    "totalAmountIncludingTax": 0.0,
    "discountAmount": 0.0
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity":  0.0,
      "unitPrice": 0.0,
      "discountPercent": 0.0,
      "taxPercent": 0.0,
      "lineAmountExcludingTax": 0.0,
      "lineAmountIncludingTax": 0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Avoir Achat": {
        "bc_entity": "purchaseCreditMemo",
        "prompt": """Extrais les champs d'un Avoir Achat (purchaseCreditMemo BC ).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "vendorCreditMemoNumber": "N° avoir EXACT ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur EXACT ou null",
    "creditMemoDate": "YYYY-MM-DD ou null",
    "invoiceNumber": "N° facture origine ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": 0.0,
    "totalTaxAmount": 0.0,
    "totalAmountIncludingTax": 0.0
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity":0.0,
      "unitPrice": 0.0,
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax":0.0,
      "lineAmountIncludingTax":0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Commande Achat": {
        "bc_entity": "purchaseOrder",
        "prompt": """Extrais les champs d'une Commande Achat (purchaseOrder BC ).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "externalDocumentNumber": "N° commande ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur ou null",
    "orderDate": "YYYY-MM-DD ou null",
    "requestedReceiptDate": "Date livraison souhaitée YYYY-MM-DD ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": 0.0,
    "totalTaxAmount":0.0,
    "totalAmountIncludingTax": 0.0,
    "discountAmount": 0.0,
    "shipToAddressLine1": "Adresse livraison ou null",
    "shipToCity": "Ville livraison ou null",
    "shipToCountry": "Pays livraison ou null",
    "buyFromAddressLine1": "Adresse fournisseur ou null",
    "buyFromCity": "Ville fournisseur ou null",
    "buyFromCountry": "Pays fournisseur ou null"
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity":0.0,
      "unitPrice": 0.0,
      "discountPercent":0.0,
      "taxPercent": 0.0,
      "lineAmountExcludingTax": 0.0,
      "lineAmountIncludingTax": 0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Commande Vente": {
        "bc_entity": "salesOrder",
        "prompt": """Extrais les champs d'une Commande Vente (salesOrder BC).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "externalDocumentNumber": "N° commande ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom client ou null",
    "orderDate": "YYYY-MM-DD ou null",
    "requestedDeliveryDate": "Date livraison YYYY-MM-DD ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax":0.0,
    "totalTaxAmount": 0.0,
    "totalAmountIncludingTax":0.0,
    "discountAmount": 0.0,
    "shipToName": "Livraison à ou null",
    "shipToAddressLine1": "Adresse livraison ou null",
    "shipToCity": "Ville livraison ou null",
    "shipToCountry": "Pays livraison ou null"
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": 0.0,
      "unitPrice": 0.0,
      "discountPercent":0.0,
      "taxPercent": 0.0,
      "lineAmountExcludingTax":0.0,
      "lineAmountIncludingTax": 0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Devis": {
        "bc_entity": "salesQuote",
        "prompt": """Extrais les champs d'un Devis (salesQuote BC ).

Retourne ce JSON :
{"analyse_preliminaire": "Texte court expliquant qui est le fournisseur, qui est le client, et l'état du tableau.",
{
  "bc_fields": {{
    "documentNumber": "N° devis ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom client ou null",
    "documentDate": "YYYY-MM-DD ou null",
    "validUntilDate": "Date validité YYYY-MM-DD ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": 0.0,
    "totalTaxAmount": 0.0,
    "totalAmountIncludingTax":0.0,
    "salesperson": "Commercial ou null",
    "sellToAddressLine1": "Adresse ou null",
    "sellToCity": "Ville ou null",
    "sellToCountry": "Pays ou null"
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": 0.0,
      "unitPrice": 0.0,
      "discountPercent":0.0,
      "taxPercent": 0.0,
      "lineAmountExcludingTax":0.0,
      "lineAmountIncludingTax": 0.0
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Autre": {
        "bc_entity": None,
        "prompt": """Extrais une description libre du document.

Retourne ce JSON :
{{
  "bc_fields": {{
    "description_libre": "Résumé du contenu"
  }},
  "bc_lines": [],
  "score_extraction": <0.0 à 1.0>
}}"""
    }
}


def extract(type_document: str, texte: str) -> dict:
    template = TEMPLATES.get(type_document, TEMPLATES.get("Facture Achat")) # Fallback par défaut

    prompt_complet = f"""{template['prompt']}

TEXTE DU DOCUMENT :
---
{texte[:7000]}
---"""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt_complet,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.0, # 0.0 est CRUCIAL pour l'extraction de données (réduit les hallucinations)
                max_output_tokens=8192,
                response_mime_type="application/json" # GÉRERA LES ERREURS 500 (Force un JSON valide)
            )
        )
        
        # Plus besoin de Regex ! response.text est garanti d'être un JSON textuel
        raw = response.text.strip()
        result = json.loads(raw)
        #print(result)

        # Normaliser score_extraction
        if "score_extraction" in result and result["score_extraction"] is not None:
            try:
                result["score_extraction"] = round(
                    max(0.0, min(1.0, float(result["score_extraction"]))), 4
                )
            except ValueError:
                result["score_extraction"] = 0.5

        return {"succes": True, "data": result}

    except json.JSONDecodeError as e:
        return {"succes": False, "erreur": f"Erreur de parsing JSON: {str(e)}", "raw": response.text if response else ""}
    except Exception as e:
        return {"succes": False, "erreur": str(e)}