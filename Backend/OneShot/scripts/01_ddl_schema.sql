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
-- 3. DATABASE ALIMENTI (Catalogo condiviso MVP; ownership utente futura)
-- ==========================================
CREATE TABLE food_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE foods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE, -- Riservato a futura separazione per nutrizionista; non usato nell'MVP
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
    diet_plan_id UUID REFERENCES diet_plans(id) ON DELETE SET NULL
    food_id UUID REFERENCES foods(id) ON DELETE SET NULL,
    food_name VARCHAR(255) NOT NULL,
    meal_type VARCHAR(15) NOT null,
    grams DECIMAL(6,2) NOT NULL,
    giorno_settimana INT,
    -- Cache calcolata (opzionale, ma utile per evitare JOIN continui)
    kcal_calculated DECIMAL(6,2),
    prot_calculated DECIMAL(6,2),
    carbs_calculated DECIMAL(6,2),
    fats_calculated DECIMAL(6,2),
    
    CHECK (
        (food_id IS NOT NULL and diet_plan_id is not null )
    )
);

CREATE TABLE micronutrients_quantities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    micronutrients_name VARCHAR(100) NOT NULL,
    minimum_rda_suggested_daily_amount DECIMAL(6,2) NOT NULL,
    maximum_rda_suggested_daily_amount DECIMAL(6,2) NOT NULL,
    unità_misura VARCHAR(10),
    alimenti_principali TEXT, 
    apparati_sistemi_coinvolti TEXT, 
    rischi_sintomi_carenza TEXT, 
    rischi_sintomi_intossicazione TEXT, 
    rapporti_altri_micro TEXT
    
);

CREATE TABLE workout_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    workout_name VARCHAR(255) NOT NULL,

    patient_id UUID NOT NULL
        REFERENCES patients(id)
        ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id)
        ON DELETE CASCADE,

    objective VARCHAR(255),
    description TEXT,

    start_date DATE,
    end_date DATE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,

    CONSTRAINT chk_workout_dates
        CHECK (
            end_date IS NULL
            OR start_date IS NULL
            OR end_date >= start_date
        )
);


-- Evita due workout attivi con lo stesso nome
-- per lo stesso paziente.
CREATE UNIQUE INDEX ux_workout_plan_patient_name
ON workout_plans (
    patient_id,
    LOWER(TRIM(workout_name))
)
WHERE archived_at IS NULL;



-- ==========================================
-- 2. WORKOUT EXERCISES
-- ==========================================

