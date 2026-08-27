import logging
from decimal import Decimal
from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect


def _normalize_item_for_db(item: dict) -> dict:
    """Converte il modello usato dalla UI nelle colonne persistite in diet_meal_items."""
    return {
        "food_id": item.get("food_id"),
        "giorno_settimana": item.get("giorno_settimana"),
        "meal_type": item.get("meal_type"),
        "food_name": item.get("food_name"),
        "grams": item.get("grams", 0),
        "kcal_calculated": item.get("kcal_calculated", item.get("kcal", 0)),
        "prot_calculated": item.get("prot_calculated", item.get("prot", 0)),
        "carbs_calculated": item.get("carbs_calculated", item.get("carbs", 0)),
        "fats_calculated": item.get("fats_calculated", item.get("fats", 0)),
    }


def _insert_diet_items(cur, diet_id, items_data: list) -> None:
    """Inserisce gli item di un piano usando un set di colonne esplicito e stabile."""
    query = """
        INSERT INTO diet_meal_items (
            diet_plan_id, food_id, giorno_settimana, meal_type, food_name, grams,
            kcal_calculated, prot_calculated, carbs_calculated, fats_calculated
        )
        VALUES (
            %(diet_plan_id)s, %(food_id)s, %(giorno_settimana)s, %(meal_type)s, %(food_name)s, %(grams)s,
            %(kcal_calculated)s, %(prot_calculated)s, %(carbs_calculated)s, %(fats_calculated)s
        );
    """
    for raw_item in items_data:
        item = _normalize_item_for_db(raw_item)
        item["diet_plan_id"] = diet_id
        if not item.get("food_id"):
            raise ValueError(
                f"food_id mancante per l'alimento '{item.get('food_name')}'. "
                "L'alimento deve essere presente nel catalogo foods prima del salvataggio."
            )
        cur.execute(query, item)


def get_diet_plans(conf, patient_id: str) -> list:
    """Recupera i piani alimentari di un assistito raggruppando i relativi item."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    dp.id,
                    dp.diet_name,
                    dp.descrizione,
                    dp.warnings,
                    dmi.food_id,
                    dmi.giorno_settimana,
                    dmi.meal_type,
                    dmi.food_name,
                    dmi.grams,
                    dmi.kcal_calculated,
                    dmi.prot_calculated,
                    dmi.carbs_calculated,
                    dmi.fats_calculated
                FROM diet_plans dp
                LEFT JOIN diet_meal_items dmi ON dp.id = dmi.diet_plan_id
                WHERE dp.patient_id = %s
                ORDER BY dp.diet_name, dmi.giorno_settimana, dmi.meal_type, dmi.food_name;
                """,
                (patient_id,),
            )
            rows = cur.fetchall()

        plans_by_id = {}
        for row in rows:
            diet_id = row["id"]
            if diet_id not in plans_by_id:
                plans_by_id[diet_id] = {
                    "id": diet_id,
                    "diet_name": row["diet_name"],
                    "descrizione": row.get("descrizione"),
                    "warnings": row.get("warnings"),
                    "items": [],
                }

            # LEFT JOIN: un piano senza item deve comunque essere restituito.
            if row.get("giorno_settimana") is not None:
                plans_by_id[diet_id]["items"].append({
                    "food_id": row["food_id"],
                    "giorno_settimana": row["giorno_settimana"],
                    "meal_type": row["meal_type"],
                    "food_name": row["food_name"],
                    "grams": row["grams"],
                    "kcal_calculated": row["kcal_calculated"],
                    "prot_calculated": row["prot_calculated"],
                    "carbs_calculated": row["carbs_calculated"],
                    "fats_calculated": row["fats_calculated"],
                })

        return list(plans_by_id.values())
    except Exception as e:
        logging.error(f"Errore in get_diet_plans: {e}")
        raise
    finally:
        disconnect(conn)


