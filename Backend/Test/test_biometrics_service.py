import pytest
from unittest.mock import MagicMock, patch
from Backend.services.biometrics_service import (
    add_biometric_record,
    get_biometrics_history,
    get_latest_biometrics,
    delete_biometric_record
)

class MockConfig:
    DATABASE_URL = "postgres://fake:fake@localhost:5432/fake_db"

@pytest.fixture
def mock_conf():
    return MockConfig()

@pytest.fixture
def mock_db():
    with patch('Backend.services.biometrics_service.connect') as mock_connect, \
         patch('Backend.services.biometrics_service.disconnect') as mock_disconnect:
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        
        yield {
            "connect": mock_connect,
            "disconnect": mock_disconnect,
            "conn": mock_conn,
            "cursor": mock_cursor
        }

def test_add_biometric_record(mock_conf, mock_db):
    mock_db["cursor"].fetchone.return_value = {'id': 'bio-123'}
    
    # Passiamo solo alcuni parametri, il generatore query farà il resto
    data = {
        'patient_id': 'pat-1',
        'peso_kg': 82.5,
        'circ_vita_cm': 85.0,
        'algoritmo_bf_usato': 'Jackson-Pollock-3'
    }
    
    new_id = add_biometric_record(mock_conf, data)
    
    assert new_id == 'bio-123'
    # Verifica che il cursore sia stato chiamato per l'esecuzione e il db abbia fatto commit
    mock_db["cursor"].execute.assert_called_once()
    mock_db["conn"].commit.assert_called_once()

def test_get_biometrics_history(mock_conf, mock_db):
    fake_history = [
        {'id': 'bio-2', 'peso_kg': 81.0, 'timestamp': '2026-08-01'},
        {'id': 'bio-1', 'peso_kg': 82.5, 'timestamp': '2026-07-01'}
    ]
    mock_db["cursor"].fetchall.return_value = fake_history
    
    result = get_biometrics_history(mock_conf, patient_id='pat-1')
    
    assert len(result) == 2
    assert result[0]['peso_kg'] == 81.0
    mock_db["cursor"].execute.assert_called_once()

def test_add_biometric_record_rollback(mock_conf, mock_db):
    # Simuliamo un crash del DB (es. un tipo di dato errato)
    mock_db["cursor"].execute.side_effect = Exception("DB Crash")
    
    with pytest.raises(Exception):
        add_biometric_record(mock_conf, {'patient_id': 'pat-1', 'peso_kg': 'ottanta'})
        
    mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].commit.assert_not_called()