CREATE TABLE workout_exercises (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    workout_plan_id UUID NOT NULL
        REFERENCES workout_plans(id)
        ON DELETE CASCADE,

    exercise_name VARCHAR(255) NOT NULL,

    -- Ordinamento generale dell'esercizio nel workout
    exercise_order SMALLINT NOT NULL DEFAULT 0,


    -- ======================================
    -- BLOCCO / ASSOCIAZIONE TRA ESERCIZI
    -- ======================================

    -- Stesso UUID = esercizi appartenenti
    -- allo stesso superset/jump-set/circuito/etc.
    block_id UUID,

    -- Esempi:
    -- STANDARD
    -- SUPERSET
    -- JUMP_SET
    -- TRI_SET
    -- GIANT_SET
    -- CIRCUIT
    block_type VARCHAR(50) NOT NULL DEFAULT 'STANDARD',

    -- Ordine dell'esercizio all'interno del blocco
    block_order SMALLINT,

    -- Numero di round del blocco, utile soprattutto
    -- per circuiti / giant set / strutture ripetute
    block_rounds SMALLINT,


    -- ======================================
    -- PRESCRIZIONE
    -- ======================================

    target_sets SMALLINT,

    target_reps_min SMALLINT,
    target_reps_max SMALLINT,

    target_load_kg DECIMAL(7,2),

    target_tut_seconds INT,

    target_rest_seconds INT,

    target_rir DECIMAL(3,1),

    target_rpe DECIMAL(3,1),


    -- ======================================
    -- TECNICA DI ALLENAMENTO
    -- ======================================

    -- Esempi:
    -- STANDARD
    -- DROP_SET
    -- REST_PAUSE
    -- MYO_REPS
    -- CLUSTER
    -- AMRAP
    -- TEMPO
    -- PAUSE_REPS
    technique VARCHAR(50) NOT NULL DEFAULT 'STANDARD',

    -- Parametri specifici della tecnica.
    --
    -- DROP SET:
    -- {
    --   "drops": 2,
    --   "load_reduction_pct": 20
    -- }
    --
    -- REST PAUSE:
    -- {
    --   "mini_sets": 3,
    --   "rest_seconds": 20
    -- }
    technique_params JSONB NOT NULL DEFAULT '{}'::JSONB,


    -- ======================================
    -- PARAMETRI DI PROGRESSIONE
    -- ======================================

    -- Uno stesso esercizio può essere monitorato
    -- tramite più metriche.
    --
    -- Esempio:
    -- {'LOAD', 'VOLUME'}
    --
    -- oppure:
    -- {'TUT', 'DENSITY'}
    progression_metrics TEXT[]
        NOT NULL
        DEFAULT ARRAY['LOAD']::TEXT[],


    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Utile per non perdere lo storico
    -- delle misurazioni
    archived_at TIMESTAMP,


    -- ======================================
    -- CHECK
    -- ======================================

    CONSTRAINT chk_workout_target_sets
        CHECK (
            target_sets IS NULL
            OR target_sets > 0
        ),

    CONSTRAINT chk_workout_target_reps
        CHECK (
            target_reps_min IS NULL
            OR target_reps_max IS NULL
            OR target_reps_max >= target_reps_min
        ),

    CONSTRAINT chk_workout_target_load
        CHECK (
            target_load_kg IS NULL
            OR target_load_kg >= 0
        ),

    CONSTRAINT chk_workout_target_tut
        CHECK (
            target_tut_seconds IS NULL
            OR target_tut_seconds >= 0
        ),

    CONSTRAINT chk_workout_target_rest
        CHECK (
            target_rest_seconds IS NULL
            OR target_rest_seconds >= 0
        ),

    CONSTRAINT chk_workout_target_rir
        CHECK (
            target_rir IS NULL
            OR target_rir BETWEEN 0 AND 10
        ),

    CONSTRAINT chk_workout_target_rpe
        CHECK (
            target_rpe IS NULL
            OR target_rpe BETWEEN 0 AND 10
        ),

    CONSTRAINT chk_workout_block_rounds
        CHECK (
            block_rounds IS NULL
            OR block_rounds > 0
        ),

    -- Una struttura diversa da STANDARD
    -- deve appartenere a un blocco.
    CONSTRAINT chk_workout_block
        CHECK (
            block_type = 'STANDARD'
            OR block_id IS NOT NULL
        ),

    CONSTRAINT chk_workout_progression_metrics
        CHECK (
            cardinality(progression_metrics) > 0
            AND progression_metrics
                <@ ARRAY[
                    'LOAD',
                    'VOLUME',
                    'TUT',
                    'DENSITY',
                    'REPS'
                ]::TEXT[]
        )
);


CREATE INDEX ix_workout_exercises_plan
ON workout_exercises(workout_plan_id);


CREATE INDEX ix_workout_exercises_block
ON workout_exercises(block_id)
WHERE block_id IS NOT NULL;


CREATE UNIQUE INDEX ux_workout_exercise_order
ON workout_exercises(
    workout_plan_id,
    exercise_order
)
WHERE archived_at IS NULL;



-- ==========================================
-- 3. WORKOUT MEASUREMENTS
-- ==========================================

CREATE TABLE workout_measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    workout_exercise_id UUID NOT NULL
        REFERENCES workout_exercises(id)
        ON DELETE RESTRICT,


    -- Identifica una specifica esecuzione del workout.
    --
    -- Lo stesso execution_id viene utilizzato
    -- per tutte le misurazioni registrate
    -- durante lo stesso allenamento.
    execution_id UUID NOT NULL,


    measurement_date TIMESTAMP
        NOT NULL
        DEFAULT CURRENT_TIMESTAMP,


    -- ======================================
    -- SERIE / SEGMENTO
    -- ======================================

    -- Serie dell'esercizio
    set_number SMALLINT NOT NULL DEFAULT 1,

    -- Utile per tecniche come drop-set,
    -- rest-pause, cluster ecc.
    --
    -- Esempio drop-set:
    --
    -- set 1 / segment 1 -> 100kg x 8
    -- set 1 / segment 2 -> 80kg  x 6
    -- set 1 / segment 3 -> 60kg  x 8
    segment_number SMALLINT NOT NULL DEFAULT 1,


    -- ======================================
    -- PERFORMANCE
    -- ======================================

    reps_completed SMALLINT,

    load_kg DECIMAL(7,2),

    tut_seconds INT,

    -- Recupero effettivamente utilizzato
    -- dopo il segmento / set
    rest_seconds INT,

    -- Durata complessiva del segmento
    duration_seconds INT,

    rir DECIMAL(3,1),

    rpe DECIMAL(3,1),

    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,


    -- ======================================
    -- CHECK
    -- ======================================

    CONSTRAINT chk_measurement_set
        CHECK (set_number > 0),

    CONSTRAINT chk_measurement_segment
        CHECK (segment_number > 0),

    CONSTRAINT chk_measurement_reps
        CHECK (
            reps_completed IS NULL
            OR reps_completed >= 0
        ),

    CONSTRAINT chk_measurement_load
        CHECK (
            load_kg IS NULL
            OR load_kg >= 0
        ),

    CONSTRAINT chk_measurement_tut
        CHECK (
            tut_seconds IS NULL
            OR tut_seconds >= 0
        ),

    CONSTRAINT chk_measurement_rest
        CHECK (
            rest_seconds IS NULL
            OR rest_seconds >= 0
        ),

    CONSTRAINT chk_measurement_duration
        CHECK (
            duration_seconds IS NULL
            OR duration_seconds >= 0
        ),

    CONSTRAINT chk_measurement_rir
        CHECK (
            rir IS NULL
            OR rir BETWEEN 0 AND 10
        ),

    CONSTRAINT chk_measurement_rpe
        CHECK (
            rpe IS NULL
            OR rpe BETWEEN 0 AND 10
        ),

    CONSTRAINT ux_workout_measurement_set
        UNIQUE (
            workout_exercise_id,
            execution_id,
            set_number,
            segment_number
        )
);

