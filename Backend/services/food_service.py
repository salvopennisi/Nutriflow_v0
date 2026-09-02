import csv
import io
import json
from decimal import Decimal, InvalidOperation

from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect


# Campi modificabili del catalogo alimenti nell'MVP.
# user_id e' intenzionalmente escluso: la separazione dei cibi per utente
# e' una feature futura e non viene applicata in questa versione.
FOOD_WRITABLE_FIELDS = (
    "category_id",
    "item_name",
    "kcal",
    "carbs_g",
    "fats_g",
    "prots_g",
    "indice_glicemico",
    "vitamina_a_mcg",
    "vitamina_d_mcg",
    "vitamina_e_mg",
    "vitamina_k_mcg",
    "vitamina_c_mg",
    "tiamina_b1_mg",
    "riboflavina_b2_mg",
    "niacina_b3_mg",
    "vitamina_b6_mg",
    "folato_b9_mcg",
    "vitamina_b12_mcg",
    "biotina_b7_mcg",
    "acido_pantotenico_b5_mg",
    "calcio_mg",
    "ferro_mg",
    "magnesio_mg",
    "zinco_mg",
    "rame_mg",
    "manganese_mg",
    "selenio_mcg",
    "iodio_mcg",
    "potassio_mg",
    "sodio_mg",
    "omega3_mg",
    "omega6_mg",
    "peculiarita_nutrizionale",
)

FOOD_NUMERIC_FIELDS = {
    "kcal",
    "carbs_g",
    "fats_g",
    "prots_g",
    "vitamina_a_mcg",
    "vitamina_d_mcg",
    "vitamina_e_mg",
    "vitamina_k_mcg",
    "vitamina_c_mg",
    "tiamina_b1_mg",
    "riboflavina_b2_mg",
    "niacina_b3_mg",
    "vitamina_b6_mg",
    "folato_b9_mcg",
    "vitamina_b12_mcg",
    "biotina_b7_mcg",
    "acido_pantotenico_b5_mg",
    "calcio_mg",
    "ferro_mg",
    "magnesio_mg",
    "zinco_mg",
    "rame_mg",
    "manganese_mg",
    "selenio_mcg",
    "iodio_mcg",
    "potassio_mg",
    "sodio_mg",
    "omega3_mg",
    "omega6_mg",
}

FOOD_IMPORT_ALIASES = {
    "item (100 gr)": "item_name",
    "item": "item_name",
    "nome": "item_name",
    "nome alimento": "item_name",
    "alimento": "item_name",
    "cibo": "item_name",
    "kcal": "kcal",
    "carbs": "carbs_g",
    "fats": "fats_g",
    "prots": "prots_g",
    "indice glicemico (ig)": "indice_glicemico",
    "indice glicemico": "indice_glicemico",
    "vitamina a (µg)": "vitamina_a_mcg",
    "vitamina d (µg)": "vitamina_d_mcg",
    "vitamina e (mg)": "vitamina_e_mg",
    "vitamina k (µg)": "vitamina_k_mcg",
    "vitamina c (mg)": "vitamina_c_mg",
    "tiamina (b1) (mg)": "tiamina_b1_mg",
    "riboflavina (b2) (mg)": "riboflavina_b2_mg",
    "niacina (b3) (mg)": "niacina_b3_mg",
    "vitamina b6 (mg)": "vitamina_b6_mg",
    "folato (b9) (µg)": "folato_b9_mcg",
    "vitamina b12 (µg)": "vitamina_b12_mcg",
    "biotina (b7) (µg)": "biotina_b7_mcg",
    "acido pantotenico (b5) (mg)": "acido_pantotenico_b5_mg",
    "calcio (mg)": "calcio_mg",
    "ferro (mg)": "ferro_mg",
    "magnesio (mg)": "magnesio_mg",
    "zinco (mg)": "zinco_mg",
    "rame (mg)": "rame_mg",
    "manganese (mg)": "manganese_mg",
    "selenio (µg)": "selenio_mcg",
    "iodio (µg)": "iodio_mcg",
    "potassio (mg)": "potassio_mg",
    "sodio (mg)": "sodio_mg",
    "omega3 (mg)": "omega3_mg",
    "omega6 (mg)": "omega6_mg",
    "peculiarità nutrizionale": "peculiarita_nutrizionale",
    "peculiarita nutrizionale": "peculiarita_nutrizionale",
}


def _normalize_header(value: str) -> str:
    return str(value or "").strip().lower().replace("μ", "µ")


def _canonical_import_key(header: str):
    normalized = _normalize_header(header)
    if normalized in FOOD_WRITABLE_FIELDS:
        return normalized
    return FOOD_IMPORT_ALIASES.get(normalized)


def _to_decimal(value, field_name: str):
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip().replace(" ", "")
    if not text:
        return None

    # Supporta sia 12,5 sia 1.234,5 / 1,234.5.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name}: valore numerico non valido '{value}'") from exc