def diet_name_exists(conf, patient_id: str, diet_name: str, exclude_diet_id=None) -> bool:
    """Verifica l'unicita del nome per assistito, ignorando maiuscole e spazi esterni."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            params = [patient_id, diet_name.strip()]
            query = """
                SELECT 1
                FROM diet_plans
                WHERE patient_id = %s
                  AND LOWER(TRIM(diet_name)) = LOWER(TRIM(%s))
            """
            if exclude_diet_id is not None:
                query += " AND id <> %s"
                params.append(exclude_diet_id)
            query += " LIMIT 1;"
            cur.execute(query, tuple(params))
            return cur.fetchone() is not None
    except Exception as e:
        logging.error(f"Errore nel controllo unicita nome piano: {e}")
        raise
    finally:
        disconnect(conn)


def add_diet_plan(conf, diet_data: dict, items_data: list) -> str:
    """Inserisce un nuovo piano alimentare con i rispettivi elementi nel database."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO diet_plans (diet_name, patient_id, user_id, descrizione, warnings)
                VALUES (%(diet_name)s, %(patient_id)s, %(user_id)s, %(descrizione)s, %(warnings)s)
                RETURNING id;
                """,
                diet_data,
            )
            diet_id = cur.fetchone()["id"]
            _insert_diet_items(cur, diet_id, items_data)

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} inserito con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in add_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


def update_diet_plan(conf, diet_id, diet_data: dict, items_data: list) -> str:
    """Aggiorna testata e dettaglio di un piano esistente in un'unica transazione."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE diet_plans
                SET diet_name = %(diet_name)s,
                    descrizione = %(descrizione)s,
                    warnings = %(warnings)s
                WHERE id = %(diet_id)s
                  AND patient_id = %(patient_id)s;
                """,
                {
                    **diet_data,
                    "diet_id": diet_id,
                },
            )
            if cur.rowcount != 1:
                raise ValueError("Piano alimentare non trovato per l'assistito selezionato.")

            # Replace atomico del dettaglio: o viene aggiornato tutto, o viene fatto rollback.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))
            _insert_diet_items(cur, diet_id, items_data)

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} aggiornato con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in update_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


def delete_diet_plan(conf, patient_id: str, diet_id) -> None:
    """Elimina un piano alimentare dell'assistito e i relativi item in modo atomico."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            # Verifica ownership prima di eliminare il dettaglio.
            cur.execute(
                """
                SELECT 1
                FROM diet_plans
                WHERE id = %s
                  AND patient_id = %s
                FOR UPDATE;
                """,
                (diet_id, patient_id),
            )
            if cur.fetchone() is None:
                raise ValueError("Piano alimentare non trovato per l'assistito selezionato.")

            # Cancellazione esplicita del dettaglio: non dipende dalla presenza di ON DELETE CASCADE.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))
            cur.execute(
                "DELETE FROM diet_plans WHERE id = %s AND patient_id = %s;",
                (diet_id, patient_id),
            )

            if cur.rowcount != 1:
                raise ValueError("Impossibile eliminare il piano alimentare selezionato.")

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} eliminato con successo.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in delete_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