-- ==========================================
-- WORKOUT MODULE
-- Data model:
-- workout_plans -> workout_sessions -> workout_exercises -> workout_measurements
-- ==========================================

DROP TABLE IF EXISTS workout_measurements CASCADE;
DROP TABLE IF EXISTS workout_exercises CASCADE;
DROP TABLE IF EXISTS workout_sessions CASCADE;
DROP TABLE IF EXISTS workout_plans CASCADE;


-- ==========================================
-- 1. WORKOUT PLANS
-- ==========================================

CREATE TABLE workout_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_name VARCHAR(255) NOT NULL,
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    objective VARCHAR(255),
    description TEXT,
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,

    CONSTRAINT chk_workout_dates CHECK (
        end_date IS NULL
        OR start_date IS NULL
        OR end_date >= start_date
    )
);

CREATE UNIQUE INDEX ux_workout_plan_patient_name
ON workout_plans (
    patient_id,
    LOWER(TRIM(workout_name))
)
WHERE archived_at IS NULL;

CREATE INDEX ix_workout_plans_patient
ON workout_plans(patient_id);


-- ==========================================
-- 2. WORKOUT SESSIONS
-- ==========================================

CREATE TABLE workout_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_plan_id UUID NOT NULL
        REFERENCES workout_plans(id)
        ON DELETE CASCADE,

    session_name VARCHAR(150) NOT NULL,
    session_order SMALLINT NOT NULL DEFAULT 1,
    description TEXT,
    notes TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,

    CONSTRAINT chk_workout_session_order CHECK (session_order > 0)
);

CREATE INDEX ix_workout_sessions_plan
ON workout_sessions(workout_plan_id);

CREATE UNIQUE INDEX ux_workout_session_order
ON workout_sessions(workout_plan_id, session_order)
WHERE archived_at IS NULL;

CREATE UNIQUE INDEX ux_workout_session_name
ON workout_sessions(workout_plan_id, LOWER(TRIM(session_name)))
WHERE archived_at IS NULL;


-- ==========================================
-- 3. WORKOUT EXERCISES
-- ==========================================