def normalize_food_payload(food_data: dict, require_name: bool = True) -> dict:
    """Valida e normalizza un payload alimento usando solo campi consentiti."""
    if not isinstance(food_data, dict):
        raise ValueError("Il payload dell'alimento deve essere un oggetto/dizionario.")

    normalized = {}
    for field in FOOD_WRITABLE_FIELDS:
        if field not in food_data:
            continue

        value = food_data[field]
        if isinstance(value, str):
            value = value.strip()
        if value == "":
            value = None

        if field == "item_name":
            normalized[field] = str(value).strip() if value is not None else None
        elif field == "indice_glicemico":
            dec_value = _to_decimal(value, field)
            if dec_value is None:
                normalized[field] = None
            elif dec_value != dec_value.to_integral_value():
                raise ValueError("indice_glicemico deve essere un numero intero.")
            else:
                normalized[field] = int(dec_value)
        elif field in FOOD_NUMERIC_FIELDS:
            normalized[field] = _to_decimal(value, field)
        else:
            normalized[field] = value

    if require_name and not normalized.get("item_name"):
        raise ValueError("item_name e' obbligatorio.")

    return normalized


def parse_food_import(file_name: str, raw_bytes: bytes):
    """
    Converte un CSV/JSON in payload alimenti pronti per l'import.

    Ritorna (rows, ignored_columns, errors). Sono accettati sia i nomi campo
    del DB sia le intestazioni del CSV legacy incluso nel progetto.
    """
    if not raw_bytes:
        return [], [], ["Il file e' vuoto."]

    name = (file_name or "").lower()
    raw_rows = []

    try:
        if name.endswith(".json"):
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
            if isinstance(payload, dict):
                for key in ("foods", "items", "data"):
                    if isinstance(payload.get(key), list):
                        payload = payload[key]
                        break
            if not isinstance(payload, list):
                raise ValueError(
                    "Il JSON deve contenere una lista di alimenti oppure un oggetto con chiave foods/items/data."
                )
            raw_rows = payload
        elif name.endswith(".csv"):
            text = raw_bytes.decode("utf-8-sig")
            sample = text[:8192]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            except csv.Error:
                delimiter = "\t" if "\t" in sample else ";" if ";" in sample else ","
                reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            raw_rows = list(reader)
        else:
            return [], [], ["Formato non supportato: carica un file .csv o .json."]
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [], [], [str(exc)]

    rows = []
    ignored_columns = set()
    errors = []

    for row_number, raw_row in enumerate(raw_rows, start=2 if name.endswith(".csv") else 1):
        if not isinstance(raw_row, dict):
            errors.append(f"Riga {row_number}: ogni elemento deve essere un oggetto chiave/valore.")
            continue

        mapped = {}
        for raw_key, raw_value in raw_row.items():
            if raw_key is None or not str(raw_key).strip():
                continue
            canonical_key = _canonical_import_key(raw_key)
            if canonical_key:
                mapped[canonical_key] = raw_value
            elif raw_value not in (None, ""):
                ignored_columns.add(str(raw_key))

        try:
            rows.append(normalize_food_payload(mapped, require_name=True))
        except ValueError as exc:
            errors.append(f"Riga {row_number}: {exc}")

    return rows, sorted(ignored_columns), errors


# ==========================================
# FOOD CATEGORIES CRUD
# ==========================================


