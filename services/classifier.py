# LLM 1 : Groq/Llama — Classification + metadata
import re
import json
from groq import Groq
from config.settings import settings

client = Groq(api_key=settings.GROQ_API_KEY)

SYSTEM = """Tu es un classificateur de documents commerciaux.
Retourne UNIQUEMENT un objet JSON valide, sans texte avant ou après, sans markdown.

RÈGLES :
- Sois honnête avec le score
- Détecte automatiquement la langue et le pays du document
- Si tu n'es pas sûr du type → mets "Autre"
RÈGLES RÉSUMÉ :
- Tu dois générer un résumé fidèle et concis de TOUT le contenu du document.
- Le résumé doit couvrir les informations principales : type de document, parties impliquées (client/fournisseur), dates, montants, articles, conditions de paiement, adresses.
- Le résumé doit être en 3 à 5 phrases maximum, pas une simple phrase.
- Tu n’inventes rien : si une information est absente, tu ne la mentionnes pas.


"""

PROMPT = """Analyse ce texte et identifie le type du document.

Types possibles :
- "Facture Achat"     → bc_entity: "purchaseInvoice"
- "Facture Vente"     → bc_entity: "salesInvoice"
- "Avoir Achat"       → bc_entity: "purchaseCreditMemo"
- "Commande Achat"    → bc_entity: "purchaseOrder"
- "Commande Vente"    → bc_entity: "salesOrder"
- "Devis"             → bc_entity: "salesQuote"
- "Autre"             → bc_entity: null

SCORE CLASSIFICATION :
1.0 = Certain (mot-clé clair : FACTURE, INVOICE, BON DE COMMANDE, DEVIS...)
0.8 = Très probable (plusieurs indices concordants)
0.6 = Probable (quelques indices)
0.4 = Incertain
0.2 = Très incertain

Retourne UNIQUEMENT ce JSON :
{{
  "type_document": "<type>",
  "bc_entity": "<entité BC ou null>",
  "categorie": "<Comptabilite | Achats | Ventes | Contrats | Autre>",
  "langue": "<fr | ar | en | mixte>",
  "pays": "<code pays ex: TN, FR, US ou null>",
  "resume": "<description courte 1-2 phrases>",
  "score_classification": <0.0 à 1.0>,
}}

TEXTE :
---
{texte}
---"""


def classify(texte: str) -> dict:
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": PROMPT.format(texte=texte[:3000])}
            ],
            temperature=0.1,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw).strip()
        result = json.loads(raw)

        # Normaliser score
        if "score_classification" in result:
            result["score_classification"] = round(
                max(0.0, min(1.0, float(result["score_classification"]))), 4
            )

        return {"succes": True, "data": result}

    except Exception as e:
        return {"succes": False, "erreur": str(e), "raw": raw if "raw" in dir() else ""}


