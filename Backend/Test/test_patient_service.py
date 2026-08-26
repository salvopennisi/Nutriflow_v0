import pytest
from unittest.mock import MagicMock, patch
from Backend.services.patient_service import (
    get_all_patients,
    get_patient_by_id,
    create_patient,
    update_patient,
    save_anamnesis_answers
)

# --- FIxTURES E SETUP ---

class MockConfig:
    DATABASE_URL = "postgres://fake:fake@localhost:5432/fake_db"

@pytest.fixture
def mock_conf():
    return MockConfig()

@pytest.fixture
def mock_db():
    """
    Fixture che mocka connect e disconnect.
    Ritorna un dizionario con i mock della connessione e del cursore per poterli testare.
    """
    with patch('Backend.services.patient_service.connect') as mock_connect, \
         patch('Backend.services.patient_service.disconnect') as mock_disconnect:
        
        # Creiamo i finti oggetti connessione e cursore
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Simuliamo il blocco "with conn.cursor() as cur:"
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        
        yield {
            "connect": mock_connect,
            "disconnect": mock_disconnect,
            "conn": mock_conn,
            "cursor": mock_cursor
        }

# --- TEST DEI SERVIZI ---

def test_get_all_patients(mock_conf, mock_db):
    # Dati finti che il DB dovrebbe ritornare
    fake_patients = [{'id': '1', 'nome': 'Mario', 'cognome': 'Rossi'}]
    mock_db["cursor"].fetchall.return_value = fake_patients
    
    # Eseguiamo la funzione
    result = get_all_patients(mock_conf, user_id='user-123')
    
    # Verifiche (Asserts)
    mock_db["cursor"].execute.assert_called_once()
    assert result == fake_patients
    mock_db["disconnect"].assert_called_once_with(mock_db["conn"])

def test_create_patient(mock_conf, mock_db):
    # Simuliamo che la query RETURNING id restituisca 'new-uuid-456'
    mock_db["cursor"].fetchone.return_value = {'id': 'new-uuid-456'}
    
    patient_data = {
        'user_id': 'user-123', 'nome': 'Luigi', 'cognome': 'Verdi', 
        'data_nascita': '1990-01-01', 'altezza_cm': 175, 'sesso': 'M',
        'stile_vita': 'Sedentario', 'categoria_energetica_professione': 'Impiegato',
        'descrizione_storia': '', 'patologie': 'Nessuna'
    }
    
    # Eseguiamo la funzione
    new_id = create_patient(mock_conf, patient_data)
    
    # Verifiche
    assert new_id == 'new-uuid-456'
    mock_db["cursor"].execute.assert_called_once()
    mock_db["conn"].commit.assert_called_once() # Verifica che la transazione sia stata confermata
    mock_db["disconnect"].assert_called_once()

def test_create_patient_rollback_on_error(mock_conf, mock_db):
    # Simuliamo un errore durante l'esecuzione della query
    mock_db["cursor"].execute.side_effect = Exception("DB Error")
    
    with pytest.raises(Exception):
        create_patient(mock_conf, {'nome': 'Test'})
    
    # Verifichiamo che in caso di errore venga fatto il rollback
    mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].commit.assert_not_called()
    mock_db["disconnect"].assert_called_once()

def test_save_anamnesis_answers(mock_conf, mock_db):
    answers = [
        {'question_id': 'q1', 'answer_text': 'Bevo 2 litri'},
        {'question_id': 'q2', 'answer_text': 'Nessuna allergia'}
    ]
    
    result = save_anamnesis_answers(mock_conf, patient_id='pat-1', answers=answers)
    
    assert result is True
    # Il cursore deve essere chiamato 3 volte: 1 DELETE + 2 INSERT
    assert mock_db["cursor"].execute.call_count == 3
    mock_db["conn"].commit.assert_called_once()