def create_food_category(conf, name: str) -> dict:
    """Crea una nuova categoria di cibi."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO food_categories (name) VALUES (%s) RETURNING id, name;",
                (name,),
            )
            category = cur.fetchone()
            conn.commit()
            return category
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def get_food_category_by_id(conf, category_id: str) -> dict:
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM food_categories WHERE id = %s;", (category_id,))
            return cur.fetchone()
    finally:
        disconnect(conn)


def get_all_food_categories(conf) -> list:
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM food_categories ORDER BY name;")
            return cur.fetchall()
    finally:
        disconnect(conn)


def update_food_category(conf, category_id: str, name: str) -> dict:
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE food_categories SET name = %s WHERE id = %s RETURNING id, name;",
                (name, category_id),
            )
            category = cur.fetchone()
            conn.commit()
            return category
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def delete_food_category(conf, category_id: str) -> bool:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM food_categories WHERE id = %s;", (category_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


# ==========================================
# FOODS CRUD - catalogo condiviso MVP
# ==========================================


def create_food(conf, food_data: dict) -> dict:
    """Crea un alimento nel catalogo condiviso dell'MVP."""
    payload = normalize_food_payload(food_data, require_name=True)
    conn = connect(conf)
    try:
        fields = list(payload.keys())
        values = list(payload.values())
        placeholders = ", ".join(["%s"] * len(fields))
        query = f"INSERT INTO foods ({', '.join(fields)}) VALUES ({placeholders}) RETURNING *;"

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            new_food = cur.fetchone()
            conn.commit()
            return new_food
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def get_all_foods(conf) -> list:
    """Recupera l'intero catalogo condiviso, ordinato per nome."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM foods ORDER BY item_name;")
            return cur.fetchall()
    finally:
        disconnect(conn)


def get_foods_for_diet_editor(conf) -> list:
    """Recupera il catalogo minimo necessario all'editor delle diete."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, item_name, kcal, carbs_g, fats_g, prots_g
                FROM foods
                ORDER BY item_name;
                """
            )
            return cur.fetchall()
    finally:
        disconnect(conn)


def get_food_by_id(conf, food_id: str) -> dict:
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM foods WHERE id = %s;", (food_id,))
            return cur.fetchone()
    finally:
        disconnect(conn)


def get_foods_by_user(conf, user_id: str = None) -> list:
    """
    Compatibilita' temporanea con il codice precedente.

    Nell'MVP il catalogo non e' separato per utente, quindi user_id viene
    deliberatamente ignorato e viene restituito l'intero catalogo.
    """
    return get_all_foods(conf)


def update_food(conf, food_id: str, food_data: dict) -> dict:
    """Aggiorna i campi consentiti di un alimento esistente."""
    payload = normalize_food_payload(food_data, require_name=False)
    if not payload:
        return get_food_by_id(conf, food_id)

    conn = connect(conf)
    try:
        set_clauses = [f"{field} = %s" for field in payload]
        values = list(payload.values())
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        values.append(food_id)

        query = f"UPDATE foods SET {', '.join(set_clauses)} WHERE id = %s RETURNING *;"
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            updated = cur.fetchone()
            conn.commit()
            return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def delete_food(conf, food_id: str) -> bool:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM foods WHERE id = %s;", (food_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def bulk_import_foods(conf, foods: list, duplicate_policy: str = "skip") -> dict:
    """
    Importa alimenti in un'unica transazione.

    duplicate_policy:
      - "skip": mantiene il record gia' esistente con lo stesso item_name;
      - "update": aggiorna il primo record esistente con lo stesso item_name.
    """
    if duplicate_policy not in {"skip", "update"}:
        raise ValueError("duplicate_policy deve essere 'skip' oppure 'update'.")

    payloads = [normalize_food_payload(row, require_name=True) for row in foods]
    if not payloads:
        return {"inserted": 0, "updated": 0, "skipped": 0, "total": 0}

    conn = connect(conf)
    inserted = 0
    updated = 0
    skipped = 0

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for payload in payloads:
                cur.execute(
                    """
                    SELECT id
                    FROM foods
                    WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(%s))
                    ORDER BY created_at NULLS LAST, id
                    LIMIT 1;
                    """,
                    (payload["item_name"],),
                )
                existing = cur.fetchone()

                if existing and duplicate_policy == "skip":
                    skipped += 1
                    continue

                if existing:
                    set_clauses = [f"{field} = %s" for field in payload]
                    values = list(payload.values())
                    set_clauses.append("updated_at = CURRENT_TIMESTAMP")
                    values.append(existing["id"])
                    cur.execute(
                        f"UPDATE foods SET {', '.join(set_clauses)} WHERE id = %s;",
                        values,
                    )
                    updated += 1
                else:
                    fields = list(payload.keys())
                    values = list(payload.values())
                    placeholders = ", ".join(["%s"] * len(fields))
                    cur.execute(
                        f"INSERT INTO foods ({', '.join(fields)}) VALUES ({placeholders});",
                        values,
                    )
                    inserted += 1

        conn.commit()
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "total": len(payloads),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


# ==========================================
# MICRONUTRIENTS QUANTITIES CRUD
# ==========================================

def create_micronutrient(conf, micro_data: dict) -> dict:
    """
    Crea un record di quantità/RDA di micronutrienti.
    """
    conn = connect(conf)
    try:
        fields = list(micro_data.keys())
        values = list(micro_data.values())
        placeholders = ["%s"] * len(fields)
        
        cols_str = ", ".join(fields)
        placeholders_str = ", ".join(placeholders)
        
        query = f"INSERT INTO micronutrients_quantities ({cols_str}) VALUES ({placeholders_str}) RETURNING *;"
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            new_micro = cur.fetchone()
            conn.commit()
            return new_micro
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)

def get_micronutrient_by_id(conf, micro_id: str) -> dict:
    """
    Recupera un record di micronutriente tramite ID.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM micronutrients_quantities WHERE id = %s;", (micro_id,))
            return cur.fetchone()
    finally:
        disconnect(conn)

def get_all_micronutrients(conf) -> list:
    """
    Recupera tutti i record di micronutrienti.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM micronutrients_quantities;")
            return cur.fetchall()
    finally:
        disconnect(conn)

def update_micronutrient(conf, micro_id: str, micro_data: dict) -> dict:
    """
    Aggiorna un record di micronutriente esistente.
    """
    conn = connect(conf)
    try:
        set_clauses = []
        values = []
        for k, v in micro_data.items():
            set_clauses.append(f"{k} = %s")
            values.append(v)
            
        values.append(micro_id)
        set_str = ", ".join(set_clauses)
        
        query = f"UPDATE micronutrients_quantities SET {set_str} WHERE id = %s RETURNING *;"
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            updated = cur.fetchone()
            conn.commit()
            return updated
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)

def delete_micronutrient(conf, micro_id: str) -> bool:
    """
    Elimina un record di micronutriente.
    """
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM micronutrients_quantities WHERE id = %s;", (micro_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)