# Colonne micronutrienti presenti in foods. I valori sono riferiti a 100 g.
# Il collegamento con micronutrients_quantities e esclusivamente logico/applicativo:
# micronutrients_name identifica la riga tipologica, senza FK o relazioni DB.
#
# supported_reference_bases rende esplicito quando il dato presente in foods e
# semanticamente confrontabile con la base scientifica del riferimento. Quando
# la base non e verificabile (es. vitamina A generica vs µg RE/RAE), l'overview
# mostra il valore ma NON genera falsi alert su min/max.
MICRONUTRIENT_DEFINITIONS = {
    "vitamina_a_mcg": {
        "name": "Vitamina A", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_d_mcg": {
        "name": "Vitamina D", "food_unit": "µg", "supported_reference_bases": ("µg", "µg VDE"),
    },
    "vitamina_e_mg": {
        "name": "Vitamina E", "food_unit": "mg", "supported_reference_bases": (),
    },
    "vitamina_k_mcg": {
        "name": "Vitamina K", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_c_mg": {
        "name": "Vitamina C", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "tiamina_b1_mg": {
        "name": "Tiamina B1", "food_unit": "mg", "supported_reference_bases": ("mg/MJ",),
    },
    "riboflavina_b2_mg": {
        "name": "Riboflavina B2", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "niacina_b3_mg": {
        "name": "Niacina B3", "food_unit": "mg", "supported_reference_bases": (),
    },
    "vitamina_b6_mg": {
        "name": "Vitamina B6", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "folato_b9_mcg": {
        "name": "Folato B9", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_b12_mcg": {
        "name": "Vitamina B12", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "biotina_b7_mcg": {
        "name": "Biotina B7", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "acido_pantotenico_b5_mg": {
        "name": "Acido pantotenico B5", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "calcio_mg": {
        "name": "Calcio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "ferro_mg": {
        "name": "Ferro", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "magnesio_mg": {
        "name": "Magnesio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "zinco_mg": {
        "name": "Zinco", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "rame_mg": {
        "name": "Rame", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "manganese_mg": {
        "name": "Manganese", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "selenio_mcg": {
        "name": "Selenio", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "iodio_mcg": {
        "name": "Iodio", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "potassio_mg": {
        "name": "Potassio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "sodio_mg": {
        "name": "Sodio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "omega3_mg": {
        "name": "Omega 3", "food_unit": "mg", "supported_reference_bases": (),
    },
    "omega6_mg": {
        "name": "Omega 6", "food_unit": "mg", "supported_reference_bases": (),
    },
}


def _safe_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _normalize_micronutrient_name(value) -> str:
    """Normalizza il nome della riga tipologica per il lookup applicativo."""
    import re

    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


def _normalize_reference_basis(value) -> str:
    """Normalizza solo la forma testuale dell'unita/base, non effettua conversioni."""
    import re

    normalized = str(value or "").strip().casefold()
    normalized = normalized.replace("μ", "u").replace("µ", "u")
    normalized = normalized.replace("α", "alpha")
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _reference_basis_is_supported(definition: dict, reference_basis: str) -> bool:
    supported = definition.get("supported_reference_bases") or ()
    normalized_reference = _normalize_reference_basis(reference_basis)
    return any(
        normalized_reference == _normalize_reference_basis(candidate)
        for candidate in supported
    )


def _effective_minimum(configured_minimum, reference_type: str, reference_basis: str, daily_energy_mj: Decimal):
    if configured_minimum is None:
        return None

    value = _safe_decimal(configured_minimum)
    ref_type = str(reference_type or "").upper()
    basis = _normalize_reference_basis(reference_basis)

    # EFSA esprime Tiamina e Niacina per MJ di energia introdotta.
    # Il coefficiente configurato viene quindi trasformato nel target effettivo
    # per la dieta corrente usando l'energia media giornaliera calcolata dai foods.
    if ref_type.endswith("_PER_MJ") or basis.endswith("mj"):
        return value * daily_energy_mj

    return value


def _status_for_reference(current: Decimal, minimum, maximum, reference_type: str, maximum_type: str) -> str:
    """Restituisce un codice di stato usato dalla UI per colorare l'intera riga."""
    ref_type = str(reference_type or "").upper()
    max_type = str(maximum_type or "").upper()

    if maximum is not None and current > maximum:
        if max_type == "UL_TOTAL":
            return "HIGH_UL"
        if max_type in {"SAFE_LEVEL", "SAFE_ADEQUATE"}:
            return "HIGH_WARNING"

    if minimum is not None and current < minimum:
        if ref_type in {"PRI", "PRI_PER_MJ"}:
            return "LOW_PRI"
        if ref_type in {"AI", "PRI_ASSUMED"}:
            return "LOW_AI"

    return "OK"


def calculate_diet_micronutrients_overview(conf, items_data: list, days_in_plan: int = 7) -> dict:
    """
    Calcola on-demand l'apporto medio giornaliero dei micronutrienti.

    Regole principali:
    - foods contiene i valori per 100 g;
    - micronutrients_quantities resta una tipologica senza FK verso foods;
    - il confronto viene effettuato solo quando la base del dato foods e
      semanticamente compatibile con reference_basis;
    - i limiti UL riferiti solo a integratori/forme specifiche non vengono
      applicati all'overview di una dieta composta da alimenti;
    - i riferimenti espressi per MJ vengono trasformati usando l'energia media
      giornaliera effettiva della dieta.
    """
    if days_in_plan <= 0:
        raise ValueError("days_in_plan deve essere maggiore di zero.")

    normalized_items = []
    food_ids = set()
    for item in items_data or []:
        food_id = item.get("food_id")
        grams = _safe_decimal(item.get("grams"))
        if not food_id or grams <= 0:
            continue
        food_id_str = str(food_id)
        food_ids.add(food_id_str)
        normalized_items.append((food_id_str, grams))

    if not normalized_items:
        return {
            "days_in_plan": days_in_plan,
            "daily_energy_mj": 0.0,
            "rows": [],
            "missing_rda_names": [definition["name"] for definition in MICRONUTRIENT_DEFINITIONS.values()],
            "missing_food_ids": [],
        }

    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            nutrient_columns = ", ".join(MICRONUTRIENT_DEFINITIONS.keys())
            cur.execute(
                f"""
                SELECT id, kcal, {nutrient_columns}
                FROM foods
                WHERE id = ANY(%s::uuid[]);
                """,
                (list(food_ids),),
            )
            food_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    micronutrients_name,
                    minimum_rda_suggested_daily_amount,
                    maximum_rda_suggested_daily_amount,
                    "unità_misura",
                    reference_type,
                    maximum_type,
                    reference_basis,
                    reference_source
                FROM micronutrients_quantities;
                """
            )
            rda_rows = cur.fetchall()
    finally:
        disconnect(conn)

    foods_by_id = {str(row["id"]): row for row in food_rows}
    rda_by_name = {
        _normalize_micronutrient_name(row.get("micronutrients_name")): row
        for row in rda_rows
        if _normalize_micronutrient_name(row.get("micronutrients_name"))
    }

    weekly_totals = {column_name: Decimal("0") for column_name in MICRONUTRIENT_DEFINITIONS}
    weekly_kcal = Decimal("0")
    missing_food_ids = set()

    for food_id, grams in normalized_items:
        food = foods_by_id.get(food_id)
        if food is None:
            missing_food_ids.add(food_id)
            continue

        ratio = grams / Decimal("100")
        weekly_kcal += _safe_decimal(food.get("kcal")) * ratio
        for column_name in MICRONUTRIENT_DEFINITIONS:
            weekly_totals[column_name] += _safe_decimal(food.get(column_name)) * ratio

    divisor = Decimal(str(days_in_plan))
    daily_energy_kcal = weekly_kcal / divisor
    daily_energy_mj = daily_energy_kcal / Decimal("238.83")

    rows = []
    missing_rda_names = []

    for column_name, definition in MICRONUTRIENT_DEFINITIONS.items():
        micronutrient_name = definition["name"]
        food_unit = definition["food_unit"]
        current = weekly_totals[column_name] / divisor
        rda = rda_by_name.get(_normalize_micronutrient_name(micronutrient_name))

        if rda is None:
            missing_rda_names.append(micronutrient_name)
            rows.append({
                "micronutrient": micronutrient_name,
                "unit": food_unit,
                "current_daily_value": float(current),
                "minimum_rda": None,
                "maximum_rda": None,
                "reference_type": None,
                "maximum_type": None,
                "reference_basis": None,
                "reference_source": None,
                "comparison_status": "NO_REFERENCE",
                "comparison_note": "Riferimento non configurato nella tipologica.",
            })
            continue

        reference_type = str(rda.get("reference_type") or "").upper()
        maximum_type = str(rda.get("maximum_type") or "NONE").upper()
        reference_basis = str(rda.get("reference_basis") or rda.get("unità_misura") or "")
        basis_supported = _reference_basis_is_supported(definition, reference_basis)

        configured_minimum = rda.get("minimum_rda_suggested_daily_amount")
        configured_maximum = rda.get("maximum_rda_suggested_daily_amount")

        if basis_supported:
            minimum = _effective_minimum(
                configured_minimum,
                reference_type,
                reference_basis,
                daily_energy_mj,
            )

            # Un limite superiore viene applicato al cibo solo quando riguarda
            # l'assunzione totale oppure e un safe level / safe adequate target.
            if maximum_type in {"UL_TOTAL", "SAFE_LEVEL", "SAFE_ADEQUATE"}:
                maximum = (
                    _safe_decimal(configured_maximum)
                    if configured_maximum is not None
                    else None
                )
            else:
                maximum = None

            status = _status_for_reference(
                current,
                minimum,
                maximum,
                reference_type,
                maximum_type,
            )
            note = None
            if configured_maximum is not None and maximum is None:
                if maximum_type == "UL_SUPPLEMENT":
                    note = "Limite superiore riferito a integratori/forme aggiunte: non applicato agli alimenti."
                elif maximum_type in {"UL_RESTRICTED", "FORM_DEPENDENT"}:
                    note = "Limite superiore valido solo per forme specifiche: non applicato al dato foods generico."
        else:
            minimum = None
            maximum = None
            status = "NOT_COMPARABLE"
            note = (
                f"Dato foods in {food_unit}; riferimento configurato in {reference_basis}. "
                "La base nutrizionale non e verificata, quindi min/max non vengono confrontati."
            )

        rows.append({
            "micronutrient": micronutrient_name,
            "unit": food_unit,
            "current_daily_value": float(current),
            "minimum_rda": float(minimum) if minimum is not None else None,
            "maximum_rda": float(maximum) if maximum is not None else None,
            "configured_minimum_rda": (
                float(configured_minimum) if configured_minimum is not None else None
            ),
            "configured_maximum_rda": (
                float(configured_maximum) if configured_maximum is not None else None
            ),
            "reference_type": reference_type,
            "maximum_type": maximum_type,
            "reference_basis": reference_basis,
            "reference_source": rda.get("reference_source"),
            "comparison_status": status,
            "comparison_note": note,
        })

    return {
        "days_in_plan": days_in_plan,
        "daily_energy_mj": float(daily_energy_mj),
        "rows": rows,
        "missing_rda_names": missing_rda_names,
        "missing_food_ids": sorted(missing_food_ids),
    }

def calculate_nutrients_proportional(food_obj: dict, grams: Decimal) -> dict:
    """
    Calcola i macronutrienti in modo proporzionale in base ai grammi inseriti,
    partendo dai valori nutrizionali di riferimento per 100g presenti in food_obj.
    """
    ratio = grams / Decimal("100.0")

    def get_safe_decimal(val):
        if val is None:
            return Decimal("0.0")
        return Decimal(str(val))

    kcal_100g = get_safe_decimal(food_obj.get("kcal"))
    carbs_100g = get_safe_decimal(food_obj.get("carbs_g"))
    fats_100g = get_safe_decimal(food_obj.get("fats_g"))
    prots_100g = get_safe_decimal(food_obj.get("prots_g"))

    return {
        "kcal": kcal_100g * ratio,
        "carbs": carbs_100g * ratio,
        "fats": fats_100g * ratio,
        "prot": prots_100g * ratio,
    }