CREATE TABLE workout_exercises (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_session_id UUID NOT NULL
        REFERENCES workout_sessions(id)
        ON DELETE CASCADE,

    exercise_name VARCHAR(255) NOT NULL,
    exercise_order SMALLINT NOT NULL DEFAULT 1,

    block_id UUID,
    block_type VARCHAR(50) NOT NULL DEFAULT 'STANDARD',
    block_order SMALLINT,
    block_rounds SMALLINT,

    target_sets SMALLINT,
    target_reps_min SMALLINT,
    target_reps_max SMALLINT,
    target_load_kg DECIMAL(7,2),
    target_tut_seconds INT,
    target_rest_seconds INT,
    target_rir DECIMAL(3,1),
    target_rpe DECIMAL(3,1),

    technique VARCHAR(50) NOT NULL DEFAULT 'STANDARD',
    technique_params JSONB NOT NULL DEFAULT '{}'::JSONB,
    progression_metrics TEXT[] NOT NULL DEFAULT ARRAY['LOAD']::TEXT[],

    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,

    CONSTRAINT chk_workout_exercise_order CHECK (exercise_order > 0),
    CONSTRAINT chk_workout_block_order CHECK (block_order IS NULL OR block_order > 0),
    CONSTRAINT chk_workout_block_rounds CHECK (block_rounds IS NULL OR block_rounds > 0),
    CONSTRAINT chk_workout_target_sets CHECK (target_sets IS NULL OR target_sets > 0),
    CONSTRAINT chk_workout_target_reps_min CHECK (target_reps_min IS NULL OR target_reps_min >= 0),
    CONSTRAINT chk_workout_target_reps_max CHECK (target_reps_max IS NULL OR target_reps_max >= 0),
    CONSTRAINT chk_workout_target_reps CHECK (
        target_reps_min IS NULL
        OR target_reps_max IS NULL
        OR target_reps_max >= target_reps_min
    ),
    CONSTRAINT chk_workout_target_load CHECK (target_load_kg IS NULL OR target_load_kg >= 0),
    CONSTRAINT chk_workout_target_tut CHECK (target_tut_seconds IS NULL OR target_tut_seconds >= 0),
    CONSTRAINT chk_workout_target_rest CHECK (target_rest_seconds IS NULL OR target_rest_seconds >= 0),
    CONSTRAINT chk_workout_target_rir CHECK (target_rir IS NULL OR target_rir BETWEEN 0 AND 10),
    CONSTRAINT chk_workout_target_rpe CHECK (target_rpe IS NULL OR target_rpe BETWEEN 0 AND 10),

    CONSTRAINT chk_workout_block_type CHECK (
        block_type IN (
            'STANDARD',
            'SUPERSET',
            'JUMP_SET',
            'TRI_SET',
            'GIANT_SET',
            'CIRCUIT'
        )
    ),

    CONSTRAINT chk_workout_block CHECK (
        block_type = 'STANDARD'
        OR block_id IS NOT NULL
    ),

    CONSTRAINT chk_workout_technique CHECK (
        technique IN (
            'STANDARD',
            'DROP_SET',
            'REST_PAUSE',
            'MYO_REPS',
            'CLUSTER',
            'AMRAP',
            'TEMPO',
            'PAUSE_REPS',
            'PARTIAL_REPS',
            'BACK_OFF'
        )
    ),

    CONSTRAINT chk_workout_progression_metrics CHECK (
        cardinality(progression_metrics) > 0
        AND progression_metrics <@ ARRAY[
            'LOAD',
            'VOLUME',
            'TUT',
            'DENSITY',
            'REPS'
        ]::TEXT[]
    )
);

CREATE INDEX ix_workout_exercises_session
ON workout_exercises(workout_session_id);

CREATE INDEX ix_workout_exercises_block
ON workout_exercises(block_id)
WHERE block_id IS NOT NULL;

CREATE UNIQUE INDEX ux_workout_exercise_order
ON workout_exercises(workout_session_id, exercise_order)
WHERE archived_at IS NULL;


-- ==========================================
-- 4. WORKOUT MEASUREMENTS
-- ==========================================

CREATE TABLE workout_measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_exercise_id UUID NOT NULL
        REFERENCES workout_exercises(id)
        ON DELETE RESTRICT,

    execution_id UUID NOT NULL,
    measurement_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    set_number SMALLINT NOT NULL DEFAULT 1,
    segment_number SMALLINT NOT NULL DEFAULT 1,

    reps_completed SMALLINT,
    load_kg DECIMAL(7,2),
    tut_seconds INT,
    rest_seconds INT,
    duration_seconds INT,
    rir DECIMAL(3,1),
    rpe DECIMAL(3,1),
    notes TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_measurement_set CHECK (set_number > 0),
    CONSTRAINT chk_measurement_segment CHECK (segment_number > 0),
    CONSTRAINT chk_measurement_reps CHECK (reps_completed IS NULL OR reps_completed >= 0),
    CONSTRAINT chk_measurement_load CHECK (load_kg IS NULL OR load_kg >= 0),
    CONSTRAINT chk_measurement_tut CHECK (tut_seconds IS NULL OR tut_seconds >= 0),
    CONSTRAINT chk_measurement_rest CHECK (rest_seconds IS NULL OR rest_seconds >= 0),
    CONSTRAINT chk_measurement_duration CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    CONSTRAINT chk_measurement_rir CHECK (rir IS NULL OR rir BETWEEN 0 AND 10),
    CONSTRAINT chk_measurement_rpe CHECK (rpe IS NULL OR rpe BETWEEN 0 AND 10),

    CONSTRAINT ux_workout_measurement_set UNIQUE (
        workout_exercise_id,
        execution_id,
        set_number,
        segment_number
    )
);

CREATE INDEX ix_workout_measurements_exercise
ON workout_measurements(workout_exercise_id);

CREATE INDEX ix_workout_measurements_execution
ON workout_measurements(execution_id);

CREATE INDEX ix_workout_measurements_date
ON workout_measurements(measurement_date);
