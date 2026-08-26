from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect

# ==========================================
# FOOD CATEGORIES CRUD
# ==========================================

def create_food_category(conf, name: str) -> dict:
    """
    Crea una nuova categoria di cibi.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO food_categories (name) VALUES (%s) RETURNING id, name;",
                (name,)
            )
            category = cur.fetchone()
            conn.commit()
            return category
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)

def get_food_category_by_id(conf, category_id: str) -> dict:
    """
    Recupera una categoria di cibi tramite ID.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name FROM food_categories WHERE id = %s;",
                (category_id,)
            )
            return cur.fetchone()
    finally:
        disconnect(conn)

def get_all_food_categories(conf) -> list:
    """
    Recupera tutte le categorie di cibi.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM food_categories ORDER BY name;")
            return cur.fetchall()
    finally:
        disconnect(conn)

def update_food_category(conf, category_id: str, name: str) -> dict:
    """
    Aggiorna una categoria di cibi esistente.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE food_categories SET name = %s WHERE id = %s RETURNING id, name;",
                (name, category_id)
            )
            category = cur.fetchone()
            conn.commit()
            return category
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)

def delete_food_category(conf, category_id: str) -> bool:
    """
    Elimina una categoria di cibi.
    """
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM food_categories WHERE id = %s;", (category_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)


# ==========================================
# FOODS CRUD
# ==========================================

def create_food(conf, food_data: dict) -> dict:
    """
    Crea un nuovo alimento (sistema o utente).
    food_data deve contenere i campi desiderati (es. user_id, category_id, item_name, kcal, ecc.).
    """
    conn = connect(conf)
    try:
        fields = list(food_data.keys())
        values = list(food_data.values())
        placeholders = ["%s"] * len(fields)
        
        cols_str = ", ".join(fields)
        placeholders_str = ", ".join(placeholders)
        
        query = f"INSERT INTO foods ({cols_str}) VALUES ({placeholders_str}) RETURNING *;"
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            new_food = cur.fetchone()
            conn.commit()
            return new_food
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        disconnect(conn)

def get_all_foods(conf) -> list:
    """
    Recupera tutti gli alimenti presenti nel database, ordinati per nome.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM foods ORDER BY item_name;")
            return cur.fetchall()
    finally:
        disconnect(conn)

def get_food_by_id(conf, food_id: str) -> dict:
    """
    Recupera un alimento tramite ID.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM foods WHERE id = %s;", (food_id,))
            return cur.fetchone()
    finally:
        disconnect(conn)

def get_foods_by_user(conf, user_id: str = None) -> list:
    """
    Recupera gli alimenti di sistema (user_id IS NULL) oppure specifici di un utente, o entrambi a seconda della logica.
    Qui restituisce i cibi dell'utente specifico oppure i cibi di sistema (user_id IS NULL).
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute(
                    "SELECT * FROM foods WHERE user_id = %s OR user_id IS NULL ORDER BY item_name;",
                    (user_id,)
                )
            else:
                cur.execute("SELECT * FROM foods WHERE user_id IS NULL ORDER BY item_name;")
            return cur.fetchall()
    finally:
        disconnect(conn)

def update_food(conf, food_id: str, food_data: dict) -> dict:
    """
    Aggiorna un alimento esistente con i campi passati in food_data.
    Gestisce automaticamente l'aggiornamento del campo updated_at se presente nella tabella.
    """
    conn = connect(conf)
    try:
        set_clauses = []
        values = []
        for k, v in food_data.items():
            set_clauses.append(f"{k} = %s")
            values.append(v)
        
        # Aggiungiamo updated_at se non esplicitamente passato
        if "updated_at" not in food_data:
            set_clauses.append("updated_at = CURRENT_TIMESTAMP")
            
        values.append(food_id)
        set_str = ", ".join(set_clauses)
        
        query = f"UPDATE foods SET {set_str} WHERE id = %s RETURNING *;"
        
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

def delete_food(conf, food_id: str) -> bool:
    """
    Elimina un alimento.
    """
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM foods WHERE id = %s;", (food_id,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        conn.rollback()
        raise e
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
