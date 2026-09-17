import os, json, base64, mimetypes, re
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from dateutil.relativedelta import relativedelta

AI_PROVIDER = os.getenv("AI_PROVIDER", "demo").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip()

def _demo_invoice_items():
    return [
        {"name": "Panadol 500mg", "generic_name": "Paracetamol", "category": "Analgesic",
         "barcode": "8964000111111", "batch_number": "PK-7861",
         "expiry_date": (date.today() + relativedelta(months=18)).isoformat(),
         "quantity": 50, "purchase_price": 45.0, "sale_price": 60.0, "confidence": 0.96},
        {"name": "Augmentin 625mg", "generic_name": "Co-Amoxiclav", "category": "Antibiotic",
         "barcode": "8964000222222", "batch_number": "AUG-4471",
         "expiry_date": (date.today() + relativedelta(months=5)).isoformat(),
         "quantity": 20, "purchase_price": 250.0, "sale_price": 320.0, "confidence": 0.91}
    ]

def _demo_medicine():
    return {"name": "Panadol 500mg", "generic_name": "Paracetamol", "category": "Analgesic",
            "barcode": "8964000111111", "batch_number": "PK-7861",
            "expiry_date": (date.today() + relativedelta(months=18)).isoformat(),
            "quantity": 1, "purchase_price": 45.0, "sale_price": 60.0, "confidence": 0.92}

def parse_invoice_file(file_path):
    if AI_PROVIDER == "openai" and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            mime, _ = mimetypes.guess_type(file_path)
            response = client.chat.completions.create(
                model=OPENAI_MODEL, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Read this pharmacy invoice image. Extract all medicine items. Return JSON with array of objects having: name, generic_name, category, barcode, batch_number, expiry_date (YYYY-MM-DD), quantity, purchase_price, sale_price, confidence."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{encoded}"}}
                ]}]
            )
            content = response.choices[0].message.content
            result = json.loads(re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE))
            return {"ai_mode": "openai", "items": result.get("items", []), "warnings": [], "error": None}
        except Exception as e:
            return {"ai_mode": "error", "items": [], "warnings": [], "error": str(e)}
    return {"ai_mode": "demo", "items": _demo_invoice_items(),
            "warnings": ["Demo AI mode active. Set AI_PROVIDER=openai for real parsing."], "error": None}

def parse_medicine_photo(file_path):
    if AI_PROVIDER == "openai" and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            mime, _ = mimetypes.guess_type(file_path)
            response = client.chat.completions.create(
                model=OPENAI_MODEL, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Read this medicine packaging photo. Extract: name, generic_name, category, barcode, batch_number, expiry_date (YYYY-MM-DD), quantity, purchase_price, sale_price, confidence. Return JSON only."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{encoded}"}}
                ]}]
            )
            content = response.choices[0].message.content
            result = json.loads(re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE))
            result["ai_mode"] = "openai"
            result["error"] = None
            return result
        except Exception as e:
            return {"ai_mode": "error", "error": str(e)}
    demo = _demo_medicine()
    demo["ai_mode"] = "demo"
    demo["error"] = None
    return demo
