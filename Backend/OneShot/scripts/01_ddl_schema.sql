-- ==========================================
-- CLEANUP (Drop tabelle esistenti)
-- ==========================================
DROP TABLE IF EXISTS diet_meal_items CASCADE;
DROP TABLE IF EXISTS diet_meals CASCADE;
DROP TABLE IF EXISTS diet_days CASCADE;
DROP TABLE IF EXISTS diet_plans CASCADE;
DROP TABLE IF EXISTS recipe_ingredients CASCADE;
DROP TABLE IF EXISTS recipes CASCADE;
DROP TABLE IF EXISTS anamnesis_answers CASCADE;
DROP TABLE IF EXISTS anamnesis_questions CASCADE;
-- DROP TABLE IF EXISTS foods CASCADE;
DROP TABLE IF EXISTS food_categories CASCADE;
DROP TABLE IF EXISTS biometrics CASCADE;
DROP TABLE IF EXISTS patients CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- ==========================================
-- 1. UTENTI & PAZIENTI
-- ==========================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role VARCHAR(50) NOT NULL,
    profession VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome VARCHAR(100) NOT NULL,
    cognome VARCHAR(100) NOT NULL,
    data_nascita DATE,
    altezza_cm DECIMAL(5,2), -- Aggiunta virgola mancante
    sesso VARCHAR(10),
    stile_vita VARCHAR(100),
    categoria_energetica_professione VARCHAR(100),
    descrizione_storia TEXT,
    patologie TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 2. BIOMETRIA & COMPOSIZIONE CORPOREA
-- ==========================================
CREATE TABLE biometrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    -- user_id rimosso: è ridondante, si ricava tramite patient_id
    insert_date DATE DEFAULT CURRENT_DATE,
    
    peso_kg DECIMAL(5,2),
    
    circ_vita_cm DECIMAL(5,2),
    circ_fianchi_cm DECIMAL(5,2),
    circ_torace_cm DECIMAL(5,2),
    circ_spalle_cm DECIMAL(5,2),
    circ_collo_cm DECIMAL(5,2),
    circ_polsi_cm DECIMAL(5,2),
    circ_braccia_cm DECIMAL(5,2),
    circ_avambracci_cm DECIMAL(5,2),
    circ_coscia_cm DECIMAL(5,2),
    circ_polpacci_cm DECIMAL(5,2),
    
    pliche_petto_mm DECIMAL(5,2),
    pliche_addome_mm DECIMAL(5,2),
    pliche_sovrailiaca_mm DECIMAL(5,2),
    pliche_tricipite_mm DECIMAL(5,2),
    pliche_sovrascapolare_mm DECIMAL(5,2),
    pliche_coscia_mm DECIMAL(5,2),
    pliche_ascellare_mm DECIMAL(5,2),
    
    bf_percent DECIMAL(5,2),
    algoritmo_bf_usato VARCHAR(50), 
    followed_diet_ID VARCHAR(50)
);

-- ==========================================
-- 3. DATABASE ALIMENTI (Con Categorie e User)
-- ==========================================
CREATE TABLE food_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE foods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE, -- NULL per cibi di sistema, valorizzato per i tuoi
    category_id UUID REFERENCES food_categories(id) ON DELETE SET NULL, -- Serve per escludere le categorie
    item_name VARCHAR(255) NOT NULL,
    
    kcal DECIMAL(6,2),
    carbs_g DECIMAL(6,2),
    fats_g DECIMAL(6,2),
    prots_g DECIMAL(6,2),
    indice_glicemico INT,
    
    vitamina_a_mcg DECIMAL(8,2),
    vitamina_d_mcg DECIMAL(8,2),
    vitamina_e_mg DECIMAL(8,2),
    vitamina_k_mcg DECIMAL(8,2),
    vitamina_c_mg DECIMAL(8,2),
    tiamina_b1_mg DECIMAL(8,2),
    riboflavina_b2_mg DECIMAL(8,2),
    niacina_b3_mg DECIMAL(8,2),
    vitamina_b6_mg DECIMAL(8,2),
    folato_b9_mcg DECIMAL(8,2),
    vitamina_b12_mcg DECIMAL(8,2),
    biotina_b7_mcg DECIMAL(8,2),
    acido_pantotenico_b5_mg DECIMAL(8,2),
    
    calcio_mg DECIMAL(8,2),
    ferro_mg DECIMAL(8,2),
    magnesio_mg DECIMAL(8,2),
    zinco_mg DECIMAL(8,2),
    rame_mg DECIMAL(8,2),
    manganese_mg DECIMAL(8,2),
    selenio_mcg DECIMAL(8,2),
    iodio_mcg DECIMAL(8,2),
    potassio_mg DECIMAL(8,2),
    sodio_mg DECIMAL(8,2),
    omega3_mg DECIMAL(8,2),
    omega6_mg DECIMAL(8,2),
    
    peculiarita_nutrizionale TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);



-- ==========================================
-- 5. ANAMNESI (Questionari Preimpostati)
-- ==========================================
CREATE TABLE anamnesis_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    question_text TEXT NOT NULL,
    question_type VARCHAR(50) DEFAULT 'text', -- es. 'text', 'boolean', 'multiple_choice'
    order_index INT DEFAULT 0
);

CREATE TABLE anamnesis_answers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES anamnesis_questions(id) ON DELETE CASCADE,
    answer_text TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 6. PIANI ALIMENTARI (DIETE GERARCHICHE)
-- ==========================================
CREATE TABLE diet_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    diet_name VARCHAR(255) NOT NULL,
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    descrizione TEXT,
    warnings TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE diet_meal_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    food_id UUID REFERENCES foods(id) ON DELETE SET NULL,
    food_name VARCHAR(255) NOT NULL,
    
    grams DECIMAL(6,2) NOT NULL,
    giorno_settimana INT,
    -- Cache calcolata (opzionale, ma utile per evitare JOIN continui)
    kcal_calculated DECIMAL(6,2),
    prot_calculated DECIMAL(6,2),
    carbs_calculated DECIMAL(6,2),
    fats_calculated DECIMAL(6,2),
    
    CHECK (
        (food_id IS NOT NULL )
    )
);

CREATE TABLE micronutrients_quantities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    minimum_rda_suggested_daily_amount DECIMAL(6,2) NOT NULL,
    maximum_rda_suggested_daily_amount DECIMAL(6,2) NOT NULL,
    unità_misura VARCHAR(10),
    alimenti_principali TEXT, 
    apparati_sistemi_coinvolti TEXT, 
    rischi_sintomi_carenza TEXT, 
    rischi_sintomi_intossicazione TEXT, 
    rapporti_altri_micro TEXT
    
);