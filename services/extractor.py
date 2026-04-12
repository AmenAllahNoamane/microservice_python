import re
import json
from google import genai
from google.genai import types
from config.settings import settings
 
client = genai.Client(api_key=settings.GEMINI_API_KEY)

SYSTEM = """Tu es un expert en GED intégrée à Microsoft Dynamics 365 Business Central.
Analyse le texte OCR d'un document commercial et retourne UNIQUEMENT un objet JSON valide, sans texte avant ou après.

RÈGLES ABSOLUES :
1. Tu n'inventes RIEN — si un champ n'est pas dans le texte, tu mets null
2. Les scores doivent être honnêtes selon ce que tu as réellement trouvé
3. Les dates doivent être au format JJ/MM/AAAA
4. Extrais TOUTES les lignes d'articles présentes dans le document
5. Détecte automatiquement la devise du document en code ISO (EUR, USD, TND...)
6.- Un numéro de document (commande, facture...) n'est JAMAIS un vendorNumber ou customerNumber

RÈGLES MONTANTS :
- Tu n'inventes jamais de montant
- Tu copies les montants EXACTEMENT comme ils apparaissent dans le document
- Tu convertis les formats locaux (ex: 1.000,00 ou 1,000.00) vers une forme universelle avec point comme séparateur décimal (ex: 1000.00)
- Les montants doivent être des nombres décimaux, pas des chaînes
- Tu ne recalcules rien : si seul le TTC est présent, mets HT et TVA à null
- Tu sépares HT, TVA et TTC si présents, sinon mets null
- Le taux de TVA doit être un nombre décimal (ex: 19.0)
- Les remises et taxes additionnelles doivent être extraites si présentes, sinon null
- VAT/TVA → taxPercent | Discount/remise → discountAmount (ne jamais confondre les deux)

TABLEAUX OCR DÉSORDONNÉS :
Les colonnes peuvent être mélangées. Stratégie :
1. Identifier les en-têtes de colonnes même séparés
2. Regrouper les données par ligne logique
3. Utiliser les NOMBRES comme indices (quantité petite, prix moyen, total grand)
4. Ne PAS abandonner si le texte est confus
RÈGLE CRITIQUE : DIFFÉRENCIER CLIENT ET FOURNISSEUR
Tu dois distinguer STRICTEMENT le client (customer) et le fournisseur (vendor/seller).
- Le FOURNISSEUR est l’entreprise qui émet le document (devis, facture…)
- Le CLIENT est le destinataire du document
RÈGLES DE DÉTECTION :
1. FOURNISSEUR (seller / vendor) :
- Peut contenir :
  - nom de société en majuscules
  - téléphone, email, site web
- Peut apparaître avec un contact (ex: commercial)
2. CLIENT (customer) :
- Souvent dans un bloc adresse (nom + rue + code postal + ville + pays)
- Peut être précédé de mots comme :
  - "Client"
  - "Bill To"
  - "Facturé à"
- Ne contient généralement PAS de téléphone ou informations commerciales
"""

# ── Templates d'extraction par type ──────────────────────

TEMPLATES = {

    "Facture Achat": {
        "bc_entity": "purchaseInvoice",
        "prompt": """Extrais les champs d'une Facture Achat (purchaseInvoice BC).

Retourne ce JSON :
{{
  "bc_fields": {{
    "vendorInvoiceNumber": "N° facture EXACT ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur EXACT ou null",
    "invoiceDate": "JJ/MM/AAAA ou null",
    "dueDate": "JJ/MM/AAAA ou null",
    "currencyCode": "TND|EUR|USD|... ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>,
    "discountAmount": <decimal ou null>
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Facture Vente": {
        "bc_entity": "salesInvoice",
        "prompt": """Extrais les champs d'une Facture Vente (salesInvoice BC).

Retourne ce JSON :
{{
  "bc_fields": {{
    "externalDocumentNumber": "N° document externe ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom EXACT du client ou null",
    "invoiceDate": "JJ/MM/AAAA ou null",
    "dueDate": "JJ/MM/AAAA ou null",
    "currencyCode": "TND|EUR|USD|... ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>,
    "discountAmount": <decimal ou null>
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Avoir Achat": {
        "bc_entity": "purchaseCreditMemo",
        "prompt": """Extrais les champs d'un Avoir Achat (purchaseCreditMemo BC ).

Retourne ce JSON :
{{
  "bc_fields": {{
    "vendorCreditMemoNumber": "N° avoir EXACT ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur EXACT ou null",
    "creditMemoDate": "JJ/MM/AAAA ou null",
    "invoiceNumber": "N° facture origine ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>
  }},
  "bc_lines": [
    {{
      "sequence": 1,
      "itemId":"L'identifiant de l'article ou null.",
      "description": "Description EXACTE",
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Commande Achat": {
        "bc_entity": "purchaseOrder",
        "prompt": """Extrais les champs d'une Commande Achat (purchaseOrder BC ).

Retourne ce JSON :
{{
  "bc_fields": {{
    "externalDocumentNumber": "N° commande ou null",
    "vendorNumber": "N° fournisseur ou null",
    "vendorName": "Nom fournisseur ou null",
    "orderDate": "JJ/MM/AAAA ou null",
    "requestedReceiptDate": "Date livraison souhaitée JJ/MM/AAAA ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>,
    "discountAmount": <decimal ou null>,
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
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Commande Vente": {
        "bc_entity": "salesOrder",
        "prompt": """Extrais les champs d'une Commande Vente (salesOrder BC).

Retourne ce JSON :
{{
  "bc_fields": {{
    "externalDocumentNumber": "N° commande ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom client ou null",
    "orderDate": "JJ/MM/AAAA ou null",
    "requestedDeliveryDate": "Date livraison JJ/MM/AAAA ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>,
    "discountAmount": <decimal ou null>,
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
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
    }}
  ],
  "score_extraction": <0.0 à 1.0>
}}"""
    },

    "Devis": {
        "bc_entity": "salesQuote",
        "prompt": """Extrais les champs d'un Devis (salesQuote BC ).

Retourne ce JSON :
{{
  "bc_fields": {{
    "documentNumber": "N° devis ou null",
    "customerNumber": "N° client ou null",
    "customerName": "Nom client ou null",
    "documentDate": "JJ/MM/AAAA ou null",
    "validUntilDate": "Date validité JJ/MM/AAAA ou null",
    "currencyCode": "TND|EUR|USD ou null",
    "totalAmountExcludingTax": <decimal ou null>,
    "totalTaxAmount": <decimal ou null>,
    "totalAmountIncludingTax": <decimal ou null>,
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
      "quantity": <decimal>,
      "unitPrice": <decimal>,
      "discountPercent":<decimal>
      "taxPercent": <decimal ex: 19.0>,
      "lineAmountExcludingTax": <decimal ou null>,
      "lineAmountIncludingTax": <decimal ou null>
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
    template = TEMPLATES.get(type_document, TEMPLATES["Autre"])

    prompt_complet = f"""{template['prompt']}

TEXTE DU DOCUMENT :
---
{texte[:5000]}
---"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_complet,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.1,
                max_output_tokens=8192,
            )
        )
        raw = response.text.strip()
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw).strip()
        result = json.loads(raw)

        # Normaliser score_extraction
        if "score_extraction" in result:
            result["score_extraction"] = round(
                max(0.0, min(1.0, float(result["score_extraction"]))), 4
            )

        return {"succes": True, "data": result}

    except Exception as e:
        return {"succes": False, "erreur": str(e), "raw": raw if "raw" in dir() else ""}


